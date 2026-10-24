import json
import os
import re
import shutil
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from facade import rt


class FakeSite:
    """A local web server for the tests: routes map a path to (status, content
    type, body) and honour Range requests; every request is recorded."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        self.header_log = []  # the headers of every request, in order
        self.truncate = {}  # path -> bytes to send before cutting the connection
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = urllib.parse.urlparse(self.path).path
                query = urllib.parse.urlparse(self.path).query
                site.requests.append((urllib.parse.unquote(path), query, self.headers.get('Range')))
                site.header_log.append(dict(self.headers))
                route = site.routes.get(urllib.parse.unquote(path))
                if route is None:
                    self.send_error(404)
                    return
                status, ctype, body = route
                body = body if isinstance(body, bytes) else json.dumps(body).encode()
                start = 0
                match = re.match(r'bytes=(\d+)-', self.headers.get('Range') or '')
                if status == 200 and match and not getattr(site, 'ignore_range', False):
                    start = int(match.group(1))
                    if start >= len(body):
                        self.send_response(416)
                        self.end_headers()
                        return
                    status = 206
                self.send_response(status)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body) - start))
                if status == 206:
                    self.send_header('Content-Range', f'bytes {start}-{len(body) - 1}/{len(body)}')
                self.end_headers()
                cut = site.truncate.get(urllib.parse.unquote(path))
                self.wfile.write(body[start:start + cut] if cut is not None else body[start:])

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class SiteCase(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite()
        self.addCleanup(self.site.close)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        patcher = mock.patch.object(rt, 'ARCHIVE_BASE', self.site.base)
        patcher.start()
        self.addCleanup(patcher.stop)

    def route(self, path, body, status=200, ctype='application/json'):
        self.site.routes[path] = (status, ctype, body)


class TestSources(unittest.TestCase):
    def test_archive_urls_give_their_identifier(self):
        for url in ('https://archive.org/details/some-show', 'https://www.archive.org/details/some-show/',
                    'https://archive.org/download/some-show/file.mp4', 'https://archive.org/metadata/some-show',
                    'http://archive.org/details/some-show?tab=about'):
            self.assertEqual(rt.archive_identifier(url), 'some-show', url)

    def test_other_urls_have_none(self):
        for url in ('https://example.com/details/some-show', 'https://archive.org/', 'https://archive.org/search?q=a',
                    'https://example.com/feed.xml', 'not a url'):
            self.assertIsNone(rt.archive_identifier(url), url)

    def test_the_configured_archive_host_counts_too(self):
        with mock.patch.object(rt, 'ARCHIVE_BASE', 'http://127.0.0.1:9999'):
            self.assertEqual(rt.archive_identifier('http://127.0.0.1:9999/details/x'), 'x')


class TestTitlesAndOrder(unittest.TestCase):
    def test_season_and_episode_come_from_the_title(self):
        self.assertEqual(rt.season_episode('Show S01E05 Pilot'), (1, 5))
        self.assertEqual(rt.season_episode('show s2 e10'), (2, 10))
        self.assertEqual(rt.season_episode('Show s02e10'), (2, 10))
        self.assertEqual(rt.season_episode('Show 3x07 Title'), (3, 7))
        self.assertEqual(rt.season_episode('1080x1920 wallpaper'), (None, None))  # no episode number that long
        self.assertEqual(rt.season_episode(''), (None, None))
        self.assertEqual(rt.season_episode(None), (None, None))

    def test_the_label_is_the_number_or_else_the_date(self):
        self.assertEqual(rt.episode_label({'season': 1, 'episode': 5, 'date': '2020-01-01'}), 'S01E05')
        self.assertEqual(rt.episode_label({'season': None, 'episode': None, 'date': '2020-01-01'}), '2020-01-01')
        self.assertEqual(rt.episode_label({}), '')

    def test_the_title_loses_the_series_name_and_the_number(self):
        self.assertEqual(rt.clean_title('Classic Show S01E01 Pilot', ['Classic Show']), 'Pilot')
        self.assertEqual(rt.clean_title('Show - 1x05 - The Fifth', ['Show']), 'The Fifth')
        self.assertEqual(rt.clean_title('classic show s01e01 pilot', ['Classic Show']), 'pilot')
        self.assertEqual(rt.clean_title('Something: S02E10 \u2013 Finale'), 'Something - Finale')

    def test_a_title_that_would_vanish_stays_whole(self):
        self.assertEqual(rt.clean_title('S01E01', ['Show']), 'S01E01')
        self.assertEqual(rt.clean_title('Classic Show', ['Classic Show']), 'Classic Show')
        self.assertEqual(rt.clean_title('', ['x']), '')
        self.assertEqual(rt.clean_title(None), '')

    def test_other_titles_are_left_alone(self):
        self.assertEqual(rt.clean_title('Pod episode 3', ['Daily Pod']), 'Pod episode 3')
        self.assertEqual(rt.clean_title('Mission: Impossible'), 'Mission: Impossible')

    def episodes(self):
        return [{'id': 'c', 'title': 'C', 'date': '2020-03-01', 'season': None, 'episode': None},
                {'id': 'a', 'title': 'A', 'date': '2020-01-01', 'season': None, 'episode': None},
                {'id': 'b', 'title': 'B', 'date': '2020-02-01', 'season': None, 'episode': None}]

    def test_oldest_first_follows_the_dates(self):
        self.assertEqual([e['id'] for e in rt.order_episodes(self.episodes(), 'oldest')], ['a', 'b', 'c'])

    def test_newest_first_turns_it_around(self):
        self.assertEqual([e['id'] for e in rt.order_episodes(self.episodes(), 'newest')], ['c', 'b', 'a'])

    def test_episodes_without_dates_keep_the_drivers_oldest_first_order_either_way(self):
        eps = [{'id': i, 'title': 'same', 'date': ''} for i in 'abc']
        self.assertEqual([e['id'] for e in rt.order_episodes(eps, 'oldest')], ['a', 'b', 'c'])
        self.assertEqual([e['id'] for e in rt.order_episodes(eps, 'newest')], ['c', 'b', 'a'])

    def test_dates_beat_the_list_order_and_ties_follow_it(self):
        eps = [{'id': 'x', 'date': '2020-01-02'}, {'id': 'y', 'date': '2020-01-01'}, {'id': 'z', 'date': '2020-01-02'}]
        self.assertEqual([e['id'] for e in rt.order_episodes(eps, 'oldest')], ['y', 'x', 'z'])
        self.assertEqual([e['id'] for e in rt.order_episodes(eps, 'newest')], ['z', 'x', 'y'])

    def test_numbers_beat_dates(self):
        eps = [{'id': 'x2', 'title': 'x', 'date': '2000-01-01', 'season': 1, 'episode': 2},
               {'id': 'x1', 'title': 'x', 'date': '2020-01-01', 'season': 1, 'episode': 1},
               {'id': 's2', 'title': 'x', 'date': '1990-01-01', 'season': 2, 'episode': 1}]
        self.assertEqual([e['id'] for e in rt.order_episodes(eps, 'oldest')], ['x1', 'x2', 's2'])

    def test_upcoming_skips_what_was_watched(self):
        self.assertEqual([e['id'] for e in rt.upcoming(self.episodes(), ['a'], 'oldest', 2)], ['b', 'c'])
        self.assertEqual([e['id'] for e in rt.upcoming(self.episodes(), ['c'], 'newest', 1)], ['b'])
        self.assertEqual(rt.upcoming(self.episodes(), ['a', 'b', 'c'], 'oldest'), [])
        self.assertEqual(rt.upcoming([], [], 'oldest'), [])


class TestMergeEpisodes(unittest.TestCase):
    def listing(self, *ids):
        return [{'id': i, 'title': f'Show S01E0{n} {i}', 'date': f'2020-01-0{n}'} for n, i in enumerate(ids, 1)]

    def entry(self):
        return rt.new_media_entry()

    def test_the_first_check_of_a_series_starts_at_the_beginning(self):
        entry = self.entry()
        self.assertEqual(rt.merge_episodes(entry, 'Show', self.listing('a', 'b', 'c'), 'oldest'), [])
        self.assertTrue(entry['baselined'])
        self.assertEqual(entry['watched'], [])
        self.assertEqual(sorted(entry['known']), ['a', 'b', 'c'])
        self.assertEqual(entry['new'], [])

    def test_the_first_check_of_a_podcast_skips_the_backlog_but_not_the_latest(self):
        entry = self.entry()
        rt.merge_episodes(entry, 'Pod', self.listing('a', 'b', 'c'), 'newest')
        self.assertEqual(sorted(entry['watched']), ['a', 'b'])
        self.assertEqual([e['id'] for e in rt.upcoming(entry['episodes'], entry['watched'], 'newest', 5)], ['c'])

    def test_later_checks_report_what_is_new(self):
        entry = self.entry()
        rt.merge_episodes(entry, 'Show', self.listing('a', 'b'), 'oldest')
        new = rt.merge_episodes(entry, 'Show', self.listing('a', 'b', 'c'), 'oldest')
        self.assertEqual([e['id'] for e in new], ['c'])
        self.assertEqual(entry['new'], ['c'])
        self.assertEqual(rt.merge_episodes(entry, 'Show', self.listing('a', 'b', 'c'), 'oldest'), [])  # reported once
        self.assertEqual(entry['new'], ['c'])  # but still marked new until watched

    def test_the_stored_list_and_title_follow_the_site(self):
        entry = self.entry()
        rt.merge_episodes(entry, 'Old Title', self.listing('a'), 'oldest')
        rt.merge_episodes(entry, 'New Title', self.listing('a', 'b'), 'oldest')
        self.assertEqual(entry['title'], 'New Title')
        self.assertEqual([e['id'] for e in entry['episodes']], ['a', 'b'])

    def test_numbers_a_driver_gives_are_kept_when_the_title_has_none(self):
        entry = self.entry()
        eps = [{'id': 'a', 'title': 'Go West, Old Man', 'date': '', 'season': 1, 'episode': 1},
               {'id': 'b', 'title': 'Caprice', 'date': '', 'season': 1, 'episode': 3}]
        rt.merge_episodes(entry, 'Show', eps, 'oldest')
        self.assertEqual([(e['season'], e['episode']) for e in entry['episodes']], [(1, 1), (1, 3)])
        self.assertEqual([rt.episode_label(e) for e in entry['episodes']], ['S01E01', 'S01E03'])

    def test_numbers_a_driver_gives_win_over_the_title(self):
        entry = self.entry()
        rt.merge_episodes(entry, 'Show', [{'id': 'a', 'title': 'Show S09E09 Odd', 'date': '', 'season': 2, 'episode': 4}], 'oldest')
        self.assertEqual((entry['episodes'][0]['season'], entry['episodes'][0]['episode']), (2, 4))

    def test_numbers_that_are_not_whole_numbers_or_only_half_given_are_read_from_the_title_instead(self):
        entry = self.entry()
        eps = [{'id': 'a', 'title': 'Show S01E05 x', 'season': '1', 'episode': 5}, {'id': 'b', 'title': 'Show S02E06 x', 'season': True, 'episode': True},
               {'id': 'c', 'title': 'Show S03E07 x', 'season': 3}, {'id': 'd', 'title': 'No number'}]
        rt.merge_episodes(entry, 'Show', eps, 'oldest')
        self.assertEqual([(e['season'], e['episode']) for e in entry['episodes']], [(1, 5), (2, 6), (3, 7), (None, None)])

    def test_numbers_are_read_from_the_titles(self):
        entry = self.entry()
        rt.merge_episodes(entry, 'Show', self.listing('a', 'b'), 'oldest')
        self.assertEqual([(e['season'], e['episode']) for e in entry['episodes']], [(1, 1), (1, 2)])

    def test_an_episode_that_left_the_site_is_dropped_from_new_but_remembered_as_known(self):
        entry = self.entry()
        rt.merge_episodes(entry, 'Show', self.listing('a'), 'oldest')
        rt.merge_episodes(entry, 'Show', self.listing('a', 'b'), 'oldest')
        rt.merge_episodes(entry, 'Show', self.listing('a'), 'oldest')
        self.assertEqual(entry['new'], [])
        self.assertIn('b', entry['known'])  # coming back is not "new" again

    def test_counts(self):
        entry = self.entry()
        rt.merge_episodes(entry, 'Show', self.listing('a', 'b'), 'oldest')
        rt.merge_episodes(entry, 'Show', self.listing('a', 'b', 'c', 'd'), 'oldest')
        self.assertEqual(rt.new_unwatched(entry), 2)
        self.assertEqual(rt.unwatched_count(entry), 4)
        entry['watched'] = ['a', 'c']
        self.assertEqual(rt.new_unwatched(entry), 1)
        self.assertEqual(rt.unwatched_count(entry), 2)


class TestArchiveFiles(unittest.TestCase):
    def files(self):
        return [{'name': 'ep1_512kb.mp4', 'source': 'derivative', 'format': '512Kb MPEG4'},
                {'name': 'ep1.mkv', 'source': 'original', 'format': 'Matroska'},
                {'name': 'ep1.mp4', 'source': 'derivative', 'format': 'h.264'},
                {'name': 'ep1.ogv', 'source': 'derivative', 'format': 'Ogg Video'},
                {'name': 'ep1.en.srt', 'source': 'original', 'format': 'SubRip'},
                {'name': 'ep1_es.vtt', 'source': 'original', 'format': 'WebVTT'},
                {'name': 'ep1.srt', 'source': 'original', 'format': 'SubRip'},
                {'name': 'ep1_meta.xml', 'source': 'metadata', 'format': 'Metadata'},
                {'name': 'ep1.jpg', 'source': 'derivative', 'format': 'JPEG'}]

    def test_the_h264_derivative_is_preferred(self):
        media, _ = rt.archive_files('x', self.files())
        self.assertEqual(media['name'], 'ep1.mp4')

    def test_without_it_an_original_beats_the_small_derivatives(self):
        files = [f for f in self.files() if f['name'] != 'ep1.mp4']
        self.assertEqual(rt.archive_files('x', files)[0]['name'], 'ep1.mkv')
        files = [f for f in files if f['source'] != 'original' or f['name'].endswith('srt')]
        self.assertEqual(rt.archive_files('x', files)[0]['name'], 'ep1_512kb.mp4')

    def test_audio_items_work_too(self):
        files = [{'name': 'talk.mp3', 'source': 'original', 'format': 'VBR MP3'},
                 {'name': 'talk_64kb.mp3', 'source': 'derivative', 'format': '64Kbps MP3'},
                 {'name': 'talk_files.xml', 'source': 'metadata'}]
        media, _ = rt.archive_files('x', files)
        self.assertEqual((media['name'], media['ext']), ('talk.mp3', '.mp3'))

    def test_subtitles_are_listed_with_their_language(self):
        _, subs = rt.archive_files('x', self.files())
        self.assertEqual([(s['name'], s['lang']) for s in subs],
                         [('ep1.en.srt', 'en'), ('ep1_es.vtt', 'es'), ('ep1.srt', None)])

    def test_urls_are_quoted(self):
        media, _ = rt.archive_files('some id', [{'name': 'My Show #1.mp4', 'source': 'original', 'format': 'MPEG4'}])
        with mock.patch.object(rt, 'ARCHIVE_BASE', 'https://archive.org'):
            media, _ = rt.archive_files('some id', [{'name': 'My Show #1.mp4', 'source': 'original', 'format': 'MPEG4'}])
        self.assertEqual(media['url'], 'https://archive.org/download/some%20id/My%20Show%20%231.mp4')

    def test_an_item_without_media_has_none(self):
        self.assertEqual(rt.archive_files('x', [{'name': 'a.xml', 'source': 'metadata'}, {'name': 'a.srt'}]),
                         (None, [{'url': mock.ANY, 'name': 'a.srt', 'ext': '.srt', 'lang': None}]))

    def test_junk_entries_are_ignored(self):
        self.assertEqual(rt.archive_files('x', [None, 'a', {'name': 5}, {}]), (None, []))


class TestReadingArchive(SiteCase):
    def collection(self):
        self.route('/metadata/show', {'metadata': {'mediatype': 'collection', 'title': 'The Show'}, 'files': []})
        self.route('/advancedsearch.php', {'response': {'numFound': 3, 'docs': [
            {'identifier': 'show-1', 'title': 'The Show S01E01 Pilot', 'date': '1955-03-01T00:00:00Z'},
            {'identifier': 'show-2', 'title': ['The Show S01E02 Next'], 'publicdate': '2020-05-06T10:00:00Z'},
            {'title': 'no identifier'}]}})

    def test_a_collection_lists_its_items_as_episodes(self):
        self.collection()
        title, episodes = rt.list_archive('show')
        self.assertEqual(title, 'The Show')
        self.assertEqual([(e['id'], e['title'], e['date'], e['kind']) for e in episodes],
                         [('show-1', 'The Show S01E01 Pilot', '1955-03-01', 'archive'),
                          ('show-2', 'The Show S01E02 Next', '2020-05-06', 'archive')])

    def test_the_search_asks_for_the_collections_items_only(self):
        self.collection()
        rt.list_archive('show')
        query = next(q for p, q, _r in self.site.requests if p == '/advancedsearch.php')
        params = urllib.parse.parse_qs(query)
        self.assertEqual(params['q'], ['collection:show AND NOT mediatype:collection'])
        self.assertEqual(params['output'], ['json'])
        self.assertEqual(params['rows'], [str(rt.ARCHIVE_MAX_ITEMS)])

    def test_a_single_item_with_one_video_is_a_movie(self):
        self.route('/metadata/film', {'metadata': {'mediatype': 'movies', 'title': 'A Film'}, 'files': [
            {'name': 'film.mp4', 'source': 'derivative', 'format': 'h.264'},
            {'name': 'film_512kb.mp4', 'source': 'derivative', 'format': '512Kb MPEG4'},
            {'name': 'film.en.srt', 'source': 'original', 'format': 'SubRip'}]})
        title, episodes = rt.list_archive('film')
        self.assertEqual(title, 'A Film')
        self.assertEqual(len(episodes), 1)
        movie = episodes[0]
        self.assertEqual((movie['id'], movie['title'], movie['ext']), ('film/film.mp4', 'A Film', '.mp4'))
        self.assertEqual(movie['url'], f'{self.site.base}/download/film/film.mp4')
        self.assertEqual([s['name'] for s in movie['subs']], ['film.en.srt'])

    def test_the_versions_of_one_file_are_one_episode_by_their_original(self):
        self.route('/metadata/film', {'metadata': {'mediatype': 'movies', 'title': 'A Film'}, 'files': [
            {'name': 'Film.avi', 'source': 'original', 'format': 'AVI'},
            {'name': 'Film.mp4', 'source': 'derivative', 'format': 'h.264', 'original': 'Film.avi'},
            {'name': 'Film.ogv', 'source': 'derivative', 'format': 'Ogg Video', 'original': 'Film.avi'},
            {'name': 'Film.thumbs', 'source': 'derivative', 'original': 'Film.avi'}]})
        _, episodes = rt.list_archive('film')
        self.assertEqual([e['id'] for e in episodes], ['film/Film.mp4'])

    def test_an_item_holding_a_series_lists_every_file_with_its_own_subtitles(self):
        self.route('/metadata/series', {'metadata': {'mediatype': 'movies', 'title': 'Whole Series'}, 'files': [
            {'name': 'Show_S01E01.mp4', 'source': 'original', 'format': 'MPEG4'},
            {'name': 'Show_S01E02.mp4', 'source': 'original', 'format': 'MPEG4'},
            {'name': 'Show_S01E01.en.srt', 'source': 'original'},
            {'name': 'Show_S01E02.en.srt', 'source': 'original'}]})
        _, episodes = rt.list_archive('series')
        self.assertEqual([e['title'] for e in episodes], ['Show S01E01', 'Show S01E02'])
        self.assertEqual([[s['name'] for s in e['subs']] for e in episodes], [['Show_S01E01.en.srt'], ['Show_S01E02.en.srt']])

    def test_a_title_given_as_a_list_is_read(self):
        self.route('/metadata/x', {'metadata': {'mediatype': ['movies'], 'title': ['First', 'Second']},
                                   'files': [{'name': 'x.mp4', 'source': 'original', 'format': 'MPEG4'}]})
        self.assertEqual(rt.list_archive('x')[0], 'First')

    def test_a_missing_title_falls_back_to_the_identifier(self):
        self.route('/metadata/x', {'metadata': {}, 'files': []})
        self.assertEqual(rt.list_archive('x'), ('x', []))

    def test_an_error_from_the_site_is_raised(self):
        with self.assertRaises(Exception):
            rt.list_archive('nothing-here')  # 404

    def test_list_episodes_picks_the_driver_by_url(self):
        self.collection()
        title, episodes = rt.list_episodes(f'{self.site.base}/details/show')
        self.assertEqual((title, len(episodes)), ('The Show', 2))

    def test_collection_items_are_resolved_only_when_needed(self):
        self.route('/metadata/show-1', {'metadata': {}, 'files': [
            {'name': 'a.mp4', 'source': 'derivative', 'format': 'h.264'}, {'name': 'a.en.srt'}]})
        files = rt.resolve_files({'id': 'show-1', 'kind': 'archive', 'ref': 'show-1'})
        self.assertEqual(files['video'], {'url': f'{self.site.base}/download/show-1/a.mp4', 'ext': '.mp4'})
        self.assertEqual([s['lang'] for s in files['subs']], ['en'])

    def test_an_item_with_nothing_to_play_cannot_be_resolved(self):
        self.route('/metadata/empty', {'metadata': {}, 'files': [{'name': 'a.xml', 'source': 'metadata'}]})
        with self.assertRaises(ValueError):
            rt.resolve_files({'id': 'empty', 'ref': 'empty'})

    def test_episodes_that_know_their_file_need_no_lookup(self):
        files = rt.resolve_files({'id': 'x', 'url': 'http://h/x.mp3', 'ext': '.mp3', 'subs': [{'url': 'u', 'ext': '.srt', 'lang': 'en'}]})
        self.assertEqual(files, {'video': {'url': 'http://h/x.mp3', 'ext': '.mp3'}, 'subs': [{'url': 'u', 'ext': '.srt', 'lang': 'en'}]})
        self.assertEqual(self.site.requests, [])


class TestReadingFeeds(SiteCase):
    def feed(self, items, title='A Podcast'):
        body = (f'<?xml version="1.0"?><rss version="2.0"><channel><title>{title}</title>{items}</channel></rss>').encode()
        self.route('/feed.xml', body, ctype='application/rss+xml')
        return f'{self.site.base}/feed.xml'

    def test_entries_with_an_audio_or_video_enclosure_are_episodes(self):
        url = self.feed('<item><title>One</title><guid>g1</guid><pubDate>Mon, 01 Sep 2025 10:00:00 +0000</pubDate>'
                        '<enclosure url="http://h/one.mp3" type="audio/mpeg" length="1"/></item>'
                        '<item><title>Two</title><guid>g2</guid><enclosure url="http://h/two.mp4" type="video/mp4" length="1"/></item>')
        title, episodes = rt.list_feed(url)
        self.assertEqual(title, 'A Podcast')
        self.assertEqual([(e['id'], e['title'], e['date'], e['url'], e['ext']) for e in episodes],
                         [(f'{self.site.base}/g1', 'One', '2025-09-01', 'http://h/one.mp3', '.mp3'),
                          (f'{self.site.base}/g2', 'Two', '', 'http://h/two.mp4', '.mp4')])  # a relative guid is resolved by feedparser
        self.assertTrue(all(e['kind'] == 'feed' and e['subs'] == [] for e in episodes))

    def test_entries_without_media_are_skipped(self):
        url = self.feed('<item><title>Text only</title><guid>t</guid></item>'
                        '<item><title>A picture</title><guid>p</guid><enclosure url="http://h/a.jpg" type="image/jpeg" length="1"/></item>'
                        '<item><title>Audio</title><guid>a</guid><enclosure url="http://h/a.mp3" type="audio/mpeg" length="1"/></item>')
        self.assertEqual([e['id'] for e in rt.list_feed(url)[1]], [f'{self.site.base}/a'])

    def test_the_extension_comes_from_the_url_else_the_type(self):
        url = self.feed('<item><title>A</title><guid>a</guid><enclosure url="http://h/download?id=5" type="audio/mp4" length="1"/></item>'
                        '<item><title>B</title><guid>b</guid><enclosure url="http://h/stream?id=6" type="video/webm" length="1"/></item>'
                        '<item><title>C</title><guid>c</guid><enclosure url="http://h/x/ep.ogg" type="application/octet-stream" length="1"/></item>')
        self.assertEqual([e['ext'] for e in rt.list_feed(url)[1]], ['.m4a', '.webm', '.ogg'])

    def test_without_a_guid_the_media_url_identifies_the_episode(self):
        url = self.feed('<item><title>A</title><enclosure url="http://h/a.mp3" type="audio/mpeg" length="1"/></item>')
        self.assertEqual(rt.list_feed(url)[1][0]['id'], 'http://h/a.mp3')

    def test_a_page_that_is_not_a_feed_raises(self):
        self.route('/feed.xml', b'<html><body>not a feed', ctype='text/html')
        with self.assertRaises(ValueError):
            rt.list_feed(f'{self.site.base}/feed.xml')

    def test_an_empty_but_valid_feed_is_no_error(self):
        self.assertEqual(rt.list_feed(self.feed('')), ('A Podcast', []))


class TestFilenames(unittest.TestCase):
    def test_safe_names(self):
        self.assertEqual(rt.safe_name('A/B\\C:D*E?F"G<H>I|J'), 'A B C D E F G H I J')
        self.assertEqual(rt.safe_name('  ..hidden.. '), 'hidden')
        self.assertEqual(rt.safe_name(''), 'untitled')
        self.assertEqual(rt.safe_name(None), 'untitled')
        self.assertEqual(rt.safe_name('x' * 200), 'x' * 90)
        self.assertEqual(rt.safe_name('a\nb\tc'), 'a b c')
        self.assertEqual(rt.safe_name('..'), 'untitled')

    def test_the_basename_is_show_label_and_title(self):
        ep = {'title': 'The Show S01E05 Pilot', 'season': 1, 'episode': 5, 'date': '1955-01-01'}
        self.assertEqual(rt.episode_basename('The Show', ep), 'The Show - S01E05 - Pilot')

    def test_without_a_number_the_date_stands_in(self):
        ep = {'title': 'Pod episode 3', 'season': None, 'episode': None, 'date': '2025-09-03'}
        self.assertEqual(rt.episode_basename('Daily Pod', ep), 'Daily Pod - 2025-09-03 - Pod episode 3')

    def test_without_either_there_is_just_show_and_title(self):
        self.assertEqual(rt.episode_basename('Show', {'title': 'Pilot'}), 'Show - Pilot')

    def test_other_names_of_the_source_are_cut_from_the_title_too(self):
        ep = {'title': 'Real Name S01E01 Pilot', 'season': 1, 'episode': 1}
        self.assertEqual(rt.episode_basename('My Name', ep, names=('Real Name',)), 'My Name - S01E01 - Pilot')

    def files(self, subs=()):
        return {'video': {'url': 'u', 'ext': '.mp4'}, 'subs': list(subs)}

    def test_paths_are_in_a_folder_named_after_the_source(self):
        ep = {'title': 'Pilot', 'season': 1, 'episode': 1}
        paths = rt.episode_paths('/videos', 'The Show', ep, self.files())
        self.assertEqual(paths, {'video': '/videos/The Show/The Show - S01E01 - Pilot.mp4', 'subs': []})

    def test_the_root_is_expanded(self):
        paths = rt.episode_paths('~/videos', 'S', {'title': 'T'}, self.files())
        self.assertEqual(paths['video'], os.path.join(os.path.expanduser('~/videos'), 'S', 'S - T.mp4'))

    def test_subtitles_sit_next_to_the_video_named_for_mpv_to_find(self):
        subs = [{'url': 'a', 'ext': '.srt', 'lang': 'en'}, {'url': 'b', 'ext': '.vtt', 'lang': 'ro'}, {'url': 'c', 'ext': '.srt', 'lang': None}]
        paths = rt.episode_paths('/v', 'S', {'title': 'T'}, self.files(subs))
        self.assertEqual(paths['subs'], ['/v/S/S - T.en.srt', '/v/S/S - T.ro.vtt', '/v/S/S - T.srt'])

    def test_two_subtitles_in_one_language_do_not_overwrite_each_other(self):
        subs = [{'url': 'a', 'ext': '.srt', 'lang': 'en'}, {'url': 'b', 'ext': '.srt', 'lang': 'en'}]
        paths = rt.episode_paths('/v', 'S', {'title': 'T'}, self.files(subs))
        self.assertEqual(len(set(paths['subs'])), 2)

    def test_the_base_path_is_the_video_path_without_its_extension(self):
        self.assertEqual(rt.episode_basepath('/v', 'S', {'title': 'T'}), '/v/S/S - T')

    def test_the_base_path_avoids_taken_names_whatever_their_extension(self):
        self.assertEqual(rt.episode_basepath('/v', 'S', {'title': 'T'}, taken={'/v/S/S - T.mkv'}), '/v/S/S - T (2)')
        self.assertEqual(rt.episode_basepath('/v', 'S', {'title': 'T'}, taken={'/v/S/S - T.mkv', '/v/S/S - T (2)'}), '/v/S/S - T (3)')

    def test_a_name_another_episode_uses_gets_a_number(self):
        taken = {'/v/S/S - T.mp4', '/v/S/S - T (2).mp4'}
        paths = rt.episode_paths('/v', 'S', {'title': 'T'}, self.files([{'url': 'a', 'ext': '.srt', 'lang': 'en'}]), taken)
        self.assertEqual(paths, {'video': '/v/S/S - T (3).mp4', 'subs': ['/v/S/S - T (3).en.srt']})


class TestDownloads(SiteCase):
    BODY = bytes(range(256)) * 400  # 102400 bytes

    def setUp(self):
        super().setUp()
        self.route('/file.bin', self.BODY, ctype='application/octet-stream')
        self.dest = os.path.join(self.dir, 'out', 'file.bin')  # the folder does not exist yet
        self.url = f'{self.site.base}/file.bin'

    def read(self, path):
        with open(path, 'rb') as f:
            return f.read()

    def test_a_download_lands_in_place_with_no_partial_file_left(self):
        rt.download_file(self.url, self.dest)
        self.assertEqual(self.read(self.dest), self.BODY)
        self.assertFalse(os.path.exists(self.dest + '.part'))

    def test_progress_reports_bytes_and_total(self):
        seen = []
        rt.download_file(self.url, self.dest, progress=lambda done, total: seen.append((done, total)), block_size=32768)
        self.assertEqual(seen[-1], (len(self.BODY), len(self.BODY)))
        self.assertEqual([d for d, _t in seen], sorted(d for d, _t in seen))
        self.assertGreater(len(seen), 1)

    def test_a_partial_file_is_carried_on_from_where_it_stopped(self):
        os.makedirs(os.path.dirname(self.dest))
        with open(self.dest + '.part', 'wb') as f:
            f.write(self.BODY[:30000])
        seen = []
        rt.download_file(self.url, self.dest, progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(self.read(self.dest), self.BODY)
        self.assertEqual(self.site.requests[-1][2], 'bytes=30000-')
        self.assertEqual(seen[-1], (len(self.BODY), len(self.BODY)))  # the total counts what was already there

    def test_a_server_that_ignores_range_restarts_from_scratch(self):
        self.site.ignore_range = True
        os.makedirs(os.path.dirname(self.dest))
        with open(self.dest + '.part', 'wb') as f:
            f.write(b'stale bytes that must not stay')
        rt.download_file(self.url, self.dest)
        self.assertEqual(self.read(self.dest), self.BODY)

    def test_a_partial_file_that_is_already_everything_is_finished_off(self):
        os.makedirs(os.path.dirname(self.dest))
        with open(self.dest + '.part', 'wb') as f:
            f.write(self.BODY)
        rt.download_file(self.url, self.dest)  # the server answers 416
        self.assertEqual(self.read(self.dest), self.BODY)
        self.assertFalse(os.path.exists(self.dest + '.part'))

    def test_a_cut_connection_raises_and_keeps_what_arrived(self):
        self.site.truncate['/file.bin'] = 40000
        with self.assertRaises(Exception):
            rt.download_file(self.url, self.dest)
        self.assertFalse(os.path.exists(self.dest))
        self.assertEqual(os.path.getsize(self.dest + '.part'), 40000)
        del self.site.truncate['/file.bin']
        rt.download_file(self.url, self.dest)  # the next try resumes
        self.assertEqual(self.read(self.dest), self.BODY)

    def test_an_error_status_raises_and_leaves_nothing(self):
        self.route('/gone.bin', b'', status=404)
        with self.assertRaises(Exception):
            rt.download_file(f'{self.site.base}/gone.bin', self.dest)
        self.assertFalse(os.path.exists(self.dest))

    def test_cancelling_stops_between_blocks_and_keeps_the_partial_file(self):
        calls = []

        def cancelled():
            calls.append(1)
            return len(calls) > 2
        with self.assertRaises(rt.DownloadCancelled):
            rt.download_file(self.url, self.dest, cancelled=cancelled, block_size=1024)
        self.assertTrue(os.path.exists(self.dest + '.part'))
        self.assertFalse(os.path.exists(self.dest))

    def test_headers_are_sent_with_the_request(self):
        rt.download_file(self.url, self.dest, headers={'Referer': 'https://embed.example/', 'X-Token': 'abc'})
        sent = self.site.header_log[-1]
        self.assertEqual((sent.get('Referer'), sent.get('X-Token')), ('https://embed.example/', 'abc'))
        self.assertIn('void-tray', sent['User-Agent'])  # ours unless the caller sets one

    def test_a_user_agent_in_the_headers_replaces_ours(self):
        rt.download_file(self.url, self.dest, headers={'User-Agent': 'Mozilla/5.0 (test)'})
        self.assertEqual(self.site.header_log[-1]['User-Agent'], 'Mozilla/5.0 (test)')

    def test_a_resumed_download_keeps_its_headers_and_adds_the_range(self):
        os.makedirs(os.path.dirname(self.dest))
        with open(self.dest + '.part', 'wb') as f:
            f.write(self.BODY[:1000])
        rt.download_file(self.url, self.dest, headers={'Referer': 'https://embed.example/'})
        self.assertEqual((self.site.header_log[-1].get('Referer'), self.site.header_log[-1].get('Range')), ('https://embed.example/', 'bytes=1000-'))

    def test_the_download_names_us(self):
        rt.download_file(self.url, self.dest)
        self.assertEqual(self.site.requests[-1][0], '/file.bin')

    def paths_and_files(self):
        base = os.path.join(self.dir, 'S')
        files = {'video': {'url': f'{self.site.base}/file.bin', 'ext': '.mp4'},
                 'subs': [{'url': f'{self.site.base}/a.srt', 'ext': '.srt', 'lang': 'en'},
                          {'url': f'{self.site.base}/b.srt', 'ext': '.srt', 'lang': 'ro'}]}
        paths = {'video': os.path.join(base, 'S - T.mp4'),
                 'subs': [os.path.join(base, 'S - T.en.srt'), os.path.join(base, 'S - T.ro.srt')]}
        return paths, files

    def test_an_episode_is_its_subtitles_and_its_video(self):
        self.route('/a.srt', b'1\n00:00:01,000 --> 00:00:02,000\nHi\n\n', ctype='text/plain')
        self.route('/b.srt', b'1\n00:00:01,000 --> 00:00:02,000\nSalut\n\n', ctype='text/plain')
        paths, files = self.paths_and_files()
        saved = rt.download_episode(paths, files)
        self.assertEqual(saved, {'video': paths['video'], 'subs': paths['subs']})
        self.assertEqual(self.read(paths['video']), self.BODY)
        self.assertEqual([r[0] for r in self.site.requests], ['/a.srt', '/b.srt', '/file.bin'])  # subtitles first: they are small

    def test_a_subtitle_that_fails_is_skipped_not_fatal(self):
        self.route('/b.srt', b'ok\n', ctype='text/plain')  # /a.srt is a 404
        paths, files = self.paths_and_files()
        saved = rt.download_episode(paths, files)
        self.assertEqual(saved['subs'], [paths['subs'][1]])
        self.assertTrue(os.path.exists(paths['video']))

    def test_a_video_that_fails_fails_the_episode(self):
        paths, files = self.paths_and_files()
        files['video']['url'] = f'{self.site.base}/missing.bin'
        with self.assertRaises(Exception):
            rt.download_episode(paths, files)

    def test_each_file_is_asked_for_with_its_own_headers(self):
        self.route('/a.srt', b'x', ctype='text/plain')
        paths, files = self.paths_and_files()
        files['subs'] = files['subs'][:1]
        paths['subs'] = paths['subs'][:1]
        files['subs'][0]['headers'] = {'X-For': 'subtitle'}
        files['video']['headers'] = {'X-For': 'video'}
        rt.download_episode(paths, files)
        self.assertEqual([h.get('X-For') for h in self.site.header_log], ['subtitle', 'video'])

    def test_an_hls_video_goes_to_ffmpeg_with_its_headers(self):
        paths, files = self.paths_and_files()
        files['video'] = {'url': 'https://cdn.example/a/master.m3u8?t=1', 'ext': '.mkv', 'headers': {'Referer': 'https://embed.example/'}}
        files['subs'] = []
        paths['subs'] = []
        paths['video'] = os.path.join(self.dir, 'S', 'S - T.mkv')
        progress, cancelled = mock.Mock(), mock.Mock()
        with mock.patch.object(rt, 'download_hls', return_value=paths['video']) as hls, mock.patch.object(rt, 'download_file') as plain:
            saved = rt.download_episode(paths, files, progress=progress, cancelled=cancelled)
        hls.assert_called_once_with('https://cdn.example/a/master.m3u8?t=1', paths['video'], headers={'Referer': 'https://embed.example/'},
                                    progress=progress, cancelled=cancelled)
        plain.assert_not_called()
        self.assertEqual(saved, {'video': paths['video'], 'subs': []})

    def test_a_plain_file_does_not_go_to_ffmpeg(self):
        self.route('/a.srt', b'x', ctype='text/plain')
        paths, files = self.paths_and_files()
        with mock.patch.object(rt, 'download_hls') as hls:
            rt.download_episode(paths, files)
        hls.assert_not_called()

    def test_the_subtitles_of_an_hls_episode_are_still_tolerant(self):
        paths, files = self.paths_and_files()  # /a.srt and /b.srt are not routes here: 404
        files['video'] = {'url': 'https://cdn.example/index.m3u8', 'ext': '.mkv'}
        with mock.patch.object(rt, 'download_hls') as hls:
            saved = rt.download_episode(paths, files)
        hls.assert_called_once()
        self.assertEqual(saved['subs'], [])

    def test_cancelling_during_a_subtitle_is_not_swallowed(self):
        paths, files = self.paths_and_files()
        self.route('/a.srt', b'x' * 5000, ctype='text/plain')
        with self.assertRaises(rt.DownloadCancelled):
            rt.download_episode(paths, files, cancelled=lambda: True)


if __name__ == '__main__':
    unittest.main()
