import unittest
from datetime import datetime
from unittest import mock

from facade import rt
from helpers import TmpConfigCase, ts


class TestTimerFormat(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(rt.format_timer_duration(0), '0 Hours, 00 Minutes and 00 Seconds')
        self.assertEqual(rt.format_timer_duration(3661), '1 Hours, 01 Minutes and 01 Seconds')
        self.assertEqual(rt.format_timer_duration(7200), '2 Hours, 00 Minutes and 00 Seconds')


class TestTimerCountdownText(unittest.TestCase):
    def test_shows_the_clock_time_the_countdown_ends(self):
        # 10:36:40 + 4 h 34 min 23 s = 15:11:03
        text = rt.format_timer_countdown(4 * 3600 + 34 * 60 + 23, now=ts(0, 10, 36, 40))
        self.assertEqual(text, '4 Hours, 34 Minutes and 23 Seconds (15:11)')

    def test_end_time_drops_the_seconds_instead_of_rounding_up(self):
        self.assertEqual(rt.format_timer_countdown(60 + 59, now=ts(0, 15, 9, 0)), '0 Hours, 01 Minutes and 59 Seconds (15:10)')

    def test_end_time_is_zero_padded_and_wraps_past_midnight(self):
        self.assertTrue(rt.format_timer_countdown(3600, now=ts(0, 23, 30)).endswith('(00:30)'))
        self.assertTrue(rt.format_timer_countdown(5 * 60, now=ts(0, 7, 0)).endswith('(07:05)'))

    def test_nothing_counting_has_no_end_time(self):
        self.assertEqual(rt.format_timer_countdown(0, now=ts(0, 10)), rt.format_timer_duration(0))

    def test_defaults_to_the_current_time(self):
        with mock.patch.object(rt.time_module, 'time', return_value=ts(2, 12, 0, 30)):
            self.assertEqual(rt.format_timer_countdown(30 * 60), '0 Hours, 30 Minutes and 00 Seconds (12:30)')


class TestTimerLabelUpdates(unittest.TestCase):
    class Stub:
        _update_timer_widgets = rt.RssTray._update_timer_widgets
        on_timer_slider_changed = rt.RssTray.on_timer_slider_changed
        _clear_timer_missed = rt.RssTray._clear_timer_missed

        def __init__(self):
            self.timer_scale = mock.Mock()
            self.timer_label = mock.Mock()
            self.timer_mode = 'countdown'
            self.timer_remaining_seconds = 0
            self.timer_running = False
            self.timer_deadline = None
            self.timer_missed_boot = None
            self._timer_updating_ui = False

    def test_dragging_the_slider_shows_the_end_time(self):
        stub = self.Stub()
        scale = mock.Mock()
        scale.get_value.return_value = 3600
        with mock.patch.object(rt.time_module, 'time', return_value=ts(0, 10, 0)):
            stub.on_timer_slider_changed(scale)
        stub.timer_label.set_text.assert_called_with('1 Hours, 00 Minutes and 00 Seconds (11:00)')

    def test_each_tick_keeps_the_end_time_in_the_label(self):
        stub = self.Stub()
        stub.timer_remaining_seconds = 90
        with mock.patch.object(rt.time_module, 'time', return_value=ts(0, 10, 0)):
            stub._update_timer_widgets()
        stub.timer_label.set_text.assert_called_with('0 Hours, 01 Minutes and 30 Seconds (10:01)')

    def test_schedule_mode_keeps_its_own_summary(self):
        stub = self.Stub()
        stub.timer_mode = 'schedule'
        stub.timer_remaining_seconds = 90
        stub._update_timer_widgets()
        stub.timer_label.set_text.assert_not_called()


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


def at(hour, minute=0, second=0, day=14):
    """A local timestamp on Wednesday 14 October 2026."""
    return datetime(2026, 10, day, hour, minute, second).timestamp()


class TestFormatClock(unittest.TestCase):
    def test_hours_minutes_seconds_zero_padded(self):
        self.assertEqual(rt.format_clock(0), '00:00:00')
        self.assertEqual(rt.format_clock(5), '00:00:05')
        self.assertEqual(rt.format_clock(3661), '01:01:01')
        self.assertEqual(rt.format_clock(4 * 3600 + 34 * 60 + 23), '04:34:23')

    def test_a_long_countdown_keeps_counting_hours(self):
        self.assertEqual(rt.format_clock(100 * 3600), '100:00:00')

    def test_negative_or_fractional_values_are_safe(self):
        self.assertEqual(rt.format_clock(-3), '00:00:00')
        self.assertEqual(rt.format_clock(59.9), '00:00:59')


class TestFormatScheduleTime(unittest.TestCase):
    def test_within_a_day_it_is_just_the_clock_time(self):
        self.assertEqual(rt.format_schedule_time(at(23, 59), at(10)), '23:59')
        self.assertEqual(rt.format_schedule_time(at(0, 30, day=15), at(23, 50)), '00:30')

    def test_a_day_or_more_away_gets_the_weekday(self):
        self.assertEqual(rt.format_schedule_time(at(7, 30, day=19), at(10)), 'Mon 07:30')
        self.assertEqual(rt.format_schedule_time(at(10, day=15), at(10)), 'Thu 10:00')  # exactly 24 h ahead


class TestTimerStatusLines(unittest.TestCase):
    class Stub:
        _timer_status_lines = rt.RssTray._timer_status_lines

        def __init__(self, remaining=0, running=False, schedules=(), overrides=None):
            self.timer_remaining_seconds = remaining
            self.timer_running = running
            self.timer_missed_boot = None
            self._schedules = list(schedules)
            self._overrides = overrides or {}

        def _schedule_settings(self):
            return {'schedules': self._schedules}

        def _schedule_state(self):
            return {}, self._overrides

    def entry(self, hh, mm, action='Suspend', days=frozenset(range(7)), enabled=True):
        return {'enabled': enabled, 'minute': hh * 60 + mm, 'days': days, 'action': action}

    def lines(self, stub, now=None):
        with mock.patch.object(rt.time_module, 'time', return_value=now or at(10)):
            return stub._timer_status_lines()

    def test_nothing_counting_nothing_to_show(self):
        self.assertEqual(self.lines(self.Stub()), [])

    def test_a_running_countdown_is_hh_mm_ss(self):
        self.assertEqual(self.lines(self.Stub(remaining=5025, running=True)), ['01:23:45'])

    def test_a_countdown_that_is_not_running_or_has_run_out_is_not_shown(self):
        self.assertEqual(self.lines(self.Stub(remaining=60, running=False)), [])
        self.assertEqual(self.lines(self.Stub(remaining=0, running=True)), [])

    def test_a_schedule_is_its_action_label_then_its_time(self):
        stub = self.Stub(schedules=[self.entry(23, 59, 'Suspend')])
        self.assertEqual(self.lines(stub), ['Suspend 23:59'])

    def test_the_countdown_comes_first_then_schedules_soonest_first(self):
        stub = self.Stub(remaining=90, running=True, schedules=[
            self.entry(23, 0, 'Power off'), self.entry(12, 0, 'Suspend')])
        self.assertEqual(self.lines(stub), ['00:01:30', 'Suspend 12:00', 'Power off 23:00'])

    def test_disabled_schedules_are_not_active(self):
        stub = self.Stub(schedules=[self.entry(12, 0, enabled=False), self.entry(13, 0, 'Power off')])
        self.assertEqual(self.lines(stub), ['Power off 13:00'])

    def test_a_schedule_that_already_ran_today_shows_its_next_day(self):
        stub = self.Stub(schedules=[self.entry(9, 0, 'Suspend')])  # 09:00 daily, now 10:00: next is tomorrow
        self.assertEqual(self.lines(stub), ['Suspend 09:00'])

    def test_a_weekly_schedule_names_its_day(self):
        stub = self.Stub(schedules=[self.entry(7, 30, 'Power off', days=frozenset({0}))])  # Mondays; today is Wednesday
        self.assertEqual(self.lines(stub), ['Power off Mon 07:30'])

    def test_a_snoozed_schedule_shows_the_snoozed_time(self):
        entry = self.entry(12, 0, 'Suspend')
        key = rt.schedule_key(entry)
        stub = self.Stub(schedules=[entry], overrides={key: {'occ': at(12), 'fire': at(12, 30)}})
        self.assertEqual(self.lines(stub), ['Suspend 12:30'])

    def test_too_many_lines_end_with_a_count_of_the_rest(self):
        stub = self.Stub(remaining=60, running=True, schedules=[self.entry(11 + i % 10, i, f'Act{i}') for i in range(8)])
        lines = self.lines(stub)
        self.assertEqual(len(lines), rt.TIMER_STATUS_MAX_LINES)
        self.assertEqual(lines[-1], '+4 more')  # 9 entries, 5 shown


class TestTimerStatusWidget(unittest.TestCase):
    class Stub:
        _update_timer_status = rt.RssTray._update_timer_status
        on_timer_toggle_clicked = rt.RssTray.on_timer_toggle_clicked
        _timer_tick = rt.RssTray._timer_tick

        def __init__(self, lines=(), slide_open=False):
            self.timer_status_label = mock.Mock()
            self.timer_status_box = mock.Mock()
            self._timer_status_text = None
            self.timer_visible = slide_open
            self.timer_box = mock.Mock()
            self.timer_running = False
            self.timer_missed_boot = None
            self.timer_deadline = None
            self._lines = list(lines)
            self._timer_status_lines = lambda: list(self._lines)
            self._schedule_tick = mock.Mock()

    def test_the_lines_become_right_aligned_rows_and_the_box_is_shown(self):
        stub = self.Stub(['01:00:00', 'Suspend 23:59'])
        stub._update_timer_status()
        stub.timer_status_label.set_text.assert_called_once_with('01:00:00\nSuspend 23:59')
        stub.timer_status_box.set_visible.assert_called_once_with(True)

    def test_nothing_counting_hides_the_box(self):
        stub = self.Stub([])
        stub._update_timer_status()
        stub.timer_status_box.set_visible.assert_called_once_with(False)

    def test_while_the_slide_is_open_the_readout_is_hidden(self):
        stub = self.Stub(['01:00:00'], slide_open=True)
        stub._update_timer_status()
        stub.timer_status_box.set_visible.assert_called_once_with(False)

    def test_the_text_is_only_set_when_it_changes(self):
        stub = self.Stub(['01:00:00'])
        stub._update_timer_status()
        stub._update_timer_status()
        self.assertEqual(stub.timer_status_label.set_text.call_count, 1)
        stub._lines = ['00:59:59']
        stub._update_timer_status()
        self.assertEqual(stub.timer_status_label.set_text.call_count, 2)

    def test_without_a_popup_nothing_happens(self):
        stub = self.Stub(['x'])
        stub.timer_status_label = None
        stub._update_timer_status()  # no error

    def test_every_tick_refreshes_it(self):
        stub = self.Stub(['01:00:00'])
        self.assertTrue(stub._timer_tick())
        stub.timer_status_label.set_text.assert_called_once_with('01:00:00')

    def test_opening_and_closing_the_slide_swaps_slide_and_readout(self):
        stub = self.Stub(['01:00:00'])
        stub._update_timer_status()
        stub.on_timer_toggle_clicked(None)  # open
        self.assertTrue(stub.timer_visible)
        stub.timer_status_box.set_visible.assert_called_with(False)
        stub.on_timer_toggle_clicked(None)  # close
        stub.timer_status_box.set_visible.assert_called_with(True)


class TestBootClock(unittest.TestCase):
    def test_it_reads_the_clock_that_keeps_counting_while_suspended(self):
        with mock.patch.object(rt.time_module, 'clock_gettime', return_value=1234.5) as clock:
            self.assertEqual(rt.boot_clock(), 1234.5)
        clock.assert_called_once_with(rt.time_module.CLOCK_BOOTTIME)

    def test_without_that_clock_the_monotonic_one_is_used(self):
        with mock.patch.object(rt.time_module, 'CLOCK_BOOTTIME', create=True, new=None), \
                mock.patch.object(rt.time_module, 'monotonic', return_value=77.0):
            self.assertEqual(rt.boot_clock(), 77.0)

    def test_a_clock_that_fails_falls_back_to_monotonic(self):
        with mock.patch.object(rt.time_module, 'clock_gettime', side_effect=OSError), \
                mock.patch.object(rt.time_module, 'monotonic', return_value=5.0):
            self.assertEqual(rt.boot_clock(), 5.0)

    def test_the_real_clock_keeps_up_with_the_monotonic_one_at_least(self):
        self.assertGreaterEqual(rt.boot_clock(), rt.time_module.monotonic() - 1)


class TestFormatMissedAgo(unittest.TestCase):
    def test_hours_and_minutes(self):
        self.assertEqual(rt.format_missed_ago(3600 + 12 * 60), 'missed 1 hour 12 minutes ago')
        self.assertEqual(rt.format_missed_ago(2 * 3600 + 5 * 60), 'missed 2 hours 5 minutes ago')

    def test_only_what_is_there_and_singular_where_it_should_be(self):
        self.assertEqual(rt.format_missed_ago(2 * 3600), 'missed 2 hours ago')
        self.assertEqual(rt.format_missed_ago(60), 'missed 1 minute ago')
        self.assertEqual(rt.format_missed_ago(45 * 60), 'missed 45 minutes ago')

    def test_under_a_minute(self):
        self.assertEqual(rt.format_missed_ago(59), 'missed less than a minute ago')
        self.assertEqual(rt.format_missed_ago(-3), 'missed less than a minute ago')

    def test_seconds_are_dropped_not_rounded_up(self):
        self.assertEqual(rt.format_missed_ago(3600 + 12 * 60 + 59), 'missed 1 hour 12 minutes ago')

    def test_days_replace_the_minutes(self):
        self.assertEqual(rt.format_missed_ago(26 * 3600 + 7 * 60), 'missed 1 day 2 hours ago')
        self.assertEqual(rt.format_missed_ago(2 * 24 * 3600 + 30 * 60), 'missed 2 days ago')


class TestMissedCountdown(unittest.TestCase):
    class Stub:
        _timer_tick = rt.RssTray._timer_tick
        _timer_missed = rt.RssTray._timer_missed
        _timer_missed_text = rt.RssTray._timer_missed_text
        _timer_status_lines = rt.RssTray._timer_status_lines

        def __init__(self, seconds=3600, started=1000.0):
            self.timer_running = True
            self.timer_deadline = started + seconds
            self.timer_remaining_seconds = seconds
            self.timer_missed_boot = None
            self.timer_mode = 'countdown'
            self.fired = 0
            self._fire_timer_done = lambda: setattr(self, 'fired', self.fired + 1)
            self._apply_missed_style = mock.Mock()
            self._update_timer_widgets = mock.Mock()
            self._update_timer_status = mock.Mock()
            self._schedule_tick = mock.Mock()
            self._schedule_settings = lambda: {'schedules': []}
            self._schedule_state = lambda: ({}, {})

    def tick(self, stub, boot_now):
        with mock.patch.object(rt, 'boot_clock', return_value=boot_now):
            return stub._timer_tick()

    def test_a_countdown_that_runs_out_on_time_rings_as_before(self):
        stub = self.Stub(seconds=10)
        self.tick(stub, 1010.4)
        self.assertEqual(stub.fired, 1)
        self.assertIsNone(stub.timer_missed_boot)
        self.assertFalse(stub.timer_running)

    def test_a_countdown_found_past_its_end_after_a_suspend_is_missed_not_rung(self):
        stub = self.Stub(seconds=1800)
        self.tick(stub, 1000 + 1800 + 3 * 3600 + 12 * 60)  # woke 3 h 12 min after it was due
        self.assertEqual(stub.fired, 0)  # no late alarm
        self.assertEqual(stub.timer_missed_boot, 2800.0)
        self.assertFalse(stub.timer_running)
        self.assertEqual(stub.timer_remaining_seconds, 0)
        self.assertIsNone(stub.timer_deadline)
        stub._apply_missed_style.assert_called_once()

    def test_the_line_between_late_and_missed_is_a_few_seconds(self):
        on_the_line = self.Stub(seconds=10)
        self.tick(on_the_line, 1010 + rt.TIMER_MISSED_AFTER_SECONDS)
        self.assertEqual(on_the_line.fired, 1)  # a busy main loop is late, not asleep
        past_it = self.Stub(seconds=10)
        self.tick(past_it, 1010 + rt.TIMER_MISSED_AFTER_SECONDS + 0.5)
        self.assertEqual(past_it.fired, 0)
        self.assertIsNotNone(past_it.timer_missed_boot)

    def test_a_countdown_that_is_still_running_after_the_wake_counts_the_time_slept(self):
        stub = self.Stub(seconds=3600)
        self.tick(stub, 1000 + 1800)  # half an hour passed, most of it suspended
        self.assertEqual(stub.timer_remaining_seconds, 1800)
        self.assertTrue(stub.timer_running)
        self.assertEqual(stub.fired, 0)
        self.assertIsNone(stub.timer_missed_boot)

    def test_the_missed_text_keeps_growing_and_the_widgets_follow(self):
        stub = self.Stub(seconds=60)
        self.tick(stub, 1060 + 600)  # missed 10 minutes ago
        stub._update_timer_widgets.reset_mock()
        with mock.patch.object(rt, 'boot_clock', return_value=1060 + 600 + 120):
            self.assertEqual(stub._timer_missed_text(), 'missed 12 minutes ago')
        self.tick(stub, 1060 + 720)
        stub._update_timer_widgets.assert_called_once()

    def test_the_corner_readout_shows_the_miss_instead_of_a_countdown(self):
        stub = self.Stub(seconds=60)
        self.tick(stub, 1060 + 3600)
        with mock.patch.object(rt, 'boot_clock', return_value=1060 + 3600), mock.patch.object(rt.time_module, 'time', return_value=at(10)):
            self.assertEqual(stub._timer_status_lines(), ['missed 1 hour ago'])

    def test_nothing_changes_for_an_idle_timer(self):
        stub = self.Stub()
        stub.timer_running = False
        stub.timer_deadline = None
        self.assertTrue(self.tick(stub, 99999))
        self.assertEqual(stub.fired, 0)
        self.assertIsNone(stub.timer_missed_boot)
        stub._update_timer_widgets.assert_not_called()


class TestAcknowledgingAMiss(unittest.TestCase):
    class Stub:
        on_timer_slider_changed = rt.RssTray.on_timer_slider_changed
        on_timer_status_clicked = rt.RssTray.on_timer_status_clicked
        _clear_timer_missed = rt.RssTray._clear_timer_missed
        _apply_missed_style = rt.RssTray._apply_missed_style

        def __init__(self, missed=5000.0):
            self.timer_missed_boot = missed
            self.timer_remaining_seconds = 0
            self.timer_running = False
            self.timer_deadline = None
            self.timer_mode = 'countdown'
            self._timer_updating_ui = False
            self.timer_label = mock.MagicMock()
            self.timer_scale = mock.MagicMock()
            self.timer_status_label = mock.MagicMock()
            self.timer_status_box = mock.MagicMock()
            self.timer_overlay = mock.MagicMock()
            self._update_timer_status = mock.Mock()
            self._update_timer_widgets = mock.Mock()

    def test_moving_the_slider_acknowledges_it_and_starts_the_new_countdown_on_the_boot_clock(self):
        stub = self.Stub()
        scale = mock.Mock()
        scale.get_value.return_value = 600
        with mock.patch.object(rt, 'boot_clock', return_value=7000.0):
            stub.on_timer_slider_changed(scale)
        self.assertIsNone(stub.timer_missed_boot)
        self.assertEqual(stub.timer_deadline, 7600.0)
        self.assertTrue(stub.timer_running)

    def test_clicking_the_readout_dismisses_a_miss(self):
        stub = self.Stub()
        self.assertTrue(stub.on_timer_status_clicked(None, None))
        self.assertIsNone(stub.timer_missed_boot)
        stub._update_timer_widgets.assert_called()

    def test_the_readout_ignores_clicks_when_there_is_no_miss(self):
        stub = self.Stub(missed=None)
        self.assertFalse(stub.on_timer_status_clicked(None, None))

    def test_a_miss_is_styled_and_clickable_until_dismissed(self):
        stub = self.Stub()
        stub._apply_missed_style()
        for widget in (stub.timer_label, stub.timer_status_label):
            widget.get_style_context.return_value.add_class.assert_called_with('missed-text')
        stub.timer_overlay.set_overlay_pass_through.assert_called_with(stub.timer_status_box, False)
        stub.on_timer_status_clicked(None, None)
        for widget in (stub.timer_label, stub.timer_status_label):
            widget.get_style_context.return_value.remove_class.assert_any_call('missed-text')
        stub.timer_status_box.get_style_context.return_value.remove_class.assert_any_call('missed-on')
        stub.timer_overlay.set_overlay_pass_through.assert_called_with(stub.timer_status_box, True)  # clicks pass through again

    def test_without_a_popup_nothing_breaks(self):
        stub = self.Stub()
        stub.timer_label = stub.timer_status_label = stub.timer_status_box = stub.timer_overlay = None
        stub._apply_missed_style()  # no error


if __name__ == '__main__':
    unittest.main()
