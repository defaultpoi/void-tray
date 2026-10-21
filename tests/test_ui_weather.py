import unittest
from unittest import mock

from facade import rt


class TestForecastSegments(unittest.TestCase):
    class Stub:
        build_forecast_weather_segments = rt.RssTray.build_forecast_weather_segments

    def _segments(self, weather_data):
        s = self.Stub()
        s.weather_data = weather_data
        with mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x):
            return s.build_forecast_weather_segments()

    def test_no_data_or_no_days_is_empty(self):
        self.assertEqual(self._segments(None), [])
        self.assertEqual(self._segments({'forecast_days': []}), [])

    def test_one_segment_per_day_with_rain_glyph_only_when_rain_likely(self):
        segs = self._segments({'forecast_days': [
            {'max_temp': 21.4, 'min_temp': 9.6, 'rain_prob': 40},
            {'max_temp': 18.0, 'min_temp': 7.0, 'rain_prob': 0},
        ]})
        self.assertEqual(len(segs), 2)
        self.assertIn('☔', segs[0])
        self.assertIn('21/10°C', segs[0])
        self.assertNotIn('☔', segs[1])
        self.assertIn('18/7°C', segs[1])

    def test_day_without_any_values_is_skipped(self):
        segs = self._segments({'forecast_days': [
            {'max_temp': None, 'min_temp': None, 'rain_prob': None},
            {'max_temp': 5.0, 'min_temp': 1.0, 'rain_prob': None},
        ]})
        self.assertEqual(len(segs), 1)


class TestPulseRedraws(unittest.TestCase):
    class Stub:
        _alert_pulse_tick = rt.RssTray._alert_pulse_tick
        _pulse_signature = rt.RssTray._pulse_signature
        _pulse_on_for = rt.RssTray._pulse_on_for
        has_active_alerts = rt.RssTray.has_active_alerts

        def __init__(self, colors):
            self.active_alerts = [{'severity_color': c} for c in colors]
            self._alert_pulse_counter = 0
            self._last_pulse_signature = None
            self.updates = 0
            self.popup = None

        def update_icon(self):
            self.updates += 1

    def test_yellow_redraws_only_when_its_phase_flips(self):
        stub = self.Stub(['yellow'])
        for _ in range(8):
            stub._alert_pulse_tick()
        self.assertEqual(stub.updates, 3)  # ticks 1, 4 and 8, not all eight

    def test_red_flips_every_tick(self):
        stub = self.Stub(['red'])
        for _ in range(8):
            stub._alert_pulse_tick()
        self.assertEqual(stub.updates, 8)

    def test_no_alerts_no_redraws(self):
        stub = self.Stub([])
        for _ in range(8):
            stub._alert_pulse_tick()
        self.assertEqual(stub.updates, 0)


class TestMonoGlyph(unittest.TestCase):
    def test_markup(self):
        m = rt.mono_glyph_markup('\u2614')
        self.assertIn('\u2614\ufe0e', m)
        self.assertIn('foreground="#2b2b2b"', m)
        self.assertIn('font_family=', m)

    def test_forecast_umbrella_is_monochrome(self):
        class Stub:
            build_forecast_weather_segments = rt.RssTray.build_forecast_weather_segments
            weather_data = {'forecast_days': [{'max_temp': 5.0, 'min_temp': 1.0, 'rain_prob': 50}]}
        with mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x):
            seg = Stub().build_forecast_weather_segments()[0]
        self.assertIn('\u2614\ufe0e', seg)



class TestWeatherArrowStaysInPlace(unittest.TestCase):
    class Stub:
        rebuild_weather_bar = rt.RssTray.rebuild_weather_bar
        on_weather_arrow_clicked = rt.RssTray.on_weather_arrow_clicked

        def __init__(self, view):
            self.weather_view = view
            self.weather_data = {'forecast_days': []}
            self.weather_box = mock.Mock()
            self.weather_box.get_children.return_value = []
            self.row = object()
            self.build_forecast_weather_segments = lambda: []
            self.build_today_weather_segments = lambda: []
            self._build_weather_row = lambda segments, empty: self.row

    def rebuilt(self, view):
        stub, buttons = self.Stub(view), []
        with mock.patch.object(rt, 'Gtk') as gtk, mock.patch.object(rt, 'weather_row_pad', return_value=''):
            gtk.Button.side_effect = lambda label=None: buttons.append(mock.Mock(label=label)) or buttons[-1]
            stub.rebuild_weather_bar()
        packed = [call.args[0] for call in stub.weather_box.pack_start.call_args_list]
        return stub, buttons, packed

    def test_the_arrow_is_the_last_thing_in_the_bar_in_both_views(self):
        for view, label in (('today', '\u203a'), ('forecast', '\u2039')):
            stub, buttons, packed = self.rebuilt(view)
            self.assertEqual([b.label for b in buttons], [label])
            self.assertEqual(packed, [stub.row, buttons[0]])  # row first, arrow at the end

    def test_each_arrow_switches_to_the_other_view(self):
        for view, target in (('today', 'forecast'), ('forecast', 'today')):
            stub, buttons, _ = self.rebuilt(view)
            buttons[0].connect.assert_called_once_with('clicked', stub.on_weather_arrow_clicked, target)



class TestWeatherFetchedRefitsThePopup(unittest.TestCase):
    class Stub:
        _on_weather_fetched = rt.RssTray._on_weather_fetched

        def __init__(self):
            self.calls = []
            self.weather_data = None
            self.active_alerts = []
            self.rebuild_weather_bar = lambda: self.calls.append('bar')
            self._refit_open_popup = lambda: self.calls.append('refit')
            self.update_icon = lambda: self.calls.append('icon')

    def test_new_values_refit_an_open_popup_after_the_bar_is_rebuilt(self):
        stub = self.Stub()
        self.assertFalse(stub._on_weather_fetched({'temp': 1}))
        self.assertEqual(stub.weather_data, {'temp': 1})
        self.assertEqual(stub.calls, ['bar', 'refit', 'icon'])


if __name__ == '__main__':
    unittest.main()
