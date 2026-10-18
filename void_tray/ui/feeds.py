"""Feed checking, the unread list actions and the feed menu."""

import feedparser
import threading
import time as time_module
import webbrowser

from ..gi_env import GLib, Gdk, Gtk, Pango
from ..config import load_feeds, load_mute_filters
from ..desktop import play_notification_sound
from ..network import run_when_online
from ..rules import (
    add_feed_mute_rule,
    add_feed_readlater_rule,
    entry_age_seconds,
    entry_author_name,
    entry_id,
    entry_matches_mute_rules,
    entry_tag_terms,
    escape_mute_value,
    feed_source_url,
    is_muted,
    load_feed_mute_rules,
    load_feed_readlater_rules,
    parse_mute_rules,
    unread_feed_terms,
)
from ..state import save_state


FEED_RETRY_SECONDS = 120  # retry a feed this soon after a failed fetch (instead of a full interval)


MAX_UNREAD_ITEMS = 500  # stored unread items; the oldest beyond this are dropped (they stay 'seen')


OPEN_TABS_DELAY_SECONDS = 0.2  # between the tabs of Open all, so the browser takes them in order
TERM_NAME_MAX_CHARS = 26  # category / author names in the feed menu are cut here
FEED_MENU_EDGE_PX = 30  # kept free of the popup's edge for the menu's arrow and padding
FEED_MENU_MIN_HEIGHT_PX = 80  # the menu is never squeezed below this, however little room there is


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
            self.check_orphans_if_due(force=force)
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
        feed_later_rules = load_feed_readlater_rules()
        later_items = []  # new items a read-later rule sends straight to the Read later list
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
                if entry_matches_mute_rules(entry, feed_later_rules.get(url, ())):
                    later_items.append(item)  # not unread: no badge, sound or popup for it
                else:
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

            if later_items:
                known = {e['id'] for e in self.state.get('read_later', [])}
                later_items = [dict(e, saved=now) for e in later_items if e['id'] not in known]
                self.state['read_later'] = later_items + self.state.get('read_later', [])
            if new_items:
                existing_ids = {e['id'] for e in self.state.get('unread', [])}
                new_items = [e for e in new_items if e['id'] not in existing_ids]
                if new_items:
                    self.state['unread'] = (new_items + self.state.get('unread', []))[:MAX_UNREAD_ITEMS]
            save_state(self.state)
        if new_items:
            GLib.idle_add(self.on_new_items)
        if later_items:
            GLib.idle_add(self._read_later_changed)

    def _read_later_changed(self):
        """Items were filed under Read later in the background: show them if the popup
        is open, without closing it and without any alert."""
        if self.popup is not None and self.popup.get_visible():
            self.refresh_list(keep_open=True)
        return False

    def on_new_items(self):
        self.update_icon()
        play_notification_sound()
        self.show_popup(auto=True)  # auto-open whenever new unread items, updates, or live channels arrive
        return False

    def _feed_display_name(self, feed_url):
        return next((name or feed_url for url, name, _interval in load_feeds() if url == feed_url), feed_url)

    def _show_feed_menu(self, row, event):
        """Popover on a feed title: open every unread item as a tab, open its website
        or XML, and for each category or author seen in its unread items either mute
        it or send it straight to Read later (either writes a rule into the feed's line
        in config.conf and acts on the matching unread items at once)."""
        feed_url = row.header_feed_url
        with self.lock:
            unread = self.state.get('unread', [])
            terms = unread_feed_terms(unread, feed_url)  # only what is still unread
            open_count = sum(1 for e in unread if e.get('feed_url') == feed_url and e.get('link'))
            links = self.state.get('feed_links')
        links = links if isinstance(links, dict) else {}

        popover = Gtk.Popover.new(row)
        allocation = row.get_allocation()
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = max(int(event.x) - allocation.x, 0), 0, 1, allocation.height
        popover.set_pointing_to(rect)
        position, max_height = self._feed_menu_room(row)
        popover.set_position(position)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for margin in ('start', 'end', 'top', 'bottom'):
            getattr(box, f'set_margin_{margin}')(4)

        def plain_label(text, markup=False):
            label = Gtk.Label()
            if markup:
                label.set_markup(text)
            else:
                label.set_text(text)
            label.set_xalign(0)
            label.get_style_context().add_class('menu-text')  # dark text: the menu sits on a white scroller
            return label

        # the three ways to open the feed share one row, so the whole menu stays short
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for label, callback, arg, sensitive, tooltip in (
                (f'Open all {open_count} as tabs', self._feed_menu_open_all, feed_url, self.online and open_count > 0,
                 'Open every unread item of this feed in the browser, one tab each, and mark them read'),
                ('Website', self._feed_menu_open, feed_source_url(feed_url, links), self.online, "Open the feed's website"),
                ('XML', self._feed_menu_open, feed_url, self.online, 'Open the feed itself')):
            button = Gtk.Button(label=label)
            button.set_sensitive(sensitive)
            button.set_tooltip_text(tooltip)
            button.connect('clicked', callback, popover, arg)
            actions.pack_start(button, True, True, 0)
        box.pack_start(actions, False, False, 0)
        for field, title in (('category', 'Categories'), ('author', 'Authors')):
            box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)
            box.pack_start(plain_label(f'<b>{title}</b>', markup=True), False, False, 0)
            entries = terms.get(field) or []
            if not entries:
                box.pack_start(plain_label('none among the unread items'), False, False, 0)
            for name, count in entries:
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                label = plain_label(f'{name} ({count})')
                label.set_hexpand(True)
                label.set_max_width_chars(TERM_NAME_MAX_CHARS)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                line.pack_start(label, True, True, 0)
                for text, callback, tooltip in (
                        ('Mute', self._feed_menu_mute, f'Hide items of this feed whose {field} is "{name}"'),
                        ('Later', self._feed_menu_later,
                         f'Send items of this feed whose {field} is "{name}" straight to Read later')):
                    button = Gtk.Button(label=text)
                    button.set_tooltip_text(tooltip)
                    button.connect('clicked', callback, popover, feed_url, field, name)
                    line.pack_start(button, False, False, 0)
                box.pack_start(line, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(max_height)
        scroller.add(box)
        popover.add(scroller)
        popover.connect('closed', self.on_launcher_closed)
        self._launcher_open = True  # the same guard the Launch list uses: don't auto-close the popup under it
        scroller.show_all()
        popover.popup()

    def _feed_menu_room(self, row):
        """(side, max height) for the feed menu. A popover cannot leave the popup
        window and is cut off where the window ends, so the menu opens on the side of
        the title with more room and scrolls inside the room that side has."""
        window = self.popup
        where = row.translate_coordinates(window, 0, 0) if window is not None else None
        if where is None:
            return Gtk.PositionType.BOTTOM, FEED_MENU_MAX_HEIGHT_PX
        top = where[1]
        below = window.get_allocated_height() - (top + row.get_allocation().height) - FEED_MENU_EDGE_PX
        above = top - FEED_MENU_EDGE_PX
        position, room = (Gtk.PositionType.BOTTOM, below) if below >= above else (Gtk.PositionType.TOP, above)
        return position, max(FEED_MENU_MIN_HEIGHT_PX, min(FEED_MENU_MAX_HEIGHT_PX, room))

    def _feed_menu_open(self, _button, popover, url):
        popover.popdown()
        webbrowser.open(url)

    def _feed_menu_open_all(self, _button, popover, feed_url):
        """Every unread item of the feed becomes a browser tab and counts as read, as
        opening one item does."""
        popover.popdown()
        with self.lock:
            opened = [e for e in self.state.get('unread', []) if e.get('feed_url') == feed_url]
            ids = {e['id'] for e in opened}
            self.state['unread'] = [e for e in self.state.get('unread', []) if e['id'] not in ids]
            save_state(self.state)
        self.update_icon()
        self.refresh_list()
        links = [e['link'] for e in opened if e.get('link')]
        if links:  # in the background, so a long list does not freeze the popup
            threading.Thread(target=self._open_tabs, args=(links,), daemon=True).start()

    @staticmethod
    def _open_tabs(links):
        for link in links:
            webbrowser.open_new_tab(link)
            time_module.sleep(OPEN_TABS_DELAY_SECONDS)

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

    def _feed_menu_later(self, _button, popover, feed_url, field, value):
        """Future items matching go straight to Read later, and so do the unread ones
        that match now (also when the rule was already there)."""
        popover.popdown()
        add_feed_readlater_rule(feed_url, field, value)
        rules = parse_mute_rules(f'{field}={escape_mute_value(value)}')
        with self.lock:
            matching = [e['id'] for e in self.state.get('unread', [])
                        if e.get('feed_url') == feed_url and entry_matches_mute_rules(e, rules)]
        if matching:
            self.save_for_later(matching)

    # ---- Read later ------------------------------------------------------------
    def save_for_later(self, item_ids):
        """Moves unread items to the Read later list (newest first) and off the unread
        list; an item that is already saved is just taken off the unread one."""
        wanted = set(item_ids)
        now = time_module.time()
        with self.lock:
            unread = self.state.get('unread', [])
            moving = [e for e in unread if e['id'] in wanted]
            self.state['unread'] = [e for e in unread if e['id'] not in wanted]
            known = {e['id'] for e in self.state.get('read_later', [])}
            added = [dict(e, saved=now) for e in moving if e['id'] not in known]
            self.state['read_later'] = added + self.state.get('read_later', [])
            save_state(self.state)
        self.update_icon()
        self.refresh_list()

    def on_save_clicked(self, _button, item_id):
        self.save_for_later([item_id])

    def remove_read_later(self, item_id):
        with self.lock:
            self.state['read_later'] = [e for e in self.state.get('read_later', []) if e['id'] != item_id]
            save_state(self.state)
        self.refresh_list()

    def on_read_later_remove_clicked(self, _button, item_id):
        self.remove_read_later(item_id)

    def open_read_later(self, item_id, link):
        """Opening a saved item takes it off the list, like reading an unread one."""
        self.remove_read_later(item_id)
        if link:
            webbrowser.open(link)

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
