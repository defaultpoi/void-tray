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


if __name__ == '__main__':
    unittest.main()
