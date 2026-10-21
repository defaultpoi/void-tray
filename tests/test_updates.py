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

    def home_with(self, *dirs):
        import tempfile
        import shutil
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        for name in dirs:
            rt.os.makedirs(rt.os.path.join(home, name))
        return home

    def test_the_user_bin_directories_that_exist_are_added_to_the_end_of_path(self):
        home = self.home_with('.local/bin', '.cargo/bin')
        with mock.patch.dict(rt.os.environ, {'HOME': home, 'PATH': '/usr/bin:/bin'}):
            env = rt.topgrade_env()
        self.assertEqual(env['PATH'], f'/usr/bin:/bin:{home}/.local/bin:{home}/.cargo/bin')

    def test_a_directory_already_on_the_path_is_not_added_again(self):
        home = self.home_with('.local/bin', '.cargo/bin')
        with mock.patch.dict(rt.os.environ, {'HOME': home, 'PATH': f'{home}/.local/bin:/usr/bin'}):
            env = rt.topgrade_env()
        self.assertEqual(env['PATH'], f'{home}/.local/bin:/usr/bin:{home}/.cargo/bin')

    def test_a_directory_that_does_not_exist_is_left_out(self):
        home = self.home_with('.local/bin')
        with mock.patch.dict(rt.os.environ, {'HOME': home, 'PATH': '/usr/bin'}):
            env = rt.topgrade_env()
        self.assertEqual(env['PATH'], f'/usr/bin:{home}/.local/bin')

    def test_a_missing_path_is_not_invented(self):
        home = self.home_with('.local/bin')
        with mock.patch.dict(rt.os.environ, {'HOME': home}):
            rt.os.environ.pop('PATH', None)
            self.assertNotIn('PATH', rt.topgrade_env())

    def test_the_real_path_is_not_modified(self):
        home = self.home_with('.local/bin')
        with mock.patch.dict(rt.os.environ, {'HOME': home, 'PATH': '/usr/bin'}):
            rt.topgrade_env()
            self.assertEqual(rt.os.environ['PATH'], '/usr/bin')


class TestVersionChange(unittest.TestCase):
    def test_the_versions_come_from_the_pkgvers_without_the_build_revision(self):
        self.assertEqual(rt.version_change('libfoo', 'libfoo-3.2_1', 'libfoo-3.3_1'), ('libfoo', '3.2', '3.3'))

    def test_names_with_dashes_and_versions_with_letters_are_kept_whole(self):
        self.assertEqual(rt.version_change('lib-foo-devel', 'lib-foo-devel-1.0.9a_2', 'lib-foo-devel-1.1_1'),
                         ('lib-foo-devel', '1.0.9a', '1.1'))

    def test_a_new_revision_of_the_same_version_keeps_the_revisions(self):
        self.assertEqual(rt.version_change('libfoo', 'libfoo-3.2_1', 'libfoo-3.2_2'), ('libfoo', '3.2_1', '3.2_2'))

    def test_a_bare_version_is_used_as_it_is(self):
        self.assertEqual(rt.version_change('a', '1', '2'), ('a', '1', '2'))

    def test_an_unknown_version_stays_unknown(self):
        self.assertEqual(rt.version_change('a', None, 'a-2_1'), ('a', None, '2_1'))
        self.assertEqual(rt.version_change('a', 'a-1_1', None), ('a', '1_1', None))


class TestFormatChange(unittest.TestCase):
    def test_name_old_arrow_new(self):
        self.assertEqual(rt.format_change(('pkg1', '3.2', '3.3')), 'pkg1 3.2 -> 3.3')

    def test_without_the_old_version_only_the_new_one_follows_the_arrow(self):
        self.assertEqual(rt.format_change(('pkg1', None, '3.3')), 'pkg1 -> 3.3')

    def test_without_the_new_version_it_is_just_the_name(self):
        self.assertEqual(rt.format_change(('pkg1', '3.2', None)), 'pkg1')
        self.assertEqual(rt.format_change(('pkg1', None, None)), 'pkg1')


class TestToolChangeReaders(unittest.TestCase):
    def test_claude_code_names_both_versions_when_it_updated(self):
        lines = ['Current version: 2.1.304', 'Checking for updates to latest version...',
                 'Updating to 2.1.305...', 'Successfully updated from 2.1.304 to version 2.1.305']
        self.assertEqual(rt.read_claude_code_changes(lines), [('Claude Code', '2.1.304', '2.1.305')])

    def test_claude_code_that_was_current_has_no_change(self):
        self.assertEqual(rt.read_claude_code_changes(['Current version: 2.1.305', 'Claude Code is up to date']), [])
        self.assertEqual(rt.read_claude_code_changes([]), [])

    def test_rustup_lists_each_toolchain_that_moved(self):
        lines = ['stable-x86_64-unknown-linux-gnu updated - rustc 1.75.0 (82e1608df 2023-12-21) (from rustc 1.74.1 (a28077b28 2023-12-04))',
                 'nightly-x86_64-unknown-linux-gnu updated - rustc 1.77.0-nightly (11f32b73e 2024-01-31) (from rustc 1.76.0-nightly (3f28fe133 2023-12-18))',
                 'info: cleaning up downloads & tmp directories']
        self.assertEqual(rt.read_rustup_changes(lines), [
            ('stable-x86_64-unknown-linux-gnu', '1.74.1', '1.75.0'),
            ('nightly-x86_64-unknown-linux-gnu', '1.76.0-nightly', '1.77.0-nightly')])

    def test_rustup_toolchains_that_did_not_change_are_skipped(self):
        lines = ['  stable-x86_64-unknown-linux-gnu unchanged - rustc 1.75.0 (82e1608df 2023-12-21)',
                 'info: checking for self-update']
        self.assertEqual(rt.read_rustup_changes(lines), [])


class TestTopgradeOutputChanges(unittest.TestCase):
    def read(self, lines):
        output = rt.TopgradeOutput()
        for line in lines:
            output.feed(line)
        return output.changes()

    def test_each_step_is_read_under_its_own_heading(self):
        changes = self.read([
            heading('System update'), 'Successfully updated from 1.0 to version 2.0',  # not the Claude Code step
            heading('rustup'), 'stable-x86_64-unknown-linux-gnu updated - rustc 1.75.0 (a 1) (from rustc 1.74.1 (b 2))',
            heading('Claude Code'), 'Successfully updated from 2.1.304 to version 2.1.305',
            heading('Summary'), 'Claude Code: OK'])
        self.assertEqual(changes, [('Claude Code', '2.1.304', '2.1.305'),
                                   ('stable-x86_64-unknown-linux-gnu', '1.74.1', '1.75.0')])

    def test_the_summary_is_not_read_as_a_steps_output(self):
        self.assertEqual(self.read([heading('Claude Code'), 'x', heading('Summary'),
                                    'Successfully updated from 1 to version 2']), [])

    def test_nothing_read_means_no_changes(self):
        self.assertEqual(self.read([]), [])
        self.assertEqual(self.read([heading('Flatpak'), 'Nothing to update.']), [])

    def test_a_flood_of_output_in_a_read_step_is_cut_but_does_not_lose_the_start(self):
        lines = [heading('Claude Code'), 'Successfully updated from 1 to version 2'] + ['noise'] * 5000
        self.assertEqual(self.read(lines), [('Claude Code', '1', '2')])


if __name__ == '__main__':
    unittest.main()
