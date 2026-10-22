"""Offline state, probes and the network reset button."""

import threading
import time as time_module
from datetime import datetime

from ..gi_env import GLib
from ..config import load_network_reset_command
from ..constants import OFFLINE_BANNER_TEXT
from ..desktop import run_launcher_command
from ..log import _log
from ..network import diagnose_connectivity, next_connectivity


CONFIRM_PROBE_SECONDS = 3  # a failed probe is re-checked this soon instead of waiting for the next tick


PROBE_REQUEST_MIN_GAP_SECONDS = 5  # throttle for probes triggered by failing requests


NETWORK_RESET_COOLDOWN_SECONDS = 20


class ConnectivityMixin:
    def probe_connectivity(self):
        """Background network probe feeding the popup's offline indicator.
        Returns True so it can also be used directly as a repeating timer."""
        if not self._probing:
            self._probing = True

            def work():
                GLib.idle_add(self._on_probe_result, diagnose_connectivity())
            threading.Thread(target=work, daemon=True).start()
        return True

    def request_probe(self):
        """Probe now (throttled) -- called when a real request fails, so the
        indicator doesn't wait for the next 10 s tick. Main thread only."""
        now = time_module.monotonic()
        if now - self._last_probe_request >= PROBE_REQUEST_MIN_GAP_SECONDS:
            self._last_probe_request = now
            self.probe_connectivity()
        return False

    def note_request_failure(self):
        """Safe from any thread."""
        GLib.idle_add(self.request_probe)

    def _confirm_probe(self):
        self._confirm_pending = False
        self.probe_connectivity()
        return False

    def _on_probe_result(self, result):
        self._probing = False
        self.offline_detail = result['detail']
        online, self._probe_failures = next_connectivity(self.online, self._probe_failures, result['online'])
        if online != self.online:
            self._set_online(online)
        elif self.offline_banner is not None and not online:
            self.offline_banner.set_tooltip_text(self._offline_tooltip())  # the cause may have changed
        if not result['online'] and self.online and not self._confirm_pending:
            # first failure while still "online": confirm quickly rather than in 10 s
            self._confirm_pending = True
            GLib.timeout_add_seconds(CONFIRM_PROBE_SECONDS, self._confirm_probe)
        return False

    def _offline_tooltip(self):
        text = self.offline_detail or 'no connection'
        if self.offline_since:
            text += f"\noffline since {datetime.fromtimestamp(self.offline_since):%H:%M}"
        return text

    def _set_online(self, online):
        came_back = online and not self.online
        self.online = online
        if not online:
            self.offline_since = time_module.time()
            _log(f'offline: {self.offline_detail}')
        elif came_back:
            _log('back online')
            self.offline_since = None
        if self.offline_banner is not None:
            self.offline_banner.set_visible(not online)
            self.offline_banner.set_tooltip_text(None if online else self._offline_tooltip())
        if came_back:
            # don't wait out the remaining intervals after an outage
            self.start_check_thread()
            self.start_weather_fetch()
            self.start_twitch_check()
            self.start_youtube_check()
            self.start_media_check()

    def on_network_reset_clicked(self, _button):
        command = load_network_reset_command()
        if not command:
            return
        run_launcher_command(command)
        self.reset_btn.set_sensitive(False)
        self.offline_banner_label.set_text('Resetting network\u2026')
        GLib.timeout_add_seconds(6, self._probe_after_reset)
        GLib.timeout_add_seconds(NETWORK_RESET_COOLDOWN_SECONDS, self._end_network_reset)

    def _probe_after_reset(self):
        self.probe_connectivity()
        return False

    def _end_network_reset(self):
        if self.offline_banner_label is not None:
            self.offline_banner_label.set_text(OFFLINE_BANNER_TEXT)
            self.reset_btn.set_sensitive(True)
        return False
