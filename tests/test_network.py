import unittest
from unittest import mock

from facade import rt


class TestConnectivityState(unittest.TestCase):
    def test_success_is_online_immediately_and_resets_failures(self):
        self.assertEqual(rt.next_connectivity(False, 5, True), (True, 0))

    def test_one_failed_probe_does_not_go_offline(self):
        self.assertEqual(rt.next_connectivity(True, 0, False), (True, 1))

    def test_consecutive_failures_go_offline_and_stay_offline(self):
        online, failures = True, 0
        for _ in range(rt.OFFLINE_AFTER_FAILED_PROBES):
            online, failures = rt.next_connectivity(online, failures, False)
        self.assertFalse(online)
        self.assertEqual(rt.next_connectivity(online, failures, False)[0], False)


class TestRunWhenOnline(unittest.TestCase):
    def test_waits_in_the_background_until_online_then_runs(self):
        done = rt.threading.Event()
        with mock.patch.object(rt, 'is_online', side_effect=[False, False, True]) as probe, \
                mock.patch.object(rt.time_module, 'sleep') as sleep:
            rt.run_when_online(done.set)
            self.assertTrue(done.wait(timeout=5))
        self.assertEqual(probe.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(rt.NETWORK_RETRY_SECONDS)

    def test_main_thread_returns_immediately(self):
        class Stub:
            initial_check = rt.RssTray.initial_check
            initial_twitch_check = rt.RssTray.initial_twitch_check
            initial_youtube_check = rt.RssTray.initial_youtube_check
            initial_weather_check = rt.RssTray.initial_weather_check
        stub = Stub()
        stub.start_check_thread = stub.start_twitch_check = stub.start_youtube_check = stub.start_weather_fetch = mock.Mock()
        with mock.patch.object(rt, 'run_when_online') as run, mock.patch.object(rt, 'is_online') as probe:
            for name in ('initial_check', 'initial_twitch_check', 'initial_youtube_check', 'initial_weather_check'):
                self.assertFalse(getattr(stub, name)())
        self.assertEqual(run.call_count, 4)
        probe.assert_not_called()  # no blocking network probe on the calling (GTK) thread


class TestConnectivityDiagnosis(unittest.TestCase):
    def diagnose(self, tcp=True, dns=True, gateway='192.168.1.1', answers=True):
        with mock.patch.object(rt, '_tcp_reachable', return_value=tcp), \
                mock.patch.object(rt, '_dns_ok', return_value=dns), \
                mock.patch.object(rt, '_default_gateway', return_value=gateway), \
                mock.patch.object(rt, '_gateway_answers', return_value=answers):
            return rt.diagnose_connectivity()

    def test_everything_works(self):
        self.assertEqual(self.diagnose(), {'online': True, 'cause': 'ok', 'detail': ''})

    def test_dns_only_outage_counts_as_offline(self):
        result = self.diagnose(tcp=True, dns=False)
        self.assertFalse(result['online'])
        self.assertEqual(result['cause'], 'dns')
        self.assertIn('DNS', result['detail'])

    def test_router_answers_but_the_internet_is_down(self):
        result = self.diagnose(tcp=False, answers=True)
        self.assertEqual((result['online'], result['cause']), (False, 'internet'))
        self.assertIn('192.168.1.1', result['detail'])

    def test_router_not_answering(self):
        result = self.diagnose(tcp=False, answers=False)
        self.assertEqual(result['cause'], 'gateway')
        self.assertIn('does not answer', result['detail'])

    def test_no_default_route_means_the_link_is_down(self):
        result = self.diagnose(tcp=False, gateway=None)
        self.assertEqual(result['cause'], 'no-route')

    def test_dns_is_not_even_tried_when_nothing_is_reachable(self):
        with mock.patch.object(rt, '_tcp_reachable', return_value=False), \
                mock.patch.object(rt, '_dns_ok') as dns, \
                mock.patch.object(rt, '_default_gateway', return_value=None):
            rt.diagnose_connectivity()
        dns.assert_not_called()

    def test_is_online_follows_the_diagnosis(self):
        with mock.patch.object(rt, 'diagnose_connectivity', return_value={'online': False, 'cause': 'dns', 'detail': ''}):
            self.assertFalse(rt.is_online())


class TestConnectivityProbes(unittest.TestCase):
    ROUTE = ('Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n'
             'wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\n'
             'wlan0\t0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\n')

    def test_default_gateway_is_decoded_from_the_route_table(self):
        self.assertEqual(rt._parse_default_gateway(self.ROUTE), '192.168.1.1')

    def test_no_default_route(self):
        table = self.ROUTE.splitlines()[0] + '\n' + self.ROUTE.splitlines()[2] + '\n'
        self.assertIsNone(rt._parse_default_gateway(table))

    def test_down_default_route_is_ignored(self):
        self.assertIsNone(rt._parse_default_gateway(self.ROUTE.replace('0003', '0001', 1)))

    def test_dns_ok_when_a_hostname_resolves(self):
        with mock.patch.object(rt.socket, 'getaddrinfo', side_effect=[OSError(), [('x',)]]):
            self.assertTrue(rt._dns_ok())  # the first name failing is fine, the second resolves

    def test_dns_fails_when_nothing_resolves(self):
        with mock.patch.object(rt.socket, 'getaddrinfo', side_effect=OSError()):
            self.assertFalse(rt._dns_ok())

    def test_a_hanging_resolver_gives_up_after_the_timeout(self):
        release = rt.threading.Event()
        self.addCleanup(release.set)
        with mock.patch.object(rt.socket, 'getaddrinfo', side_effect=lambda *a, **k: release.wait(10)):
            started = rt.time_module.monotonic()
            self.assertFalse(rt._dns_ok(timeout=0.2))
        self.assertLess(rt.time_module.monotonic() - started, 2)

    def test_gateway_that_refuses_connections_is_alive(self):
        with mock.patch.object(rt.socket, 'create_connection', side_effect=ConnectionRefusedError()):
            self.assertTrue(rt._gateway_answers('192.168.1.1'))

    def test_gateway_that_times_out_everywhere_does_not_answer(self):
        with mock.patch.object(rt.socket, 'create_connection', side_effect=OSError('timed out')) as connect:
            self.assertFalse(rt._gateway_answers('192.168.1.1'))
        self.assertEqual(connect.call_count, len(rt.GATEWAY_PORTS))

    def test_tcp_probe_succeeds_on_any_target(self):
        calls = []

        def connect(addr, timeout):
            calls.append(addr)
            if len(calls) < 3:
                raise OSError()
            return mock.MagicMock()
        with mock.patch.object(rt.socket, 'create_connection', side_effect=connect):
            self.assertTrue(rt._tcp_reachable())
        self.assertEqual(len(calls), 3)


class TestLiveRowStats(unittest.TestCase):
    def build(self, entry, name=None):
        """Runs the real row builder with GTK widgets mocked, collecting the labels' markup."""
        class Stub:
            _build_live_row = rt.RssTray._build_live_row
        labels = []

        def make_label(*a, **k):
            label = mock.MagicMock()
            label.set_markup.side_effect = lambda markup: labels.append(markup)
            return label
        with mock.patch.object(rt.Gtk, 'Label', side_effect=make_label), \
                mock.patch.object(rt.Gtk, 'Box'), mock.patch.object(rt.Gtk, 'ListBoxRow'), \
                mock.patch.object(rt.GLib, 'markup_escape_text', side_effect=lambda x: x), \
                mock.patch.object(rt, 'is_stream_playing', return_value=False), \
                mock.patch.object(rt.time_module, 'time', return_value=1_000_000.0):
            self.row = Stub()._build_live_row(entry, 'Twitch', '#000000', name)
        return labels

    def test_row_shows_user_category_viewers_and_elapsed(self):
        text = ' '.join(self.build({'channel': 'channel1', 'category': 'Chess', 'title': 'T',
                                    'viewers': 283, 'started': 1_000_000.0 - (3 * 3600 + 21 * 60)}))
        self.assertIn('channel1', text)
        self.assertIn('Chess', text)
        self.assertIn('- 283 - 3h 21min', text)

    def test_the_display_name_replaces_the_channel_in_the_row(self):
        text = ' '.join(self.build({'channel': 'channel1', 'category': 'Chess', 'viewers': 5}, name='Some Name'))
        self.assertIn('<b>Some Name</b> - Chess', text)
        self.assertNotIn('channel1', text)

    def test_without_a_display_name_the_channel_is_shown(self):
        self.assertIn('<b>channel1</b>', ' '.join(self.build({'channel': 'channel1'})))

    def test_the_row_remembers_the_name_for_the_player_window(self):
        self.build({'channel': 'channel1'}, name='Some Name')
        self.assertEqual(self.row.live_name, 'Some Name')
        self.build({'channel': 'channel1'})
        self.assertIsNone(self.row.live_name)

    def test_row_without_stats_has_no_dangling_separator(self):
        labels = self.build({'channel': 'channel1', 'category': 'Chess', 'title': 'T'})
        self.assertEqual(len(labels), 2)            # the bullet and the name/category only
        self.assertNotIn('- 283', ' '.join(labels))


if __name__ == '__main__':
    unittest.main()
