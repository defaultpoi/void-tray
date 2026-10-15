import threading
import unittest
from unittest import mock

from facade import rt


class TestTempIconLayout(unittest.TestCase):
    def test_digits_and_ring_are_centred_together(self):
        left, cx, cy = rt.temp_icon_layout(24, 12.0, 9.0)
        ring_outer = 2 * rt.TEMP_RING_RADIUS + rt.TEMP_RING_LINE_WIDTH
        right_edge = cx + ring_outer / 2
        self.assertAlmostEqual(left, 24 - right_edge, places=6)  # equal margins either side

    def test_ring_follows_the_digits_and_sits_at_their_top(self):
        left, cx, cy = rt.temp_icon_layout(24, 12.0, 9.0)
        self.assertGreater(cx, left + 12.0)             # to the right of the digits
        digits_top = 24 / 2 - 9.0 / 2
        self.assertAlmostEqual(cy - (2 * rt.TEMP_RING_RADIUS + rt.TEMP_RING_LINE_WIDTH) / 2, digits_top, places=6)

    def test_ring_never_leaves_the_icon_top(self):
        _left, _cx, cy = rt.temp_icon_layout(24, 12.0, 40.0)
        self.assertGreaterEqual(cy - rt.TEMP_RING_RADIUS - rt.TEMP_RING_LINE_WIDTH / 2, -1e-9)


class FakeContext:
    """Records what render_icon draws, standing in for a cairo context."""

    def __init__(self):
        self.calls = []
        self.font_size = 12

    def _record(self, name, *args):
        self.calls.append((name, args))

    def set_source_rgba(self, *rgba):
        self._record('rgba', *rgba)

    def set_font_size(self, size):
        self.font_size = size

    def text_extents(self, text):
        return (0, 0, 0.6 * self.font_size * len(text), 0.7 * self.font_size, 0, 0)

    def __getattr__(self, name):
        return lambda *args: self._record(name, *args)

    def drawn(self, name):
        return [args for call, args in self.calls if call == name]


class TestAlertIconText(unittest.TestCase):
    class Stub:
        render_icon = rt.RssTray.render_icon
        _draw_temperature = rt.RssTray._draw_temperature
        _current_temperature = rt.RssTray._current_temperature
        total_badge_count = rt.RssTray.total_badge_count
        _should_show_weather_icon = rt.RssTray._should_show_weather_icon
        has_pkg_update = rt.RssTray.has_pkg_update
        has_active_alerts = rt.RssTray.has_active_alerts
        _worst_alert_color = rt.RssTray._worst_alert_color
        _pulse_on_for = rt.RssTray._pulse_on_for

    def render(self, temp=24.6, color='red', unread=0, updates=0, pulse=0, alert=True):
        stub = self.Stub()
        stub.schedule_warning = None
        stub._schedule_flash_on = False
        stub.weather_data = {'temp': temp} if temp is not None else None
        stub.active_alerts = [{'severity_color': color, 'hazard': 'Wind'}] if alert else []
        stub._alert_pulse_counter = pulse
        stub.state = {'unread': [object()] * unread, 'available_updates': [object()] * updates}
        stub.lock = threading.RLock()
        ctx = FakeContext()
        fake_cairo = mock.Mock(Context=lambda _surface: ctx)
        with mock.patch.object(rt, 'cairo', fake_cairo), mock.patch.object(rt, 'Gdk', mock.Mock()):
            stub.render_icon(stub.total_badge_count())
        return ctx

    def texts(self, ctx):
        return [args[0] for args in ctx.drawn('show_text')]

    def test_an_alert_with_nothing_unread_shows_the_temperature_not_zero(self):
        ctx = self.render(temp=24.6)
        self.assertEqual(self.texts(ctx), ['25'])
        self.assertEqual(len(ctx.drawn('stroke')), 1)  # the degree ring

    def test_the_disc_still_pulses_in_the_alert_colour(self):
        bright, dim = rt.ALERT_BADGE_COLORS['red']
        self.assertEqual(self.render(pulse=0).drawn('rgba')[0], bright)
        self.assertEqual(self.render(pulse=1).drawn('rgba')[0], dim)
        self.assertEqual(self.texts(self.render(pulse=1)), ['25'])

    def test_negative_temperatures_keep_their_sign(self):
        self.assertEqual(self.texts(self.render(temp=-5.2, color='yellow')), ['-5'])

    def test_without_a_temperature_the_disc_falls_back_to_zero(self):
        self.assertEqual(self.texts(self.render(temp=None)), ['0'])
        self.assertEqual(len(self.render(temp=None).drawn('stroke')), 0)

    def test_unread_items_still_show_their_count_during_an_alert(self):
        ctx = self.render(unread=3)
        self.assertEqual(self.texts(ctx), ['3'])
        self.assertEqual(len(ctx.drawn('stroke')), 0)

    def test_pending_package_updates_still_show_their_count_during_an_alert(self):
        self.assertEqual(self.texts(self.render(updates=2)), ['2'])

    def test_without_an_alert_the_temperature_is_drawn_without_a_disc(self):
        ctx = self.render(alert=False)
        self.assertEqual(self.texts(ctx), ['25'])
        self.assertEqual(len(ctx.drawn('fill')), 0)

    def test_the_alert_disc_uses_a_smaller_font_than_the_plain_temperature_icon(self):
        plain = self.render(alert=False, temp=25)
        disc = self.render(temp=25)
        self.assertEqual(self.texts(disc), ['25'])
        self.assertLess(disc.font_size, plain.font_size)


if __name__ == '__main__':
    unittest.main()
