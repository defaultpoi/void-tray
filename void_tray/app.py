"""The RssTray application class, signal handling and main()."""

import signal
import socket
import threading

from .gi_env import GLib, GLibUnix, Gtk
from .config import (
    ensure_config,
    load_media_settings,
    load_twitch_check_interval,
    load_weather_refresh_interval,
    load_youtube_check_interval,
    migrate_legacy_config,
)
from .constants import DEFAULT_HIGHLIGHT_WORDS, MEDIA_PLUGIN_DIR
from .state import load_media, load_state
from .ui.connectivity import ConnectivityMixin
from .ui.feeds import FeedsMixin
from .ui.icon import IconMixin
from .mediasources import MediaSources
from .ui.live import LiveMixin
from .ui.media import MediaMixin
from .ui.popup import PopupMixin, WINDOW_WIDTH
from .ui.timer import TimerMixin
from .ui.updates import UpdatesMixin
from .ui.weather import WeatherMixin


class RssTray(ConnectivityMixin, FeedsMixin, IconMixin, LiveMixin, MediaMixin, PopupMixin, TimerMixin, UpdatesMixin, WeatherMixin):
    def __init__(self):
        ensure_config()
        self.state = load_state()
        self.lock = threading.Lock()
        self._check_lock = threading.Lock()  # guards overlapping feed-check cycles only
        self._xbps_lock = threading.Lock()   # guards xbps db access (scans + installs), separately
        self._twitch_lock = threading.Lock()  # guards overlapping Twitch-check cycles
        self._youtube_lock = threading.Lock()  # guards overlapping YouTube-check cycles
        self.media = load_media()  # podcasts, series and movies (media.json)
        self.media_status = {}  # (source url, episode id) -> download progress, in memory only
        self.media_errors = {}  # source -> why its last check failed, in memory only
        self.media_registry = MediaSources(MEDIA_PLUGIN_DIR)  # built-in drivers and the plugins
        self._media_request_lock = threading.Lock()  # guards the four fields below
        self._media_checking = False  # a media-check thread is running
        self._media_pending_all = False  # a full check was asked for while one was running
        self._media_pending = set()  # sources to check once it is done
        self._media_known_sources = set()  # the [media] sources the last look at config.conf found
        self._media_config_monitor = None  # keeps the config.conf watcher alive
        self._media_config_timer = None  # the pending reaction to a change of it
        self._media_data_lock = threading.Lock()  # guards self.media
        self._media_queue_lock = threading.Lock()  # guards the download queue below
        self._media_queue = []
        self._media_current = None
        self._media_worker_running = False
        self.popup = None
        self.popup_width = WINDOW_WIDTH  # WINDOW_WIDTH, or wider when the weather bar needs it (see _fit_popup_width)
        self.listbox = None
        self.scroller = None
        self._launcher_open = False
        self._highlight_words = DEFAULT_HIGHLIGHT_WORDS
        self._breaking_rows = []          # news rows that pulse (rebuilt with the list)
        self._breaking_on = False
        self.online = True            # last known connectivity (see probe_connectivity)
        self._probe_failures = 0
        self._probing = False
        self._last_probe_request = 0.0
        self._confirm_pending = False
        self.offline_detail = ''
        self.offline_since = None
        self.offline_banner = None
        self.offline_banner_label = None
        self.reset_btn = None
        self.weather_data = None
        self.weather_box = None
        self.weather_view = 'today'
        self.active_alerts = []
        self._alert_pulse_counter = 0
        self._last_pulse_signature = None
        self.active_installs = 0
        self.install_status = {}  # pkgname -> 'Waiting…'/'Downloading…'/'Installing…'/'Done'/'Failed'
        self.orphan_status = None  # 'Removing…'/'Done'/'Failed' while the orphaned-packages row shows a result
        self.orphans_expanded = False  # the row has been clicked: Remove / Cancel are showing under it
        self.update_summary = None  # topgrade's closing summary of the last Install all, shown until dismissed
        self.timer_remaining_seconds = 0
        self.timer_running = False
        self.timer_deadline = None  # boot_clock() value at which the timer fires
        self.timer_end_wall = None  # the same moment as a wall-clock timestamp, kept in state.json across restarts
        self.timer_missed_boot = None  # boot_clock() value a countdown was due at when it ran out unseen (suspend)
        self.timer_overlay = None
        self._timer_updating_ui = False
        self.timer_scale = None
        self.timer_label = None
        self.timer_box = None
        self.timer_status_box = None  # the corner readout shown while the Timer slide is closed
        self.timer_status_label = None
        self._timer_status_text = None
        self.timer_visible = False
        self.timer_mode = 'countdown'     # or 'schedule' (the slide's two modes)
        self._mode_updating = False
        self.mode_buttons = {}
        self.countdown_box = None
        self.schedule_box = None
        self.sched = None                 # widgets of the Schedule controls (per popup)
        self.sched_index = 0              # which schedule the controls show
        self._sched_updating = False
        self.list_spacer = None
        self.schedule_banner = None
        self.schedule_banner_label = None
        self.schedule_warning = None      # {'occ', 'left', 'action'} while the pre-action warning shows
        self._schedule_flash_on = False
        self._last_schedule_tick = None
        self._schedule_cache = (None, None)
        self._schedule_resync = True

        self._apply_compact_css()
        self._restore_countdown()

        self.status_icon = Gtk.StatusIcon()
        self.status_icon.connect('activate', self.toggle_popup)
        self.status_icon.connect('popup-menu', self.toggle_popup)
        self.update_icon()

        GLib.timeout_add_seconds(1, self.initial_check)
        GLib.timeout_add_seconds(SCHEDULER_TICK_SECONDS, self.periodic_check)
        GLib.timeout_add(800, self.maybe_auto_show_startup)
        GLib.timeout_add_seconds(2, self.initial_weather_check)
        GLib.timeout_add_seconds(load_weather_refresh_interval(), self.periodic_weather_check)
        GLib.timeout_add(ALERT_PULSE_INTERVAL_MS, self._alert_pulse_tick)
        GLib.timeout_add_seconds(3, self.initial_twitch_check)
        GLib.timeout_add_seconds(load_twitch_check_interval(), self.periodic_twitch_check)
        GLib.timeout_add_seconds(4, self.initial_youtube_check)
        GLib.timeout_add_seconds(load_youtube_check_interval(), self.periodic_youtube_check)
        GLib.timeout_add_seconds(5, self.initial_media_check)
        GLib.timeout_add_seconds(load_media_settings()['interval'], self.periodic_media_check)
        self.watch_media_config()
        GLib.timeout_add_seconds(1, self._timer_tick)
        GLib.timeout_add_seconds(1, lambda: self.probe_connectivity() and False)
        GLib.timeout_add_seconds(OFFLINE_PROBE_SECONDS, self.probe_connectivity)
        GLib.timeout_add(BREAKING_PULSE_MS, self._breaking_pulse_tick)
        self._scheduled_active = self._current_scheduled_active()
        GLib.timeout_add_seconds(60, self._live_schedule_tick)


socket.setdefaulttimeout(15)  # avoid feed fetches hanging indefinitely on slow/broken servers


SCHEDULER_TICK_SECONDS = 60  # how often we check whether any feed is due


OFFLINE_PROBE_SECONDS = 10  # how often the popup's offline indicator re-probes the network


BREAKING_PULSE_MS = 700  # pulse period of highlighted ('breaking') news rows


ALERT_PULSE_INTERVAL_MS = 600


def _unix_signal_add(signum, callback):
    """Runs `callback` on the GLib main loop when `signum` arrives. Newer GLib
    moved this to GLibUnix (GLib.unix_signal_add is deprecated there and warns
    on every call); older ones only have the GLib version."""
    if GLibUnix is not None:
        for name in ('signal_add', 'signal_add_full'):
            add = getattr(GLibUnix, name, None)
            if add is not None:
                return add(GLib.PRIORITY_DEFAULT, signum, callback)
    return GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, callback)


def _install_quit_signal_handlers():
    """SIGTERM (session logout, `kill`) and SIGINT (Ctrl+C) leave the GTK main
    loop cleanly, so atexit hooks run and the local streamlink server isn't
    left behind."""
    def quit_loop():
        Gtk.main_quit()
        return GLib.SOURCE_REMOVE
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            _unix_signal_add(sig, quit_loop)
        except (AttributeError, TypeError):
            pass  # very old PyGObject: keep the default behaviour


def main():
    migrate_legacy_config()
    RssTray()
    _install_quit_signal_handlers()
    Gtk.main()
