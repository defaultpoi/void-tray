"""Feed checking, the unread list actions and the feed menu."""

import feedparser
import threading
import time as time_module
import webbrowser

from ..gi_env import GLib, Gdk, Gtk
from ..config import load_feeds, load_mute_filters
from ..desktop import play_notification_sound
from ..network import run_when_online
from ..rules import (
    add_feed_mute_rule,
    entry_age_seconds,
    entry_author_name,
    entry_id,
    entry_matches_mute_rules,
    entry_tag_terms,
    escape_mute_value,
    feed_source_url,
    is_muted,
    load_feed_mute_rules,
    parse_mute_rules,
    unread_feed_terms,
)
from ..state import save_state


FEED_RETRY_SECONDS = 120  # retry a feed this soon after a failed fetch (instead of a full interval)


MAX_UNREAD_ITEMS = 500  # stored unread items; the oldest beyond this are dropped (they stay 'seen')


MAX_ITEM_AGE_SECONDS = 24 * 3600  # ignore entries older than this on first sight


SEEN_RETENTION_SECONDS = 30 * 24 * 3600  # prune seen-item records older than this


FEED_MENU_MAX_HEIGHT_PX = 280  # so does the feed menu


class FeedsMixin:
    def initial_check(self):
        run_when_online(lambda: self.start_check_thread(force=True))
        return False

    def periodic_check(self):
        self.start_check_thread()
        return True

    def start_check_thread(self, force=False):
        if not self._check_lock.acquire(blocking=False):
            return  # a check is already in flight — skip this tick rather than overlap
        threading.Thread(target=self._check_feeds_guarded, args=(force,), daemon=True).start()

    def _check_feeds_guarded(self, force):
        try:
            self.check_feeds(force)
            self.check_updates_if_due(force=force)
        finally:
            self._check_lock.release()

    def check_feeds(self, force=False):
        if not self.online and not force:
            return  # offline: nothing could be fetched, and a failed fetch must not count as a check
        feeds = load_feeds()
        mute_phrases = load_mute_filters()
        new_items = []
        now = time_module.time()
        with self.lock:
            seen_ids = set(self.state.get('seen', {}).keys())
            last_checked = dict(self.state.get('last_checked', {}))
        newly_seen_ids = set()
        feed_mute_rules = load_feed_mute_rules()
        feed_links = {}
        due_urls = []
        intervals = {}
        for url, _custom_name, interval_seconds in feeds:
            intervals[url] = interval_seconds
            last = last_checked.get(url, 0)
            if force or (now - last) >= interval_seconds:
                due_urls.append(url)
        for url in due_urls:
            retry_soon = now - intervals[url] + min(intervals[url], FEED_RETRY_SECONDS)
            try:
                parsed = feedparser.parse(url)
            except Exception:
                last_checked[url] = retry_soon
                self.note_request_failure()
                continue
            if getattr(parsed, 'bozo', False) and not parsed.entries:
                last_checked[url] = retry_soon  # fetch/parse failed
                self.note_request_failure()
                continue
            last_checked[url] = now
            site_link = (getattr(parsed, 'feed', None) or {}).get('link')
            if isinstance(site_link, str) and site_link:
                feed_links[url] = site_link
            for entry in parsed.entries:
                eid = entry_id(entry, url)
                if eid in seen_ids:
                    continue
                seen_ids.add(eid)
                newly_seen_ids.add(eid)
                title = entry.get('title', '(untitled)')
                if is_muted(title, mute_phrases) or entry_matches_mute_rules(entry, feed_mute_rules.get(url, ())):
                    continue  # matches mute filters — mark as seen, don't surface
                age = entry_age_seconds(entry)
                if age is not None and age > MAX_ITEM_AGE_SECONDS:
                    continue  # too old — mark as seen, don't surface
                item = {
                    'id': eid,
                    'title': title,
                    'link': entry.get('link', ''),
                    'feed_url': url,
                    'pkg_match': None,
                }
                tags, author = entry_tag_terms(entry), entry_author_name(entry)
                if tags:
                    item['tags'] = tags
                if author:
                    item['author'] = author
                new_items.append(item)
        with self.lock:
            merged_seen = dict(self.state.get('seen', {}))
            for eid in newly_seen_ids:
                merged_seen.setdefault(eid, now)
            cutoff = now - SEEN_RETENTION_SECONDS
            merged_seen = {eid: ts for eid, ts in merged_seen.items() if ts >= cutoff}

            merged_last_checked = dict(self.state.get('last_checked', {}))
            merged_last_checked.update(last_checked)

            self.state['seen'] = merged_seen
            self.state['last_checked'] = merged_last_checked
            if feed_links:
                links = self.state.get('feed_links')
                self.state['feed_links'] = {**(links if isinstance(links, dict) else {}), **feed_links}

            if new_items:
                existing_ids = {e['id'] for e in self.state.get('unread', [])}
                new_items = [e for e in new_items if e['id'] not in existing_ids]
                if new_items:
                    self.state['unread'] = (new_items + self.state.get('unread', []))[:MAX_UNREAD_ITEMS]
            save_state(self.state)
        if new_items:
            GLib.idle_add(self.on_new_items)

    def on_new_items(self):
        self.update_icon()
        play_notification_sound()
        self.show_popup(auto=True)  # auto-open whenever new unread items, updates, or live channels arrive
        return False

    def _feed_display_name(self, feed_url):
        return next((name or feed_url for url, name, _interval in load_feeds() if url == feed_url), feed_url)

    def _show_feed_menu(self, row, event):
        """Popover on a feed title: open its XML / website, and mute one of the
        categories or authors seen in its latest items (writes the rule into
        config.conf and hides the matching unread items at once)."""
        feed_url = row.header_feed_url
        with self.lock:
            terms = unread_feed_terms(self.state.get('unread', []), feed_url)  # only what is still unread
            links = self.state.get('feed_links')
        links = links if isinstance(links, dict) else {}

        popover = Gtk.Popover.new(row)
        allocation = row.get_allocation()
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = max(int(event.x) - allocation.x, 0), 0, 1, allocation.height
        popover.set_pointing_to(rect)
        popover.set_position(Gtk.PositionType.BOTTOM)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for margin in ('start', 'end', 'top', 'bottom'):
            getattr(box, f'set_margin_{margin}')(4)

        def add_button(label, callback, *args, sensitive=True, tooltip=None):
            button = Gtk.Button(label=label)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.get_child().set_xalign(0)
            button.set_sensitive(sensitive)
            if tooltip:
                button.set_tooltip_text(tooltip)
            button.connect('clicked', callback, popover, *args)
            box.pack_start(button, False, False, 0)

        add_button('Open website', self._feed_menu_open, feed_source_url(feed_url, links), sensitive=self.online)
        add_button('Open feed XML', self._feed_menu_open, feed_url, sensitive=self.online)
        for field, title in (('category', 'Mute category'), ('author', 'Mute author')):
            box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)
            heading = Gtk.Label()
            heading.set_markup(f'<b>{title}</b>')
            heading.set_xalign(0)
            box.pack_start(heading, False, False, 0)
            entries = terms.get(field) or []
            if not entries:
                hint = Gtk.Label(label='none among the unread items')
                hint.set_xalign(0)
                box.pack_start(hint, False, False, 0)
            for name, count in entries:
                add_button(f'{name} ({count})', self._feed_menu_mute, feed_url, field, name,
                           tooltip=f'Hide items of this feed whose {field} is "{name}"')
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(FEED_MENU_MAX_HEIGHT_PX)
        scroller.add(box)
        popover.add(scroller)
        popover.connect('closed', self.on_launcher_closed)
        self._launcher_open = True  # the same guard the Launch list uses: don't auto-close the popup under it
        scroller.show_all()
        popover.popup()

    def _feed_menu_open(self, _button, popover, url):
        popover.popdown()
        webbrowser.open(url)

    def _feed_menu_mute(self, _button, popover, feed_url, field, value):
        popover.popdown()
        if add_feed_mute_rule(feed_url, field, value):
            rules = parse_mute_rules(f'{field}={escape_mute_value(value)}')
            with self.lock:
                self.state['unread'] = [e for e in self.state.get('unread', [])
                                        if not (e.get('feed_url') == feed_url and entry_matches_mute_rules(e, rules))]
                save_state(self.state)
            self.update_icon()
            self.refresh_list()

    def _remove_unread(self, item_id):
        with self.lock:
            self.state['unread'] = [e for e in self.state.get('unread', []) if e['id'] != item_id]
            save_state(self.state)

    def mark_feed_read(self, feed_url):
        with self.lock:
            self.state['unread'] = [
                e for e in self.state.get('unread', [])
                if e.get('feed_url', '') != feed_url
            ]
            save_state(self.state)
        self.update_icon()
        self.refresh_list()

    def on_mark_read_clicked(self, _button, item_id):
        self._remove_unread(item_id)
        self.update_icon()
        self.refresh_list()

    def on_mark_all_read(self, *_args):
        with self.lock:
            self.state['unread'] = []
            save_state(self.state)
        self.update_icon()
        self.refresh_list()
