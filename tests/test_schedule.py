import unittest

from facade import rt
from helpers import TmpConfigCase, ts


class TestScheduleLogic(unittest.TestCase):
    S = dict(rt.NEW_SCHEDULE, enabled=True, minute=30, warn=60, snooze=30, grace=120)

    def status(self, now, fired=None, override=None, **settings):
        settings = dict(self.S, **settings)
        if fired is None:  # as in the app: occurrences long past are already handled
            fired = rt._schedule_occurrences(now - 1000, settings['days'], settings['minute'])[0] or 0
        return rt.schedule_status(now, settings, fired, override)

    def test_occurrences_are_the_latest_past_and_first_future(self):
        previous, upcoming = rt._schedule_occurrences(ts(2, 12), rt._ALL_DAYS, 30)
        self.assertEqual((previous, upcoming), (ts(2, 0, 30), ts(3, 0, 30)))

    def test_weekday_filter(self):
        wed_only = frozenset({2})
        previous, upcoming = rt._schedule_occurrences(ts(0, 12), wed_only, 30)   # Monday noon
        self.assertEqual(upcoming, ts(2, 0, 30))
        self.assertEqual(previous, ts(2, 0, 30) - 7 * 86400)

    def test_far_from_the_time_is_idle(self):
        self.assertEqual(self.status(ts(1, 12))[0], 'idle')

    def test_warning_starts_warn_seconds_before_with_the_seconds_left(self):
        occurrence = ts(2, 0, 30)
        self.assertEqual(self.status(occurrence - 61)[0], 'idle')
        self.assertEqual(self.status(occurrence - 60), ('warn', occurrence, 60))
        kind, _occ, left = self.status(occurrence - 10)
        self.assertEqual((kind, left), ('warn', 10))

    def test_fires_at_the_time_and_within_the_grace(self):
        occurrence = ts(2, 0, 30)
        self.assertEqual(self.status(occurrence), ('fire', occurrence, 0))
        self.assertEqual(self.status(occurrence + 120)[0], 'fire')

    def test_missed_by_more_than_the_grace_is_skipped_not_run_late(self):
        occurrence = ts(2, 0, 30)
        self.assertEqual(self.status(occurrence + 121), ('skip', occurrence, 0))

    def test_handled_occurrence_is_not_repeated(self):
        occurrence = ts(2, 0, 30)
        self.assertEqual(self.status(occurrence + 5, fired=occurrence)[0], 'idle')

    def test_no_warning_when_warn_is_zero(self):
        occurrence = ts(2, 0, 30)
        self.assertEqual(self.status(occurrence - 5, warn=0)[0], 'idle')
        self.assertEqual(self.status(occurrence, warn=0)[0], 'fire')

    def test_days_restrict_the_runs(self):
        thursday_0030 = ts(3, 0, 30)
        self.assertEqual(self.status(thursday_0030, days=frozenset({2}))[0], 'idle')

    def test_snooze_moves_the_fire_time_for_that_occurrence_only(self):
        occurrence = ts(2, 0, 30)
        override = {'occ': occurrence, 'fire': occurrence + 30 * 60}
        done = occurrence - 86400
        self.assertEqual(self.status(occurrence + 5, fired=done, override=override)[0], 'idle')      # snoozed
        self.assertEqual(self.status(occurrence + 30 * 60 - 40, fired=done, override=override)[0], 'warn')
        self.assertEqual(self.status(occurrence + 30 * 60, fired=done, override=override)[0], 'fire')

    def test_snooze_override_counts_from_the_fire_time_or_from_now(self):
        occurrence = ts(2, 0, 30)
        done = occurrence - 86400                                                    # yesterday's run is handled
        before = rt.schedule_snooze_override(occurrence - 3600, self.S, done, None)   # pressed early
        self.assertEqual(before, {'occ': occurrence, 'fire': occurrence + 30 * 60})
        during = rt.schedule_snooze_override(occurrence - 20, self.S, done, None)     # pressed in the warning
        self.assertEqual(during['fire'], occurrence + 30 * 60)
        again = rt.schedule_snooze_override(occurrence + 100, self.S, done, during)   # pressed again later
        self.assertEqual(again['fire'], occurrence + 60 * 60)
        late = rt.schedule_snooze_override(occurrence + 3 * 3600, self.S, done, again)  # that fire time is long gone
        self.assertEqual(late['occ'], ts(3, 0, 30))                                   # so it snoozes tomorrow's run

    def test_a_stale_unhandled_occurrence_is_not_snoozed(self):
        stale = rt.schedule_snooze_override(ts(2, 12), self.S, 0, None)               # 0:30 passed 11.5 h ago, unhandled
        self.assertEqual(stale['occ'], ts(3, 0, 30))                                  # snoozes the upcoming one instead

    def test_nothing_pending_means_no_override(self):
        s = dict(self.S, days=frozenset())
        self.assertIsNone(rt.schedule_snooze_override(ts(2, 12), s, 0, None))


class TestActionsAndScheduleConfig(TmpConfigCase):
    def test_actions_default_to_suspend_and_power_off(self):
        self.assertEqual([a[0] for a in rt.load_actions()], ['Suspend', 'Power off'])
        self.assertEqual(dict(rt.load_actions())['Suspend'], 'loginctl suspend')

    def test_actions_from_config(self):
        self.conf('[actions]\nHibernate|loginctl hibernate\nLock|loginctl lock-session\n')
        self.assertEqual(rt.load_actions(), [('Hibernate', 'loginctl hibernate'), ('Lock', 'loginctl lock-session')])

    def test_format_days(self):
        self.assertEqual(rt.format_days(range(7)), 'daily')
        self.assertEqual(rt.format_days(range(5)), 'weekdays')
        self.assertEqual(rt.format_days({5, 6}), 'weekends')
        self.assertEqual(rt.format_days({0, 2, 4}), 'mon,wed,fri')
        for days in ({0, 2, 4}, {6}, set(range(7)), set(range(5))):  # round-trips through the parser
            self.assertEqual(set(rt._parse_days(rt.format_days(days))), days)

    def test_defaults_without_a_section(self):
        s = rt.load_schedule_settings()
        self.assertEqual((s['warn'], s['snooze'], s['grace'], s['schedules']), (60, 30, 120, []))

    def test_several_schedules_and_shared_settings(self):
        self.conf('[schedule]\nwarn=0\nsnooze=45 # min\ngrace=30\n'
                  'on|00:30|daily|Suspend\noff|07:00|weekdays|Lock screen\non|23.15|mon,wed|Power off\n')
        s = rt.load_schedule_settings()
        self.assertEqual((s['warn'], s['snooze'], s['grace']), (0, 45, 30))
        self.assertEqual([(e['enabled'], e['minute'], rt.format_days(e['days']), e['action']) for e in s['schedules']],
                         [(True, 30, 'daily', 'Suspend'), (False, 420, 'weekdays', 'Lock screen'),
                          (True, 23 * 60 + 15, 'mon,wed', 'Power off')])

    def test_unreadable_lines_are_skipped_and_missing_fields_defaulted(self):
        self.conf('[schedule]\nnonsense\non|25:99|daily|Suspend\non|funday|x\non|08:00\n')
        entries = rt.load_schedule_settings()['schedules']
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]['minute'], entries[0]['action'], set(entries[0]['days'])), (480, 'Suspend', set(range(7))))

    def test_identical_lines_are_collapsed(self):
        self.conf('[schedule]\non|00:30|daily|Suspend\noff|00:30|daily|Suspend\n')
        self.assertEqual(len(rt.load_schedule_settings()['schedules']), 1)

    def test_original_single_schedule_keys_are_still_read(self):
        self.conf('[schedule]\nenabled=true\ntime=00:30\ndays=weekdays\naction=Power off\nwarn=20\n')
        s = rt.load_schedule_settings()
        self.assertEqual(s['warn'], 20)
        self.assertEqual(len(s['schedules']), 1)
        e = s['schedules'][0]
        self.assertEqual((e['enabled'], e['minute'], rt.format_days(e['days']), e['action']), (True, 30, 'weekdays', 'Power off'))

    def test_save_replaces_entries_keeps_comments_settings_and_other_sections_and_drops_old_keys(self):
        self.conf('[feeds]\nhttps://a\n[schedule]\n# my note\nwarn=20\nenabled=true\ntime=00:30\n'
                  'on|01:00|daily|Suspend\n[twitch]\nchan\n')
        entries = [dict(rt.NEW_SCHEDULE, enabled=True, minute=7 * 60 + 5, days=frozenset({0, 1}), action='Lock screen'),
                   dict(rt.NEW_SCHEDULE)]
        rt.save_schedules(entries)
        with open(rt.CONFIG_FILE) as f:
            text = f.read()
        self.assertIn('# my note', text)
        self.assertIn('warn=20', text)
        self.assertNotIn('enabled=true', text)
        self.assertNotIn('time=00:30', text)
        self.assertNotIn('01:00', text)
        s = rt.load_schedule_settings()
        self.assertEqual([(e['enabled'], e['minute'], rt.format_days(e['days']), e['action']) for e in s['schedules']],
                         [(True, 425, 'mon,tue', 'Lock screen'), (False, 30, 'daily', 'Suspend')])
        self.assertEqual(rt.load_twitch_channels(), ['chan'])

    def test_save_creates_the_section(self):
        self.conf('[feeds]\nhttps://a\n')
        rt.save_schedules([dict(rt.NEW_SCHEDULE, enabled=True)])
        self.assertEqual(len(rt.load_schedule_settings()['schedules']), 1)

    def test_schedule_key_changes_when_the_schedule_does(self):
        a = dict(rt.NEW_SCHEDULE)
        self.assertEqual(rt.schedule_key(a), rt.schedule_key(dict(a, enabled=True)))  # on/off keeps identity
        self.assertNotEqual(rt.schedule_key(a), rt.schedule_key(dict(a, minute=31)))
        self.assertNotEqual(rt.schedule_key(a), rt.schedule_key(dict(a, action='Power off')))


if __name__ == '__main__':
    unittest.main()
