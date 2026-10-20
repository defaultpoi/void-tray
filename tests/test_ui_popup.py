import threading
import unittest
from unittest import mock

from facade import rt

REAL_AVAILABLE_ICON = rt._available_icon


def setUpModule():
    """Rows are built without a screen here, so the icon theme cannot be asked."""
    patcher = mock.patch.object(rt, '_available_icon', return_value='icon-name')
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class TestPopupCss(unittest.TestCase):
    def _rules(self):
        rules = {}
        for block in rt.POPUP_CSS.split('}'):
            if '{' in block:
                selectors, body = block.split('{', 1)
                for sel in selectors.split(','):
                    rules.setdefault(sel.strip(), []).append(body.strip())
        return rules

    def test_list_area_background_uses_the_shorthand_that_resets_theme_images(self):
        rules = self._rules()
        for selector in ('list', 'viewport', 'scrolledwindow', 'overlay', '.popup-content', '.weather-bar', '.timer-bar'):
            self.assertTrue(any('background:' in body for body in rules[selector]), selector)

    def test_no_plain_background_color_left_on_those_widgets(self):
        self.assertNotIn('background-color', rt.POPUP_CSS)

    def test_list_text_is_forced_dark(self):
        rules = self._rules()
        self.assertTrue(any('color: #000000' in b for b in rules['list label']))


class TestEmptyAreaClick(unittest.TestCase):
    class Stub:
        on_empty_area_button_press = rt.RssTray.on_empty_area_button_press

        def __init__(self):
            self.popup = mock.MagicMock()

    def test_right_click_closes_the_popup(self):
        stub = self.Stub()
        self.assertTrue(stub.on_empty_area_button_press(None, mock.Mock(button=3)))
        stub.popup.hide.assert_called_once()

    def test_other_buttons_are_left_alone(self):
        stub = self.Stub()
        self.assertFalse(stub.on_empty_area_button_press(None, mock.Mock(button=1)))
        stub.popup.hide.assert_not_called()


class TestPopupRebuildRules(unittest.TestCase):
    class Stub:
        show_popup = rt.RssTray.show_popup

        def __init__(self, visible):
            self.popup = mock.MagicMock()
            self.popup.get_visible.return_value = visible
            self.refresh_list = mock.Mock()
            self.probe_connectivity = mock.Mock()
            self.check_orphans_if_due = mock.Mock()
            self.position_popup = mock.Mock()
            self._update_scroller_max_height = mock.Mock()
            self._launcher_open = True
            self.old_popup = self.popup

        def build_popup_window(self):
            self.popup = mock.MagicMock()

    def test_auto_show_on_a_visible_popup_only_refreshes_the_list(self):
        stub = self.Stub(visible=True)
        with mock.patch.object(rt, 'is_fullscreen_active', return_value=False):
            stub.show_popup(auto=True)
        stub.refresh_list.assert_called_once()
        stub.old_popup.destroy.assert_not_called()
        self.assertTrue(stub._launcher_open is True)

    def test_manual_show_rebuilds_and_clears_the_launch_guard(self):
        stub = self.Stub(visible=True)
        with mock.patch.object(rt, 'is_fullscreen_active', return_value=False), \
                mock.patch.object(rt.threading, 'Thread') as thread:
            stub.show_popup()
        stub.old_popup.destroy.assert_called_once()
        self.assertFalse(stub._launcher_open)
        stub.popup.show_all.assert_called_once()
        # a hand-opened popup looks for orphaned packages first, so the list it offers to remove is current
        thread.assert_called_once_with(target=stub.check_orphans_if_due, kwargs={'force': True}, daemon=True)
        thread.return_value.start.assert_called_once()

    def test_auto_show_on_a_hidden_popup_rebuilds(self):
        stub = self.Stub(visible=False)
        with mock.patch.object(rt, 'is_fullscreen_active', return_value=False), \
                mock.patch.object(rt.threading, 'Thread') as thread:
            stub.show_popup(auto=True)
        stub.old_popup.destroy.assert_called_once()
        thread.assert_not_called()  # an automatic open (new items) does not trigger a scan


class TestBreakingPulse(unittest.TestCase):
    class Stub:
        _breaking_pulse_tick = rt.RssTray._breaking_pulse_tick
        _apply_breaking_state = rt.RssTray._apply_breaking_state
        build_row = rt.RssTray.build_row

        def __init__(self, visible=True):
            self.popup = mock.MagicMock()
            self.popup.get_visible.return_value = visible
            self._breaking_rows = []
            self._breaking_on = False
            self.timer_missed_boot = None
            self.timer_status_box = None
            self.timer_label = None
            self._highlight_words = ('breaking',)
            self.on_save_clicked = mock.Mock()
            self.on_read_later_remove_clicked = mock.Mock()

    def make_row(self):
        return mock.MagicMock()

    def test_build_row_registers_only_highlighted_titles(self):
        stub = self.Stub()
        with mock.patch.object(rt.Gtk, 'ListBoxRow', side_effect=self.make_row), mock.patch.object(rt.Gtk, 'Box'), \
                mock.patch.object(rt.Gtk, 'Button'), mock.patch.object(rt.Gtk, 'Image'), mock.patch.object(rt.Gtk, 'Label'), \
                mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x):
            plain = stub.build_row({'id': '1', 'link': 'l', 'title': 'Quiet day'})
            breaking = stub.build_row({'id': '2', 'link': 'l', 'title': 'Breaking: something happened'})
        self.assertEqual(stub._breaking_rows, [breaking])
        self.assertNotIn(plain, stub._breaking_rows)

    def test_build_row_also_registers_items_highlighted_by_a_category(self):
        stub = self.Stub()
        with mock.patch.object(rt.Gtk, 'ListBoxRow', side_effect=self.make_row), mock.patch.object(rt.Gtk, 'Box'), \
                mock.patch.object(rt.Gtk, 'Button'), mock.patch.object(rt.Gtk, 'Image'), mock.patch.object(rt.Gtk, 'Label'), \
                mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x):
            tagged = stub.build_row({'id': '1', 'link': 'l', 'title': 'Quiet day', 'tags': ['Sport', 'Breaking News']})
            other = stub.build_row({'id': '2', 'link': 'l', 'title': 'Quiet day', 'tags': ['Sport']})
        self.assertEqual(stub._breaking_rows, [tagged])
        self.assertNotIn(other, stub._breaking_rows)

    def test_tick_toggles_the_pulse_class_on_every_highlighted_row(self):
        stub = self.Stub()
        row = self.make_row()
        stub._breaking_rows = [row]
        self.assertTrue(stub._breaking_pulse_tick())
        row.get_style_context().add_class.assert_called_with('breaking-on')
        stub._breaking_pulse_tick()
        row.get_style_context().remove_class.assert_called_with('breaking-on')

    def test_no_work_while_the_popup_is_hidden_or_nothing_is_highlighted(self):
        hidden = self.Stub(visible=False)
        row = self.make_row()
        hidden._breaking_rows = [row]
        hidden._breaking_pulse_tick()
        row.get_style_context().add_class.assert_not_called()
        empty = self.Stub()
        self.assertTrue(empty._breaking_pulse_tick())
        self.assertFalse(empty._breaking_on)

    def test_a_row_built_mid_pulse_starts_in_the_current_phase(self):
        stub = self.Stub()
        stub._breaking_on = True
        with mock.patch.object(rt.Gtk, 'ListBoxRow', side_effect=self.make_row), mock.patch.object(rt.Gtk, 'Box'), \
                mock.patch.object(rt.Gtk, 'Button'), mock.patch.object(rt.Gtk, 'Image'), mock.patch.object(rt.Gtk, 'Label'), \
                mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x):
            row = stub.build_row({'id': '2', 'link': 'l', 'title': 'BREAKING news'})
        row.get_style_context().add_class.assert_called_with('breaking-on')

    def test_css_uses_the_background_shorthand(self):
        self.assertIn('row.breaking-on { background:', rt.POPUP_CSS)


class TestItemTooltipDetails(unittest.TestCase):
    def tooltip(self, entry):
        stub = TestBreakingPulse.Stub()
        row = mock.MagicMock()
        with mock.patch.object(rt.Gtk, 'ListBoxRow', return_value=row), mock.patch.object(rt.Gtk, 'Box'), \
                mock.patch.object(rt.Gtk, 'Button'), mock.patch.object(rt.Gtk, 'Image'), mock.patch.object(rt.Gtk, 'Label'), \
                mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x):
            stub.build_row(entry)
        return row.set_tooltip_text.call_args[0][0] if row.set_tooltip_text.called else None

    def test_categories_and_author_are_in_the_tooltip(self):
        self.assertEqual(self.tooltip({'id': '1', 'link': 'l', 'title': 'Short', 'tags': ['a', 'b'], 'author': 'X'}),
                         'Categories: a, b\nAuthor: X')

    def test_a_long_title_comes_first(self):
        long_title = 'x' * (rt.MAX_TITLE_LEN + 20)
        text = self.tooltip({'id': '1', 'link': 'l', 'title': long_title, 'tags': ['a']})
        self.assertEqual(text, long_title + '\nCategories: a')

    def test_no_tooltip_for_a_short_title_without_details(self):
        self.assertIsNone(self.tooltip({'id': '1', 'link': 'l', 'title': 'Short'}))


class TestPopupReservesRoomForTheScheduleSlide(unittest.TestCase):
    """The popup window is never resized, so a short or empty list must already
    be tall enough for the Schedule slide when the person switches to it."""

    def build(self):
        gtk = mock.MagicMock()
        app = mock.MagicMock()
        app.timer_remaining_seconds = 0
        app.timer_mode = 'countdown'
        app.timer_visible = True
        with mock.patch.object(rt, 'Gtk', gtk), mock.patch.object(rt, 'Gdk', mock.MagicMock()), \
                mock.patch.object(rt, 'load_timer_settings', return_value={'max_seconds': 7200, 'step_seconds': 60}):
            rt.RssTray.build_popup_window(app)
        return gtk.ScrolledWindow.return_value

    def test_the_list_area_is_at_least_as_tall_as_the_schedule_slide_plus_a_row(self):
        scroller = self.build()
        scroller.set_min_content_height.assert_called_once_with(rt.TIMER_RESERVED_HEIGHT_PX)
        self.assertGreaterEqual(
            rt.TIMER_RESERVED_HEIGHT_PX,
            rt.LIST_ROW_RESERVE_PX + rt.TIMER_HEIGHT_SCHEDULE_PX + rt.TIMER_SPACER_EXTRA_PX)

    def test_the_closed_timer_readout_is_an_overlay_that_lets_clicks_through(self):
        gtk = mock.MagicMock()
        app = mock.MagicMock()
        app.timer_remaining_seconds = 0
        app.timer_mode = 'countdown'
        app.timer_visible = False
        with mock.patch.object(rt, 'Gtk', gtk), mock.patch.object(rt, 'Gdk', mock.MagicMock()), \
                mock.patch.object(rt, 'load_timer_settings', return_value={'max_seconds': 7200, 'step_seconds': 60}):
            rt.RssTray.build_popup_window(app)
        overlay = gtk.Overlay.return_value
        overlay.set_overlay_pass_through.assert_called_once()
        self.assertIs(app.timer_status_box, gtk.EventBox.return_value)
        self.assertIs(app.timer_status_label, gtk.Label.return_value)
        gtk.Label.return_value.set_justify.assert_any_call(gtk.Justification.RIGHT)
        app._update_timer_status.assert_called()

    def test_the_reserve_covers_the_slide_it_is_for(self):
        self.assertGreater(rt.TIMER_RESERVED_HEIGHT_PX, rt.TIMER_HEIGHT_SCHEDULE_PX)
        self.assertGreater(rt.TIMER_HEIGHT_SCHEDULE_PX, rt.TIMER_HEIGHT_COUNTDOWN_PX)


class TestOrphansHeader(unittest.TestCase):
    class Stub:
        build_orphans_header = rt.RssTray.build_orphans_header
        _plural = staticmethod(rt.RssTray._plural)

        def __init__(self, status=None):
            self.orphan_status = status
            self.build_header_row = mock.Mock()

    def build(self, names, status=None, **kwargs):
        stub = self.Stub(status)
        row = stub.build_orphans_header(names, **kwargs)
        return stub, row

    def test_the_title_counts_and_pluralises(self):
        stub, _ = self.build(['a', 'b', 'c'])
        self.assertEqual(stub.build_header_row.call_args[0][1], '3 orphaned packages')
        stub, _ = self.build(['a'])
        self.assertEqual(stub.build_header_row.call_args[0][1], '1 orphaned package')

    def test_it_is_a_clickable_header_flagged_for_the_orphan_menu(self):
        stub, _ = self.build(['a'], is_first=True)
        args, kwargs = stub.build_header_row.call_args
        self.assertEqual(args[0], '__orphans__')
        self.assertTrue(kwargs['orphans'])
        self.assertFalse(kwargs['clickable'])  # not a feed title: left-click opens the menu, nothing is marked read
        self.assertTrue(kwargs['is_first'])

    def test_a_running_or_finished_removal_replaces_the_count(self):
        stub, _ = self.build([], status='Removing…')
        self.assertEqual(stub.build_header_row.call_args[0][1], 'Orphaned packages · Removing…')
        stub, _ = self.build(['left'], status='Done')
        self.assertEqual(stub.build_header_row.call_args[0][1], 'Orphaned packages · Done')

    def test_the_tooltip_lists_the_first_fifteen_and_counts_the_rest(self):
        names = [f'pkg{i:02d}' for i in range(20)]
        _, row = self.build(names)
        tip = row.set_tooltip_text.call_args[0][0]
        self.assertIn('pkg00', tip)
        self.assertIn('pkg14', tip)
        self.assertNotIn('pkg15', tip)
        self.assertIn('... and 5 more', tip)

    def test_the_tooltip_without_names_is_just_the_explanation(self):
        _, row = self.build([], status='Done')
        self.assertNotIn('\n', row.set_tooltip_text.call_args[0][0])


class TestRefreshListOrphans(unittest.TestCase):
    class Stub:
        refresh_list = rt.RssTray.refresh_list
        build_orphans_header = rt.RssTray.build_orphans_header
        add_orphan_rows = rt.RssTray.add_orphan_rows
        add_update_summary_rows = rt.RssTray.add_update_summary_rows
        _orphan_info_row = rt.RssTray._orphan_info_row
        _plural = staticmethod(rt.RssTray._plural)
        _name_list = staticmethod(rt.RssTray._name_list)

        def __init__(self, state, popup_visible=False, status=None, expanded=False):
            self.lock = threading.RLock()
            self.state = state
            self.orphan_status = status
            self.orphans_expanded = expanded
            self.update_summary = None
            self.listbox = mock.MagicMock()
            self.listbox.get_children.return_value = []
            self.popup = mock.MagicMock()
            self.popup.get_visible.return_value = popup_visible
            self.headers = []

            def header(feed_url, name, **kwargs):
                self.headers.append(feed_url)
                return mock.MagicMock()

            self.build_header_row = header
            self.build_row = mock.Mock(return_value=mock.MagicMock())
            self.build_info_row = mock.Mock(return_value=mock.MagicMock())
            self.build_twitch_row = self.build_youtube_row = mock.Mock(return_value=mock.MagicMock())

    def refresh(self, stub, nudge=True):
        with mock.patch.object(rt, 'Gtk', mock.MagicMock()), \
                mock.patch.object(rt, 'load_feeds', return_value=[]), \
                mock.patch.object(rt, 'load_highlight_words', return_value=('breaking',)), \
                mock.patch.object(rt, 'load_orphan_nudge', return_value=nudge):
            stub.refresh_list()

    def unread(self, n=2):
        return [{'id': str(i), 'title': f'Item {i}', 'link': 'l', 'feed_url': 'f'} for i in range(n)]

    def test_orphans_alone_are_still_offered_on_a_hand_opened_popup(self):
        stub = self.Stub({'orphans': ['libfoo']})
        self.refresh(stub)
        self.assertEqual(stub.headers, ['__orphans__'])

    def test_orphans_come_last_after_the_news(self):
        stub = self.Stub({'unread': self.unread(), 'orphans': ['libfoo']})
        self.refresh(stub)
        self.assertEqual(stub.headers, ['f', '__orphans__'])

    def test_orphans_come_after_the_available_updates(self):
        stub = self.Stub({'available_updates': [{'pkgname': 'p', 'id': '1', 'title': 't', 'link': ''}], 'orphans': ['libfoo']})
        self.refresh(stub)
        self.assertEqual(stub.headers, ['__updates__', '__orphans__'])

    def test_no_orphans_no_row(self):
        stub = self.Stub({'unread': self.unread()})
        self.refresh(stub)
        self.assertEqual(stub.headers, ['f'])

    def test_switched_off_hides_the_row_even_with_orphans(self):
        stub = self.Stub({'unread': self.unread(), 'orphans': ['libfoo']})
        self.refresh(stub, nudge=False)
        self.assertEqual(stub.headers, ['f'])

    def test_a_removal_result_keeps_the_row_even_when_nothing_is_left(self):
        stub = self.Stub({'orphans': []}, status='Done')
        self.refresh(stub)
        self.assertEqual(stub.headers, ['__orphans__'])

    def test_reading_the_last_item_still_closes_the_popup_despite_orphans(self):
        stub = self.Stub({'orphans': ['libfoo']}, popup_visible=True)
        self.refresh(stub)
        stub.popup.hide.assert_called_once()  # the same auto-close as before: orphans never hold it open

    def test_a_refresh_nobody_asked_for_leaves_the_popup_open_and_still_lists_the_orphans(self):
        stub = self.Stub({'orphans': ['libfoo']}, popup_visible=True)
        with mock.patch.object(rt, 'Gtk', mock.MagicMock()), mock.patch.object(rt, 'load_feeds', return_value=[]), \
                mock.patch.object(rt, 'load_highlight_words', return_value=()), mock.patch.object(rt, 'load_orphan_nudge', return_value=True):
            stub.refresh_list(keep_open=True)
        stub.popup.hide.assert_not_called()
        self.assertEqual(stub.headers, ['__orphans__'])

    def test_the_popup_stays_while_a_removal_shows_its_status(self):
        for status in ('Removing…', 'Done', 'Failed'):
            stub = self.Stub({'orphans': []}, popup_visible=True, status=status)
            self.refresh(stub)
            stub.popup.hide.assert_not_called()


class TestOrphansStayOutOfTheAlerts(unittest.TestCase):
    class Stub:
        total_badge_count = rt.RssTray.total_badge_count
        has_anything_to_show = rt.RssTray.has_anything_to_show
        has_pkg_update = rt.RssTray.has_pkg_update

        def __init__(self, state):
            self.lock = threading.RLock()
            self.state = state

    def test_orphans_do_not_count_in_the_tray_badge(self):
        stub = self.Stub({'unread': [1, 2], 'available_updates': [3], 'orphans': ['a', 'b', 'c', 'd']})
        self.assertEqual(stub.total_badge_count(), 3)
        self.assertEqual(self.Stub({'orphans': ['a']}).total_badge_count(), 0)

    def test_orphans_alone_are_not_something_to_show_so_they_never_auto_open_the_popup(self):
        self.assertFalse(self.Stub({'orphans': ['a']}).has_anything_to_show())

    def test_orphans_do_not_turn_the_icon_into_the_package_update_colour(self):
        self.assertFalse(self.Stub({'orphans': ['a']}).has_pkg_update())


class TestOrphanRowActivation(unittest.TestCase):
    class Stub:
        on_row_activated = rt.RssTray.on_row_activated

        def __init__(self, online=True, active=0, status=None, expanded=False):
            self.online = online
            self.active_installs = active
            self.orphan_status = status
            self.orphans_expanded = expanded
            self.refresh_list = mock.Mock()
            self.remove_orphans = mock.Mock()
            self.install_all_updates = mock.Mock()

    def header(self):
        return mock.Mock(orphans_header=True, summary_header=False, spec_set=None)

    def action(self, name):
        return mock.Mock(orphans_action=name, spec_set=None, orphans_header=False, summary_header=False, install_all_header=False)

    def test_clicking_the_header_expands_and_collapses_the_choices(self):
        stub = self.Stub()
        stub.on_row_activated(None, self.header())
        self.assertTrue(stub.orphans_expanded)
        stub.refresh_list.assert_called_once_with(keep_open=True)
        stub.on_row_activated(None, self.header())
        self.assertFalse(stub.orphans_expanded)
        stub.remove_orphans.assert_not_called()  # opening the choices removes nothing

    def test_it_works_offline_because_removal_needs_no_network(self):
        stub = self.Stub(online=False)
        stub.on_row_activated(None, self.header())
        self.assertTrue(stub.orphans_expanded)

    def test_the_header_does_nothing_while_an_install_or_removal_runs_or_shows_its_result(self):
        for kwargs in ({'active': 1}, {'status': 'Removing…'}, {'status': 'Done'}):
            stub = self.Stub(**kwargs)
            stub.on_row_activated(None, self.header())
            self.assertFalse(stub.orphans_expanded, kwargs)
            stub.refresh_list.assert_not_called()

    def test_the_header_does_not_trigger_install_all(self):
        stub = self.Stub()
        stub.on_row_activated(None, self.header())
        stub.install_all_updates.assert_not_called()

    def test_remove_collapses_and_removes_without_touching_the_cache(self):
        stub = self.Stub(expanded=True)
        stub.on_row_activated(None, self.action('remove'))
        self.assertFalse(stub.orphans_expanded)
        stub.remove_orphans.assert_called_once_with(clean_cache=False)

    def test_remove_and_clean_cache_asks_for_the_cache_too(self):
        stub = self.Stub(expanded=True)
        stub.on_row_activated(None, self.action('remove_cache'))
        stub.remove_orphans.assert_called_once_with(clean_cache=True)

    def test_cancel_collapses_and_removes_nothing(self):
        stub = self.Stub(expanded=True)
        stub.on_row_activated(None, self.action('cancel'))
        self.assertFalse(stub.orphans_expanded)
        stub.remove_orphans.assert_not_called()
        stub.refresh_list.assert_called_once_with(keep_open=True)

    def test_the_choices_work_offline_too(self):
        stub = self.Stub(online=False, expanded=True)
        stub.on_row_activated(None, self.action('remove'))
        stub.remove_orphans.assert_called_once()


class TestExpandedOrphanRows(unittest.TestCase):
    """The choices under the header are plain list rows, so a short popup cannot clip them."""

    Stub = TestRefreshListOrphans.Stub

    def rows(self, orphans, **kwargs):
        stub = self.Stub({'orphans': orphans}, **kwargs)
        made = []

        def make_row():
            row = mock.MagicMock()
            made.append(row)
            return row

        with mock.patch.object(rt.Gtk, 'ListBoxRow', side_effect=make_row), mock.patch.object(rt.Gtk, 'Label') as label, \
                mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x):
            stub.add_orphan_rows(orphans)
        added = [call.args[0] for call in stub.listbox.add.call_args_list]
        return stub, made, added, label

    def actions(self, made):
        return [row.orphans_action for row in made if hasattr(row, 'orphans_action') and isinstance(row.orphans_action, str)]

    def test_collapsed_shows_only_the_header(self):
        stub, made, added, _ = self.rows(['a', 'b'], expanded=False)
        self.assertEqual(len(added), 1)
        self.assertEqual(stub.headers, ['__orphans__'])
        self.assertEqual(self.actions(made), [])

    def test_expanded_offers_remove_then_remove_and_clean_then_cancel_above_the_names(self):
        _, made, _, _ = self.rows(['libfoo', 'libbar'], expanded=True)
        self.assertEqual(self.actions(made), ['remove', 'remove_cache', 'cancel'])

    def test_every_name_is_listed_and_the_actions_are_activatable_but_the_names_are_not(self):
        _, made, _, label = self.rows(['libfoo', 'libbar'], expanded=True)
        markups = [c.args[0] for l in [label.return_value] for c in l.set_markup.call_args_list]
        self.assertTrue(any('libfoo' in m for m in markups))
        self.assertTrue(any('libbar' in m for m in markups))
        action_rows = [r for r in made if isinstance(getattr(r, 'orphans_action', None), str)]
        self.assertTrue(all(r.set_activatable.call_args[0][0] for r in action_rows))
        info_rows = [r for r in made if r not in action_rows]
        self.assertTrue(all(not r.set_activatable.call_args[0][0] for r in info_rows))

    def test_the_remove_row_says_how_many_packages(self):
        _, _, _, label = self.rows(['a', 'b', 'c'], expanded=True)
        markups = [c.args[0] for c in label.return_value.set_markup.call_args_list]
        self.assertIn('<b>Remove 3 packages</b>', markups)
        _, _, _, label = self.rows(['a'], expanded=True)
        markups = [c.args[0] for c in label.return_value.set_markup.call_args_list]
        self.assertIn('<b>Remove 1 package</b>', markups)

    def test_a_long_list_is_cut_with_a_summary(self):
        names = [f'pkg{i}' for i in range(rt.ORPHAN_LIST_MAX_NAMES + 4)]
        _, _, _, label = self.rows(names, expanded=True)
        markups = [c.args[0] for c in label.return_value.set_markup.call_args_list]
        self.assertIn('... and 4 more', markups)
        self.assertFalse(any(f'pkg{rt.ORPHAN_LIST_MAX_NAMES}' in m for m in markups))

    def test_the_hint_on_keeping_a_package_is_shown(self):
        _, _, _, label = self.rows(['a'], expanded=True)
        markups = [c.args[0] for c in label.return_value.set_markup.call_args_list]
        self.assertTrue(any('xbps-pkgdb -m manual' in m for m in markups))

    def test_no_choices_while_a_removal_runs_or_shows_its_result(self):
        for status in ('Removing…', 'Done', 'Failed'):
            _, made, added, _ = self.rows(['a'], expanded=True, status=status)
            self.assertEqual(len(added), 1, status)
            self.assertEqual(self.actions(made), [], status)

    def test_nothing_to_choose_when_no_orphans_are_left(self):
        _, made, added, _ = self.rows([], expanded=True, status=None)
        self.assertEqual(self.actions(made), [])


class TestUpdateSummaryRows(unittest.TestCase):
    Stub = TestRefreshListOrphans.Stub

    def summary(self, ok=True, lines=('System update: OK',), details=()):
        return {'ok': ok, 'when': '10:46', 'lines': list(lines), 'details': list(details)}

    def rows(self, summary):
        stub = self.Stub({})
        stub.update_summary = summary
        markups = []
        with mock.patch.object(rt.Gtk, 'ListBoxRow', side_effect=lambda: mock.MagicMock()), \
                mock.patch.object(rt.Gtk, 'Label') as label, \
                mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x):
            stub.header_calls = []
            stub.build_header_row = mock.Mock(return_value=mock.MagicMock())
            stub.add_update_summary_rows(is_first=True)
            markups = [c.args[0] for c in label.return_value.set_markup.call_args_list]
        return stub, markups

    def test_the_header_names_the_time_and_the_verdict_and_is_a_dismiss_target(self):
        stub, _ = self.rows(self.summary())
        args, kwargs = stub.build_header_row.call_args
        self.assertEqual(args[:2], ('__summary__', 'Topgrade · 10:46 · OK'))
        self.assertTrue(kwargs['summary'])
        self.assertFalse(kwargs['clickable'])  # a click dismisses it; it is not a feed title
        self.assertTrue(kwargs['is_first'])
        stub.build_header_row.return_value.set_tooltip_text.assert_called_once_with('Click to dismiss')

    def test_a_failed_run_says_failed(self):
        stub, _ = self.rows(self.summary(ok=False, lines=['A: FAILED']))
        self.assertEqual(stub.build_header_row.call_args[0][1], 'Topgrade · 10:46 · FAILED')

    def title(self, **extra):
        summary = self.summary()
        summary.update(extra)
        stub, _ = self.rows(summary)
        return stub.build_header_row.call_args[0][1]

    def test_the_header_counts_the_packages_that_were_updated(self):
        self.assertEqual(self.title(updated=['a', 'b', 'c']), 'Topgrade · 10:46 · OK · 3 packages updated')
        self.assertEqual(self.title(updated=['a']), 'Topgrade · 10:46 · OK · 1 package updated')
        self.assertEqual(self.title(updated=[]), 'Topgrade · 10:46 · OK · no packages updated')

    def test_a_failed_run_still_says_how_many_were_updated(self):
        stub, _ = self.rows(self.summary(ok=False, lines=['A: FAILED']) | {'updated': ['a', 'b']})
        self.assertEqual(stub.build_header_row.call_args[0][1], 'Topgrade · 10:46 · FAILED · 2 packages updated')

    def test_a_summary_without_the_package_lists_has_no_count(self):
        self.assertEqual(self.title(), 'Topgrade · 10:46 · OK')

    def test_the_updated_package_names_follow_on_one_wrapping_line(self):
        _, markups = self.rows(self.summary() | {'updated': ['libfoo', 'bar-devel', 'baz']})
        self.assertIn('Updated: libfoo, bar-devel, baz', markups)

    def test_packages_that_did_not_update_are_called_out_in_red(self):
        _, markups = self.rows(self.summary() | {'updated': ['a'], 'not_updated': ['held']})
        line = [m for m in markups if 'Not updated' in m][0]
        self.assertIn('Not updated: held', line)
        self.assertIn('foreground="#b3261e"', line)

    def test_no_name_rows_when_both_lists_are_empty(self):
        _, markups = self.rows(self.summary() | {'updated': [], 'not_updated': []})
        self.assertFalse([m for m in markups if m.startswith('Updated') or 'Not updated' in m])

    def test_a_very_long_list_is_cut_with_a_count_of_the_rest(self):
        names = [f'pkg{i}' for i in range(rt.SUMMARY_MAX_NAMES + 5)]
        _, markups = self.rows(self.summary() | {'updated': names})
        line = [m for m in markups if m.startswith('Updated')][0]
        self.assertTrue(line.endswith('… and 5 more'))
        self.assertIn(f'pkg{rt.SUMMARY_MAX_NAMES - 1}', line)
        self.assertNotIn(f'pkg{rt.SUMMARY_MAX_NAMES},', line)

    def test_the_name_lines_wrap_instead_of_widening_the_popup(self):
        stub = self.Stub({})
        stub.update_summary = self.summary() | {'updated': ['a']}
        with mock.patch.object(rt.Gtk, 'ListBoxRow'), mock.patch.object(rt.Gtk, 'Label') as label, \
                mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x):
            stub.build_header_row = mock.Mock(return_value=mock.MagicMock())
            stub.add_update_summary_rows()
        label.return_value.set_line_wrap.assert_called_with(True)

    def test_every_summary_line_is_a_row_and_failed_ones_stand_out(self):
        _, markups = self.rows(self.summary(ok=False, lines=['System update: OK', 'Flatpak: FAILED']))
        self.assertIn('System update: OK', markups)
        failed = [m for m in markups if 'Flatpak' in m][0]
        self.assertIn('foreground="#b3261e"', failed)
        self.assertNotIn('foreground', [m for m in markups if 'System update' in m][0])

    def test_a_failed_steps_output_follows_in_small_type(self):
        _, markups = self.rows(self.summary(ok=False, lines=['Broken: FAILED'], details=['Broken: sudo: a password is required']))
        self.assertIn('<small>Broken: sudo: a password is required</small>', markups)

    def test_markup_in_the_output_is_escaped(self):
        stub = self.Stub({})
        stub.update_summary = self.summary(lines=['<b>x</b>: OK'])
        with mock.patch.object(rt.Gtk, 'ListBoxRow'), mock.patch.object(rt.Gtk, 'Label'), \
                mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x.replace('<', '&lt;')) as esc:
            stub.build_header_row = mock.Mock(return_value=mock.MagicMock())
            stub.add_update_summary_rows()
        esc.assert_any_call('<b>x</b>: OK')


class TestSummaryInTheList(unittest.TestCase):
    Stub = TestRefreshListOrphans.Stub

    def refresh(self, stub):
        with mock.patch.object(rt, 'Gtk', mock.MagicMock()), mock.patch.object(rt, 'load_feeds', return_value=[]), \
                mock.patch.object(rt, 'load_highlight_words', return_value=()), mock.patch.object(rt, 'load_orphan_nudge', return_value=True):
            stub.refresh_list()

    def summary(self):
        return {'ok': True, 'when': '10:46', 'lines': ['System update: OK'], 'details': []}

    def test_it_sits_after_the_updates_and_before_the_orphans(self):
        stub = self.Stub({'available_updates': [{'pkgname': 'p', 'id': '1', 'title': 't', 'link': ''}], 'orphans': ['libfoo']})
        stub.update_summary = self.summary()
        stub.add_update_summary_rows = lambda is_first=False: stub.headers.append('__summary__')
        self.refresh(stub)
        self.assertEqual(stub.headers, ['__updates__', '__summary__', '__orphans__'])

    def test_it_is_shown_even_when_nothing_else_is(self):
        stub = self.Stub({})
        stub.update_summary = self.summary()
        stub.add_update_summary_rows = lambda is_first=False: stub.headers.append('__summary__')
        self.refresh(stub)
        self.assertEqual(stub.headers, ['__summary__'])

    def test_it_keeps_a_visible_popup_open_so_the_result_is_seen(self):
        stub = self.Stub({}, popup_visible=True)
        stub.update_summary = self.summary()
        stub.add_update_summary_rows = lambda is_first=False: None
        self.refresh(stub)
        stub.popup.hide.assert_not_called()

    def test_without_a_summary_an_empty_popup_still_closes(self):
        stub = self.Stub({}, popup_visible=True)
        self.refresh(stub)
        stub.popup.hide.assert_called_once()


class TestSummaryRowActivation(unittest.TestCase):
    class Stub:
        on_row_activated = rt.RssTray.on_row_activated

        def __init__(self, online=True):
            self.online = online
            self._clear_update_summary = mock.Mock()
            self.refresh_list = mock.Mock()

    def test_clicking_the_summary_header_dismisses_it_even_offline(self):
        for online in (True, False):
            stub = self.Stub(online=online)
            stub.on_row_activated(None, mock.Mock(summary_header=True))
            stub._clear_update_summary.assert_called_once_with()


class TestAvailableIcon(unittest.TestCase):
    def theme(self, have):
        theme = mock.Mock()
        theme.has_icon.side_effect = lambda name: name in have
        return theme

    def test_the_first_icon_the_theme_has_wins(self):
        with mock.patch.object(rt.Gtk.IconTheme, 'get_default', return_value=self.theme({'b', 'c'})):
            self.assertEqual(REAL_AVAILABLE_ICON(('a', 'b', 'c')), 'b')

    def test_without_any_of_them_the_last_is_used(self):
        with mock.patch.object(rt.Gtk.IconTheme, 'get_default', return_value=self.theme(set())):
            self.assertEqual(REAL_AVAILABLE_ICON(('a', 'b', 'c')), 'c')

    def test_with_no_screen_to_ask_the_first_is_used(self):
        with mock.patch.object(rt.Gtk.IconTheme, 'get_default', return_value=None):
            self.assertEqual(REAL_AVAILABLE_ICON(('a', 'b', 'c')), 'a')

    def test_the_save_and_remove_candidates_are_bookmark_and_minus_icons(self):
        self.assertEqual(rt.SAVE_ICONS[0], 'bookmark-new-symbolic')
        self.assertEqual(rt.REMOVE_ICONS[0], 'list-remove-symbolic')


class TestRowFrontButton(unittest.TestCase):
    class Stub:
        build_row = rt.RssTray.build_row
        _apply_breaking_state = rt.RssTray._apply_breaking_state

        def __init__(self):
            self._breaking_rows = []
            self._breaking_on = False
            self.timer_missed_boot = None
            self.timer_status_box = None
            self.timer_label = None
            self._highlight_words = ('breaking',)
            self.on_save_clicked = mock.Mock(name='on_save_clicked')
            self.on_read_later_remove_clicked = mock.Mock(name='on_remove')

    ENTRY = {'id': '7', 'title': 'Breaking: a story', 'link': 'https://x.example/7'}

    def build(self, saved):
        stub = self.Stub()
        buttons = []

        def make_button(*a, **k):
            button = mock.MagicMock()
            buttons.append(button)
            return button

        with mock.patch.object(rt.Gtk, 'ListBoxRow', side_effect=lambda: mock.MagicMock(spec_set=None)), \
                mock.patch.object(rt.Gtk, 'Box'), mock.patch.object(rt.Gtk, 'Button', side_effect=make_button), \
                mock.patch.object(rt.Gtk, 'Image') as image, mock.patch.object(rt.Gtk, 'Label'), \
                mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x), \
                mock.patch.object(rt, '_available_icon', return_value='the-icon') as avail:
            row = stub.build_row(self.ENTRY, saved=saved)
        return stub, row, buttons, image, avail

    def test_an_unread_rows_icon_saves_it_for_later(self):
        stub, row, buttons, image, avail = self.build(saved=False)
        avail.assert_called_once_with(rt.SAVE_ICONS)
        image.new_from_icon_name.assert_called_once()
        self.assertEqual(image.new_from_icon_name.call_args[0][0], 'the-icon')
        buttons[0].set_tooltip_text.assert_called_once_with('Save for later')
        buttons[0].connect.assert_called_once_with('clicked', stub.on_save_clicked, '7')

    def test_an_unread_row_is_an_unread_row_with_an_entry_id(self):
        _, row, _, _, _ = self.build(saved=False)
        self.assertEqual((row.entry_id, row.link), ('7', 'https://x.example/7'))
        self.assertFalse(hasattr(row, 'readlater_id') and isinstance(row.readlater_id, str))

    def test_a_saved_rows_icon_removes_it_from_the_list(self):
        stub, row, buttons, _, avail = self.build(saved=True)
        avail.assert_called_once_with(rt.REMOVE_ICONS)
        buttons[0].set_tooltip_text.assert_called_once_with('Remove from Read later')
        buttons[0].connect.assert_called_once_with('clicked', stub.on_read_later_remove_clicked, '7')

    def test_a_saved_row_is_marked_for_the_read_later_handlers_not_the_unread_ones(self):
        _, row, _, _, _ = self.build(saved=True)
        self.assertEqual((row.readlater_id, row.link), ('7', 'https://x.example/7'))
        self.assertNotIsInstance(getattr(row, 'entry_id', None), str)

    def test_saved_rows_do_not_pulse_even_with_a_highlighted_title(self):
        stub, _, _, _, _ = self.build(saved=True)
        self.assertEqual(stub._breaking_rows, [])
        stub, _, _, _, _ = self.build(saved=False)
        self.assertEqual(len(stub._breaking_rows), 1)


class TestReadLaterSection(unittest.TestCase):
    Stub = TestRefreshListOrphans.Stub

    def entries(self, n):
        return [{'id': str(i), 'title': f't{i}', 'link': f'l{i}', 'feed_url': 'f'} for i in range(n)]

    def stub(self, state, **kwargs):
        stub = self.Stub(state, **kwargs)
        stub.add_read_later_rows = lambda items, is_first=False: stub.headers.append('__readlater__')
        stub.add_update_summary_rows = lambda is_first=False: stub.headers.append('__summary__')
        return stub

    def refresh(self, stub):
        with mock.patch.object(rt, 'Gtk', mock.MagicMock()), mock.patch.object(rt, 'load_feeds', return_value=[]), \
                mock.patch.object(rt, 'load_highlight_words', return_value=()), mock.patch.object(rt, 'load_orphan_nudge', return_value=True):
            stub.refresh_list()

    def unread(self):
        return [{'id': '1', 'title': 'Item', 'link': 'l', 'feed_url': 'f'}]

    def test_it_follows_the_news_and_precedes_the_updates(self):
        stub = self.stub({'unread': self.unread(), 'read_later': self.entries(2),
                          'available_updates': [{'pkgname': 'p', 'id': '1', 'title': 't', 'link': ''}], 'orphans': ['o']})
        self.refresh(stub)
        self.assertEqual(stub.headers, ['f', '__readlater__', '__updates__', '__orphans__'])

    def test_it_is_listed_even_when_nothing_is_unread(self):
        stub = self.stub({'read_later': self.entries(1)})
        self.refresh(stub)
        self.assertEqual(stub.headers, ['__readlater__'])

    def test_an_empty_list_has_no_section(self):
        stub = self.stub({'unread': self.unread(), 'read_later': []})
        self.refresh(stub)
        self.assertEqual(stub.headers, ['f'])

    def test_saved_items_never_keep_an_empty_popup_open(self):
        stub = self.stub({'read_later': self.entries(3)}, popup_visible=True)
        self.refresh(stub)
        stub.popup.hide.assert_called_once()  # the same auto-close as before: only unread items hold it open

    def test_the_section_has_its_own_header_with_the_count_and_a_row_per_item(self):
        stub = self.Stub({})
        added = []
        stub.listbox.add.side_effect = added.append
        rows = []
        stub.build_row = mock.Mock(side_effect=lambda e, saved=False: rows.append((e['id'], saved)) or mock.MagicMock())
        real = rt.RssTray.add_read_later_rows
        with mock.patch.object(rt.Gtk, 'ListBoxRow'), mock.patch.object(rt.Gtk, 'Label'):
            real(stub, self.entries(3), is_first=True)
        self.assertEqual(stub.headers, ['__readlater__'])
        self.assertEqual(rows, [('0', True), ('1', True), ('2', True)])
        self.assertEqual(len(added), 4)  # header + three rows

    def test_a_very_long_list_is_cut_with_a_count_of_the_rest(self):
        stub = self.Stub({})
        built = []
        stub.build_row = mock.Mock(side_effect=lambda e, saved=False: built.append(e) or mock.MagicMock())
        real = rt.RssTray.add_read_later_rows
        with mock.patch.object(rt.Gtk, 'ListBoxRow'), mock.patch.object(rt.Gtk, 'Label') as label:
            real(stub, self.entries(rt.MAX_LIST_ITEMS + 3))
        self.assertEqual(len(built), rt.MAX_LIST_ITEMS)
        label.assert_any_call(label='... and 3 more')

    def test_the_header_names_the_count(self):
        stub = self.Stub({})
        titles = []
        stub.build_header_row = lambda feed_url, title, **kw: titles.append((feed_url, title, kw)) or mock.MagicMock()
        stub.build_row = mock.Mock(return_value=mock.MagicMock())
        with mock.patch.object(rt.Gtk, 'ListBoxRow'), mock.patch.object(rt.Gtk, 'Label'):
            rt.RssTray.add_read_later_rows(stub, self.entries(4))
        self.assertEqual(titles[0][:2], ('__readlater__', 'Read later (4)'))
        self.assertFalse(titles[0][2]['clickable'])


class TestSavedRowActivation(unittest.TestCase):
    class Stub:
        on_row_activated = rt.RssTray.on_row_activated

        def __init__(self, online=True):
            self.online = online
            self.active_installs = 0
            self.orphan_status = None
            self.orphans_expanded = False
            self.open_read_later = mock.Mock()
            self.refresh_list = mock.Mock()

    def saved(self):
        return mock.Mock(spec=['readlater_id', 'link'], readlater_id='7', link='https://x.example/7')

    def test_clicking_a_saved_item_opens_it_and_takes_it_off_the_list(self):
        stub = self.Stub()
        stub.on_row_activated(None, self.saved())
        stub.open_read_later.assert_called_once_with('7', 'https://x.example/7')

    def test_offline_it_does_nothing_like_the_news_items(self):
        stub = self.Stub(online=False)
        stub.on_row_activated(None, self.saved())
        stub.open_read_later.assert_not_called()


class TestFeedSectionOrder(unittest.TestCase):
    Stub = TestRefreshListOrphans.Stub
    FEEDS = [('a', 'Feed A', None), ('b', 'Feed B', None), ('c', 'Feed C', None)]

    def unread(self, *feeds):
        return [{'id': str(i), 'title': f't{i}', 'link': 'l', 'feed_url': f} for i, f in enumerate(feeds)]

    def headers(self, unread, feeds=None):
        stub = self.Stub({'unread': unread})
        with mock.patch.object(rt, 'Gtk', mock.MagicMock()), mock.patch.object(rt, 'load_feeds', return_value=feeds or self.FEEDS), \
                mock.patch.object(rt, 'load_highlight_words', return_value=()), mock.patch.object(rt, 'load_orphan_nudge', return_value=True):
            stub.refresh_list()
        return stub.headers

    def test_feeds_are_listed_in_config_order_whatever_the_newest_item_is(self):
        self.assertEqual(self.headers(self.unread('c', 'b', 'a')), ['a', 'b', 'c'])

    def test_marking_the_newest_item_read_leaves_the_feed_order_alone(self):
        unread = self.unread('a', 'b', 'a', 'b', 'c')
        before = self.headers(unread)
        after = self.headers(unread[1:])  # a's newest item was read; the next in line is from b
        self.assertEqual(before, ['a', 'b', 'c'])
        self.assertEqual(after, before)

    def test_a_feed_that_runs_out_of_unread_items_just_disappears(self):
        unread = self.unread('a', 'b', 'c')
        self.assertEqual(self.headers(unread[1:]), ['b', 'c'])

    def test_the_order_is_the_same_when_only_part_of_a_long_list_is_shown(self):
        unread = self.unread(*(['c'] * rt.MAX_LIST_ITEMS + ['a']))
        self.assertEqual(self.headers(unread), ['c'])  # the cut falls inside c's items: a is not shown at all


class TestMissedCountdownPulse(unittest.TestCase):
    Stub = TestBreakingPulse.Stub

    def stub(self, missed, visible=True):
        stub = self.Stub(visible=visible)
        stub.timer_missed_boot = missed
        stub.timer_status_box = mock.MagicMock()
        stub.timer_label = mock.MagicMock()
        return stub

    def test_a_missed_countdown_pulses_even_with_no_highlighted_news(self):
        stub = self.stub(missed=5000.0)
        stub._breaking_pulse_tick()
        self.assertTrue(stub._breaking_on)
        for widget in (stub.timer_status_box, stub.timer_label):
            widget.get_style_context.return_value.add_class.assert_called_with('missed-on')
        stub._breaking_pulse_tick()
        for widget in (stub.timer_status_box, stub.timer_label):
            widget.get_style_context.return_value.remove_class.assert_called_with('missed-on')

    def test_no_pulse_while_the_popup_is_hidden(self):
        stub = self.stub(missed=5000.0, visible=False)
        stub._breaking_pulse_tick()
        self.assertFalse(stub._breaking_on)

    def test_nothing_pulses_without_a_miss(self):
        stub = self.stub(missed=None)
        stub._breaking_pulse_tick()
        self.assertFalse(stub._breaking_on)  # no highlighted rows and no miss: nothing to pulse
        stub.timer_status_box.get_style_context.assert_not_called()

    def test_the_timer_widgets_do_not_pulse_for_highlighted_news_alone(self):
        stub = self.stub(missed=None)
        stub._breaking_rows = [mock.MagicMock()]
        stub._breaking_pulse_tick()
        stub.timer_status_box.get_style_context.assert_not_called()
        stub.timer_label.get_style_context.assert_not_called()


if __name__ == '__main__':
    unittest.main()
