"""Podcasts, series and movies: checking sources, keeping the next episodes
downloaded, playing them in mpv and remembering what was watched."""

import os
import threading
import time as time_module

from ..gi_env import GLib, Gtk, Pango
from ..config import load_media_settings, load_media_sources
from ..media import (
    clean_title,
    download_episode,
    episode_label,
    episode_paths,
    list_episodes,
    merge_episodes,
    new_unwatched,
    resolve_files,
    source_order,
    unwatched_count,
    upcoming,
)
from ..network import run_when_online
from ..playback import counts_as_watched, play_media_file
from ..state import new_media_entry, save_media


MEDIA_MARKER_COLORS = {'ready': '#2e8b57', 'busy': '#d08000', 'failed': '#c0392b', 'idle': '#808080'}


def media_row_view(name, entry, order, downloaded, status):
    """What a source's row shows: (name, next episode text, stats text, tooltip,
    marker colour key). `downloaded` is whether the next episode is on disk and
    `status` its entry in the downloads' progress ('queued', 'downloading' with a
    percent, or 'failed' with an error), None when nothing is going on."""
    last = entry.get('last_watched')
    last_line = f"Last watched: {last.get('title') or last['id']}" if last else ''
    news = new_unwatched(entry)
    nxt = upcoming(entry.get('episodes', []), entry.get('watched', []), order, 1)
    if not nxt:
        text = 'all watched' if entry.get('episodes') else 'no episodes yet'
        return name, text, (f'{news} new' if news else ''), last_line, 'idle'
    episode = nxt[0]
    label = episode_label(episode)
    text = ' '.join(p for p in (label, clean_title(episode.get('title'), (name, entry.get('title')))) if p)
    state = (status or {}).get('state')
    if state == 'downloading':
        stats, color, hint = f"{status.get('percent', 0)}%", 'busy', 'Downloading'
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
    tooltip = '\n'.join(p for p in (text, hint, f'{unwatched_count(entry)} unwatched', last_line) if p)
    return name, text, stats, tooltip, color


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

    def start_media_check(self):
        if not self._media_check_lock.acquire(blocking=False):
            return  # a check is already in flight: skip this tick
        threading.Thread(target=self._check_media_guarded, daemon=True).start()

    def _check_media_guarded(self):
        try:
            self._check_media()
        finally:
            self._media_check_lock.release()

    def _check_media(self):
        """Reads every configured source, remembers what is new, then queues the
        downloads. A source that cannot be read keeps what is known of it."""
        new_total = 0
        for url, _name, order in load_media_sources():
            try:
                title, episodes = list_episodes(url)
            except Exception:  # network, bad JSON or feed: try again at the next check
                self.note_request_failure()
                continue
            with self._media_data_lock:
                entry = self.media['sources'].setdefault(url, new_media_entry())
                new_total += len(merge_episodes(entry, title, episodes, source_order(url, order)))
                save_media(self.media)
        GLib.idle_add(self._on_media_new if new_total else self._refresh_media_rows)
        self._plan_media_downloads()

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
                        for episode in upcoming(entry['episodes'], entry['watched'], source_order(url, order), ahead):
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

        def progress(done, total):
            percent = int(done * 100 / total) if total else 0
            if percent != shown[0]:
                shown[0] = percent
                self.media_status[key] = {'state': 'downloading', 'percent': percent}
                GLib.idle_add(self._refresh_media_rows)

        try:
            files = resolve_files(episode)
            paths = episode_paths(load_media_settings()['path'], name, episode, files, taken, names=(entry.get('title'),))
            saved = download_episode(paths, files, progress=progress)
        except Exception as error:  # the partial file stays, so the next try carries on
            self.media_status[key] = {'state': 'failed', 'error': str(error) or error.__class__.__name__}
            GLib.idle_add(self._refresh_media_rows)
            return
        with self._media_data_lock:
            entry['files'][episode_id] = saved
            save_media(self.media)
        self.media_status.pop(key, None)
        GLib.idle_add(self._refresh_media_rows)

    # ---- playing and remembering ---------------------------------------------

    def _media_source_order(self, url):
        return next((source_order(u, o) for u, _n, o in load_media_sources() if u == url), source_order(url, None))

    def on_media_row_clicked(self, url):
        """Plays a source's next unwatched episode, or starts downloading it first."""
        order = self._media_source_order(url)
        with self._media_data_lock:
            entry = self.media['sources'].get(url)
            nxt = upcoming(entry['episodes'], entry['watched'], order, 1) if entry else []
            if not nxt:
                return
            episode = nxt[0]
            ready = self._media_file_ready(entry, episode['id'])
            path = entry['files'][episode['id']]['video'] if ready else None
            name = self._media_display_name(url, entry)
        if not ready:
            if self.media_status.get((url, episode['id']), {}).get('state') not in ('queued', 'downloading'):
                self._enqueue_media([(url, episode['id'])], front=True)
            return
        if self.popup:
            self.popup.hide()
        shown = clean_title(episode.get('title'), (name, entry.get('title')))
        title = f"{name} \u00b7 {' '.join(p for p in (episode_label(episode), shown) if p)}"
        episode_id = episode['id']
        play_media_file(path, title, on_finished=lambda percent, remaining:
                        GLib.idle_add(self._on_media_played, url, episode_id, percent, remaining))

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
        """The configured sources that have been read at least once, in config order."""
        with self._media_data_lock:
            return [url for url, _n, _o in load_media_sources() if self.media['sources'].get(url, {}).get('episodes')]

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
            if not entry:
                return
            name, order = configured.get(url, (None, None))
            order = source_order(url, order)
            nxt = upcoming(entry['episodes'], entry['watched'], order, 1)
            ready = bool(nxt) and self._media_file_ready(entry, nxt[0]['id'])
            status = self.media_status.get((url, nxt[0]['id'])) if nxt else None
            name, text, stats, tooltip, color = media_row_view(name or entry.get('title') or url, entry, order, ready, status)
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
