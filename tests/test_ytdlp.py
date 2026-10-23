import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from facade import rt


FAKE_YTDLP = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as f:
    f.write(json.dumps(args) + '\n')
url = args[-1]
mode = os.environ.get('FAKE_MODE', 'ok')
if '--dump-single-json' in args:
    answer = json.load(open(os.environ['FAKE_DATA'])).get(url)
    if answer is None:
        print('ERROR: [generic] Unsupported URL: ' + url, file=sys.stderr)
        sys.exit(1)
    if isinstance(answer, dict) and 'error' in answer:
        print('WARNING: something minor', file=sys.stderr)
        print('ERROR: ' + answer['error'], file=sys.stderr)
        sys.exit(1)
    if answer == 'garbage':
        print('this is not json')
        sys.exit(0)
    if answer == 'empty':
        sys.exit(0)
    print(json.dumps(answer))
    sys.exit(0)
out = args[args.index('-o') + 1]
if mode == 'fail':
    print('[youtube] abc: Downloading webpage')
    print('[download] 12 more lines of chatter')
    print('ERROR: [youtube] abc: Video unavailable. This video is private')
    sys.exit(1)
path = out.replace('%(ext)s', 'mp4')
base = out[:-len('.%(ext)s')]
os.makedirs(os.path.dirname(path), exist_ok=True)
print('[download] Destination: ' + path)
name = path
lines = ['YTDLP_PROGRESS 44 44 NA ' + base + '.en.srt', 'YTDLP_PROGRESS 250 1000 NA ' + name, 'YTDLP_PROGRESS 500 1000 NA ' + name,
         'YTDLP_PROGRESS 1000 1000 NA ' + name]
if mode == 'variants':
    lines = ['YTDLP_PROGRESS 100 NA 2000 ' + name, 'YTDLP_PROGRESS NA NA NA ' + name, 'YTDLP_PROGRESS 50 NA NA ' + name, 'YTDLP_PROGRESS 7']
for line in lines:
    print(line, flush=True)
    if mode == 'slow':
        time.sleep(30)
open(path, 'wb').write(b'video')
if mode != 'nosubs':
    open(base + '.en.srt', 'w').write('1\n')
    open(base + '.ro.vtt', 'w').write('WEBVTT\n')
open(base + '.jpg', 'w').write('thumbnail')  # not a subtitle
open(os.path.join(os.path.dirname(base), 'Other - 2020-01-01 - Episode.en.srt'), 'w').write('another episode')
if mode != 'nofile':
    print('YTDLP_FILE ' + path)
'''


def video(ident, title=None, **extra):
    entry = {'_type': 'url', 'ie_key': 'Youtube', 'id': ident, 'url': f'https://www.youtube.com/watch?v={ident}',
             'title': title or f'Video {ident}'}
    entry.update(extra)
    return entry


class FakeCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.bin = os.path.join(self.dir, 'bin')
        os.mkdir(self.bin)
        script = os.path.join(self.bin, 'yt-dlp')
        with open(script, 'w') as f:
            f.write(FAKE_YTDLP)
        os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.dir, 'calls.log')
        self.data = os.path.join(self.dir, 'data.json')
        self.answers = {}
        with open(self.data, 'w') as f:
            f.write('{}')
        self.env = {'PATH': self.bin + os.pathsep + os.environ.get('PATH', ''), 'FAKE_LOG': self.log, 'FAKE_DATA': self.data}
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def answer(self, url, data):
        self.answers[url] = data
        with open(self.data, 'w') as f:
            json.dump(self.answers, f)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]


class TestUrls(unittest.TestCase):
    def test_youtube_addresses(self):
        for url in ('https://www.youtube.com/@StarTalk/podcasts', 'https://youtube.com/playlist?list=PL1',
                    'https://m.youtube.com/watch?v=x', 'https://music.youtube.com/playlist?list=x', 'https://youtu.be/abc',
                    'HTTPS://WWW.YOUTUBE.COM/@x'):
            self.assertTrue(rt.is_youtube_url(url), url)

    def test_other_addresses(self):
        for url in ('https://example.com/feed.xml', 'https://notyoutube.com/x', 'https://www.youtube.com.evil.example/x',
                    'https://vimeo.com/channels/x', 'youtube.com/@x', ''):
            self.assertFalse(rt.is_youtube_url(url), url)


class TestFindingIt(unittest.TestCase):
    def test_it_is_found_on_the_given_path(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = os.path.join(directory, 'yt-dlp')
        open(path, 'w').close()
        os.chmod(path, 0o755)
        self.assertEqual(rt.find_ytdlp({'PATH': directory}), path)

    def test_missing_is_an_error_that_says_how_to_install_it(self):
        with self.assertRaises(ValueError) as caught:
            rt.find_ytdlp({'PATH': tempfile.gettempdir() + '/nowhere'})
        self.assertEqual(str(caught.exception), 'yt-dlp is not installed (sudo xbps-install yt-dlp)')

    def test_error_text_is_the_last_error_line_without_its_prefix(self):
        output = 'WARNING: a\nERROR: [youtube] abc: first\nmore\nERROR: [youtube] abc: Video unavailable\ntrailing chatter\n'
        self.assertEqual(rt._error_text(output), 'abc: Video unavailable')
        self.assertEqual(rt._error_text('ERROR: plain message'), 'plain message')
        self.assertEqual(rt._error_text('only chatter\nlast line'), 'last line')
        self.assertEqual(rt._error_text(''), 'yt-dlp failed')
        self.assertEqual(len(rt._error_text('ERROR: ' + 'x' * 1000)), 300)


class TestListing(FakeCase):
    PLAYLIST = 'https://www.youtube.com/playlist?list=PL1'
    CHANNEL = 'https://www.youtube.com/@Chan/videos'

    def list(self, url, entries=None, **top):
        data = {'_type': 'playlist', 'title': 'The Playlist'}
        if entries is not None:
            data['entries'] = entries
        data.update(top)
        self.answer(url, data)
        return rt.list_ytdlp(url)

    def test_a_playlist_keeps_its_own_order(self):
        title, episodes = self.list(self.PLAYLIST, [video('a'), video('b'), video('c')])
        self.assertEqual(title, 'The Playlist')
        self.assertEqual([e['id'] for e in episodes], ['a', 'b', 'c'])
        self.assertEqual(episodes[0], {'id': 'a', 'title': 'Video a', 'date': '', 'url': 'https://www.youtube.com/watch?v=a'})

    def test_a_channel_tab_is_turned_around_to_oldest_first(self):
        _, episodes = self.list(self.CHANNEL, [video('new'), video('mid'), video('old')])
        self.assertEqual([e['id'] for e in episodes], ['old', 'mid', 'new'])

    def test_every_kind_of_channel_address_counts_as_a_channel(self):
        for url in ('https://www.youtube.com/@Chan', 'https://www.youtube.com/channel/UCabc/videos',
                    'https://www.youtube.com/c/Chan/streams', 'https://www.youtube.com/user/Chan'):
            _, episodes = self.list(url, [video('new'), video('old')])
            self.assertEqual([e['id'] for e in episodes], ['old', 'new'], url)

    def test_dates_come_from_the_upload_date_or_the_timestamp(self):
        _, episodes = self.list(self.PLAYLIST, [video('a', upload_date='20210203'), video('b', timestamp=1612310400),
                                                video('c', release_timestamp=1612310400), video('d', upload_date='soon'), video('e')])
        self.assertEqual([e['date'] for e in episodes], ['2021-02-03', '2021-02-03', '2021-02-03', '', ''])

    def test_the_watch_page_is_preferred_and_a_bare_youtube_id_becomes_one(self):
        _, episodes = self.list(self.PLAYLIST, [
            video('a', webpage_url='https://www.youtube.com/watch?v=a&list=PL1'),
            {'ie_key': 'Youtube', 'id': 'b', 'url': 'b', 'title': 'Bare'},
            {'ie_key': 'Vimeo', 'id': 'c', 'url': 'c', 'title': 'Not usable'},
            {'id': 'd', 'title': 'No url'}, {'url': 'https://x/y', 'title': 'No id'}])
        self.assertEqual([e['url'] for e in episodes],
                         ['https://www.youtube.com/watch?v=a&list=PL1', 'https://www.youtube.com/watch?v=b'])

    def test_videos_that_cannot_be_downloaded_are_left_out(self):
        _, episodes = self.list(self.PLAYLIST, [
            video('ok'), video('p', '[Private video]'), video('d', '[deleted video]'), video('live', live_status='is_live'),
            video('soon', live_status='is_upcoming'), video('past', live_status='was_live'),
            video('priv', availability='private'), video('login', availability='needs_auth'),
            video('members', availability='subscriber_only'), video('pay', availability='premium_only'),
            video('public', availability='public')])
        self.assertEqual([e['id'] for e in episodes], ['ok', 'past', 'public'])

    def test_a_video_in_two_places_is_listed_once(self):
        _, episodes = self.list(self.PLAYLIST, [video('a'), video('a', 'Again'), video('b')])
        self.assertEqual([(e['id'], e['title']) for e in episodes], [('a', 'Video a'), ('b', 'Video b')])

    def test_names_fall_back(self):
        self.answer(self.PLAYLIST, {'entries': [video('a', title=None)], 'uploader': 'The Uploader'})
        entry = video('a')
        del entry['title']
        self.answer(self.PLAYLIST, {'entries': [entry], 'uploader': 'The Uploader'})
        title, episodes = rt.list_ytdlp(self.PLAYLIST)
        self.assertEqual((title, episodes[0]['title']), ('The Uploader', 'a'))
        self.answer(self.PLAYLIST, {'entries': [], 'channel': 'The Channel'})
        self.assertEqual(rt.list_ytdlp(self.PLAYLIST)[0], 'The Channel')
        self.answer(self.PLAYLIST, {'entries': []})
        self.assertEqual(rt.list_ytdlp(self.PLAYLIST)[0], self.PLAYLIST)

    def test_an_empty_playlist_has_no_episodes(self):
        self.assertEqual(self.list(self.PLAYLIST, [])[1], [])

    def test_junk_in_the_entries_is_ignored(self):
        _, episodes = self.list(self.PLAYLIST, [None, 'text', 5, [], video('a')])
        self.assertEqual([e['id'] for e in episodes], ['a'])

    def test_a_single_video_is_one_episode(self):
        url = 'https://www.youtube.com/watch?v=abc'
        self.answer(url, {'id': 'abc', 'title': 'A Movie', 'webpage_url': url, 'upload_date': '20200102'})
        title, episodes = rt.list_ytdlp(url)
        self.assertEqual(title, 'A Movie')
        self.assertEqual(episodes, [{'id': 'abc', 'title': 'A Movie', 'date': '2020-01-02', 'url': url}])

    def test_a_single_video_that_is_live_is_nothing(self):
        url = 'https://www.youtube.com/watch?v=abc'
        self.answer(url, {'id': 'abc', 'title': 'Live now', 'webpage_url': url, 'live_status': 'is_live'})
        self.assertEqual(rt.list_ytdlp(url)[1], [])

    TABS = 'https://www.youtube.com/@StarTalk/podcasts'

    def playlist_entry(self, number):
        return {'_type': 'url', 'ie_key': 'YoutubeTab', 'id': f'PL{number}', 'title': f'Show {number}',
                'url': f'https://www.youtube.com/playlist?list=PL{number}'}

    def test_the_shows_in_a_podcasts_tab_are_opened_and_their_episodes_used(self):
        self.answer(self.TABS, {'_type': 'playlist', 'title': 'StarTalk - Podcasts',
                                'entries': [self.playlist_entry(1), video('stray'), self.playlist_entry(2)]})
        self.answer('https://www.youtube.com/playlist?list=PL1', {'entries': [video('a'), video('b')]})
        self.answer('https://www.youtube.com/playlist?list=PL2', {'entries': [video('b'), video('c')]})
        title, episodes = rt.list_ytdlp(self.TABS)
        self.assertEqual(title, 'StarTalk - Podcasts')
        self.assertEqual([e['id'] for e in episodes], ['a', 'b', 'stray', 'c'])  # tab order, not turned around, no repeats
        self.assertEqual([c[-1] for c in self.calls()],
                         [self.TABS, 'https://www.youtube.com/playlist?list=PL1', 'https://www.youtube.com/playlist?list=PL2'])

    def test_a_show_that_cannot_be_read_does_not_lose_the_others(self):
        self.answer(self.TABS, {'entries': [self.playlist_entry(1), self.playlist_entry(2)]})
        self.answer('https://www.youtube.com/playlist?list=PL1', {'error': 'Playlist does not exist'})
        self.answer('https://www.youtube.com/playlist?list=PL2', {'entries': [video('c')]})
        self.assertEqual([e['id'] for e in rt.list_ytdlp(self.TABS)[1]], ['c'])

    def test_playlists_within_those_playlists_are_not_followed(self):
        self.answer(self.TABS, {'entries': [self.playlist_entry(1)]})
        self.answer('https://www.youtube.com/playlist?list=PL1', {'entries': [self.playlist_entry(9), video('a')]})
        self.assertEqual([e['id'] for e in rt.list_ytdlp(self.TABS)[1]], ['a'])
        self.assertEqual(len(self.calls()), 2)

    def test_only_so_many_shows_are_opened(self):
        entries = [self.playlist_entry(n) for n in range(rt.MAX_PLAYLISTS + 3)]
        self.answer(self.TABS, {'entries': entries})
        for n in range(rt.MAX_PLAYLISTS + 3):
            self.answer(f'https://www.youtube.com/playlist?list=PL{n}', {'entries': [video(f'v{n}')]})
        _, episodes = rt.list_ytdlp(self.TABS)
        self.assertEqual(len(episodes), rt.MAX_PLAYLISTS)
        self.assertEqual(len(self.calls()), rt.MAX_PLAYLISTS + 1)

    def test_a_nested_entry_is_recognised_by_its_type_or_its_address(self):
        self.answer(self.TABS, {'entries': [
            {'_type': 'playlist', 'id': 'X', 'title': 'by type', 'url': 'https://www.youtube.com/playlist?list=X'},
            {'id': 'Y', 'title': 'by address', 'url': 'https://www.youtube.com/playlist?list=Y'}]})
        self.answer('https://www.youtube.com/playlist?list=X', {'entries': [video('x1')]})
        self.answer('https://www.youtube.com/playlist?list=Y', {'entries': [video('y1')]})
        self.assertEqual([e['id'] for e in rt.list_ytdlp(self.TABS)[1]], ['x1', 'y1'])

    def test_the_command_lists_without_touching_the_videos(self):
        self.list(self.PLAYLIST, [video('a')])
        command = self.calls()[0]
        self.assertEqual(command, ['--flat-playlist', '--dump-single-json', '--no-warnings', '--extractor-args',
                                   'youtubetab:approximate_date', '--', self.PLAYLIST])

    def test_extra_arguments_come_before_the_url_and_after_the_defaults(self):
        self.answer(self.PLAYLIST, {'entries': []})
        rt.list_ytdlp(self.PLAYLIST, ['--cookies-from-browser', 'firefox'])
        command = self.calls()[0]
        self.assertEqual(command[-4:], ['--cookies-from-browser', 'firefox', '--', self.PLAYLIST])
        self.assertLess(command.index('--flat-playlist'), command.index('--cookies-from-browser'))

    def test_a_url_that_looks_like_an_option_cannot_be_one(self):
        self.answer('--exec=evil', {'entries': []})
        rt.list_ytdlp('--exec=evil')
        self.assertEqual(self.calls()[0][-2:], ['--', '--exec=evil'])

    def test_a_failure_gives_ytdlps_reason(self):
        self.answer(self.PLAYLIST, {'error': '[youtube:tab] PL1: The playlist does not exist.'})
        with self.assertRaises(ValueError) as caught:
            rt.list_ytdlp(self.PLAYLIST)
        self.assertEqual(str(caught.exception), 'PL1: The playlist does not exist.')

    def test_an_unsupported_site_is_reported(self):
        with self.assertRaises(ValueError) as caught:
            rt.list_ytdlp('https://nowhere.example/x')
        self.assertEqual(str(caught.exception), 'Unsupported URL: https://nowhere.example/x')

    def test_an_answer_that_is_not_json_is_reported(self):
        self.answer(self.PLAYLIST, 'garbage')
        with self.assertRaises(ValueError) as caught:
            rt.list_ytdlp(self.PLAYLIST)
        self.assertIn('not JSON', str(caught.exception))

    def test_no_answer_at_all_is_an_error(self):
        self.answer(self.PLAYLIST, 'empty')
        with self.assertRaises(ValueError):
            rt.list_ytdlp(self.PLAYLIST)

    def test_taking_too_long_is_reported(self):
        with mock.patch.object(rt.subprocess, 'run', side_effect=subprocess.TimeoutExpired('yt-dlp', 1)):
            with self.assertRaises(ValueError) as caught:
                rt.list_ytdlp(self.PLAYLIST)
        self.assertIn('too long', str(caught.exception))

    def test_without_ytdlp_the_listing_says_how_to_get_it(self):
        with mock.patch.dict(os.environ, {'PATH': tempfile.gettempdir() + '/nowhere'}), \
                mock.patch.object(rt.os.path, 'isdir', return_value=False):
            with self.assertRaises(ValueError) as caught:
                rt.list_ytdlp(self.PLAYLIST)
        self.assertIn('yt-dlp is not installed', str(caught.exception))


class TestDownloading(FakeCase):
    def setUp(self):
        super().setUp()
        self.base = os.path.join(self.dir, 'videos', 'Show', 'Show - 2020-01-02 - Episode')
        self.episode = {'id': 'abc', 'title': 'Episode', 'url': 'https://www.youtube.com/watch?v=abc'}

    def run_download(self, mode='ok', **kwargs):
        with mock.patch.dict(os.environ, {'FAKE_MODE': mode}):
            return rt.download_ytdlp(self.episode, self.base, **kwargs)

    def test_the_video_and_its_own_subtitles_are_returned(self):
        saved = self.run_download()
        self.assertEqual(saved['video'], self.base + '.mp4')
        self.assertEqual(saved['subs'], [self.base + '.en.srt', self.base + '.ro.vtt'])  # not the thumbnail, not another episode's
        self.assertTrue(os.path.isfile(saved['video']))

    def test_the_folder_is_made(self):
        self.assertFalse(os.path.exists(os.path.dirname(self.base)))
        self.run_download()
        self.assertTrue(os.path.isdir(os.path.dirname(self.base)))

    def test_an_episode_without_subtitles_has_an_empty_list(self):
        self.assertEqual(self.run_download('nosubs')['subs'], [])

    def test_the_command(self):
        self.run_download()
        command = self.calls()[0]
        self.assertEqual(command[-2:], ['--', self.episode['url']])
        self.assertEqual(command[command.index('-o') + 1], self.base + '.%(ext)s')
        for expected in ('--write-subs', '--no-playlist', '--newline', '--no-simulate', '--progress'):
            self.assertIn(expected, command)
        self.assertEqual(command[command.index('-S') + 1], 'res:1080')
        self.assertEqual(command[command.index('--sub-langs') + 1], 'en.*,-live_chat')
        self.assertEqual(command[command.index('--sub-format') + 1], 'srt/vtt/best')
        self.assertIn('YTDLP_PROGRESS %(progress.downloaded_bytes)s', command[command.index('--progress-template') + 1])
        self.assertTrue(command[command.index('--progress-template') + 1].endswith('%(progress.filename)s'))
        self.assertIn('YTDLP_FILE %(filepath)s', command[command.index('--print') + 1])

    def test_extra_arguments_follow_the_defaults_so_they_win(self):
        self.run_download(extra_args=['-S', 'res:480', '--sub-langs', 'en,ro'])
        command = self.calls()[0]
        self.assertGreater(len(command) - command[::-1].index('res:480') - 1, command.index('res:1080'))
        self.assertGreater(command.index('en,ro'), command.index('en.*,-live_chat'))
        self.assertEqual(command[-2:], ['--', self.episode['url']])

    def test_progress_is_reported_as_bytes_and_a_total(self):
        seen = []
        self.run_download(progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen, [(250, 1000), (500, 1000), (1000, 1000)])

    def test_a_subtitles_own_progress_is_not_shown_as_the_videos(self):
        seen = []
        self.run_download(progress=lambda done, total: seen.append((done, total)))
        self.assertNotIn((44, 44), seen)  # the .srt line comes first in the fake, as it does in the real yt-dlp

    def test_an_estimated_total_is_used_when_the_exact_one_is_not_known_and_unknown_numbers_are_skipped(self):
        seen = []
        self.run_download('variants', progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen, [(100, 2000), (50, None), (7, None)])

    def test_a_failure_gives_ytdlps_reason_and_nothing_else(self):
        with self.assertRaises(ValueError) as caught:
            self.run_download('fail')
        self.assertEqual(str(caught.exception), 'abc: Video unavailable. This video is private')

    def test_finishing_without_saying_where_the_file_is_an_error(self):
        with self.assertRaises(ValueError) as caught:
            self.run_download('nofile')
        self.assertIn('finished without saving a file', str(caught.exception))

    def test_cancelling_stops_yt_dlp_quickly(self):
        started = time.monotonic()
        with self.assertRaises(rt.DownloadCancelled):
            self.run_download('slow', cancelled=lambda: True)
        self.assertLess(time.monotonic() - started, 10)  # the fake would otherwise sleep for 30 seconds

    def test_without_ytdlp_the_download_says_how_to_get_it(self):
        with mock.patch.dict(os.environ, {'PATH': tempfile.gettempdir() + '/nowhere'}), \
                mock.patch.object(rt.os.path, 'isdir', return_value=False):
            with self.assertRaises(ValueError) as caught:
                rt.download_ytdlp(self.episode, self.base)
        self.assertIn('yt-dlp is not installed', str(caught.exception))


class RealPage:
    """A web page with a video and two subtitle tracks, which yt-dlp's generic
    extractor reads, so the real binary can be run without the internet."""

    def __init__(self):
        routes = {
            '/page.html': ('text/html', b'<html><head><title>A Local Movie</title></head><body>'
                           b'<video controls src="/clip.mp4">'
                           b'<track kind="subtitles" srclang="en" label="English" src="/en.vtt">'
                           b'<track kind="subtitles" srclang="ro" label="Romanian" src="/ro.vtt"></video></body></html>'),
            '/clip.mp4': ('video/mp4', b'not really a video but yt-dlp does not look inside' * 100),
            '/en.vtt': ('text/vtt', b'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n'),
            '/ro.vtt': ('text/vtt', b'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nSalut\n'),
        }

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                route = routes.get(self.path.split('?')[0])
                if not route:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', route[0])
                self.send_header('Content-Length', str(len(route[1])))
                self.end_headers()
                self.wfile.write(route[1])

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/page.html'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@unittest.skipUnless(shutil.which('yt-dlp'), 'yt-dlp is not installed')
class TestWithTheRealYtDlp(unittest.TestCase):
    """The real binary against a local page (each run of it takes a second or two,
    so the page is listed once for the whole class)."""

    @classmethod
    def setUpClass(cls):
        cls.page = RealPage()
        cls.addClassCleanup(cls.page.close)
        cls.dir = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.dir, ignore_errors=True)
        cls.registry = rt.MediaSources(os.path.join(cls.dir, 'plugins'))
        cls.driver, given = cls.registry.driver_for('yt-dlp:' + cls.page.url)  # the prefix is how any site is added
        cls.title, cls.episodes = cls.driver.list_episodes(given)

    def test_a_page_with_a_video_is_listed_as_one_episode(self):
        self.assertEqual(self.driver.name, 'yt-dlp')
        self.assertEqual(len(self.episodes), 1)
        episode = self.episodes[0]
        self.assertTrue(episode['id'])
        self.assertEqual((episode['url'], episode['driver']), (self.page.url, 'yt-dlp'))
        self.assertTrue(self.title)

    def test_the_video_and_its_english_subtitles_are_downloaded_through_the_driver(self):
        base = os.path.join(self.dir, 'Show', 'Show - Movie')
        seen = []
        saved = rt.normalize_saved(self.driver.download(self.episodes[0], base, lambda d, t: seen.append((d, t)), lambda: False))
        self.assertEqual(saved['video'], base + '.mp4')
        self.assertGreater(os.path.getsize(saved['video']), 1000)
        self.assertEqual(saved['subs'], [base + '.en.vtt'])  # the default asks for English only
        with open(saved['subs'][0]) as f:
            self.assertIn('Hello', f.read())
        self.assertTrue(seen)  # progress comes through, though yt-dlp is quiet because of --print
        self.assertEqual(seen[-1][0], os.path.getsize(saved['video']))

    def test_other_languages_come_with_the_extra_arguments(self):
        base = os.path.join(self.dir, 'Other', 'Other - Movie')
        saved = rt.download_ytdlp(self.episodes[0], base, extra_args=['--sub-langs', 'en,ro'])
        self.assertEqual(saved['subs'], [base + '.en.vtt', base + '.ro.vtt'])

    def test_a_page_that_is_not_there_is_an_error_with_a_reason(self):
        with self.assertRaises(ValueError) as caught:
            rt.list_ytdlp(self.page.url.replace('page.html', 'missing.html'))
        self.assertTrue(str(caught.exception))


if __name__ == '__main__':
    unittest.main()
