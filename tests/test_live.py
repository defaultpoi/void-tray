import json
import unittest
from unittest import mock

from facade import rt


class TestTwitchLive(unittest.TestCase):
    def _resp(self, payload):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return resp

    def test_no_channels_no_request(self):
        with mock.patch.object(rt.urllib.request, 'urlopen') as urlopen:
            self.assertEqual(rt.check_twitch_live_channels([]), {})
            urlopen.assert_not_called()

    def test_live_offline_and_missing_user(self):
        payload = [
            {'data': {'user': {'stream': {'type': 'live', 'title': 'hi', 'game': {'name': 'Chess'},
                                          'viewersCount': 283, 'createdAt': '2026-10-11T10:00:00Z'}}}},
            {'data': {'user': {'stream': None}}},
            {'data': {'user': None}},
        ]
        with mock.patch.object(rt.urllib.request, 'urlopen', return_value=self._resp(payload)):
            self.assertEqual(rt.check_twitch_live_channels(['a', 'b', 'c']),
                             {'a': {'title': 'hi', 'category': 'Chess', 'viewers': 283,
                                    'started': rt.parse_iso_timestamp('2026-10-11T10:00:00Z')}})

    def test_live_without_game_has_empty_category(self):
        payload = [{'data': {'user': {'stream': {'type': 'live', 'title': 'hi', 'game': None}}}}]
        with mock.patch.object(rt.urllib.request, 'urlopen', return_value=self._resp(payload)):
            self.assertEqual(rt.check_twitch_live_channels(['a']),
                             {'a': {'title': 'hi', 'category': '', 'viewers': None, 'started': None}})

    def test_query_requests_the_game_name(self):
        with mock.patch.object(rt.urllib.request, 'urlopen',
                               return_value=self._resp([{'data': {'user': None}}])) as urlopen:
            rt.check_twitch_live_channels(['a'])
        query = urlopen.call_args[0][0].data.decode()
        self.assertIn('game { name }', query)
        self.assertIn('viewersCount', query)
        self.assertIn('createdAt', query)

    def test_nobody_live_is_empty_dict_not_none(self):
        payload = [{'data': {'user': {'stream': None}}}]
        with mock.patch.object(rt.urllib.request, 'urlopen', return_value=self._resp(payload)):
            self.assertEqual(rt.check_twitch_live_channels(['a']), {})

    def test_request_error_is_none(self):
        with mock.patch.object(rt.urllib.request, 'urlopen', side_effect=OSError):
            self.assertIsNone(rt.check_twitch_live_channels(['a']))

    def test_non_list_response_is_none(self):
        with mock.patch.object(rt.urllib.request, 'urlopen', return_value=self._resp({'error': 1})):
            self.assertIsNone(rt.check_twitch_live_channels(['a']))


class TestTwitchBatching(unittest.TestCase):
    def test_channels_are_split_into_batches(self):
        sizes = []

        def batch(channels):
            sizes.append(len(channels))
            return {c: {'title': '', 'category': ''} for c in channels[:1]}
        with mock.patch.object(rt, '_check_twitch_batch', side_effect=batch):
            live = rt.check_twitch_live_channels([f'c{i}' for i in range(45)])
        self.assertEqual(sizes, [20, 20, 5])
        self.assertEqual(set(live), {'c0', 'c20', 'c40'})

    def test_any_failed_batch_means_no_result(self):
        with mock.patch.object(rt, '_check_twitch_batch', side_effect=[{}, None, {}]):
            self.assertIsNone(rt.check_twitch_live_channels([f'c{i}' for i in range(45)]))

    def test_no_channels_is_an_empty_dict_without_a_request(self):
        with mock.patch.object(rt, '_check_twitch_batch') as batch:
            self.assertEqual(rt.check_twitch_live_channels([]), {})
        batch.assert_not_called()


class TestLiveStatsFormatting(unittest.TestCase):
    def test_elapsed(self):
        self.assertEqual(rt.format_elapsed(3 * 3600 + 21 * 60 + 40), '3h 21min')
        self.assertEqual(rt.format_elapsed(45 * 60), '45min')
        self.assertEqual(rt.format_elapsed(20), '<1min')
        self.assertEqual(rt.format_elapsed(-5), '<1min')
        self.assertEqual(rt.format_elapsed(3600), '1h 0min')
        self.assertEqual(rt.format_elapsed(2 * 86400 + 5 * 3600 + 59 * 60), '2d 5h')

    def test_viewers(self):
        self.assertEqual([rt.format_viewers(n) for n in (0, 283, 999, 1000, 1234, 9999, 15400, 120000)],
                         ['0', '283', '999', '1.0k', '1.2k', '10.0k', '15k', '120k'])

    def test_stats_text_uses_whatever_is_known(self):
        now = 1_000_000.0
        full = {'viewers': 283, 'started': now - (3 * 3600 + 21 * 60)}
        self.assertEqual(rt.live_stats_text(full, now), '283 - 3h 21min')
        self.assertEqual(rt.live_stats_text({'viewers': 283}, now), '283')
        self.assertEqual(rt.live_stats_text({'started': now - 600}, now), '10min')
        self.assertEqual(rt.live_stats_text({'viewers': 0, 'started': None}, now), '0')
        self.assertEqual(rt.live_stats_text({}, now), '')
        self.assertEqual(rt.live_stats_text({'viewers': 'x', 'started': 'y'}, now), '')

    def test_iso_timestamps(self):
        self.assertEqual(rt.parse_iso_timestamp('2026-10-11T10:00:00Z'), rt.parse_iso_timestamp('2026-10-11T10:00:00+00:00'))
        self.assertEqual(rt.parse_iso_timestamp('2026-10-11T13:00:00+03:00'), rt.parse_iso_timestamp('2026-10-11T10:00:00Z'))
        self.assertIsNone(rt.parse_iso_timestamp('not a date'))
        self.assertIsNone(rt.parse_iso_timestamp(None))

    def test_viewer_count_texts(self):
        for text, expected in (('283 watching now', 283), ('1,234 watching', 1234), ('1.234 de persoane', 1234),
                               ('1\u00a0234 watching', 1234), ('1.2K watching', 1200), ('2,5 M', 2500000),
                               ('watching: 5', None), ('', None), (None, None)):
            self.assertEqual(rt.parse_viewer_count(text), expected, text)


class TestYoutubeLivePage(unittest.TestCase):
    PAGE = ('<html><script>var ytInitialPlayerResponse = {"microformat":{"playerMicroformatRenderer":'
            '{"liveBroadcastDetails":{"isLiveNow":true,"startTimestamp":"2026-10-11T10:00:00+00:00"}}}};</script>'
            '<script>var ytInitialData = {"contents":{"twoColumnWatchNextResults":{"results":{"results":{"contents":['
            '{"videoPrimaryInfoRenderer":{"viewCount":{"videoViewCountRenderer":{"isLive":true,'
            '"viewCount":{"runs":[{"text":"283"},{"text":" watching now"}]}}}}}]}}}}};</script></html>')

    def test_viewers_and_start_time_are_read_from_the_page_data(self):
        details = rt.parse_youtube_live_details(self.PAGE)
        self.assertEqual(details['viewers'], 283)
        self.assertEqual(details['started'], rt.parse_iso_timestamp('2026-10-11T10:00:00+00:00'))

    def test_simple_text_form_of_the_count(self):
        page = self.PAGE.replace('"viewCount":{"runs":[{"text":"283"},{"text":" watching now"}]}',
                                 '"viewCount":{"simpleText":"1,234 watching now"}')
        self.assertEqual(rt.parse_youtube_live_details(page)['viewers'], 1234)

    def test_page_without_the_data_gives_none(self):
        self.assertEqual(rt.parse_youtube_live_details('<html>nothing here</html>'), {'viewers': None, 'started': None})
        self.assertEqual(rt.parse_youtube_live_details('var ytInitialData = {broken'), {'viewers': None, 'started': None})

    def test_a_non_live_view_count_is_not_taken_for_viewers(self):
        page = self.PAGE.replace('"isLive":true', '"isLive":false')
        self.assertIsNone(rt.parse_youtube_live_details(page)['viewers'])

    def test_fetch_sends_the_consent_cookie_and_survives_errors(self):
        seen = {}

        def urlopen(req, timeout):
            seen['cookie'] = req.get_header('Cookie')
            seen['url'] = req.full_url
            resp = mock.MagicMock()
            resp.__enter__.return_value.read.return_value = self.PAGE.encode()
            return resp
        with mock.patch.object(rt.urllib.request, 'urlopen', side_effect=urlopen):
            details = rt.fetch_youtube_live_details('@somehandle')
        self.assertEqual(details['viewers'], 283)
        self.assertEqual(seen['url'], 'https://www.youtube.com/@somehandle/live')
        self.assertIn('SOCS=', seen['cookie'])
        with mock.patch.object(rt.urllib.request, 'urlopen', side_effect=OSError('429')):
            self.assertEqual(rt.fetch_youtube_live_details('@somehandle'), {'viewers': None, 'started': None})


class TestReverseGeocodeRegion(unittest.TestCase):
    def _resp(self, payload):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return resp

    def _geocode(self, payload):
        with mock.patch.object(rt.urllib.request, 'urlopen', return_value=self._resp(payload)):
            return rt.reverse_geocode_region(10.0, 20.0)

    def test_county_and_country_code(self):
        self.assertEqual(
            self._geocode({'address': {'country': 'Exampleland', 'county': 'Northshire', 'country_code': 'xx'}}),
            ('Exampleland', 'Northshire', 'XX'))

    def test_state_used_when_no_county(self):
        self.assertEqual(self._geocode({'address': {'country': 'Exampleland', 'state': 'Westland'}}),
                         ('Exampleland', 'Westland', None))

    def test_missing_region_still_returns_country(self):
        self.assertEqual(self._geocode({'address': {'country': 'Exampleland', 'country_code': 'xx'}}),
                         ('Exampleland', None, 'XX'))

    def test_missing_country_is_none(self):
        self.assertIsNone(self._geocode({'address': {'county': 'Northshire'}}))
        self.assertIsNone(self._geocode({'address': {}}))

    def test_request_error_is_none(self):
        with mock.patch.object(rt.urllib.request, 'urlopen', side_effect=OSError):
            self.assertIsNone(rt.reverse_geocode_region(10.0, 20.0))


if __name__ == '__main__':
    unittest.main()
