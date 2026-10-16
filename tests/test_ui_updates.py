import threading
import unittest
from unittest import mock

from facade import rt


class FakeLock:
    """An RLock stand-in whose non-blocking acquire can be made to fail."""

    def __init__(self, free=True):
        self.free = free
        self.released = 0

    def acquire(self, blocking=True):
        return self.free

    def release(self):
        self.released += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestCheckOrphans(unittest.TestCase):
    class Stub:
        check_orphans_if_due = rt.RssTray.check_orphans_if_due
        _orphans_changed = rt.RssTray._orphans_changed

        def __init__(self, orphans=(), last_checked=0, lock_free=True):
            self.lock = threading.RLock()
            self._xbps_lock = FakeLock(lock_free)
            self.state = {'orphans': list(orphans), 'orphans_last_checked': last_checked}
            self.popup = mock.MagicMock()
            self.refresh_list = mock.Mock()

    def run_check(self, stub, found, nudge=True, force=False, now=10000.0, interval=3600):
        with mock.patch.object(rt, 'load_orphan_nudge', return_value=nudge), \
                mock.patch.object(rt, 'load_updates_check_interval', return_value=interval), \
                mock.patch.object(rt, 'list_orphans', return_value=found) as query, \
                mock.patch.object(rt, 'save_state') as save, \
                mock.patch.object(rt.time_module, 'time', return_value=now), \
                mock.patch.object(rt, 'GLib') as glib:
            stub.check_orphans_if_due(force=force)
        return query, save, glib

    def test_a_new_list_is_stored_with_the_time_and_announced(self):
        stub = self.Stub()
        query, save, glib = self.run_check(stub, ['libfoo', 'libbar'])
        self.assertEqual(stub.state['orphans'], ['libfoo', 'libbar'])
        self.assertEqual(stub.state['orphans_last_checked'], 10000.0)
        save.assert_called_once_with(stub.state)
        glib.idle_add.assert_called_once_with(stub._orphans_changed)

    def test_an_unchanged_list_does_not_rebuild_the_popup(self):
        stub = self.Stub(orphans=['libfoo'])
        _, _, glib = self.run_check(stub, ['libfoo'])
        glib.idle_add.assert_not_called()

    def test_not_due_means_no_query_unless_forced(self):
        stub = self.Stub(last_checked=900.0)
        query, _, _ = self.run_check(stub, ['x'], now=1000.0, interval=3600)
        query.assert_not_called()
        query, _, _ = self.run_check(stub, ['x'], now=1000.0, interval=3600, force=True)
        query.assert_called_once()

    def test_due_again_after_the_interval(self):
        stub = self.Stub(last_checked=1000.0)
        query, _, _ = self.run_check(stub, ['x'], now=1000.0 + 3600, interval=3600)
        query.assert_called_once()

    def test_switched_off_never_queries(self):
        stub = self.Stub()
        query, save, _ = self.run_check(stub, ['x'], nudge=False, force=True)
        query.assert_not_called()
        save.assert_not_called()

    def test_a_running_install_or_removal_skips_the_scan(self):
        stub = self.Stub(lock_free=False)
        query, save, _ = self.run_check(stub, ['x'], force=True)
        query.assert_not_called()
        save.assert_not_called()

    def test_the_lock_is_released_after_the_query(self):
        stub = self.Stub()
        self.run_check(stub, [])
        self.assertEqual(stub._xbps_lock.released, 1)

    def test_a_failed_query_keeps_the_previous_list_and_waits_the_interval(self):
        stub = self.Stub(orphans=['libfoo'])
        _, _, glib = self.run_check(stub, None, force=True)
        self.assertEqual(stub.state['orphans'], ['libfoo'])
        self.assertEqual(stub.state['orphans_last_checked'], 10000.0)
        glib.idle_add.assert_not_called()

    def test_the_popup_is_rebuilt_only_while_it_is_showing(self):
        stub = self.Stub()
        stub.popup.get_visible.return_value = True
        self.assertFalse(stub._orphans_changed())
        stub.refresh_list.assert_called_once_with(keep_open=True)
        stub.refresh_list.reset_mock()
        stub.popup.get_visible.return_value = False
        stub._orphans_changed()
        stub.refresh_list.assert_not_called()
        stub.popup = None
        stub._orphans_changed()
        stub.refresh_list.assert_not_called()


class TestRemoveOrphans(unittest.TestCase):
    class Stub:
        remove_orphans = rt.RssTray.remove_orphans
        _run_orphan_removal = rt.RssTray._run_orphan_removal
        _finish_orphan_removal = rt.RssTray._finish_orphan_removal
        _clear_orphan_status = rt.RssTray._clear_orphan_status
        _end_install = rt.RssTray._end_install

        def __init__(self, active=0):
            self.lock = threading.RLock()
            self._xbps_lock = FakeLock()
            self.active_installs = active
            self.orphan_status = None
            self.state = {'orphans': ['libfoo', 'libbar']}
            self.popup = mock.MagicMock()
            self.update_icon = mock.Mock()
            self.refresh_list = mock.Mock()
            self.check_orphans_if_due = mock.Mock()

        def _begin_install(self):
            self.active_installs += 1

    def run_removal(self, stub, clean_cache=False, results=((0, []),), remaining=()):
        with mock.patch.object(rt, '_run_install_command', side_effect=list(results)) as run, \
                mock.patch.object(rt, 'list_orphans', return_value=None if remaining is None else list(remaining)), \
                mock.patch.object(rt, 'save_state') as save, \
                mock.patch.object(rt, '_log') as log, \
                mock.patch.object(rt, 'GLib') as glib, \
                mock.patch.object(rt, 'PRIVILEGE_CMD', ['sudo', '-n']):
            stub._run_orphan_removal(clean_cache)
        return run, save, log, glib

    def test_it_runs_exactly_the_orphan_removal_command_a_sudoers_rule_can_allow(self):
        run, _, _, _ = self.run_removal(self.Stub())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[0][0], ['sudo', '-n', 'xbps-remove', '-o', '-y'])

    def test_cleaning_the_cache_adds_a_second_exact_command(self):
        run, _, _, _ = self.run_removal(self.Stub(), clean_cache=True, results=[(0, []), (0, [])])
        self.assertEqual([c[0][0] for c in run.call_args_list],
                         [['sudo', '-n', 'xbps-remove', '-o', '-y'], ['sudo', '-n', 'xbps-remove', '-O', '-y']])

    def test_success_stores_what_is_left_and_reports_done(self):
        stub = self.Stub()
        _, save, _, glib = self.run_removal(stub, remaining=['libleft'])
        self.assertEqual(stub.state['orphans'], ['libleft'])
        save.assert_called_once()
        glib.idle_add.assert_called_once_with(stub._finish_orphan_removal, 'Done')

    def test_a_failed_removal_stops_there_logs_the_output_and_reports_failed(self):
        stub = self.Stub()
        run, _, log, glib = self.run_removal(stub, clean_cache=True, results=[(1, ['sudo: a password is required'])],
                                             remaining=['libfoo', 'libbar'])
        self.assertEqual(run.call_count, 1)  # the cache cleanup is not attempted after a failure
        self.assertIn('password is required', log.call_args[0][0])
        glib.idle_add.assert_called_once_with(stub._finish_orphan_removal, 'Failed')

    def test_an_unreadable_list_afterwards_keeps_the_old_one(self):
        stub = self.Stub()
        self.run_removal(stub, remaining=None)
        self.assertEqual(stub.state['orphans'], ['libfoo', 'libbar'])

    def test_the_removal_holds_the_xbps_lock_like_installs_do(self):
        stub = self.Stub()
        stub._xbps_lock = mock.MagicMock()
        self.run_removal(stub)
        stub._xbps_lock.__enter__.assert_called_once()

    def test_starting_marks_the_row_and_runs_in_a_thread(self):
        stub = self.Stub()
        with mock.patch.object(rt.threading, 'Thread') as thread:
            stub.remove_orphans(clean_cache=True)
        self.assertEqual(stub.orphan_status, 'Removing…')
        self.assertEqual(stub.active_installs, 1)
        thread.assert_called_once_with(target=stub._run_orphan_removal, args=(True,), daemon=True)
        thread.return_value.start.assert_called_once()

    def test_nothing_starts_while_an_install_or_removal_is_running(self):
        stub = self.Stub(active=1)
        with mock.patch.object(rt.threading, 'Thread') as thread:
            stub.remove_orphans()
        thread.assert_not_called()
        self.assertIsNone(stub.orphan_status)

    def test_finishing_shows_the_result_then_clears_it(self):
        stub = self.Stub(active=1)
        with mock.patch.object(rt, 'GLib') as glib:
            self.assertFalse(stub._finish_orphan_removal('Done'))
        self.assertEqual(stub.active_installs, 0)
        self.assertEqual(stub.orphan_status, 'Done')
        stub.update_icon.assert_called_once()
        stub.refresh_list.assert_called_once()
        glib.timeout_add.assert_called_once_with(rt.ORPHAN_STATUS_CLEAR_MS, stub._clear_orphan_status)
        stub.refresh_list.reset_mock()
        self.assertFalse(stub._clear_orphan_status())
        self.assertIsNone(stub.orphan_status)
        stub.refresh_list.assert_called_once()

    def test_an_install_that_ends_looks_for_orphans_it_may_have_left(self):
        stub = self.Stub(active=1)
        with mock.patch.object(rt.threading, 'Thread') as thread:
            stub._end_install()
        thread.assert_called_once_with(target=stub.check_orphans_if_due, kwargs={'force': True}, daemon=True)
        thread.return_value.start.assert_called_once()
        self.assertEqual(stub.active_installs, 0)


if __name__ == '__main__':
    unittest.main()
