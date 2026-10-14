"""Twitch/YouTube checks wired to the popup and the live rows."""

import threading
import time as time_module
from datetime import datetime

from ..gi_env import GLib, Gtk, Pango
from ..config import (
    load_twitch_channels,
    load_twitch_check_interval,
    load_twitch_default_quality,
    load_twitch_qualities,
    load_twitch_schedules,
    load_youtube_channels,
    load_youtube_check_interval,
    load_youtube_default_quality,
    load_youtube_qualities,
    load_youtube_schedules,
)
from ..live import check_twitch_live_channels, check_youtube_live_channels, live_stats_text
from ..network import run_when_online
from ..playback import is_stream_playing, live_marker_markup
from ..state import save_state
from ..windows import channels_due, scheduled_active_channels


class LiveMixin:
    @staticmethod
    def _current_scheduled_active(now=None):
        now = now or datetime.now()
        return {
            'twitch': scheduled_active_channels(load_twitch_schedules(), now),
            'youtube': scheduled_active_channels(load_youtube_schedules(), now),
        }

    def _live_schedule_tick(self):
        """Once a minute (no network): when a scheduled channel's window has
        just opened, check right away instead of waiting for the next
        interval, so a stream starting on time shows up on time."""
        current = self._current_scheduled_active()
        opened = {platform: current[platform] - self._scheduled_active[platform] for platform in current}
        self._scheduled_active = current
        if opened['twitch']:
            self.start_twitch_check()
        if opened['youtube']:
            self.start_youtube_check()
        return True

    def initial_twitch_check(self):
        run_when_online(self.start_twitch_check)
        return False

    def periodic_twitch_check(self):
        self.start_twitch_check()
        # re-arm with the interval currently in config.conf, so edits apply
        # after the next check without restarting the app
        GLib.timeout_add_seconds(load_twitch_check_interval(), self.periodic_twitch_check)
        return False

    def start_twitch_check(self):
        if not self._twitch_lock.acquire(blocking=False):
            return  # a check is already in flight — skip this tick
        threading.Thread(target=self._check_twitch_guarded, daemon=True).start()

    def _check_twitch_guarded(self):
        try:
            channels = load_twitch_channels()
            if channels:
                with self.lock:
                    live = {e['channel'] for e in self.state.get('live_channels', [])}
                due = channels_due(channels, load_twitch_schedules(), live, datetime.now())
                live_now = check_twitch_live_channels(due)
                if live_now is not None:  # failed check: keep last known live state
                    GLib.idle_add(self._on_twitch_checked, live_now)
                else:
                    self.note_request_failure()
        finally:
            self._twitch_lock.release()

    def _on_twitch_checked(self, live_now):
        with self.lock:
            was_live = {e['channel'] for e in self.state.get('live_channels', [])}
            self.state['live_channels'] = [
                {'channel': ch, **live_now[ch]} for ch in sorted(live_now)
            ]
            save_state(self.state)
        newly_live = set(live_now) - was_live
        if newly_live:
            self.on_new_items()
        else:
            self.update_icon()
            if self.popup and self.popup.get_visible():
                self.refresh_list()
        return False

    def initial_youtube_check(self):
        run_when_online(self.start_youtube_check)
        return False

    def periodic_youtube_check(self):
        self.start_youtube_check()
        GLib.timeout_add_seconds(load_youtube_check_interval(), self.periodic_youtube_check)
        return False

    def start_youtube_check(self):
        if not self._youtube_lock.acquire(blocking=False):
            return  # a check is already in flight — skip this tick
        threading.Thread(target=self._check_youtube_guarded, daemon=True).start()

    def _check_youtube_guarded(self):
        try:
            channels = load_youtube_channels()
            if channels:
                with self.lock:
                    live = {e['channel'] for e in self.state.get('live_youtube_channels', [])}
                due = channels_due(channels, load_youtube_schedules(), live, datetime.now())
                live_now = check_youtube_live_channels(due)
                if live_now is not None:  # failed check: keep last known live state
                    GLib.idle_add(self._on_youtube_checked, live_now)
        finally:
            self._youtube_lock.release()

    def _on_youtube_checked(self, live_now):
        with self.lock:
            was_live = {e['channel'] for e in self.state.get('live_youtube_channels', [])}
            self.state['live_youtube_channels'] = [
                {'channel': ch, **live_now[ch]} for ch in sorted(live_now)
            ]
            save_state(self.state)
        newly_live = set(live_now) - was_live
        if newly_live:
            self.on_new_items()
        else:
            self.update_icon()
            if self.popup and self.popup.get_visible():
                self.refresh_list()
        return False

    def _build_live_row(self, entry, site, color):
        """Shared by the Twitch and YouTube rows: '<user> - <category> -
        <viewers> - <elapsed>' (parts the platform doesn't give are left out)
        with the stream title as a tooltip; the leading bullet becomes a play
        triangle while this channel is what mpv is playing."""
        channel = entry['channel']
        title = entry.get('title') or ''
        category = entry.get('category') or ''

        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(True)
        row.live_title = title

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_margin_start(3)
        box.set_margin_end(3)
        box.set_margin_top(0)
        box.set_margin_bottom(0)

        marker = Gtk.Label()
        marker.set_markup(live_marker_markup(color, is_stream_playing(site, channel)))
        box.pack_start(marker, False, False, 0)

        markup = f'<b>{GLib.markup_escape_text(channel)}</b>'
        if category:
            markup += f' - {GLib.markup_escape_text(category)}'
        label = Gtk.Label()
        label.set_markup(f'<span foreground="#000000">{markup}</span>')
        label.set_xalign(0)
        label.set_ellipsize(Pango.EllipsizeMode.END)  # a long game name shrinks, the stats stay visible
        box.pack_start(label, False, False, 0)

        stats = live_stats_text(entry, time_module.time())
        if stats:
            stats_label = Gtk.Label()
            stats_label.set_markup(f'<span foreground="#000000">- {GLib.markup_escape_text(stats)}</span>')
            box.pack_start(stats_label, False, False, 0)
        filler = Gtk.Box()
        filler.set_hexpand(True)
        box.pack_start(filler, True, True, 0)

        if title:
            row.set_tooltip_text(title)
            label.set_tooltip_text(title)

        row.add(box)
        return row

    def build_twitch_row(self, entry):
        row = self._build_live_row(entry, 'Twitch', '#9146FF')
        row.twitch_channel = entry['channel']
        row.twitch_quality = load_twitch_qualities().get(entry['channel']) or load_twitch_default_quality()
        return row

    def build_youtube_row(self, entry):
        row = self._build_live_row(entry, 'YouTube', '#FF0000')
        row.youtube_channel = entry['channel']
        row.youtube_quality = load_youtube_qualities().get(entry['channel']) or load_youtube_default_quality()
        return row
