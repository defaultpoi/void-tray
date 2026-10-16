import unittest
from unittest import mock

from facade import rt


class TestParseXbpsUpdates(unittest.TestCase):
    def test_real_line(self):
        out = 'cryptsetup-2.8.8_1 update x86_64 https://repo-default.voidlinux.org/current 3203607 568523\n'
        self.assertEqual(rt.parse_xbps_updates(out), ['cryptsetup'])

    def test_hyphenated_pkgname(self):
        out = 'python3-feedparser-6.0.11_1 update x86_64 https://r 1 2\n'
        self.assertEqual(rt.parse_xbps_updates(out), ['python3-feedparser'])

    def test_non_update_actions_and_junk_ignored(self):
        out = ('foo-1.0_1 install x86_64 https://r 1 2\n'
               'bar-1.0_1 remove x86_64 https://r 1 2\n'
               '\nshort\n'
               'baz-2.0_1 update x86_64 https://r 1 2\n')
        self.assertEqual(rt.parse_xbps_updates(out), ['baz'])

    def test_empty(self):
        self.assertEqual(rt.parse_xbps_updates(''), [])


class TestInstallerChoice(unittest.TestCase):
    def test_auto_uses_topgrade_when_it_is_installed(self):
        with mock.patch.object(rt.shutil, 'which', return_value='/usr/bin/topgrade'):
            self.assertEqual(rt.choose_installer('auto'), 'topgrade')

    def test_auto_falls_back_to_xbps_without_topgrade(self):
        with mock.patch.object(rt.shutil, 'which', return_value=None):
            self.assertEqual(rt.choose_installer('auto'), 'xbps')

    def test_an_explicit_choice_is_kept_whether_or_not_topgrade_is_installed(self):
        with mock.patch.object(rt.shutil, 'which', return_value=None):
            self.assertEqual(rt.choose_installer('topgrade'), 'topgrade')
        with mock.patch.object(rt.shutil, 'which', return_value='/usr/bin/topgrade'):
            self.assertEqual(rt.choose_installer('xbps'), 'xbps')


class TestTopgradeCommand(unittest.TestCase):
    FLAGS = ['--yes', '--no-retry', '--no-self-update', '--skip-notify']

    def test_the_default_runs_only_the_system_step_unattended(self):
        self.assertEqual(rt.topgrade_command(('system',)), ['topgrade', '--only', 'system'] + self.FLAGS)

    def test_several_steps_follow_a_single_only(self):
        self.assertEqual(rt.topgrade_command(('system', 'flatpak')),
                         ['topgrade', '--only', 'system', 'flatpak'] + self.FLAGS)

    def test_all_steps_means_no_only_flag(self):
        self.assertEqual(rt.topgrade_command(None), ['topgrade'] + self.FLAGS)


if __name__ == '__main__':
    unittest.main()
