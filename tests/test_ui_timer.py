import unittest
from unittest import mock

from facade import rt
from helpers import TmpConfigCase, ts


class TestTimerFormat(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(rt.format_timer_duration(0), '0 Hours, 00 Minutes and 00 Seconds')
        self.assertEqual(rt.format_timer_duration(3661), '1 Hours, 01 Minutes and 01 Seconds')
        self.assertEqual(rt.format_timer_duration(7200), '2 Hours, 00 Minutes and 00 Seconds')


class TestScheduleRuntime(TmpConfigCase):
    class Stub:
        _schedule_settings = rt.RssTray._schedule_settings
        _prune_schedule_state = rt.RssTray._prune_schedule_state
        _schedule_state = rt.RssTray._schedule_state
        _schedule_tick = rt.RssTray._schedule_tick
        _mark_schedule_handled = rt.RssTray._mark_schedule_handled
        _skip_missed_schedule = rt.RssTray._skip_missed_schedule
        _fire_schedule = rt.RssTray._fire_schedule
        _set_schedule_warning = rt.RssTray._set_schedule_warning
        on_schedule_cancel = rt.RssTray.on_schedule_cancel
        on_schedule_snooze = rt.RssTray.on_schedule_snooze
        on_schedule_snooze_selected = rt.RssTray.on_schedule_snooze_selected
        _snooze_schedule = rt.RssTray._snooze_schedule
        _selected_schedule = rt.RssTray._selected_schedule
        _schedule_summary = rt.RssTray._schedule_summary
        _refresh_schedule_ui = rt.RssTray._refresh_schedule_ui

        def __init__(self):
            self.lock = rt.threading.Lock()
            self.state = {}
            self.schedule_warning = None
            self._schedule_flash_on = False
            self._last_schedule_tick = None
            self._schedule_cache = (None, None)
            self._schedule_resync = True
            self.timer_mode = 'countdown'
            self.timer_label = self.schedule_banner = self.sched = None
            self.sched_index = 0
            self.popup = None
            self.shown = 0
            self.icon_updates = 0

        def show_popup(self, auto=False):
            self.shown += 1

        def update_icon(self):
            self.icon_updates += 1

    NIGHT = '[schedule]\nwarn=60\nsnooze=30\ngrace=120\non|00:30|daily|Suspend\n'

    def setUp(self):
        super().setUp()
        self.conf(self.NIGHT)
        self.stub = self.Stub()
        patches = [
            mock.patch.object(rt, 'save_state'),
            mock.patch.object(rt, 'run_launcher_command'),
            mock.patch.object(rt, 'play_notification_sound'),
        ]
        self.save, self.run, self.sound = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def edit_config(self, text):
        self.conf(text)
        st = rt.os.stat(rt.CONFIG_FILE)
        rt.os.utime(rt.CONFIG_FILE, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))  # a visible edit

    def tick(self, now):
        with mock.patch.object(rt.time_module, 'time', return_value=now):
            self.stub._schedule_tick()

    def walk(self, start, end):
        for now in range(int(start), int(end) + 1):  # one tick per second, like the real timer
            self.tick(now)

    KEY = '0030|daily|Suspend'

    def test_runs_the_configured_action_exactly_once_at_the_time(self):
        occurrence = ts(2, 0, 30)
        self.walk(occurrence - 90, occurrence + 30)
        self.run.assert_called_once_with('loginctl suspend')
        self.assertEqual(self.stub.state['schedule_fired'][self.KEY], occurrence)

    def test_warning_flow_sound_popup_and_seconds_left(self):
        occurrence = ts(2, 0, 30)
        self.walk(occurrence - 90, occurrence - 55)
        self.assertEqual(self.stub.schedule_warning['left'], 55)
        self.assertEqual(self.sound.call_count, 1)    # once, when the warning starts
        self.assertEqual(self.stub.shown, 1)          # popup forced open once
        self.run.assert_not_called()

    def test_icon_flashes_every_second_during_the_warning(self):
        occurrence = ts(2, 0, 30)
        self.walk(occurrence - 59, occurrence - 50)
        self.assertEqual(self.stub.icon_updates, 10)

    def test_cancel_skips_this_run_but_not_the_next(self):
        occurrence = ts(2, 0, 30)
        self.walk(occurrence - 90, occurrence - 30)
        self.stub.on_schedule_cancel()
        self.assertIsNone(self.stub.schedule_warning)
        self.walk(occurrence - 29, occurrence + 300)
        self.run.assert_not_called()
        self.walk(ts(3, 0, 29), ts(3, 0, 31))
        self.run.assert_called_once()

    def test_snooze_postpones_by_the_configured_minutes_then_runs(self):
        occurrence = ts(2, 0, 30)
        self.walk(occurrence - 90, occurrence - 30)
        with mock.patch.object(rt.time_module, 'time', return_value=occurrence - 30):
            self.stub.on_schedule_snooze()
        self.assertEqual(self.stub.state['schedule_override'][self.KEY], {'occ': occurrence, 'fire': occurrence + 1800})
        self.walk(occurrence - 29, occurrence + 1800 - 61)
        self.run.assert_not_called()
        self.walk(occurrence + 1800 - 60, occurrence + 1800 + 5)
        self.run.assert_called_once()
        self.assertNotIn(self.KEY, self.stub.state['schedule_override'])   # consumed

    def test_slide_snooze_postpones_the_selected_schedule_before_the_warning(self):
        occurrence = ts(2, 0, 30)
        self.walk(occurrence - 7200, occurrence - 7190)
        with mock.patch.object(rt.time_module, 'time', return_value=occurrence - 7190):
            self.stub.on_schedule_snooze_selected()
        self.assertEqual(self.stub.state['schedule_override'][self.KEY]['fire'], occurrence + 1800)

    def test_a_missed_time_is_not_run_late_after_waking_from_sleep(self):
        occurrence = ts(2, 0, 30)
        self.walk(occurrence - 200, occurrence - 100)           # running normally
        self.tick(occurrence + 60)                               # machine slept through, wakes 60 s after
        self.walk(occurrence + 61, occurrence + 130)
        self.run.assert_not_called()

    def test_starting_the_app_just_after_the_time_does_not_run_it(self):
        occurrence = ts(2, 0, 30)
        self.walk(occurrence + 20, occurrence + 60)
        self.run.assert_not_called()

    def test_off_schedules_never_run(self):
        self.conf('[schedule]\noff|00:30|daily|Suspend\n')
        occurrence = ts(2, 0, 30)
        self.walk(occurrence - 90, occurrence + 30)
        self.run.assert_not_called()
        self.assertIsNone(self.stub.schedule_warning)

    def test_turning_a_schedule_on_after_its_time_passed_does_not_run_it(self):
        self.conf('[schedule]\noff|00:30|daily|Suspend\n')
        occurrence = ts(2, 0, 30)
        self.walk(occurrence + 10, occurrence + 20)
        self.edit_config('[schedule]\nwarn=60\ngrace=120\non|00:30|daily|Suspend\n')
        self.walk(occurrence + 21, occurrence + 60)
        self.run.assert_not_called()

    def test_two_schedules_each_run_their_own_action_at_their_own_time(self):
        self.edit_config('[schedule]\nwarn=0\non|00:30|daily|Suspend\non|07:00|weekdays|Power off\n')
        self.walk(ts(2, 0, 29), ts(2, 0, 31))
        self.run.assert_called_once_with('loginctl suspend')
        self.run.reset_mock()
        self.walk(ts(2, 6, 59), ts(2, 7, 1))
        self.run.assert_called_once_with('loginctl poweroff')

    def test_weekday_only_schedule_does_not_run_on_the_weekend(self):
        self.edit_config('[schedule]\nwarn=0\non|07:00|weekdays|Power off\n')
        self.walk(ts(5, 6, 59), ts(5, 7, 1))   # Saturday
        self.run.assert_not_called()
        self.walk(ts(7, 6, 59), ts(7, 7, 1))   # the following Monday
        self.run.assert_called_once()

    def test_two_schedules_at_the_same_time_both_run(self):
        self.edit_config('[schedule]\nwarn=0\non|00:30|daily|Suspend\non|00:30|mon,tue,wed|Power off\n')
        self.walk(ts(2, 0, 29), ts(2, 0, 31))
        self.assertEqual(sorted(c.args[0] for c in self.run.call_args_list), ['loginctl poweroff', 'loginctl suspend'])

    def test_the_soonest_warning_is_the_one_shown(self):
        self.edit_config('[schedule]\nwarn=60\non|00:30|daily|Suspend\non|00:31|daily|Power off\n')
        self.walk(ts(2, 0, 29, 40), ts(2, 0, 29, 45))
        self.assertEqual(self.stub.schedule_warning['action'], 'Suspend')   # 20 s away beats 80 s away
        self.assertEqual(self.sound.call_count, 1)

    def test_cancel_only_affects_the_schedule_that_was_warning(self):
        self.edit_config('[schedule]\nwarn=60\non|00:30|daily|Suspend\non|00:45|daily|Power off\n')
        self.walk(ts(2, 0, 29, 10), ts(2, 0, 29, 30))
        self.stub.on_schedule_cancel()
        self.walk(ts(2, 0, 29, 31), ts(2, 0, 31))
        self.run.assert_not_called()                                   # 00:30 cancelled
        self.walk(ts(2, 0, 44), ts(2, 0, 46))
        self.run.assert_called_once_with('loginctl poweroff')          # 00:45 untouched

    def test_state_of_removed_schedules_is_pruned(self):
        occurrence = ts(2, 0, 30)
        self.walk(occurrence - 5, occurrence + 5)
        self.assertIn(self.KEY, self.stub.state['schedule_fired'])
        self.edit_config('[schedule]\non|08:00|daily|Suspend\n')
        self.tick(occurrence + 10)
        self.assertNotIn(self.KEY, self.stub.state['schedule_fired'])

    def test_state_from_the_single_schedule_version_is_discarded(self):
        self.stub.state = {'schedule_fired': 1234.5, 'schedule_override': {'occ': 1, 'fire': 2}}
        occurrence = ts(2, 0, 30)
        self.walk(occurrence - 5, occurrence + 5)
        self.run.assert_called_once()
        self.assertIsInstance(self.stub.state['schedule_fired'], dict)

    def test_unknown_action_is_logged_and_runs_nothing(self):
        self.conf('[schedule]\nwarn=0\non|00:30|daily|Nonsense\n')
        occurrence = ts(2, 0, 30)
        with mock.patch.object(rt, '_log') as log:
            self.walk(occurrence - 5, occurrence + 5)
        self.run.assert_not_called()
        self.assertIn('Nonsense', log.call_args[0][0])

    def test_action_label_match_ignores_case(self):
        self.conf('[schedule]\nwarn=0\non|00:30|daily|power off\n')
        occurrence = ts(2, 0, 30)
        self.walk(occurrence - 5, occurrence + 5)
        self.run.assert_called_once_with('loginctl poweroff')

    def test_summary_shows_the_soonest_of_all_schedules(self):
        self.conf('[schedule]\non|07:00|daily|Power off\non|00:30|daily|Suspend\noff|01:00|daily|Suspend\n')
        with mock.patch.object(rt.time_module, 'time', return_value=ts(2, 12)):
            self.assertEqual(self.stub._schedule_summary(rt.load_schedule_settings()), 'Next: Thu 00:30 Suspend')
        self.stub.schedule_warning = {'key': 'k', 'occ': 1, 'left': 42, 'action': 'Suspend'}
        self.assertEqual(self.stub._schedule_summary(rt.load_schedule_settings()), 'Suspend in 42 s')

    def test_summary_when_everything_is_off_or_empty(self):
        self.conf('[schedule]\noff|00:30|daily|Suspend\n')
        self.assertEqual(self.stub._schedule_summary(rt.load_schedule_settings()), 'Schedule off')
        self.conf('[feeds]\n')
        self.assertEqual(self.stub._schedule_summary(rt.load_schedule_settings()), 'Schedule off')


if __name__ == '__main__':
    unittest.main()
