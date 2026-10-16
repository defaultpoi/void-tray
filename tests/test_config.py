import os
import shutil
import tempfile
import unittest
from unittest import mock

from facade import rt
from helpers import TmpConfigCase, dt


class TestFeeds(TmpConfigCase):
    def test_missing_config_gives_no_feeds(self):
        self.assertEqual(rt.load_feeds(), [])

    def test_url_only_uses_default_interval(self):
        self.conf('[feeds]\nhttps://a.example/feed\n')
        self.assertEqual(rt.load_feeds(), [('https://a.example/feed', None, rt.CHECK_INTERVAL)])

    def test_name_and_interval(self):
        self.conf('[feeds]\nhttps://a|My Blog|5\n')
        self.assertEqual(rt.load_feeds(), [('https://a', 'My Blog', 300)])

    def test_empty_name_keeps_interval(self):
        self.conf('[feeds]\nhttps://a||5\n')
        self.assertEqual(rt.load_feeds(), [('https://a', None, 300)])

    def test_bad_interval_falls_back_to_default(self):
        self.conf('[feeds]\nhttps://a|x|abc\n')
        self.assertEqual(rt.load_feeds(), [('https://a', 'x', rt.CHECK_INTERVAL)])

    def test_zero_interval_clamped_to_one_minute(self):
        self.conf('[feeds]\nhttps://a||0\n')
        self.assertEqual(rt.load_feeds()[0][2], 60)

    def test_comments_blanks_and_unknown_sections_ignored(self):
        self.conf('# c\n\n[feeds]\n# x\nhttps://a\n[other]\nhttps://b\n')
        self.assertEqual([f[0] for f in rt.load_feeds()], ['https://a'])

    def test_section_names_case_insensitive(self):
        self.conf('[FEEDS]\nhttps://a\n')
        self.assertEqual(len(rt.load_feeds()), 1)


class TestTwitchChannels(TmpConfigCase):
    def test_lowercased_deduped_order_preserved(self):
        self.conf('[twitch]\nFoo\nbar\nfoo\n')
        self.assertEqual(rt.load_twitch_channels(), ['foo', 'bar'])


class TestWeatherCoords(TmpConfigCase):
    @staticmethod
    def coords():
        s = rt.load_weather_settings()
        return s['lat'], s['lon']

    def test_no_section_gives_none(self):
        self.assertEqual(self.coords(), (None, None))

    def test_valid_coords_parsed(self):
        self.conf('[weather]\n40.0|-3.7\n')
        self.assertEqual(self.coords(), (40.0, -3.7))

    def test_garbage_gives_none(self):
        self.conf('[weather]\nnot-a-number|also-not\n')
        self.assertEqual(self.coords(), (None, None))

    def test_missing_second_field_gives_none(self):
        self.conf('[weather]\n40.0\n')
        self.assertEqual(self.coords(), (None, None))

    def test_fetch_weather_skips_network_without_coords(self):
        with mock.patch.object(rt.urllib.request, 'urlopen') as urlopen:
            self.assertIsNone(rt.fetch_weather())
        urlopen.assert_not_called()


class TestDebugLogging(unittest.TestCase):
    def test_silent_by_default(self):
        with mock.patch.dict(rt.os.environ, {}, clear=False), mock.patch('builtins.print') as p:
            rt.os.environ.pop('RSS_TRAY_DEBUG', None)
            rt._log('hello')
        p.assert_not_called()

    def test_prints_when_debug_env_set(self):
        with mock.patch.dict(rt.os.environ, {'RSS_TRAY_DEBUG': '1'}), mock.patch('builtins.print') as p:
            rt._log('hello')
        self.assertIn('hello', p.call_args[0][0])


class TestLauncher(TmpConfigCase):
    def test_label_and_command_in_config_order(self):
        self.conf('[launcher]\nFiles|thunar ~\nUpdate|xfce4-terminal -e "sudo xbps-install -Su"\n')
        self.assertEqual(rt.load_launcher_entries(), [
            ('Files', 'thunar ~'), ('Update', 'xfce4-terminal -e "sudo xbps-install -Su"')])

    def test_command_may_contain_pipes_and_equals(self):
        self.conf('[launcher]\nBusy|ps aux | grep -c FOO=1\n')
        self.assertEqual(rt.load_launcher_entries(), [('Busy', 'ps aux | grep -c FOO=1')])

    def test_bare_line_is_label_and_command(self):
        self.conf('[launcher]\nmousepad\n')
        self.assertEqual(rt.load_launcher_entries(), [('mousepad', 'mousepad')])

    def test_empty_commands_and_comments_are_skipped(self):
        self.conf('[launcher]\n# note\nNothing|\n\nReal|true\n')
        self.assertEqual(rt.load_launcher_entries(), [('Real', 'true')])

    def test_no_section_is_empty(self):
        self.conf('[feeds]\n')
        self.assertEqual(rt.load_launcher_entries(), [])

    def test_run_command_is_detached_through_the_shell_from_home(self):
        with mock.patch.object(rt.subprocess, 'Popen') as popen, mock.patch.object(rt.threading, 'Thread'):
            self.assertTrue(rt.run_launcher_command('thunar ~ && echo hi'))
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ['sh', '-c', 'thunar ~ && echo hi'])
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['cwd'], rt.os.path.expanduser('~'))
        self.assertIs(kwargs['stdout'], rt.subprocess.DEVNULL)

    def test_run_command_failure_is_reported_not_raised(self):
        with mock.patch.object(rt.subprocess, 'Popen', side_effect=OSError('nope')), mock.patch.object(rt, '_log'):
            self.assertFalse(rt.run_launcher_command('x'))

    def test_popup_stays_open_while_the_launch_list_has_focus(self):
        class Stub:
            on_popup_focus_out = rt.RssTray.on_popup_focus_out
            _launcher_open = True
        win = mock.MagicMock()
        Stub().on_popup_focus_out(win, None)
        win.hide.assert_not_called()
        Stub._launcher_open = False
        Stub().on_popup_focus_out(win, None)
        win.hide.assert_called_once()


class TestScheduleIconAndCss(unittest.TestCase):
    def test_warning_css_keeps_button_text_dark(self):
        self.assertIn('.schedule-warning button label { color: #000000', rt.POPUP_CSS)

    def test_template_config_parses_with_no_schedules_by_default(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with mock.patch.object(rt, 'CONFIG_DIR', d), mock.patch.object(rt, 'CONFIG_FILE', os.path.join(d, 'config.conf')):
            rt.ensure_config()
            s = rt.load_schedule_settings()
            self.assertEqual(s['schedules'], [])
            self.assertEqual((s['warn'], s['snooze'], s['grace']), (60, 30, 120))
            self.assertEqual([a[0] for a in rt.load_actions()], ['Suspend', 'Power off'])


class TestDefaultStreamQuality(TmpConfigCase):
    def test_best_when_nothing_is_configured(self):
        self.assertEqual(rt.load_twitch_default_quality(), 'best')
        self.assertEqual(rt.load_youtube_default_quality(), 'best')

    def test_section_default_quality_with_fallbacks_and_comment(self):
        self.conf('[twitch]\nquality=720p60,720p,best # laptop screen\nchan\n[youtube]\nquality = 720p,480p,best\n@h\n')
        self.assertEqual(rt.load_twitch_default_quality(), '720p60,720p,best')
        self.assertEqual(rt.load_youtube_default_quality(), '720p,480p,best')

    def test_empty_value_means_best(self):
        self.conf('[twitch]\nquality=\n')
        self.assertEqual(rt.load_twitch_default_quality(), 'best')

    def test_the_setting_line_is_not_a_channel_and_does_not_leak_into_qualities(self):
        self.conf('[twitch]\nquality=720p\nchan\nother|1080p60\n')
        self.assertEqual(rt.load_twitch_channels(), ['chan', 'other'])
        self.assertEqual(rt.load_twitch_qualities(), {'other': '1080p60'})

    def test_rows_use_their_own_quality_else_the_section_default(self):
        self.conf('[twitch]\nquality=720p,best\nplain\nown|480p\n[youtube]\nquality=360p,best\n@a\n@b|1080p\n')

        class Stub:
            build_twitch_row = rt.RssTray.build_twitch_row
            build_youtube_row = rt.RssTray.build_youtube_row

            def _build_live_row(self, entry, site, color):
                return mock.Mock()
        stub = Stub()
        self.assertEqual(stub.build_twitch_row({'channel': 'plain'}).twitch_quality, '720p,best')
        self.assertEqual(stub.build_twitch_row({'channel': 'own'}).twitch_quality, '480p')
        self.assertEqual(stub.build_youtube_row({'channel': '@a'}).youtube_quality, '360p,best')
        self.assertEqual(stub.build_youtube_row({'channel': '@b'}).youtube_quality, '1080p')


class TestIntervalDefaults(TmpConfigCase):
    def test_weather_refresh_interval(self):
        self.assertEqual(rt.load_weather_refresh_interval(), 30 * 60)
        self.conf('[weather]\n10.0|20.0\ninterval=10\n')
        self.assertEqual(rt.load_weather_refresh_interval(), 600)
        self.conf('[weather]\ninterval=1\n')
        self.assertEqual(rt.load_weather_refresh_interval(), rt.MIN_WEATHER_REFRESH_MINUTES * 60)
        self.assertIsNone(rt.load_weather_settings()['lat'])  # the setting isn't mistaken for coordinates

    def test_weather_interval_does_not_disturb_the_other_weather_settings(self):
        self.conf('[weather]\n10.0|20.0\ninterval=10\nalerts=true\n')
        s = rt.load_weather_settings()
        self.assertEqual((s['lat'], s['lon'], s['alerts_enabled']), (10.0, 20.0, True))

    def test_documented_defaults(self):
        self.assertEqual(rt.load_twitch_check_interval(), 15 * 60)
        self.assertEqual(rt.load_youtube_check_interval(), 15 * 60)
        self.assertEqual(rt.load_updates_check_interval(), 12 * 3600)  # twice a day

    def test_updates_interval_is_in_hours_with_a_one_hour_floor(self):
        self.conf('[updates]\ninterval=6\n')
        self.assertEqual(rt.load_updates_check_interval(), 6 * 3600)
        self.conf('[updates]\ninterval=0\n')
        self.assertEqual(rt.load_updates_check_interval(), 3600)
        self.conf('[updates]\ninterval=soon\n')
        self.assertEqual(rt.load_updates_check_interval(), 12 * 3600)


class TestUpdatesInstaller(TmpConfigCase):
    def test_defaults_are_auto_and_the_system_step(self):
        self.assertEqual(rt.load_updates_installer(), ('auto', ('system',)))
        self.conf('[updates]\ninterval=6\n')
        self.assertEqual(rt.load_updates_installer(), ('auto', ('system',)))

    def test_installer_choices_and_unknown_values(self):
        for value in ('auto', 'topgrade', 'xbps'):
            self.conf('[updates]\ninstaller=%s\n' % value)
            self.assertEqual(rt.load_updates_installer()[0], value)
        self.conf('[updates]\ninstaller = TopGrade # comment\n')
        self.assertEqual(rt.load_updates_installer()[0], 'topgrade')
        self.conf('[updates]\ninstaller=apt\n')
        self.assertEqual(rt.load_updates_installer()[0], 'auto')

    def test_steps_accept_commas_pipes_and_spaces_without_duplicates(self):
        self.conf('[updates]\nsteps=System, flatpak|cargo  system\n')
        self.assertEqual(rt.load_updates_installer()[1], ('system', 'flatpak', 'cargo'))

    def test_names_that_are_not_plain_identifiers_are_dropped(self):
        self.conf('[updates]\nsteps=sys;tem, rustup, --yes, $(x)\n')
        self.assertEqual(rt.load_updates_installer()[1], ('rustup',))

    def test_nothing_usable_falls_back_to_the_system_step(self):
        self.conf('[updates]\nsteps=\n')
        self.assertEqual(rt.load_updates_installer()[1], ('system',))
        self.conf('[updates]\nsteps=;;, ||\n')
        self.assertEqual(rt.load_updates_installer()[1], ('system',))

    def test_all_means_every_step(self):
        self.conf('[updates]\nsteps=all\n')
        self.assertIsNone(rt.load_updates_installer()[1])

    def test_new_configs_document_both_keys_and_still_parse(self):
        rt.ensure_config()
        with open(rt.CONFIG_FILE) as f:
            text = f.read()
        self.assertIn('# installer=', text)
        self.assertIn('# steps=', text)
        self.assertEqual(rt.load_updates_installer(), ('auto', ('system',)))


class TestOrphanNudgeSetting(TmpConfigCase):
    def test_on_by_default(self):
        self.assertTrue(rt.load_orphan_nudge())
        self.conf('[updates]\ninterval=6\n')
        self.assertTrue(rt.load_orphan_nudge())

    def test_switched_off_by_any_of_the_usual_words(self):
        for value in ('false', 'FALSE', 'no', 'off', '0', 'false # not wanted'):
            self.conf('[updates]\norphans=%s\n' % value)
            self.assertFalse(rt.load_orphan_nudge(), value)

    def test_explicitly_on(self):
        for value in ('true', 'yes', 'on', '1', 'anything-else'):
            self.conf('[updates]\norphans=%s\n' % value)
            self.assertTrue(rt.load_orphan_nudge(), value)

    def test_new_configs_document_the_key(self):
        rt.ensure_config()
        with open(rt.CONFIG_FILE) as f:
            self.assertIn('# orphans=false', f.read())
        self.assertTrue(rt.load_orphan_nudge())


class TestLiveCheckIntervals(TmpConfigCase):
    def test_defaults_without_setting(self):
        self.conf('[twitch]\nchan\n[youtube]\n@h\n')
        self.assertEqual(rt.load_twitch_check_interval(), rt.TWITCH_CHECK_INTERVAL_SECONDS)
        self.assertEqual(rt.load_youtube_check_interval(), rt.YOUTUBE_CHECK_INTERVAL_SECONDS)

    def test_defaults_without_config_file(self):
        self.assertEqual(rt.load_twitch_check_interval(), rt.TWITCH_CHECK_INTERVAL_SECONDS)

    def test_minutes_are_converted_to_seconds(self):
        self.conf('[twitch]\ninterval=10\nchan\n[youtube]\ninterval = 15 # note\n@h\n')
        self.assertEqual(rt.load_twitch_check_interval(), 600)
        self.assertEqual(rt.load_youtube_check_interval(), 900)

    def test_low_values_are_raised_to_the_minimum(self):
        self.conf('[twitch]\ninterval=0\n[youtube]\ninterval=1\n')
        self.assertEqual(rt.load_twitch_check_interval(), rt.MIN_TWITCH_CHECK_MINUTES * 60)
        self.assertEqual(rt.load_youtube_check_interval(), rt.MIN_YOUTUBE_CHECK_MINUTES * 60)

    def test_garbage_falls_back_to_default(self):
        self.conf('[twitch]\ninterval=often\n')
        self.assertEqual(rt.load_twitch_check_interval(), rt.TWITCH_CHECK_INTERVAL_SECONDS)

    def test_setting_is_per_section(self):
        self.conf('[twitch]\ninterval=7\n')
        self.assertEqual(rt.load_twitch_check_interval(), 420)
        self.assertEqual(rt.load_youtube_check_interval(), rt.YOUTUBE_CHECK_INTERVAL_SECONDS)

    def test_interval_lines_are_not_channels(self):
        self.conf('[twitch]\ninterval=10\nchan|720p60\n[youtube]\ninterval=20\n@h|480p\n')
        self.assertEqual(rt.load_twitch_channels(), ['chan'])
        self.assertEqual(rt.load_twitch_qualities(), {'chan': '720p60'})
        self.assertEqual(rt.load_youtube_channels(), ['@h'])
        self.assertEqual(rt.load_youtube_qualities(), {'@h': '480p'})


class TestScheduleConfig(TmpConfigCase):
    def test_schedule_is_the_third_field_and_quality_may_be_empty(self):
        self.conf('[twitch]\nplain\nweekly||wed 18:00-23:00\nboth|720p60|tue-sun 13:00-; sat 10:00-12:00\n'
                  '[youtube]\n@Handle|480p|mon 20:00-\n')
        self.assertEqual(rt.load_twitch_channels(), ['plain', 'weekly', 'both'])
        self.assertEqual(rt.load_twitch_qualities(), {'both': '720p60'})
        schedules = rt.load_twitch_schedules()
        self.assertEqual(set(schedules), {'weekly', 'both'})
        self.assertEqual(len(schedules['both']), 2)
        self.assertEqual(rt.load_youtube_channels(), ['@Handle'])
        self.assertEqual(rt.load_youtube_qualities(), {'@Handle': '480p'})
        self.assertEqual(set(rt.load_youtube_schedules()), {'@Handle'})

    def test_invalid_schedule_means_always_checked(self):
        self.conf('[twitch]\nbroken||someday 99:99\nfine||wed 18:00\n')
        self.assertEqual(set(rt.load_twitch_schedules()), {'fine'})
        self.assertEqual(rt.channels_due(rt.load_twitch_channels(), rt.load_twitch_schedules(), set(), dt(0, 3)),
                         ['broken'])

    def test_first_line_wins_for_duplicates(self):
        self.conf('[twitch]\nChan|720p60\nchan|480p||wed 18:00\n')
        self.assertEqual(rt.load_twitch_channels(), ['chan'])
        self.assertEqual(rt.load_twitch_qualities(), {'chan': '720p60'})
        self.assertEqual(rt.load_twitch_schedules(), {})


class TestWeatherSettings(TmpConfigCase):
    def test_defaults(self):
        s = rt.load_weather_settings()
        self.assertIsNone(s['lat'])
        self.assertIsNone(s['lon'])
        self.assertFalse(s['alerts_enabled'])
        self.assertIsNone(s['country'])
        self.assertIsNone(s['region'])

    def test_full_section(self):
        self.conf('[weather]\n40.0|-3.7\nalerts=true\ncountry=Exampleland\nregion=Northshire\n')
        s = rt.load_weather_settings()
        self.assertEqual((s['lat'], s['lon']), (40.0, -3.7))
        self.assertTrue(s['alerts_enabled'])
        self.assertEqual(s['country'], 'Exampleland')
        self.assertEqual(s['region'], 'Northshire')

    def test_alerts_case_insensitive_and_variants(self):
        for val in ('True', 'YES', '1', 'on'):
            self.conf(f'[weather]\nalerts={val}\n')
            self.assertTrue(rt.load_weather_settings()['alerts_enabled'])
        for val in ('false', '0', 'no', ''):
            self.conf(f'[weather]\nalerts={val}\n')
            self.assertFalse(rt.load_weather_settings()['alerts_enabled'])


class TestUpdateWeatherRegionCache(TmpConfigCase):
    def test_round_trip_preserves_other_content(self):
        self.conf(
            '[feeds]\nhttps://a\n'
            '\n[weather]\n45.0|26.0\nalerts=true\n# a comment\n'
            '\n[twitch]\nsomechannel\n'
        )
        rt._update_weather_region_cache('Northshire', 'Exampleland')
        s = rt.load_weather_settings()
        self.assertEqual((s['country'], s['region']), ('Exampleland', 'Northshire'))
        self.assertEqual((s['lat'], s['lon']), (45.0, 26.0))
        self.assertTrue(s['alerts_enabled'])
        self.assertEqual(rt.load_twitch_channels(), ['somechannel'])
        self.assertEqual([f[0] for f in rt.load_feeds()], ['https://a'])

    def test_overwrites_previous_region_values(self):
        self.conf('[weather]\n45.0|26.0\nregion=Old\n')
        rt._update_weather_region_cache('New', 'Newland')
        s = rt.load_weather_settings()
        self.assertEqual((s['country'], s['region']), ('Newland', 'New'))

    def test_country_written_and_replaced(self):
        self.conf('[weather]\n45.0|26.0\ncountry=Old\nregion=Old\n')
        rt._update_weather_region_cache('New', 'Newland')
        s = rt.load_weather_settings()
        self.assertEqual((s['country'], s['region']), ('Newland', 'New'))
        with open(rt.CONFIG_FILE) as f:
            self.assertEqual(f.read().count('country='), 1)

    def test_creates_section_if_missing(self):
        self.conf('[feeds]\nhttps://a\n')
        rt._update_weather_region_cache('Northshire', 'Exampleland')
        self.assertEqual(rt.load_weather_settings()['region'], 'Northshire')


class TestDisableWeatherAlerts(TmpConfigCase):
    def test_flips_alerts_and_drops_cache_keeping_rest(self):
        self.conf('[feeds]\nhttps://a\n\n[weather]\n10.0|20.0\nalerts=true\n# note\n'
                  'country=X\nregion=Y\n\n[twitch]\nsomechannel\n')
        rt._disable_weather_alerts()
        s = rt.load_weather_settings()
        self.assertFalse(s['alerts_enabled'])
        self.assertEqual((s['lat'], s['lon']), (10.0, 20.0))
        self.assertIsNone(s['country'])
        self.assertIsNone(s['region'])
        self.assertEqual(rt.load_twitch_channels(), ['somechannel'])
        with open(rt.CONFIG_FILE) as f:
            self.assertIn('# note', f.read())

    def test_adds_alerts_false_when_line_missing(self):
        self.conf('[weather]\n10.0|20.0\n')
        rt._disable_weather_alerts()
        self.assertFalse(rt.load_weather_settings()['alerts_enabled'])
        with open(rt.CONFIG_FILE) as f:
            self.assertIn('alerts=false', f.read())


class TestFetchAlertsBg(TmpConfigCase):
    class Stub:
        _fetch_alerts_bg = rt.RssTray._fetch_alerts_bg

    def test_non_meteoalarm_country_disables_alerts_in_config(self):
        self.conf('[weather]\n10.0|20.0\nalerts=true\n')
        with mock.patch.object(rt, 'reverse_geocode_region', return_value=('Elsewhere', 'Somestate', 'ZZ')), \
                mock.patch.object(rt, 'fetch_meteoalarm_alerts') as fetch:
            self.assertEqual(self.Stub()._fetch_alerts_bg(), [])
        fetch.assert_not_called()
        self.assertFalse(rt.load_weather_settings()['alerts_enabled'])

    def test_covered_country_caches_location_and_fetches(self):
        self.conf('[weather]\n10.0|20.0\nalerts=true\n')
        with mock.patch.object(rt, 'reverse_geocode_region', return_value=('Exampleland', 'Northshire', 'RO')), \
                mock.patch.object(rt, 'fetch_meteoalarm_alerts', return_value=[]) as fetch:
            self.assertEqual(self.Stub()._fetch_alerts_bg(), [])
        fetch.assert_called_once_with('Northshire', 'Exampleland')
        s = rt.load_weather_settings()
        self.assertTrue(s['alerts_enabled'])
        self.assertEqual((s['country'], s['region']), ('Exampleland', 'Northshire'))

    def test_geocode_failure_with_nothing_cached_is_none(self):
        self.conf('[weather]\n10.0|20.0\nalerts=true\n')
        with mock.patch.object(rt, 'reverse_geocode_region', return_value=None):
            self.assertIsNone(self.Stub()._fetch_alerts_bg())
        self.assertTrue(rt.load_weather_settings()['alerts_enabled'])

    def test_cached_location_is_never_looked_up_again(self):
        self.conf('[weather]\n10.0|20.0\nalerts=true\ncountry=Exampleland\nregion=Northshire\n')
        with mock.patch.object(rt, 'reverse_geocode_region') as geocode, \
                mock.patch.object(rt, 'fetch_meteoalarm_alerts', return_value=[]) as fetch:
            self.Stub()._fetch_alerts_bg()
        geocode.assert_not_called()
        fetch.assert_called_once_with('Northshire', 'Exampleland')

    def test_lookup_runs_again_once_the_values_are_deleted(self):
        self.conf('[weather]\n10.0|20.0\nalerts=true\ncountry=Exampleland\n')  # region missing
        with mock.patch.object(rt, 'reverse_geocode_region', return_value=('Exampleland', 'Eastshire', 'RO')) as geocode, \
                mock.patch.object(rt, 'fetch_meteoalarm_alerts', return_value=[]):
            self.Stub()._fetch_alerts_bg()
        geocode.assert_called_once()
        self.assertEqual(rt.load_weather_settings()['region'], 'Eastshire')

    def test_unknown_country_code_does_not_disable(self):
        self.conf('[weather]\n10.0|20.0\nalerts=true\n')
        with mock.patch.object(rt, 'reverse_geocode_region', return_value=('Exampleland', 'Northshire', None)), \
                mock.patch.object(rt, 'fetch_meteoalarm_alerts', return_value=[]):
            self.Stub()._fetch_alerts_bg()
        self.assertTrue(rt.load_weather_settings()['alerts_enabled'])


class TestTwitchQuality(TmpConfigCase):
    def test_channels_still_just_names(self):
        self.conf('[twitch]\nfoo\nbar|720p60\n')
        self.assertEqual(rt.load_twitch_channels(), ['foo', 'bar'])

    def test_qualities_parsed(self):
        self.conf('[twitch]\nfoo\nbar|720p60\nbaz|1080p60\n')
        self.assertEqual(rt.load_twitch_qualities(), {'bar': '720p60', 'baz': '1080p60'})

    def test_channel_without_quality_absent_from_qualities(self):
        self.conf('[twitch]\nfoo\n')
        self.assertEqual(rt.load_twitch_qualities(), {})


class TestTimerSettings(TmpConfigCase):
    def test_defaults(self):
        s = rt.load_timer_settings()
        self.assertEqual(s['max_seconds'], rt.DEFAULT_TIMER_MAX_MINUTES * 60)
        self.assertEqual(s['step_seconds'], rt.DEFAULT_TIMER_STEP_SECONDS)

    def test_custom_values(self):
        self.conf('[timer]\nmax=90\nstep=5\n')
        s = rt.load_timer_settings()
        self.assertEqual(s['max_seconds'], 90 * 60)
        self.assertEqual(s['step_seconds'], 5)

    def test_inline_comments_ignored(self):
        self.conf('[timer]\nmax=120 #minutes\nstep=5 #seconds\n')
        s = rt.load_timer_settings()
        self.assertEqual(s['max_seconds'], 120 * 60)
        self.assertEqual(s['step_seconds'], 5)

    def test_bad_values_fall_back_to_defaults(self):
        self.conf('[timer]\nmax=abc\nstep=-5\n')
        s = rt.load_timer_settings()
        self.assertEqual(s['max_seconds'], rt.DEFAULT_TIMER_MAX_MINUTES * 60)
        self.assertEqual(s['step_seconds'], rt.DEFAULT_TIMER_STEP_SECONDS)

    def test_partial_section_keeps_other_default(self):
        self.conf('[timer]\nmax=30\n')
        s = rt.load_timer_settings()
        self.assertEqual(s['max_seconds'], 30 * 60)
        self.assertEqual(s['step_seconds'], rt.DEFAULT_TIMER_STEP_SECONDS)


class TestYoutubeChannelsAndQualities(TmpConfigCase):
    def test_channels_case_preserved_dedup_case_insensitive(self):
        self.conf('[youtube]\n@SomeHandle\nchannel/UCxxxx|720p60\n@somehandle\n')
        self.assertEqual(rt.load_youtube_channels(), ['@SomeHandle', 'channel/UCxxxx'])

    def test_qualities_keyed_case_sensitive(self):
        self.conf('[youtube]\n@SomeHandle|1080p60\nchannel/UCxxxx|720p60\n')
        self.assertEqual(rt.load_youtube_qualities(),
                          {'@SomeHandle': '1080p60', 'channel/UCxxxx': '720p60'})

    def test_channel_without_quality_absent_from_qualities(self):
        self.conf('[youtube]\n@foo\n')
        self.assertEqual(rt.load_youtube_qualities(), {})


if __name__ == '__main__':
    unittest.main()
