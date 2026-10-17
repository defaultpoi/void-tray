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


class TestParseOrphans(unittest.TestCase):
    def test_names_come_from_the_pkgver_lines_sorted(self):
        out = 'libzstd-1.5.6_1\nbzip2-devel-1.0.8_2\nlibstdc++-14.2.0_1\n'
        self.assertEqual(rt.parse_orphans(out), ['bzip2-devel', 'libstdc++', 'libzstd'])

    def test_blank_and_non_pkgver_lines_are_ignored(self):
        out = '\n   \nxbps-query: warning: something\n[*] note\nfoo-1.0_1\nnot a package\n'
        self.assertEqual(rt.parse_orphans(out), ['foo'])

    def test_a_trailing_column_after_the_pkgver_is_ignored_and_duplicates_collapse(self):
        self.assertEqual(rt.parse_orphans('foo-1.0_1 something\nfoo-1.0_1\n'), ['foo'])

    def test_nothing_to_report(self):
        self.assertEqual(rt.parse_orphans(''), [])


class TestListOrphans(unittest.TestCase):
    def popen(self, out='', returncode=0, **kwargs):
        proc = mock.MagicMock()
        proc.communicate.return_value = (out, '')
        proc.returncode = returncode
        return mock.Mock(return_value=proc, **kwargs), proc

    def test_runs_the_read_only_query_without_root(self):
        popen, _ = self.popen('foo-1.0_1\nbar-2.1_3\n')
        with mock.patch.object(rt.subprocess, 'Popen', popen):
            self.assertEqual(rt.list_orphans(), ['bar', 'foo'])
        self.assertEqual(popen.call_args[0][0], ['xbps-query', '-O'])

    def test_no_orphans_is_an_empty_list_not_a_failure(self):
        popen, _ = self.popen('')
        with mock.patch.object(rt.subprocess, 'Popen', popen):
            self.assertEqual(rt.list_orphans(), [])

    def test_a_failing_query_is_none(self):
        popen, _ = self.popen('foo-1.0_1\n', returncode=1)
        with mock.patch.object(rt.subprocess, 'Popen', popen):
            self.assertIsNone(rt.list_orphans())

    def test_a_missing_xbps_query_is_none(self):
        with mock.patch.object(rt.subprocess, 'Popen', side_effect=FileNotFoundError('xbps-query')):
            self.assertIsNone(rt.list_orphans())

    def test_a_hung_query_is_killed_and_is_none(self):
        popen, proc = self.popen()
        proc.communicate.side_effect = rt.subprocess.TimeoutExpired('xbps-query', 60)
        with mock.patch.object(rt.subprocess, 'Popen', popen), mock.patch.object(rt, '_kill_process_group') as kill:
            self.assertIsNone(rt.list_orphans())
        kill.assert_called_once_with(proc)


BAR = '\u2015\u2015'


def heading(name, clock='10:46:16'):
    return f'{BAR} {clock} - {name} {BAR}'


class TestTopgradeOutput(unittest.TestCase):
    def read(self, lines, returncode=0, tail=()):
        output = rt.TopgradeOutput()
        for line in lines:
            output.feed(line)
        return output, output.summary(returncode, tail)

    def test_a_clean_run_is_ok_and_keeps_the_summary_lines_as_printed(self):
        _, summary = self.read([heading('System update'), '[*] Downloading packages', heading('Summary'), 'System update: OK'])
        self.assertTrue(summary['ok'])
        self.assertEqual(summary['lines'], ['System update: OK'])
        self.assertEqual(summary['details'], [])

    def test_several_steps_are_all_listed_in_order(self):
        output, summary = self.read([heading('System update'), 'x', heading('Flatpak'), 'y',
                                     heading('Summary'), 'System update: OK', 'Flatpak: OK'])
        self.assertEqual(output.results(), [('System update', 'OK'), ('Flatpak', 'OK')])
        self.assertEqual(summary['lines'], ['System update: OK', 'Flatpak: OK'])

    def test_a_failed_step_fails_the_run_and_brings_its_last_output_lines(self):
        _, summary = self.read([heading('System update'), 'fine', heading('Broken step'), 'sudo: a password is required',
                                heading('Summary'), 'System update: OK', 'Broken step: FAILED'], returncode=1)
        self.assertFalse(summary['ok'])
        self.assertEqual(summary['details'], ['Broken step: sudo: a password is required'])

    def test_only_the_last_few_lines_of_a_failed_step_are_kept(self):
        lines = [heading('Noisy')] + [f'line {i}' for i in range(10)] + [heading('Summary'), 'Noisy: FAILED']
        _, summary = self.read(lines, returncode=1)
        self.assertEqual(summary['details'], ['Noisy: line 7', 'Noisy: line 8', 'Noisy: line 9'])

    def test_output_of_steps_that_succeeded_is_not_reported(self):
        _, summary = self.read([heading('Fine'), 'secret detail', heading('Summary'), 'Fine: OK'])
        self.assertEqual(summary['details'], [])

    def test_a_nonzero_exit_fails_the_run_even_if_every_step_reads_ok(self):
        _, summary = self.read([heading('A'), heading('Summary'), 'A: OK'], returncode=1)
        self.assertFalse(summary['ok'])

    def test_step_names_may_contain_colons(self):
        output, _ = self.read([heading('Summary'), 'Custom: thing: FAILED'])
        self.assertEqual(output.results(), [('Custom: thing', 'FAILED')])

    def test_without_a_summary_the_last_output_lines_explain_a_failure(self):
        _, summary = self.read(['sudo: not found'], returncode=1, tail=['boom', 'sudo: not found'])
        self.assertFalse(summary['ok'])
        self.assertEqual(summary['lines'], [])
        self.assertEqual(summary['details'], ['boom', 'sudo: not found'])

    def test_without_a_summary_a_clean_exit_is_simply_ok(self):
        _, summary = self.read([], returncode=0, tail=['x'])
        self.assertTrue(summary['ok'])
        self.assertEqual(summary['details'], [])

    def test_lines_before_the_first_heading_and_unrelated_dashes_are_ignored(self):
        _, summary = self.read(['preamble', '-- not a heading --', heading('Summary'), 'A: OK'])
        self.assertEqual(summary['lines'], ['A: OK'])


class TestTopgradeEnv(unittest.TestCase):
    def test_forces_english_output_and_keeps_the_rest_of_the_environment(self):
        with mock.patch.dict(rt.os.environ, {'LC_ALL': 'ro_RO.UTF-8', 'KEEP_ME': '1'}):
            env = rt.topgrade_env()
        self.assertEqual(env['LC_ALL'], 'C.UTF-8')
        self.assertEqual(env['KEEP_ME'], '1')

    def test_the_real_environment_is_not_modified(self):
        with mock.patch.dict(rt.os.environ, {'LC_ALL': 'ro_RO.UTF-8'}):
            rt.topgrade_env()
            self.assertEqual(rt.os.environ['LC_ALL'], 'ro_RO.UTF-8')


if __name__ == '__main__':
    unittest.main()
