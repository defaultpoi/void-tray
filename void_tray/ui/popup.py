"""The popup window and its list."""

import threading
import webbrowser

from ..gi_env import GLib, Gdk, Gtk, Pango
from ..config import (
    load_feeds,
    load_highlight_words,
    load_launcher_entries,
    load_network_reset_command,
    load_orphan_nudge,
    load_timer_settings,
)
from ..constants import CONFIG_FILE, OFFLINE_BANNER_TEXT, TIMER_HEIGHT_COUNTDOWN_PX
from ..desktop import edit_file_externally, is_fullscreen_active, run_launcher_command
from ..playback import open_twitch_stream, open_youtube_stream
from ..rules import feed_source_url, item_detail_text, item_is_highlighted
from .timer import TIMER_RESERVED_HEIGHT_PX, format_timer_countdown


MAX_LIST_ITEMS = 40


MAX_TITLE_LEN = 60


                          # change to ['doas'] if that's what you use; requires
                          # passwordless (NOPASSWD) rules for xbps-install, since
                          # updates run headlessly with no terminal/tty attached
WINDOW_WIDTH = 471  # 380 * 1.2, +15px total


LAUNCHER_MAX_HEIGHT_PX = 300  # the Launch list scrolls beyond this


LIST_BOTTOM_SPACE_PX = 85  # reserved under the last row for the timer slide (countdown mode)
ORPHAN_LIST_MAX_NAMES = 30  # package names listed under an expanded orphaned-packages header
SUMMARY_MAX_NAMES = 60  # package names listed in one line of the update summary


POPUP_CSS = """
list row { padding: 1px 3px; min-height: 0px; }
button { padding: 1px; }
list, viewport, scrolledwindow, overlay, .popup-content { background: #ffffff; }
list, list label { color: #000000; }
.weather-bar { background: #e8eef5; }
row.breaking-on { background: #ffc9c9; }
.offline-banner { background: #b3261e; color: #ffffff; padding: 3px 6px; font-weight: bold; }
.offline-banner label { color: #ffffff; font-weight: bold; }
.offline-banner button label { color: #000000; font-weight: normal; }
.schedule-warning { background: #b3261e; padding: 3px 6px; }
.schedule-warning label { color: #ffffff; font-weight: bold; }
.schedule-warning button label { color: #000000; font-weight: normal; }
.weather-bar label, .timer-bar label { color: #000000; }
.timer-bar { background: #ffffff; }
"""


class PopupMixin:
    def maybe_auto_show_startup(self):
        if self.has_anything_to_show():
            self.show_popup(auto=True)
        return False

    def _apply_compact_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(POPUP_CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _update_scroller_max_height(self):
        try:
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            screen_height = monitor.get_geometry().height
        except Exception:
            screen_height = 1080
        max_height = int(screen_height * 0.75)
        self.scroller.set_max_content_height(max_height)

    def on_popup_focus_out(self, win, _event):
        if self._launcher_open:
            return False  # the Launch list took focus; don't close under it
        win.hide()
        return False

    def on_launcher_button_clicked(self, button):
        """Builds the Launch list fresh (so config edits show up immediately)
        and pops it up above the button."""
        entries = load_launcher_entries()
        popover = Gtk.Popover.new(button)
        popover.set_position(Gtk.PositionType.TOP)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(4)
        box.set_margin_end(4)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        if not entries:
            hint = Gtk.Label(label='No commands yet — add them under [launcher]\nin the config (Label|command).')
            hint.set_margin_start(6)
            hint.set_margin_end(6)
            hint.set_margin_top(4)
            hint.set_margin_bottom(4)
            box.pack_start(hint, False, False, 0)
        for label, command in entries:
            item = Gtk.Button(label=label)
            item.set_relief(Gtk.ReliefStyle.NONE)
            item.get_child().set_xalign(0)
            item.set_tooltip_text(command)
            item.connect('clicked', self.on_launcher_item_clicked, command, popover)
            box.pack_start(item, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(LAUNCHER_MAX_HEIGHT_PX)
        scroller.add(box)
        popover.add(scroller)
        popover.connect('closed', self.on_launcher_closed)
        self._launcher_open = True
        scroller.show_all()
        popover.popup()

    def on_launcher_closed(self, popover):
        self._launcher_open = False
        GLib.idle_add(popover.destroy)

    def on_launcher_item_clicked(self, _item, command, popover):
        run_launcher_command(command)
        popover.popdown()
        if self.popup:
            self.popup.hide()

    def build_popup_window(self):
        win = Gtk.Window(type=Gtk.WindowType.POPUP)
        win.set_decorated(False)
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        win.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        win.set_keep_above(True)
        win.set_default_size(WINDOW_WIDTH, -1)
        win.connect('focus-out-event', self.on_popup_focus_out)
        win.connect('key-press-event', self.on_popup_key)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        weather_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        weather_box.get_style_context().add_class('weather-bar')
        weather_box.set_margin_start(6)
        weather_box.set_margin_end(6)
        weather_box.set_margin_top(4)
        weather_box.set_margin_bottom(4)
        self.weather_box = weather_box
        self.rebuild_weather_bar()
        outer.pack_start(weather_box, False, False, 0)
        outer.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        banner.get_style_context().add_class('offline-banner')
        banner_label = Gtk.Label(label=OFFLINE_BANNER_TEXT)
        banner_label.set_hexpand(True)
        reset_btn = Gtk.Button(label='Reset network')
        reset_btn.set_tooltip_text(load_network_reset_command())
        reset_btn.connect('clicked', self.on_network_reset_clicked)
        banner.pack_start(banner_label, True, True, 0)
        banner.pack_start(reset_btn, False, False, 0)
        banner.set_no_show_all(True)  # shown/hidden by _set_online; children are shown explicitly
        banner_label.show()
        reset_btn.set_visible(bool(load_network_reset_command()))  # no command configured: no button
        banner.set_visible(not self.online)
        banner.set_tooltip_text(None if self.online else self._offline_tooltip())
        self.offline_banner = banner
        self.offline_banner_label = banner_label
        self.reset_btn = reset_btn
        outer.pack_start(banner, False, False, 0)

        warn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        warn_box.get_style_context().add_class('schedule-warning')
        warn_label = Gtk.Label()
        warn_label.set_xalign(0)
        warn_label.set_hexpand(True)
        cancel_btn = Gtk.Button(label='Cancel')
        cancel_btn.connect('clicked', self.on_schedule_cancel)
        warn_snooze_btn = Gtk.Button(label=f"Snooze {self._schedule_settings()['snooze']} min")
        warn_snooze_btn.connect('clicked', self.on_schedule_snooze)
        for widget in (warn_label, cancel_btn, warn_snooze_btn):
            warn_box.pack_start(widget, widget is warn_label, widget is warn_label, 0)
        warn_box.set_no_show_all(True)  # shown by _refresh_schedule_ui; children shown explicitly
        for widget in (warn_label, cancel_btn, warn_snooze_btn):
            widget.show()
        if self.schedule_warning:
            warn_label.set_text(f"{self.schedule_warning['action']} in {self.schedule_warning['left']} s")
        warn_box.set_visible(self.schedule_warning is not None)
        self.schedule_banner = warn_box
        self.schedule_banner_label = warn_label
        outer.pack_start(warn_box, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_min_content_height(TIMER_RESERVED_HEIGHT_PX)  # room for the Schedule slide even with an empty list
        self.scroller = scroller
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.connect('row-activated', self.on_row_activated)
        self.listbox.connect('button-press-event', self.on_listbox_button_press)

        # The list sits in a box together with a real spacer that reserves the
        # strip the timer slides over (timer_box is 60px tall, valign END; the
        # extra 25px keeps a gap above it instead of its top edge sitting flush
        # against the last row). It must be actual content rather than a margin
        # on the list: a margin lies outside every widget's paint area, so once
        # the list was scrolled to its end that strip rendered as an unpainted
        # black rectangle. An EventBox wraps it all so a right-click on any
        # empty part -- including the strip behind a closed timer -- closes
        # the popup.
        spacer = Gtk.Box()
        spacer.set_size_request(-1, LIST_BOTTOM_SPACE_PX)
        self.list_spacer = spacer
        list_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        list_content.pack_start(self.listbox, False, False, 0)
        list_content.pack_start(spacer, False, False, 0)
        list_events = Gtk.EventBox()
        list_events.get_style_context().add_class('popup-content')
        list_events.connect('button-press-event', self.on_empty_area_button_press)
        list_events.add(list_content)
        scroller.add(list_events)

        content_overlay = Gtk.Overlay()
        content_overlay.add(scroller)

        timer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        timer_box.set_margin_start(8)
        timer_box.set_margin_end(8)
        timer_box.set_margin_top(3)
        timer_box.set_margin_bottom(4)
        timer_box.set_size_request(-1, TIMER_HEIGHT_COUNTDOWN_PX)
        timer_box.set_halign(Gtk.Align.FILL)
        timer_box.set_valign(Gtk.Align.END)
        timer_box.get_style_context().add_class('timer-bar')
        timer_box.set_no_show_all(True)
        timer_box.set_visible(self.timer_visible)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.mode_buttons = {}
        for mode, title in (('countdown', 'Countdown'), ('schedule', 'Schedule')):
            mode_button = Gtk.ToggleButton(label=title)
            mode_button.set_active(self.timer_mode == mode)
            mode_button.connect('toggled', self.on_timer_mode_toggled, mode)
            self.mode_buttons[mode] = mode_button
            mode_row.pack_start(mode_button, False, False, 0)
        timer_label = Gtk.Label()
        timer_label.set_xalign(1)
        timer_label.set_hexpand(True)
        timer_label.set_text(format_timer_countdown(self.timer_remaining_seconds))
        self.timer_label = timer_label
        header.pack_start(mode_row, False, False, 0)
        header.pack_start(timer_label, True, True, 0)
        timer_box.pack_start(header, False, False, 0)

        timer_settings = load_timer_settings()
        max_seconds = timer_settings['max_seconds']
        step_seconds = timer_settings['step_seconds']
        if self.timer_remaining_seconds > max_seconds:
            self.timer_remaining_seconds = max_seconds  # clamp: config may have
                                                          # lowered max since a
                                                          # timer was last set
        timer_adjustment = Gtk.Adjustment(
            value=self.timer_remaining_seconds, lower=0, upper=max_seconds,
            step_increment=step_seconds, page_increment=step_seconds * 5, page_size=0
        )
        timer_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=timer_adjustment)
        timer_scale.set_draw_value(False)
        timer_scale.connect('value-changed', self.on_timer_slider_changed)
        self.timer_scale = timer_scale
        countdown_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        countdown_box.pack_start(timer_scale, False, False, 0)
        timer_box.pack_start(countdown_box, False, False, 0)
        self.sched = None
        schedule_box = self._build_schedule_box()
        timer_box.pack_start(schedule_box, False, False, 0)
        self.countdown_box, self.schedule_box = countdown_box, schedule_box

        self.timer_box = timer_box
        content_overlay.add_overlay(timer_box)
        # timer_box has no_show_all(True) so that the popup's own show_all()
        # doesn't reveal it prematurely — but that flag ALSO blocks show_all()
        # called on timer_box itself (no_show_all stops recursion at the
        # widget that has it set, however show_all() was invoked). So its
        # children must be shown individually with .show(), which no_show_all
        # does not affect, rather than via any show_all() call on the box.
        header.show_all()
        countdown_box.show_all()
        schedule_box.show_all()
        self._apply_timer_mode()

        outer.pack_start(content_overlay, True, True, 0)

        outer.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        footer.set_margin_start(6)
        footer.set_margin_end(6)
        footer.set_margin_top(4)
        footer.set_margin_bottom(4)
        launch_btn = Gtk.Button(label='Launch')
        launch_btn.set_tooltip_text('Run one of your [launcher] commands')
        launch_btn.connect('clicked', self.on_launcher_button_clicked)
        footer.pack_start(launch_btn, True, True, 0)
        timer_toggle_btn = Gtk.Button(label='Timer')
        timer_toggle_btn.set_tooltip_text('Show/hide countdown timer')
        timer_toggle_btn.connect('clicked', self.on_timer_toggle_clicked)
        footer.pack_start(timer_toggle_btn, True, True, 0)
        edit_btn = Gtk.Button(label='Edit config')
        edit_btn.connect('clicked', lambda *_a: edit_file_externally(CONFIG_FILE))
        footer.pack_start(edit_btn, True, True, 0)
        refresh_btn = Gtk.Button(label='Refresh')
        refresh_btn.connect('clicked', lambda *_a: (
            self.start_check_thread(force=True), self.start_twitch_check()
        ))
        footer.pack_start(refresh_btn, True, True, 0)
        mark_all_btn = Gtk.Button(label='Mark all read')
        mark_all_btn.connect('clicked', self.on_mark_all_read)
        footer.pack_start(mark_all_btn, True, True, 0)
        quit_btn = Gtk.Button(label='Quit')
        quit_btn.connect('clicked', lambda *_a: Gtk.main_quit())
        footer.pack_start(quit_btn, True, True, 0)
        outer.pack_start(footer, False, False, 0)

        win.add(outer)
        self.popup = win

    def on_popup_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            widget.hide()
        return False

    def toggle_popup(self, *_args):
        if self.popup and self.popup.get_visible():
            self.popup.hide()
            return
        self.show_popup()

    def show_popup(self, auto=False):
        if auto and is_fullscreen_active():
            return  # don't interrupt a fullscreen video/game/presentation
        if auto and self.popup is not None and self.popup.get_visible():
            self.refresh_list()  # already open: update in place, keeping scroll position and any open Launch list
            return
        if self.popup is not None:
            self._launcher_open = False  # its Launch list (if open) dies with the window
            self.popup.destroy()
            self.popup = None
            self.timer_scale = None
            self.timer_label = None
            self.timer_box = None
            self.offline_banner = self.offline_banner_label = self.reset_btn = None
            self.countdown_box = self.schedule_box = self.sched = None
            self.list_spacer = self.schedule_banner = self.schedule_banner_label = None
            self.mode_buttons = {}
        self.weather_view = 'today'
        self.orphans_expanded = False
        self.probe_connectivity()  # fresh answer for the indicator while the popup is open
        if not auto:  # the orphaned-packages list is cheap to refresh and must be current before anyone removes from it
            threading.Thread(target=self.check_orphans_if_due, kwargs={'force': True}, daemon=True).start()
        self.build_popup_window()
        self._update_scroller_max_height()
        self.refresh_list()
        self.position_popup()
        self.popup.show_all()
        self.popup.present()
        self.popup.grab_focus()

    def position_popup(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geo = monitor.get_geometry()

        x = geo.x + geo.width - WINDOW_WIDTH  # flush against the right edge

        y = geo.y + 2  # flush against the top, as a fallback
        try:
            ok, _screen, area, _orientation = self.status_icon.get_geometry()
            if ok and area is not None:
                y = area.y + area.height + 4  # 4px below the panel/tray icon
        except Exception:
            pass

        self.popup.move(max(x, 0), max(y, 0))

    def _apply_breaking_state(self):
        for row in self._breaking_rows:
            context = row.get_style_context()
            if self._breaking_on:
                context.add_class('breaking-on')
            else:
                context.remove_class('breaking-on')

    def _breaking_pulse_tick(self):
        """Pulses highlighted news rows -- only while the popup is showing."""
        if self._breaking_rows and self.popup and self.popup.get_visible():
            self._breaking_on = not self._breaking_on
            self._apply_breaking_state()
        return True

    def refresh_list(self, keep_open=False):
        """Rebuilds the list. With nothing to show a visible popup closes itself,
        unless keep_open is set (refreshes the person did not ask for, such as a
        finished orphan scan) or an orphan removal is showing its result."""
        self._breaking_rows = []
        self._highlight_words = load_highlight_words()
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        with self.lock:
            unread = list(self.state.get('unread', []))
            available = list(self.state.get('available_updates', []))
            orphans = list(self.state.get('orphans', []))
            live_channels = list(self.state.get('live_channels', []))
            live_youtube_channels = list(self.state.get('live_youtube_channels', []))

        feeds_map = {url: (custom_name or url) for url, custom_name, _interval in load_feeds()}
        # orphaned packages never keep the popup open or fill it on their own: they are
        # shown below whatever else there is, and when the popup is opened by hand
        show_orphans = load_orphan_nudge() and bool(orphans or self.orphan_status)

        any_live = bool(live_channels or live_youtube_channels)
        truly_empty = not unread and not available and not any_live
        only_live_remains = (not unread and not available) and any_live
        popup_already_visible = bool(self.popup and self.popup.get_visible())
        should_hide = truly_empty or (only_live_remains and popup_already_visible)
        close_popup = should_hide and not keep_open and not self.orphan_status and not self.update_summary

        if should_hide:
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.set_activatable(False)
            lbl = Gtk.Label(label='No unread items')
            lbl.set_margin_top(8)
            lbl.set_margin_bottom(8)
            row.add(lbl)
            self.listbox.add(row)
            if self.update_summary:
                self.add_update_summary_rows()
            if show_orphans:
                self.add_orphan_rows(orphans, is_first=False)
            if self.popup and close_popup:
                self.popup.hide()
        else:
            is_first_section = True

            if any_live:
                self.listbox.add(self.build_header_row(
                    '__live__', 'Live now', is_first=is_first_section, clickable=False
                ))
                is_first_section = False
                for entry in live_channels:
                    self.listbox.add(self.build_twitch_row(entry))
                for entry in live_youtube_channels:
                    self.listbox.add(self.build_youtube_row(entry))

            if unread:
                shown = unread[:MAX_LIST_ITEMS]
                groups = {}
                order = []
                for entry in shown:
                    feed_url = entry.get('feed_url', '')
                    if feed_url not in groups:
                        groups[feed_url] = []
                        order.append(feed_url)
                    groups[feed_url].append(entry)

                for i, feed_url in enumerate(order):
                    feed_name = feeds_map.get(feed_url, feed_url)
                    self.listbox.add(self.build_header_row(
                        feed_url, feed_name, is_first=(is_first_section and i == 0)
                    ))
                    for entry in groups[feed_url]:
                        self.listbox.add(self.build_row(entry))
                is_first_section = False

                if len(unread) > MAX_LIST_ITEMS:
                    row = Gtk.ListBoxRow()
                    row.set_selectable(False)
                    row.set_activatable(False)
                    lbl = Gtk.Label(label=f"... and {len(unread) - MAX_LIST_ITEMS} more")
                    row.add(lbl)
                    self.listbox.add(row)

            if available:
                self.listbox.add(self.build_header_row(
                    '__updates__', 'Updates available',
                    is_first=is_first_section, clickable=False, install_all=True
                ))
                is_first_section = False
                for entry in available[:MAX_LIST_ITEMS]:
                    self.listbox.add(self.build_info_row(entry))
                if len(available) > MAX_LIST_ITEMS:
                    row = Gtk.ListBoxRow()
                    row.set_selectable(False)
                    row.set_activatable(False)
                    lbl = Gtk.Label(label=f"... and {len(available) - MAX_LIST_ITEMS} more")
                    row.add(lbl)
                    self.listbox.add(row)
            if self.update_summary:
                self.add_update_summary_rows(is_first=is_first_section)
                is_first_section = False
            if show_orphans:
                self.add_orphan_rows(orphans, is_first=is_first_section)
        self.listbox.show_all()

    def add_update_summary_rows(self, is_first=False):
        """Topgrade's closing summary after Install all: a header (click it to dismiss)
        and one row per summary line, with a failed step's last output lines under it."""
        summary = self.update_summary
        verdict = 'OK' if summary['ok'] else 'FAILED'
        title = f"Topgrade · {summary['when']} · {verdict}"
        updated, not_updated = summary.get('updated', []), summary.get('not_updated', [])
        if 'updated' in summary:
            title += f" · {self._plural(len(updated), 'package')} updated" if updated else ' · no packages updated'
        row = self.build_header_row('__summary__', title, is_first=is_first, clickable=False, summary=True)
        row.set_tooltip_text('Click to dismiss')
        self.listbox.add(row)
        for line in summary['lines']:
            escaped = GLib.markup_escape_text(line)
            self.listbox.add(self._orphan_info_row(
                f'<b><span foreground="#b3261e">{escaped}</span></b>' if line.endswith(': FAILED') else escaped))
        if updated:
            self.listbox.add(self._orphan_info_row(f"Updated: {self._name_list(updated)}", wrap=True))
        if not_updated:
            self.listbox.add(self._orphan_info_row(
                f'<span foreground="#b3261e">Not updated: {self._name_list(not_updated)}</span>', wrap=True))
        for line in summary['details']:
            self.listbox.add(self._orphan_info_row(f'<small>{GLib.markup_escape_text(line)}</small>', indent=28))

    @staticmethod
    def _name_list(names):
        """Package names for one wrapped line, escaped, cut after SUMMARY_MAX_NAMES."""
        shown = ', '.join(GLib.markup_escape_text(name) for name in names[:SUMMARY_MAX_NAMES])
        return shown + (f", … and {len(names) - SUMMARY_MAX_NAMES} more" if len(names) > SUMMARY_MAX_NAMES else '')

    def add_orphan_rows(self, orphans, is_first=False):
        """The orphaned-packages header and, once it has been clicked, the choices
        under it: Remove, Remove and clean the cache, Cancel, then what would go.
        They are plain list rows, so a short popup cannot clip them."""
        self.listbox.add(self.build_orphans_header(orphans, is_first=is_first))
        if not self.orphans_expanded or self.orphan_status or not orphans:
            return
        for text, action in ((f"Remove {self._plural(len(orphans), 'package')}", 'remove'),
                             ('Remove and clean the package cache', 'remove_cache'),
                             ('Cancel', 'cancel')):
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.set_activatable(True)
            row.orphans_action = action
            label = Gtk.Label()
            label.set_markup(f'<b>{GLib.markup_escape_text(text)}</b>' if action == 'remove' else GLib.markup_escape_text(text))
            label.set_margin_top(3)
            label.set_margin_bottom(3)
            row.add(label)
            self.listbox.add(row)
        shown = orphans[:ORPHAN_LIST_MAX_NAMES]
        for name in shown:
            self.listbox.add(self._orphan_info_row(GLib.markup_escape_text(name)))
        if len(orphans) > len(shown):
            self.listbox.add(self._orphan_info_row(f"... and {len(orphans) - len(shown)} more"))
        self.listbox.add(self._orphan_info_row('<small>To keep one: xbps-pkgdb -m manual &lt;name&gt;</small>'))

    @staticmethod
    def _plural(count, noun):
        return f"{count} {noun}{'s' if count != 1 else ''}"

    def _orphan_info_row(self, markup, indent=12, wrap=False):
        """A plain, inert text row (also used for the update summary); wrap=True lets a
        long line break at spaces instead of widening the popup."""
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        label = Gtk.Label()
        label.set_markup(markup)
        label.set_xalign(0)
        label.set_margin_start(indent)
        if wrap:
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_max_width_chars(46)
        row.add(label)
        return row

    def build_orphans_header(self, orphans, is_first=False):
        """The "N orphaned packages" section header; clicking it opens the confirmation
        popover. While a removal runs or just finished it shows that status instead."""
        count = len(orphans)
        if self.orphan_status:
            title = f"Orphaned packages · {self.orphan_status}"
        else:
            title = f"{self._plural(count, 'orphaned package')}"
        row = self.build_header_row('__orphans__', title, is_first=is_first, clickable=False, orphans=True)
        shown = orphans[:15]
        listing = '\n'.join(shown) + (f"\n... and {count - len(shown)} more" if count > len(shown) else '')
        row.set_tooltip_text("Packages installed as dependencies that nothing needs any more. Click to review and remove them."
                             + (f"\n\n{listing}" if listing else ''))
        return row

    def build_header_row(self, feed_url, feed_name, is_first=False, clickable=True, install_all=False, orphans=False, summary=False):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(clickable or install_all or orphans or summary)
        if orphans:
            row.orphans_header = True
        if summary:
            row.summary_header = True
        if install_all:
            row.install_all_header = True
            row.set_tooltip_text("Install all available updates")
        elif clickable:
            row.header_feed_url = feed_url
            row.set_tooltip_text(f"Mark all '{feed_name}' items as read (middle-click: its website, right-click: menu)")

        row.set_margin_top(4 if is_first else 20)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(4)
        box.set_margin_end(4)
        box.set_margin_bottom(2)

        label = Gtk.Label()
        label.set_markup(f'<span size="larger" weight="bold">{GLib.markup_escape_text(feed_name)}</span>')
        label.set_xalign(0.5)
        label.set_halign(Gtk.Align.CENTER)
        box.pack_start(label, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_hexpand(True)
        box.pack_start(sep, False, False, 0)

        row.add(box)
        return row

    def build_info_row(self, entry):
        """Purely informational row: no mark-as-read button, no click action.
        Used for 'Updates available' entries — install is only ever triggered
        via the section header (install all), never per-row."""
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        box.set_margin_start(3)
        box.set_margin_end(3)
        box.set_margin_top(0)
        box.set_margin_bottom(0)

        status = self.install_status.get(entry.get('pkgname'))
        if status:
            status_label = Gtk.Label()
            status_label.set_markup(
                f'<span foreground="#2b5fad"><i>{GLib.markup_escape_text(status)}</i></span>'
            )
            box.pack_start(status_label, False, False, 0)

        full_title = entry['title']
        truncated = len(full_title) > MAX_TITLE_LEN
        title = full_title[:MAX_TITLE_LEN - 1] + '\u2026' if truncated else full_title
        text = GLib.markup_escape_text(title)
        label = Gtk.Label()
        label.set_markup(f'<span foreground="#000000"><b>{text}</b></span>')
        label.set_xalign(0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_hexpand(True)
        if truncated:
            label.set_tooltip_text(full_title)
            row.set_tooltip_text(full_title)

        box.pack_start(label, True, True, 0)
        row.add(box)
        return row

    def build_row(self, entry):
        row = Gtk.ListBoxRow()
        row.entry_id = entry['id']
        row.link = entry['link']

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        box.set_margin_start(3)
        box.set_margin_end(3)
        box.set_margin_top(0)
        box.set_margin_bottom(0)

        mark_btn = Gtk.Button()
        mark_btn.set_relief(Gtk.ReliefStyle.NONE)
        mark_icon = Gtk.Image.new_from_icon_name('mail-mark-read-symbolic', Gtk.IconSize.MENU)
        mark_btn.add(mark_icon)
        mark_btn.set_tooltip_text('Mark as read')
        mark_btn.connect('clicked', self.on_mark_read_clicked, entry['id'])
        box.pack_start(mark_btn, False, False, 0)

        full_title = entry['title']
        truncated = len(full_title) > MAX_TITLE_LEN
        title = full_title[:MAX_TITLE_LEN - 1] + '\u2026' if truncated else full_title
        text = GLib.markup_escape_text(title)
        label = Gtk.Label()
        label.set_markup(f'<span foreground="#000000"><b>{text}</b></span>')
        label.set_xalign(0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_hexpand(True)
        tooltip = '\n'.join(part for part in (full_title if truncated else '', item_detail_text(entry)) if part)
        if tooltip:
            row.set_tooltip_text(tooltip)
            label.set_tooltip_text(tooltip)
        if item_is_highlighted(entry, self._highlight_words):
            self._breaking_rows.append(row)
            self._apply_breaking_state()

        box.pack_start(label, True, True, 0)
        row.add(box)
        return row

    def on_empty_area_button_press(self, _widget, event):
        if event.button == 3 and self.popup:  # right-click on empty space: close
            self.popup.hide()
            return True
        return False

    def on_listbox_button_press(self, listbox, event):
        if event.button == 2:  # middle-click on a feed title: open that feed's website
            row = listbox.get_row_at_y(int(event.y))
            if row is not None and hasattr(row, 'header_feed_url'):
                if self.online:  # offline, feed titles do nothing (like a left-click)
                    with self.lock:
                        links = self.state.get('feed_links')
                    webbrowser.open(feed_source_url(row.header_feed_url, links if isinstance(links, dict) else {}))
                return True
            return False
        if event.button == 3:  # right-click
            row = listbox.get_row_at_y(int(event.y))
            if row is not None and hasattr(row, 'header_feed_url'):
                self._show_feed_menu(row, event)
                return True
            if row is not None and hasattr(row, 'entry_id'):
                self.on_mark_read_clicked(None, row.entry_id)  # mark as read without opening
                return True
            if row is None:
                if self.popup:
                    self.popup.hide()  # empty space below the last item — close the popup
                return True
        return False

    def on_row_activated(self, _listbox, row):
        if getattr(row, 'summary_header', False):
            self._clear_update_summary()  # dismissed; works offline like the orphan rows
            return
        if getattr(row, 'orphans_header', False):  # these work offline: nothing here needs the network
            if self.active_installs == 0 and not self.orphan_status:
                self.orphans_expanded = not self.orphans_expanded
                self.refresh_list(keep_open=True)
            return
        action = getattr(row, 'orphans_action', None)
        if action:
            self.orphans_expanded = False
            if action == 'cancel':
                self.refresh_list(keep_open=True)
            else:
                self.remove_orphans(clean_cache=(action == 'remove_cache'))
            return
        if not self.online and (getattr(row, 'install_all_header', False)
                                or hasattr(row, 'header_feed_url') or hasattr(row, 'entry_id')
                                or hasattr(row, 'twitch_channel') or hasattr(row, 'youtube_channel')):
            return  # offline: news, feed headers, "install all" and live channels do nothing
        if getattr(row, 'install_all_header', False):
            self.install_all_updates()
            return
        if hasattr(row, 'header_feed_url'):
            self.mark_feed_read(row.header_feed_url)
            return
        if hasattr(row, 'twitch_channel'):
            if self.popup:
                self.popup.hide()
            open_twitch_stream(row.twitch_channel, getattr(row, 'twitch_quality', 'best'),
                               title=getattr(row, 'live_title', None))
            return
        if hasattr(row, 'youtube_channel'):
            if self.popup:
                self.popup.hide()
            open_youtube_stream(row.youtube_channel, getattr(row, 'youtube_quality', 'best'),
                                title=getattr(row, 'live_title', None))
            return
        if not hasattr(row, 'entry_id'):
            return
        item_id, link = row.entry_id, row.link
        self._remove_unread(item_id)
        if link:
            webbrowser.open(link)
        self.update_icon()
        self.refresh_list()
