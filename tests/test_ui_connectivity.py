import unittest
from unittest import mock

from facade import rt
from helpers import TmpConfigCase


class TestOfflineBehavior(unittest.TestCase):
    class Stub:
        on_row_activated = rt.RssTray.on_row_activated
        _on_probe_result = rt.RssTray._on_probe_result
        _set_online = rt.RssTray._set_online
        _offline_tooltip = rt.RssTray._offline_tooltip
        probe_connectivity = rt.RssTray.probe_connectivity

        def __init__(self, online=True):
            self.online = online
            self._probe_failures = 0
            self._probing = True
            self._confirm_pending = True   # no real timers in unit tests
            self.offline_detail = ''
            self.offline_since = None
            self.offline_banner = mock.MagicMock()
            self.start_check_thread = mock.Mock()
            self.start_weather_fetch = mock.Mock()
            self.start_twitch_check = mock.Mock()
            self.start_youtube_check = mock.Mock()
            self.removed = []
            self.updates = 0
            self.refreshes = 0

        def _remove_unread(self, item_id):
            self.removed.append(item_id)

        def update_icon(self):
            self.updates += 1

        def refresh_list(self):
            self.refreshes += 1

    def _news_row(self):
        return mock.Mock(spec=['entry_id', 'link'], entry_id='id1', link='https://example.com/a')

    def test_online_click_opens_and_marks_read(self):
        stub = self.Stub(online=True)
        with mock.patch.object(rt.webbrowser, 'open') as wb_open:
            stub.on_row_activated(None, self._news_row())
        wb_open.assert_called_once_with('https://example.com/a')
        self.assertEqual(stub.removed, ['id1'])

    def test_offline_left_click_on_a_news_item_does_nothing(self):
        stub = self.Stub(online=False)
        with mock.patch.object(rt.webbrowser, 'open') as wb_open:
            stub.on_row_activated(None, self._news_row())
        wb_open.assert_not_called()
        self.assertEqual(stub.removed, [])
        self.assertEqual((stub.updates, stub.refreshes), (0, 0))

    def test_offline_feed_header_click_does_nothing(self):
        stub = self.Stub(online=False)
        stub.mark_feed_read = mock.Mock()
        stub.on_row_activated(None, mock.Mock(spec=['header_feed_url'], header_feed_url='https://a'))
        stub.mark_feed_read.assert_not_called()

    def test_online_feed_header_click_marks_the_feed_read(self):
        stub = self.Stub(online=True)
        stub.mark_feed_read = mock.Mock()
        stub.on_row_activated(None, mock.Mock(spec=['header_feed_url'], header_feed_url='https://a'))
        stub.mark_feed_read.assert_called_once_with('https://a')

    def test_offline_install_all_header_does_nothing(self):
        stub = self.Stub(online=False)
        stub.install_all_updates = mock.Mock()
        stub.on_row_activated(None, mock.Mock(spec=['install_all_header'], install_all_header=True))
        stub.install_all_updates.assert_not_called()

    def test_online_install_all_header_installs(self):
        stub = self.Stub(online=True)
        stub.install_all_updates = mock.Mock()
        stub.on_row_activated(None, mock.Mock(spec=['install_all_header'], install_all_header=True))
        stub.install_all_updates.assert_called_once()

    def test_offline_live_channel_rows_do_nothing(self):
        stub = self.Stub(online=False)
        stub.popup = mock.MagicMock()
        with mock.patch.object(rt, 'open_twitch_stream') as twitch, mock.patch.object(rt, 'open_youtube_stream') as yt:
            stub.on_row_activated(None, mock.Mock(spec=['twitch_channel'], twitch_channel='chan'))
            stub.on_row_activated(None, mock.Mock(spec=['youtube_channel'], youtube_channel='@h'))
        twitch.assert_not_called()
        yt.assert_not_called()
        stub.popup.hide.assert_not_called()

    def test_online_live_channel_rows_play(self):
        stub = self.Stub(online=True)
        stub.popup = mock.MagicMock()
        with mock.patch.object(rt, 'open_twitch_stream') as twitch:
            stub.on_row_activated(None, mock.Mock(spec=['twitch_channel'], twitch_channel='chan'))
        twitch.assert_called_once()

    def test_offline_right_click_still_dismisses(self):
        stub = self.Stub(online=False)
        stub.on_listbox_button_press = rt.RssTray.on_listbox_button_press.__get__(stub)
        stub.on_mark_read_clicked = rt.RssTray.on_mark_read_clicked.__get__(stub)
        listbox = mock.Mock()
        listbox.get_row_at_y.return_value = self._news_row()
        event = mock.Mock(button=3, y=10.0)
        self.assertTrue(stub.on_listbox_button_press(listbox, event))
        self.assertEqual(stub.removed, ['id1'])

    @staticmethod
    def probe(ok, detail=''):
        return {'online': ok, 'cause': 'ok' if ok else 'internet', 'detail': detail}

    def test_probe_results_drive_the_banner(self):
        stub = self.Stub(online=True)
        stub._on_probe_result(self.probe(False, 'no route'))
        stub.offline_banner.set_visible.assert_not_called()   # first failure: still online
        stub._on_probe_result(self.probe(False, 'no route'))
        self.assertFalse(stub.online)
        stub.offline_banner.set_visible.assert_called_with(True)
        stub._on_probe_result(self.probe(True))
        self.assertTrue(stub.online)
        stub.offline_banner.set_visible.assert_called_with(False)

    def test_probe_does_not_overlap_itself(self):
        stub = self.Stub()
        stub._probing = True
        with mock.patch.object(rt.threading, 'Thread') as thread:
            self.assertTrue(stub.probe_connectivity())
        thread.assert_not_called()
        stub._probing = False
        with mock.patch.object(rt.threading, 'Thread') as thread:
            stub.probe_connectivity()
        thread.assert_called_once()

    def test_banner_css_uses_the_shorthand(self):
        self.assertIn('.offline-banner { background:', rt.POPUP_CSS)


class TestReconnectCatchUp(unittest.TestCase):
    def test_coming_back_online_triggers_every_refresh_once(self):
        stub = TestOfflineBehavior.Stub(online=False)
        stub._set_online(True)
        for name in ('start_check_thread', 'start_weather_fetch', 'start_twitch_check', 'start_youtube_check'):
            getattr(stub, name).assert_called_once()

    def test_going_offline_or_staying_online_triggers_nothing(self):
        stub = TestOfflineBehavior.Stub(online=True)
        stub._set_online(True)
        stub._set_online(False)
        stub.start_check_thread.assert_not_called()
        stub.start_weather_fetch.assert_not_called()


class TestImmediateProbes(unittest.TestCase):
    class Stub:
        request_probe = rt.RssTray.request_probe
        note_request_failure = rt.RssTray.note_request_failure
        _on_probe_result = rt.RssTray._on_probe_result
        _confirm_probe = rt.RssTray._confirm_probe
        _set_online = rt.RssTray._set_online
        _offline_tooltip = rt.RssTray._offline_tooltip

        def __init__(self, online=True):
            self.online = online
            self._probe_failures = 0
            self._probing = False
            self._confirm_pending = False
            self._last_probe_request = 0.0
            self.offline_detail = ''
            self.offline_since = None
            self.offline_banner = None
            self.probes = 0

        def probe_connectivity(self):
            self.probes += 1
            return True

    def test_request_failure_probes_right_away_on_the_main_loop(self):
        stub = self.Stub()
        with mock.patch.object(rt.GLib, 'idle_add') as idle:
            stub.note_request_failure()
        idle.assert_called_once_with(stub.request_probe)

    def test_failure_probes_are_throttled(self):
        stub = self.Stub()
        with mock.patch.object(rt.time_module, 'monotonic', side_effect=[100.0, 101.0, 106.0]):
            stub.request_probe()
            stub.request_probe()   # 1 s later: ignored
            stub.request_probe()   # 6 s after the first: allowed
        self.assertEqual(stub.probes, 2)

    def test_a_failed_probe_while_online_schedules_one_quick_confirmation(self):
        stub = self.Stub(online=True)
        failure = {'online': False, 'cause': 'internet', 'detail': 'x'}
        with mock.patch.object(rt.GLib, 'timeout_add_seconds') as timer:
            stub._on_probe_result(failure)
            stub._on_probe_result(failure)   # already pending: not scheduled twice
        timer.assert_called_once_with(rt.CONFIRM_PROBE_SECONDS, stub._confirm_probe)
        # (a second failure flipped it offline)
        self.assertFalse(stub.online)

    def test_confirmation_runs_a_probe_and_clears_the_pending_flag(self):
        stub = self.Stub()
        stub._confirm_pending = True
        stub._confirm_probe()
        self.assertEqual(stub.probes, 1)
        self.assertFalse(stub._confirm_pending)

    def test_no_confirmation_needed_for_a_healthy_probe(self):
        stub = self.Stub()
        with mock.patch.object(rt.GLib, 'timeout_add_seconds') as timer:
            stub._on_probe_result({'online': True, 'cause': 'ok', 'detail': ''})
        timer.assert_not_called()

    def test_tooltip_shows_the_cause_and_since_when(self):
        stub = self.Stub(online=True)
        stub.offline_banner = mock.MagicMock()
        stub.offline_detail = 'DNS lookups fail'
        with mock.patch.object(rt.time_module, 'time', return_value=rt.datetime(2026, 10, 5, 3, 14).timestamp()), \
                mock.patch.object(rt, '_log') as log:
            stub._set_online(False)
        text = stub.offline_banner.set_tooltip_text.call_args[0][0]
        self.assertIn('DNS lookups fail', text)
        self.assertIn('03:14', text)
        self.assertIn('DNS lookups fail', log.call_args[0][0])   # also in the debug log

    def test_tooltip_is_cleared_when_back_online(self):
        stub = self.Stub(online=False)
        stub.offline_banner = mock.MagicMock()
        for name in ('start_check_thread', 'start_weather_fetch', 'start_twitch_check', 'start_youtube_check'):
            setattr(stub, name, mock.Mock())
        with mock.patch.object(rt, '_log'):
            stub._set_online(True)
        stub.offline_banner.set_tooltip_text.assert_called_with(None)
        self.assertIsNone(stub.offline_since)


class TestNetworkReset(TmpConfigCase):
    def test_reset_command_from_config(self):
        self.assertEqual(rt.load_network_reset_command(), '')
        self.conf('[network]\nreset = ~/.local/bin/reset-network.sh --force\n')
        self.assertEqual(rt.load_network_reset_command(), '~/.local/bin/reset-network.sh --force')

    def test_click_runs_the_command_and_locks_the_button_for_a_while(self):
        self.conf('[network]\nreset=reset-network.sh\n')

        class Stub:
            on_network_reset_clicked = rt.RssTray.on_network_reset_clicked
            _end_network_reset = rt.RssTray._end_network_reset
            _probe_after_reset = rt.RssTray._probe_after_reset

            def __init__(self):
                self.reset_btn = mock.MagicMock()
                self.offline_banner_label = mock.MagicMock()
                self.probe_connectivity = mock.Mock()
        stub = Stub()
        with mock.patch.object(rt, 'run_launcher_command') as run, mock.patch.object(rt.GLib, 'timeout_add_seconds') as timer:
            stub.on_network_reset_clicked(None)
        run.assert_called_once_with('reset-network.sh')
        stub.reset_btn.set_sensitive.assert_called_with(False)
        self.assertIn('Resetting', stub.offline_banner_label.set_text.call_args[0][0])
        self.assertEqual({c.args[0] for c in timer.call_args_list}, {6, rt.NETWORK_RESET_COOLDOWN_SECONDS})
        stub._end_network_reset()
        stub.reset_btn.set_sensitive.assert_called_with(True)
        stub.offline_banner_label.set_text.assert_called_with(rt.OFFLINE_BANNER_TEXT)

    def test_without_a_command_the_click_does_nothing(self):
        class Stub:
            on_network_reset_clicked = rt.RssTray.on_network_reset_clicked
        with mock.patch.object(rt, 'run_launcher_command') as run:
            Stub().on_network_reset_clicked(None)
        run.assert_not_called()

    def test_offline_banner_css_keeps_button_text_dark(self):
        self.assertIn('.offline-banner button label { color: #000000', rt.POPUP_CSS)


if __name__ == '__main__':
    unittest.main()
