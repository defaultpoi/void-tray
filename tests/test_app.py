import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from facade import rt
from helpers import TmpConfigCase


class TestRunUpdateAll(unittest.TestCase):
    class Stub:
        _run_update_all = rt.RssTray._run_update_all

        def __init__(self):
            self._xbps_lock = rt.threading.Lock()
            self.statuses = []

        def _set_status(self, pkg, status):
            self.statuses.append((pkg, status))

        def _finalize_package_removal(self, pkg):
            pass

        def _end_install(self):
            pass

    def _run(self, pkgnames, versions, returncode=0):
        """versions: successive get_installed_version() results, in call order."""
        stub = self.Stub()
        proc = mock.MagicMock()
        proc.stdout = iter([])
        proc.returncode = returncode
        with mock.patch.object(rt, 'get_installed_version', side_effect=versions), \
                mock.patch.object(rt.subprocess, 'Popen', return_value=proc) as popen, \
                mock.patch.object(rt.threading, 'Timer'), \
                mock.patch.object(rt, 'PRIVILEGE_CMD', []):
            stub._run_update_all(pkgnames)
        return stub, popen

    def test_dependency_updated_by_an_earlier_package_is_done_not_failed(self):
        # start: mesa v1, libfoo v1 | mesa: before v1, after v2 | libfoo: before v2 (changed by mesa)
        stub, popen = self._run(['mesa', 'libfoo'], ['v1', 'v1', 'v1', 'v2', 'v2'])
        self.assertEqual(popen.call_count, 1)  # xbps never ran for libfoo
        self.assertIn(('mesa', 'Done'), stub.statuses)
        self.assertIn(('libfoo', 'Done'), stub.statuses)
        self.assertNotIn(('libfoo', 'Failed'), stub.statuses)

    def test_package_that_really_did_not_update_is_still_failed(self):
        # held/blocked package: xbps exits 0 but the version never changes
        stub, popen = self._run(['held'], ['v1', 'v1', 'v1'])
        self.assertIn(('held', 'Failed'), stub.statuses)

    def test_nonzero_exit_is_failed(self):
        stub, _ = self._run(['pkg'], ['v1', 'v1', 'v2'], returncode=1)
        self.assertIn(('pkg', 'Failed'), stub.statuses)

    def test_normal_update_is_done(self):
        stub, _ = self._run(['pkg'], ['v1', 'v1', 'v2'])
        self.assertIn(('pkg', 'Done'), stub.statuses)


class TestUnixSignalAdd(unittest.TestCase):
    def test_prefers_glibunix_signal_add(self):
        unix = mock.Mock(spec=['signal_add', 'signal_add_full'])
        with mock.patch.object(rt, 'GLibUnix', unix), mock.patch.object(rt.GLib, 'unix_signal_add') as old:
            rt._unix_signal_add(15, print)
        unix.signal_add.assert_called_once_with(rt.GLib.PRIORITY_DEFAULT, 15, print)
        unix.signal_add_full.assert_not_called()
        old.assert_not_called()

    def test_uses_signal_add_full_where_plain_one_is_not_exposed(self):
        unix = mock.Mock(spec=['signal_add_full'])
        with mock.patch.object(rt, 'GLibUnix', unix), mock.patch.object(rt.GLib, 'unix_signal_add') as old:
            rt._unix_signal_add(15, print)
        unix.signal_add_full.assert_called_once_with(rt.GLib.PRIORITY_DEFAULT, 15, print)
        old.assert_not_called()

    def test_falls_back_to_the_old_glib_function(self):
        with mock.patch.object(rt, 'GLibUnix', None), mock.patch.object(rt.GLib, 'unix_signal_add') as old:
            rt._unix_signal_add(15, print)
        old.assert_called_once_with(rt.GLib.PRIORITY_DEFAULT, 15, print)

    def test_namespace_without_either_function_falls_back_too(self):
        with mock.patch.object(rt, 'GLibUnix', mock.Mock(spec=[])), \
                mock.patch.object(rt.GLib, 'unix_signal_add') as old:
            rt._unix_signal_add(2, print)
        old.assert_called_once()


class TestFailureTriggers(TmpConfigCase):
    def test_a_failed_twitch_check_asks_for_a_probe(self):
        self.conf('[twitch]\nchan\n')

        class Stub:
            _check_twitch_guarded = rt.RssTray._check_twitch_guarded

            def __init__(self):
                self.lock = rt.threading.Lock()
                self.state = {}
                self._twitch_lock = rt.threading.Lock()
                self._twitch_lock.acquire()
                self.note_request_failure = mock.Mock()
                self._on_twitch_checked = mock.Mock()
        stub = Stub()
        with mock.patch.object(rt, 'check_twitch_live_channels', return_value=None):
            stub._check_twitch_guarded()
        stub.note_request_failure.assert_called_once()

    def test_a_successful_twitch_check_does_not(self):
        self.conf('[twitch]\nchan\n')

        class Stub:
            _check_twitch_guarded = rt.RssTray._check_twitch_guarded

            def __init__(self):
                self.lock = rt.threading.Lock()
                self.state = {}
                self._twitch_lock = rt.threading.Lock()
                self._twitch_lock.acquire()
                self.note_request_failure = mock.Mock()
                self._on_twitch_checked = mock.Mock()
        stub = Stub()
        with mock.patch.object(rt, 'check_twitch_live_channels', return_value={}), mock.patch.object(rt.GLib, 'idle_add'):
            stub._check_twitch_guarded()
        stub.note_request_failure.assert_not_called()

    def test_weather_failure_only_counts_when_weather_is_configured(self):
        class Stub:
            _fetch_weather_bg = rt.RssTray._fetch_weather_bg

            def __init__(self):
                self.note_request_failure = mock.Mock()
                self._fetch_alerts_bg = lambda: []
                self._on_weather_fetched = mock.Mock()
        self.conf('[weather]\n10.0|20.0\n')
        configured = Stub()
        with mock.patch.object(rt, 'fetch_weather', return_value=None), mock.patch.object(rt.GLib, 'idle_add'):
            configured._fetch_weather_bg()
        configured.note_request_failure.assert_called_once()
        self.conf('[feeds]\n')
        unconfigured = Stub()
        with mock.patch.object(rt, 'fetch_weather', return_value=None), mock.patch.object(rt.GLib, 'idle_add'):
            unconfigured._fetch_weather_bg()
        unconfigured.note_request_failure.assert_not_called()


class TestUpdatesDue(TmpConfigCase):
    class Stub:
        check_updates_if_due = rt.RssTray.check_updates_if_due

        def __init__(self, last_checked):
            self.lock = rt.threading.Lock()
            self._xbps_lock = rt.threading.Lock()
            self.state = {'updates_last_checked': last_checked}

    def _scans(self, last_checked_ago_hours, force=False):
        now = 1_000_000_000.0
        stub = self.Stub(now - last_checked_ago_hours * 3600)
        with mock.patch.object(rt.time_module, 'time', return_value=now), \
                mock.patch.object(rt, 'list_all_updates', return_value=None) as scan, \
                mock.patch.object(rt, 'save_state'):
            stub.check_updates_if_due(force=force)
        return scan.call_count

    def test_default_is_twice_a_day(self):
        self.assertEqual(self._scans(11.9), 0)
        self.assertEqual(self._scans(12.1), 1)

    def test_configured_interval_is_used(self):
        self.conf('[updates]\ninterval=24\n')
        self.assertEqual(self._scans(13), 0)
        self.assertEqual(self._scans(24.1), 1)

    def test_force_ignores_the_interval(self):
        self.assertEqual(self._scans(0.1, force=True), 1)


class TestMpvIpc(unittest.TestCase):
    """Runs _mpv_ipc against a real unix socket standing in for mpv."""

    def _serve(self, replies):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, 'mpv.sock')
        self.received = []
        server = rt.socket.socket(rt.socket.AF_UNIX, rt.socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        self.addCleanup(server.close)

        def run():
            conn, _ = server.accept()
            buf = b''
            for reply in replies:
                while b'\n' not in buf:
                    buf += conn.recv(4096)
                line, buf = buf.split(b'\n', 1)
                self.received.append(json.loads(line))
                conn.sendall(b'{"event":"noise"}\n' + (json.dumps(reply) + '\n').encode())
            conn.close()
        rt.threading.Thread(target=run, daemon=True).start()
        patcher = mock.patch.object(rt, 'MPV_IPC_SOCKET', path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_success_replies(self):
        self._serve([{'error': 'success'}, {'error': 'success'}])
        self.assertTrue(rt._mpv_ipc([['set_property', 'a', 'b'], ['loadfile', 'u', 'replace']]))
        self.assertEqual(self.received[1], {'command': ['loadfile', 'u', 'replace']})

    def test_error_reply_is_false(self):
        self._serve([{'error': 'property unavailable'}])
        self.assertFalse(rt._mpv_ipc([['get_property', 'x']]))

    def test_no_socket_is_false(self):
        with mock.patch.object(rt, 'MPV_IPC_SOCKET', '/nonexistent/none.sock'):
            self.assertFalse(rt._mpv_ipc([['get_property', 'pid']]))


class TestUpdateScanVsInstall(TestUpdatesDue):
    def test_scan_is_skipped_while_an_install_holds_the_xbps_lock(self):
        stub = self.Stub(0)
        stub._xbps_lock.acquire()
        with mock.patch.object(rt.time_module, 'time', return_value=1_000_000_000.0), \
                mock.patch.object(rt, 'list_all_updates') as scan, mock.patch.object(rt, 'save_state'):
            stub.check_updates_if_due(force=True)
        scan.assert_not_called()
        self.assertEqual(stub.state.get('updates_fail_count', 0), 0)  # skipped, not failed

    def test_lock_is_released_after_a_scan(self):
        stub = self.Stub(0)
        with mock.patch.object(rt.time_module, 'time', return_value=1_000_000_000.0), \
                mock.patch.object(rt, 'list_all_updates', return_value=[]), mock.patch.object(rt, 'save_state'):
            stub.check_updates_if_due(force=True)
        self.assertTrue(stub._xbps_lock.acquire(blocking=False))


class TestInstallStatusUpdates(TestRunUpdateAll):
    def test_repeated_phases_are_not_reported_again(self):
        stub = self.Stub()
        proc = mock.MagicMock()
        proc.stdout = iter(['[*] Downloading packages\n', 'file line\n', '[*] Collecting package files\n',
                            '[*] Unpacking packages\n', '[*] Configuring unpacked packages\n'])
        proc.returncode = 0
        with mock.patch.object(rt, 'get_installed_version', side_effect=['v1', 'v1', 'v2']), \
                mock.patch.object(rt.subprocess, 'Popen', return_value=proc), \
                mock.patch.object(rt.threading, 'Timer'), mock.patch.object(rt, 'PRIVILEGE_CMD', []):
            stub._run_update_all(['pkg'])
        statuses = [s for p, s in stub.statuses]
        self.assertEqual(statuses, ['Installing…', 'Downloading…', 'Installing…', 'Done'])


if __name__ == '__main__':
    unittest.main()
