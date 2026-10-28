"""Podcasts, series and movies: checking sources, keeping the next episodes
downloaded, playing them in mpv and remembering what was watched."""

import os
import threading
import time as time_module

from ..gi_env import GLib, Gdk, Gio, Gtk, Pango
from ..config import load_media_settings, load_media_sources
from ..constants import CONFIG_FILE
from ..desktop import primary_monitor_size
from ..hls import is_hls
from ..media import (
    clean_title,
    download_episode,
    episode_basepath,
    episode_label,
    episode_paths,
    merge_episodes,
    new_unwatched,
    resolve_files,
    unwatched_count,
    upcoming,
)
from ..mediasources import NoDriver, normalize_files, normalize_saved
from ..network import run_when_online
from ..playback import counts_as_watched, play_media_file
from ..screen import media_target_size, remember_screen_size
from ..state import new_media_entry, save_media


MEDIA_CONFIG_DEBOUNCE_MS = 800  # an editor or the browser extension may write the file in several steps
PLAY_PARTIAL_MIN_BYTES = 1_500_000  # enough of a download for mpv to start on (about a minute of video)
MEDIA_MARKER_COLORS = {'ready': '#2e8b57', 'busy': '#d08000', 'failed': '#c0392b', 'idle': '#808080'}


def media_row_view(name, entry, order, downloaded, status, error=None):
    """What a source's row shows: (name, next episode text, stats text, tooltip,
    marker colour key). `downloaded` is whether the next episode is on disk and
    `status` its entry in the downloads' progress ('queued', 'downloading' with a
    percent, or 'failed' with an error), None when nothing is going on. `error` is
    why the source's last check failed, if it did."""
    last = entry.get('last_watched')
    last_line = f"Last watched: {last.get('title') or last['id']}" if last else ''
    error_line = f'Last check failed: {error}' if error else ''
    news = new_unwatched(entry)
    nxt = upcoming(entry.get('episodes', []), entry.get('watched', []), order, 1)
    if not entry.get('episodes') and error:
        return name, 'cannot be read', '', '\n'.join((error, 'Click to try again')), 'failed'
    if not nxt:
        text = 'all watched' if entry.get('episodes') else 'no episodes yet'
        return name, text, (f'{news} new' if news else ''), '\n'.join(p for p in (last_line, error_line) if p), 'idle'
    episode = nxt[0]
    label = episode_label(episode)
    text = ' '.join(p for p in (label, clean_title(episode.get('title'), (name, entry.get('title')))) if p)
    state = (status or {}).get('state')
    if state == 'downloading':
        stats, color, hint = f"{status.get('percent', 0)}%", 'busy', ('Click to watch while it downloads' if status.get('part') else 'Downloading')
    elif state == 'queued':
        stats, color, hint = 'queued', 'busy', 'Waiting to download'
    elif state == 'failed':
        stats, color, hint = 'failed', 'failed', f"Download failed: {status.get('error') or 'unknown error'} (retried at the next check; click to retry now)"
    elif downloaded:
        stats, color, hint = 'ready', 'ready', 'Click to play'
    else:
        stats, color, hint = 'not downloaded', 'idle', 'Click to download'
    if news:
        stats = f'{stats} - {news} new' if stats else f'{news} new'
    tooltip = '\n'.join(p for p in (text, hint, f'{unwatched_count(entry)} unwatched', last_line, error_line) if p)
    return name, text, stats, tooltip, color


def describe_media_error(error):
    """A source's failure as one line for its row: a driver's own message as it is
    (a ValueError, or no driver), anything else with the kind of error."""
    message = str(error).strip()
    if isinstance(error, (NoDriver, ValueError)) and message:
        return message[:300]
    return f'{error.__class__.__name__}: {message}'[:300].rstrip(': ')


class MediaMixin:
    # ---- checking the sources ------------------------------------------------

    def initial_media_check(self):
        run_when_online(self.start_media_check)
        return False

    def periodic_media_check(self):
        self.start_media_check()
        # re-arm with the interval currently in config.conf, so edits apply after the next check
        GLib.timeout_add_seconds(load_media_settings()['interval'], self.periodic_media_check)
        return False

    def start_media_check(self, only=None):
        """Checks every source, or just those in `only` (sources that were added to
        the config). A full check is skipped while another is running; a check of
        particular sources is queued behind it, never lost."""
        with self._media_request_lock:
            if only is None and self._media_checking:
                return  # a check is already in flight: skip this tick
            if only is None:
                self._media_pending_all = True
            else:
                self._media_pending |= set(only)
            if self._media_checking:
                return  # the running check looks for requests when it finishes
            self._media_checking = True
        threading.Thread(target=self._check_media_guarded, daemon=True).start()

    def _check_media_guarded(self):
        """Runs the requested checks one after another until none are waiting."""
        while True:
            with self._media_request_lock:
                everything, wanted = self._media_pending_all, set(self._media_pending)
                self._media_pending_all, self._media_pending = False, set()
                if not everything and not wanted:
                    self._media_checking = False  # decided under the lock, so no request slips in unseen
                    return
            try:
                self._check_media(None if everything else wanted)
            except BaseException:
                with self._media_request_lock:
                    self._media_checking = False
                raise

    def _check_media(self, only=None):
        """Reads every configured source (or those in `only`), remembers what is
        new, then queues the downloads. A source that cannot be read keeps what is
        known of it."""
        new_total = 0
        self.media_registry.refresh()  # picks up plugins that were added or edited
        for url, _name, configured in load_media_sources():
            if only is not None and url not in only:
                continue
            try:
                driver, target = self.media_registry.driver_for(url)
                title, episodes = driver.list_episodes(target)
            except Exception as error:  # network, bad JSON or feed, a plugin's bug: try again at the next check
                self.media_errors[url] = describe_media_error(error)
                if not isinstance(error, (NoDriver, ValueError)):
                    self.note_request_failure()
                continue
            self.media_errors.pop(url, None)
            with self._media_data_lock:
                entry = self.media['sources'].setdefault(url, new_media_entry())
                new_total += len(merge_episodes(entry, title, episodes, configured or driver.order))
                save_media(self.media)
        GLib.idle_add(self._on_media_new if new_total else self._refresh_media_rows)
        self._plan_media_downloads()

    def _measure_screen(self):
        """Remembers the monitor's size for the download threads, which choose how large
        a video to fetch by it, now and whenever the monitors change."""
        remember_screen_size(primary_monitor_size())
        screen = Gdk.Screen.get_default()
        if screen is not None:
            screen.connect('monitors-changed', lambda *_: remember_screen_size(primary_monitor_size()))

    def watch_media_config(self):
        """Looks out for changes to config.conf, so that a source added by an editor or
        by the browser extension is read at once instead of at the next check."""
        self._media_known_sources = {u for u, _n, _o in load_media_sources()}
        try:
            self._media_config_monitor = Gio.File.new_for_path(CONFIG_FILE).monitor_file(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return  # no file monitoring here: new sources wait for the next check
        self._media_config_monitor.connect('changed', self._on_media_config_event)

    def _on_media_config_event(self, _monitor, _file, _other, _event):
        if self._media_config_timer is not None:
            GLib.source_remove(self._media_config_timer)  # one reaction to a burst of events
        self._media_config_timer = GLib.timeout_add(MEDIA_CONFIG_DEBOUNCE_MS, self._media_config_changed)

    def _media_config_changed(self):
        self._media_config_timer = None
        urls = [u for u, _n, _o in load_media_sources()]
        new = [u for u in urls if u not in self._media_known_sources]
        self._media_known_sources = set(urls)
        if new:
            self.start_media_check(only=new)
        else:  # a name, an order or `ahead` may have changed: show it and keep the right ones downloaded
            self._plan_media_downloads()
            self._refresh_media_rows()
        return False

    def _on_media_new(self):
        if self.popup and self.popup.get_visible():
            self._refresh_media_rows()
        else:
            self.on_new_items()  # like a channel going live: sound and the popup opens
        return False

    # ---- downloads -----------------------------------------------------------

    @staticmethod
    def _media_file_ready(entry, episode_id):
        files = entry['files'].get(episode_id)
        return bool(files) and os.path.exists(files['video'])

    def _plan_media_downloads(self):
        """Queues the next `ahead` unwatched episodes of every source that are not on disk."""
        ahead = load_media_settings()['ahead']
        wanted = []
        if ahead > 0:
            with self._media_data_lock:
                for url, _name, order in load_media_sources():
                    entry = self.media['sources'].get(url)
                    if entry:
                        for episode in upcoming(entry['episodes'], entry['watched'], self._effective_order(url, order), ahead):
                            if not self._media_file_ready(entry, episode['id']):
                                wanted.append((url, episode['id']))
        self._enqueue_media(wanted)

    def _enqueue_media(self, items, front=False):
        with self._media_queue_lock:
            fresh = [i for i in items if i not in self._media_queue and i != self._media_current]
            self._media_queue = fresh + self._media_queue if front else self._media_queue + fresh
            for key in fresh:
                self.media_status[key] = {'state': 'queued'}
            start = bool(self._media_queue) and not self._media_worker_running
            if start:
                self._media_worker_running = True
        if start:
            threading.Thread(target=self._media_worker, daemon=True).start()
        if fresh:
            GLib.idle_add(self._refresh_media_rows)

    def _media_worker(self):
        while True:
            with self._media_queue_lock:
                if not self._media_queue:
                    self._media_worker_running = False
                    self._media_current = None
                    return
                key = self._media_queue.pop(0)
                self._media_current = key
            self._download_media_episode(*key)

    @staticmethod
    def _file_size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _media_display_name(self, url, entry):
        configured = {u: n for u, n, _o in load_media_sources()}.get(url)
        return configured or entry.get('title') or url

    def _download_media_episode(self, url, episode_id):
        key = (url, episode_id)
        with self._media_data_lock:
            entry = self.media['sources'].get(url)
            episode = next((e for e in entry['episodes'] if e['id'] == episode_id), None) if entry else None
            taken = {f['video'] for i, f in entry['files'].items() if i != episode_id} if entry else set()  # its own old path is free
            name = self._media_display_name(url, entry) if entry else ''
        if episode is None:
            self.media_status.pop(key, None)
            return
        self.media_status[key] = {'state': 'downloading', 'percent': 0}
        GLib.idle_add(self._refresh_media_rows)
        shown = [-1]
        watchable = {}  # filled in for an HLS download to .ts or .mkv: its partial file plays while it grows

        def progress(done, total):
            percent = int(done * 100 / total) if total else 0
            if percent != shown[0]:
                shown[0] = percent
                self.media_status[key] = {'state': 'downloading', 'percent': percent, **watchable,
                                          **({'length': total} if watchable and total else {})}  # seconds, for HLS
                GLib.idle_add(self._refresh_media_rows)

        try:
            driver = self.media_registry.driver_of(episode)
            root, names = load_media_settings()['path'], (entry.get('title'),)
            if driver.download:  # the driver fetches the files itself (yt-dlp, a plugin)
                base = episode_basepath(root, name, episode, taken, names=names)
                saved = normalize_saved(driver.download(episode, base, progress, lambda: False))
            else:
                files = normalize_files(driver.resolve(episode) if driver.resolve else resolve_files(episode))
                paths = episode_paths(root, name, episode, files, taken, names=names)
                if is_hls(files['video']['url']) and paths['video'].lower().endswith(('.ts', '.mkv')):
                    watchable['part'] = paths['video'] + '.part'
                    self.media_status[key] = {'state': 'downloading', 'percent': max(shown[0], 0), **watchable}
                saved = download_episode(paths, files, progress=progress, screen=media_target_size())
        except Exception as error:  # the partial file stays, so the next try carries on
            self.media_status[key] = {'state': 'failed', 'error': describe_media_error(error)}
            GLib.idle_add(self._refresh_media_rows)
            return
        with self._media_data_lock:
            entry['files'][episode_id] = saved
            save_media(self.media)
        self.media_status.pop(key, None)
        GLib.idle_add(self._refresh_media_rows)

    # ---- playing and remembering ---------------------------------------------

    def _effective_order(self, url, configured):
        """The configured order, else the default of the driver that handles the source."""
        if configured in ('oldest', 'newest'):
            return configured
        try:
            return self.media_registry.driver_for(url)[0].order
        except NoDriver:
            return 'newest'

    def _media_source_order(self, url):
        return self._effective_order(url, next((o for u, _n, o in load_media_sources() if u == url), None))

    def on_media_row_clicked(self, url):
        """Plays a source's next unwatched episode, or starts downloading it first."""
        order = self._media_source_order(url)
        with self._media_data_lock:
            entry = self.media['sources'].get(url)
            nxt = upcoming(entry['episodes'], entry['watched'], order, 1) if entry else []
            if not nxt:
                retry = not (entry and entry['episodes']) and url in self.media_errors
                if retry:
                    self.start_media_check()  # an unreadable source: try it again now
                return
            episode = nxt[0]
            ready = self._media_file_ready(entry, episode['id'])
            path = entry['files'][episode['id']]['video'] if ready else None
            name = self._media_display_name(url, entry)
        length = None  # the playlist's length, when playing a download that is not finished
        if not ready:
            status = self.media_status.get((url, episode['id']), {})
            part = status.get('part')
            if status.get('state') == 'downloading' and part and self._file_size(part) >= PLAY_PARTIAL_MIN_BYTES:
                path, length = part, status.get('length')  # watch it while it downloads
            else:
                if status.get('state') not in ('queued', 'downloading'):
                    self._enqueue_media([(url, episode['id'])], front=True)
                return
        if self.popup:
            self.popup.hide()
        shown = clean_title(episode.get('title'), (name, entry.get('title')))
        title = f"{name} \u00b7 {' '.join(p for p in (episode_label(episode), shown) if p)}"
        episode_id = episode['id']
        # A partial file's own length is only an estimate, so how far it got is measured against the
        # playlist's; without that, playing it is not tracked at all.
        track = ready or bool(length)
        play_media_file(path, title, total_seconds=length,
                        on_finished=(lambda percent, remaining:
                                     GLib.idle_add(self._on_media_played, url, episode_id, percent, remaining)) if track else None)

    def _on_media_played(self, url, episode_id, percent, remaining):
        if counts_as_watched(percent, remaining):
            self.mark_media_watched(url, episode_id)
        return False

    def mark_media_watched(self, url, episode_id):
        with self._media_data_lock:
            entry = self.media['sources'].get(url)
            if not entry:
                return
            if episode_id not in entry['watched']:
                entry['watched'].append(episode_id)
            episode = next((e for e in entry['episodes'] if e['id'] == episode_id), {})
            entry['last_watched'] = {'id': episode_id, 'title': episode.get('title') or episode_id, 'at': time_module.time()}
            entry['new'] = [i for i in entry['new'] if i != episode_id]
            save_media(self.media)
        self._plan_media_downloads()  # keeps the next ones ready
        self._refresh_media_rows()

    # ---- the popup's rows ----------------------------------------------------

    def media_source_urls(self):
        """The configured sources that have been read at least once, or that could not
        be, in config order."""
        with self._media_data_lock:
            return [url for url, _n, _o in load_media_sources()
                    if self.media['sources'].get(url, {}).get('episodes') or url in self.media_errors]

    def build_media_row(self, url):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(True)
        row.media_source = url

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_margin_start(3)
        box.set_margin_end(3)
        row.media_marker = Gtk.Label()
        box.pack_start(row.media_marker, False, False, 0)
        row.media_label = Gtk.Label()
        row.media_label.set_xalign(0)
        row.media_label.set_ellipsize(Pango.EllipsizeMode.END)  # a long title shrinks, the stats stay visible
        box.pack_start(row.media_label, False, False, 0)
        row.media_stats = Gtk.Label()
        box.pack_start(row.media_stats, False, False, 0)
        filler = Gtk.Box()
        filler.set_hexpand(True)
        box.pack_start(filler, True, True, 0)
        row.add(box)
        self._update_media_row(row, {u: (n, o) for u, n, o in load_media_sources()})
        return row

    def _update_media_row(self, row, configured):
        url = row.media_source
        with self._media_data_lock:
            entry = self.media['sources'].get(url)
            error = self.media_errors.get(url)
            if not entry and not error:
                return
            entry = entry or new_media_entry()  # a source that has never been read, only failed
            name, order = configured.get(url, (None, None))
            order = self._effective_order(url, order)
            nxt = upcoming(entry['episodes'], entry['watched'], order, 1)
            ready = bool(nxt) and self._media_file_ready(entry, nxt[0]['id'])
            status = self.media_status.get((url, nxt[0]['id'])) if nxt else None
            name, text, stats, tooltip, color = media_row_view(
                name or entry.get('title') or url, entry, order, ready, status, error)
        escape = GLib.markup_escape_text
        row.media_marker.set_markup(f'<span foreground="{MEDIA_MARKER_COLORS[color]}">\u25cf</span>')
        row.media_label.set_markup(f'<span foreground="#000000"><b>{escape(name)}</b> - {escape(text)}</span>')
        row.media_stats.set_markup(f'<span foreground="#000000">- {escape(stats)}</span>' if stats else '')
        row.set_tooltip_text(tooltip or None)

    def _refresh_media_rows(self):
        """Updates the rows in place: rebuilding the list would collapse a popup
        that has only media to show."""
        if self.popup:
            configured = {u: (n, o) for u, n, o in load_media_sources()}
            for row in self.listbox.get_children():
                if hasattr(row, 'media_source'):
                    self._update_media_row(row, configured)
        return False
