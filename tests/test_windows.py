import unittest

from facade import rt
from helpers import dt


class TestParseSchedule(unittest.TestCase):
    def test_single_day_with_range(self):
        (days, start, end), = rt.parse_schedule('wed 18:00-23:30')
        self.assertEqual((set(days), start, end), ({2}, 18 * 60, 23 * 60 + 30))

    def test_dot_separator_and_full_day_names(self):
        (days, start, end), = rt.parse_schedule('Wednesday 18.00-20.15')
        self.assertEqual((set(days), start, end), ({2}, 18 * 60, 20 * 60 + 15))

    def test_open_end_means_until_midnight(self):
        for text in ('tue-sun 13:00-', 'tue-sun 13:00'):
            (days, start, end), = rt.parse_schedule(text)
            self.assertEqual((set(days), start, end), ({1, 2, 3, 4, 5, 6}, 13 * 60, 24 * 60))

    def test_day_lists_ranges_and_keywords(self):
        self.assertEqual(set(rt.parse_schedule('mon,wed,fri 10:00')[0][0]), {0, 2, 4})
        self.assertEqual(set(rt.parse_schedule('fri-mon 10:00')[0][0]), {4, 5, 6, 0})  # wraps
        self.assertEqual(set(rt.parse_schedule('weekdays 10:00')[0][0]), {0, 1, 2, 3, 4})
        self.assertEqual(set(rt.parse_schedule('weekends 10:00')[0][0]), {5, 6})
        self.assertEqual(set(rt.parse_schedule('daily 10:00')[0][0]), set(range(7)))
        self.assertEqual(set(rt.parse_schedule('10:00-12:00')[0][0]), set(range(7)))  # no days

    def test_spaces_inside_a_day_list(self):
        self.assertEqual(set(rt.parse_schedule('mon, wed 10:00')[0][0]), {0, 2})

    def test_days_only_means_the_whole_day(self):
        (days, start, end), = rt.parse_schedule('tue-sun')
        self.assertEqual((set(days), start, end), ({1, 2, 3, 4, 5, 6}, 0, 24 * 60))

    def test_several_windows(self):
        windows = rt.parse_schedule('wed 18:00-20:00; sat 10:00-12:00')
        self.assertEqual([set(w[0]) for w in windows], [{2}, {5}])

    def test_invalid_input_raises(self):
        for bad in ('', ';', 'funday 10:00', 'wed 25:00', 'wed 10:75', 'wed 10:00-99:00',
                    'wed,,thu 10:00', 'wed 10', 'mo 10:00'):
            with self.assertRaises(ValueError, msg=bad):
                rt.parse_schedule(bad)


class TestScheduleActive(unittest.TestCase):
    def test_wednesday_evening_only(self):
        w = rt.parse_schedule('wed 18:00-23:00')
        self.assertTrue(rt.schedule_active(w, dt(2, 18, 0)))
        self.assertTrue(rt.schedule_active(w, dt(2, 22, 59)))
        self.assertFalse(rt.schedule_active(w, dt(2, 17, 59)))
        self.assertFalse(rt.schedule_active(w, dt(2, 23, 0)))   # end is exclusive
        self.assertFalse(rt.schedule_active(w, dt(3, 18, 30)))  # Thursday

    def test_every_day_except_monday_from_13(self):
        w = rt.parse_schedule('tue-sun 13:00-')
        self.assertFalse(rt.schedule_active(w, dt(0, 15)))      # Monday
        self.assertFalse(rt.schedule_active(w, dt(1, 12, 59)))
        for day in range(1, 7):
            self.assertTrue(rt.schedule_active(w, dt(day, 13)))
            self.assertTrue(rt.schedule_active(w, dt(day, 23, 59)))

    def test_window_past_midnight_spills_into_the_next_day(self):
        w = rt.parse_schedule('fri 22:00-02:00')
        self.assertTrue(rt.schedule_active(w, dt(4, 23)))
        self.assertTrue(rt.schedule_active(w, dt(5, 1, 59)))    # Saturday early morning
        self.assertFalse(rt.schedule_active(w, dt(5, 2, 0)))
        self.assertFalse(rt.schedule_active(w, dt(4, 21)))
        self.assertFalse(rt.schedule_active(w, dt(6, 1)))       # Sunday early: no Saturday window

    def test_sunday_window_past_midnight_wraps_to_monday(self):
        w = rt.parse_schedule('sun 23:00-01:00')
        self.assertTrue(rt.schedule_active(w, dt(0, 0, 30)))

    def test_any_of_several_windows(self):
        w = rt.parse_schedule('wed 18:00-19:00; sat 10:00-11:00')
        self.assertTrue(rt.schedule_active(w, dt(5, 10, 30)))
        self.assertFalse(rt.schedule_active(w, dt(5, 18, 30)))


class TestChannelsDue(unittest.TestCase):
    def test_unscheduled_always_scheduled_only_in_window_live_always(self):
        schedules = {'weekly': rt.parse_schedule('wed 18:00-23:00'),
                     'daily': rt.parse_schedule('tue-sun 13:00-')}
        channels = ['plain', 'weekly', 'daily']
        self.assertEqual(rt.channels_due(channels, schedules, set(), dt(2, 19)), ['plain', 'weekly', 'daily'])
        self.assertEqual(rt.channels_due(channels, schedules, set(), dt(0, 19)), ['plain'])
        self.assertEqual(rt.channels_due(channels, schedules, {'weekly'}, dt(0, 19)), ['plain', 'weekly'])

    def test_scheduled_active_channels(self):
        schedules = {'weekly': rt.parse_schedule('wed 18:00-23:00')}
        self.assertEqual(rt.scheduled_active_channels(schedules, dt(2, 18)), {'weekly'})
        self.assertEqual(rt.scheduled_active_channels(schedules, dt(2, 12)), set())


if __name__ == '__main__':
    unittest.main()
