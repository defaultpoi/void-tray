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
        with mock.patch.object(rt, 'is_fullscreen_active', return_value=False):
            stub.show_popup()
        stub.old_popup.destroy.assert_called_once()
        self.assertFalse(stub._launcher_open)
        stub.popup.show_all.assert_called_once()

    def test_auto_show_on_a_hidden_popup_rebuilds(self):
        stub = self.Stub(visible=False)
        with mock.patch.object(rt, 'is_fullscreen_active', return_value=False):
            stub.show_popup(auto=True)
        stub.old_popup.destroy.assert_called_once()


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


if __name__ == '__main__':
    unittest.main()
