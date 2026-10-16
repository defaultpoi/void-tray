import threading
import unittest
from unittest import mock

from facade import rt


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
            self._highlight_words = ('breaking',)
            self.on_mark_read_clicked = mock.Mock()

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
        _orphan_info_row = rt.RssTray._orphan_info_row
        _plural = staticmethod(rt.RssTray._plural)

        def __init__(self, state, popup_visible=False, status=None, expanded=False):
            self.lock = threading.RLock()
            self.state = state
            self.orphan_status = status
            self.orphans_expanded = expanded
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
        return mock.Mock(orphans_header=True, spec_set=None)

    def action(self, name):
        return mock.Mock(orphans_action=name, spec_set=None, orphans_header=False, install_all_header=False)

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


if __name__ == '__main__':
    unittest.main()
