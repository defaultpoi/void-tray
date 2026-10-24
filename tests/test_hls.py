import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from facade import rt


MEDIA_PLAYLIST = ('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXT-X-PLAYLIST-TYPE:VOD\n'
                  '#EXTINF:10.000,\nseg0.ts\n#EXTINF:10.000,\nseg1.ts\n#EXTINF:5.500,\nseg2.ts\n#EXT-X-ENDLIST\n')
MASTER_PLAYLIST = ('#EXTM3U\n'
                   '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=358300,RESOLUTION=720x360,CODECS="avc1.4d401f,mp4a.40.2"\nlow.m3u8?token=a\n'
                   '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1276054,RESOLUTION=1440x720,CODECS="avc1.640020,mp4a.40.2"\nhigh/index.m3u8?token=b\n')

FAKE_FFMPEG = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as f:
    f.write(json.dumps(args) + '\n')
mode = os.environ.get('FAKE_FF_MODE', 'ok')
out = args[-1]
if mode == 'fail':
    print('[hls @ 0x55] Error when loading first segment', flush=True)
    print('Invalid data found when processing input', flush=True)
    sys.exit(1)
if mode == 'stall':
    time.sleep(60)
if mode == 'slow':
    print('out_time_us=1000000', flush=True)
    time.sleep(60)
if mode == 'noisy':   # far more output than a pipe holds, with nobody reading it fast
    for i in range(60000):
        print('frame=%d' % i)
    sys.stdout.flush()
steps = [(1, 1000), (2, 2000), (3, 3000)]
if mode == 'nan':
    steps = []
    print('out_time_us=N/A\nout_time_ms=-9223372036854775807\ntotal_size=N/A\nprogress=continue', flush=True)
for seconds, size in steps:
    print('out_time_us=%d\nout_time_ms=%d\ntotal_size=%d\nspeed=1x\nprogress=continue' % (seconds * 1000000, seconds * 1000000, size), flush=True)
    time.sleep(0.12)
if mode != 'empty':
    open(out, 'wb').write(b'video' * 100)
else:
    open(out, 'wb').close()
print('progress=end', flush=True)
'''


class Playlists:
    """A local web server for playlists: routes map a path to text; the headers of
    every request are kept. Optionally it insists on a Referer."""

    def __init__(self, routes, referer=None):
        self.routes, self.referer, self.header_log, self.paths = routes, referer, [], []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                site.header_log.append(dict(self.headers))
                site.paths.append(self.path)
                if site.referer and self.headers.get('Referer') != site.referer:
                    self.send_error(403)
                    return
                body = site.routes.get(self.path.split('?')[0])
                if body is None:
                    self.send_error(404)
                    return
                body = body.encode() if isinstance(body, str) else body
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TestPlaylistReading(unittest.TestCase):
    def test_hls_addresses(self):
        for url in ('https://h/x/master.m3u8', 'https://h/x/index.M3U8?token=a', 'https://h/x/list.m3u'):
            self.assertTrue(rt.is_hls(url), url)
        for url in ('https://h/x/video.mp4', 'https://h/x/m3u8', 'https://h/x/page?file=a.m3u8', ''):
            self.assertFalse(rt.is_hls(url), url)

    def test_the_variants_of_a_master_playlist(self):
        variants = rt.parse_master(MASTER_PLAYLIST, 'https://cdn.example/a/master.m3u8?x=1')
        self.assertEqual(variants, [
            {'url': 'https://cdn.example/a/low.m3u8?token=a', 'bandwidth': 358300, 'height': 360},
            {'url': 'https://cdn.example/a/high/index.m3u8?token=b', 'bandwidth': 1276054, 'height': 720}])

    def test_a_media_playlist_has_no_variants(self):
        self.assertEqual(rt.parse_master(MEDIA_PLAYLIST, 'https://h/index.m3u8'), [])

    def test_absolute_variant_urls_and_missing_attributes(self):
        text = '#EXTM3U\n#EXT-X-STREAM-INF:PROGRAM-ID=1\nhttps://other.example/v.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=5\n'
        self.assertEqual(rt.parse_master(text, 'https://h/m.m3u8'), [{'url': 'https://other.example/v.m3u8', 'bandwidth': 0, 'height': 0}])

    def test_a_comment_between_the_tag_and_its_uri_is_skipped(self):
        text = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\n# a comment\n\nv.m3u8\n'
        self.assertEqual(rt.parse_master(text, 'https://h/m.m3u8')[0]['url'], 'https://h/v.m3u8')

    def test_the_best_variant_has_the_most_bandwidth_then_the_most_height(self):
        variants = [{'url': 'a', 'bandwidth': 100, 'height': 1080}, {'url': 'b', 'bandwidth': 300, 'height': 360},
                    {'url': 'c', 'bandwidth': 300, 'height': 720}]
        self.assertEqual(rt.best_variant(variants)['url'], 'c')

    def test_the_length_is_the_sum_of_the_segments(self):
        self.assertEqual(rt.playlist_seconds(MEDIA_PLAYLIST), 25.5)
        self.assertEqual(rt.playlist_seconds('#EXTM3U\n'), 0)
        self.assertEqual(rt.playlist_seconds('#EXTM3U\n#EXTINF:abc,\n'), 0)

    def test_headers_for_ffmpeg(self):
        self.assertEqual(rt._ffmpeg_headers({'Referer': 'https://e/', 'User-Agent': 'UA', 'Cookie': 'a=b'}),
                         ['-user_agent', 'UA', '-headers', 'Referer: https://e/\r\nCookie: a=b\r\n'])
        self.assertEqual(rt._ffmpeg_headers({'user-agent': 'lower'}), ['-user_agent', 'lower'])
        self.assertEqual(rt._ffmpeg_headers({})[0], '-user_agent')
        self.assertEqual(rt._ffmpeg_headers({})[1], rt.DEFAULT_USER_AGENT)

    def test_ffmpegs_complaint_is_its_last_line_that_is_not_progress(self):
        lines = ['out_time_us=5', '[hls] Error when loading first segment', 'progress=end', '', 'Invalid data found', 'total_size=1']
        self.assertEqual(rt._ffmpeg_error_text(lines), 'Invalid data found')
        self.assertEqual(rt._ffmpeg_error_text(['out_time_us=5']), 'ffmpeg failed')
        self.assertEqual(rt._ffmpeg_error_text([]), 'ffmpeg failed')


class FakeFfmpegCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        bin_dir = os.path.join(self.dir, 'bin')
        os.mkdir(bin_dir)
        script = os.path.join(bin_dir, 'ffmpeg')
        with open(script, 'w') as f:
            f.write(FAKE_FFMPEG)
        os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.dir, 'ffmpeg.log')
        patcher = mock.patch.dict(os.environ, {'PATH': bin_dir + os.pathsep + os.environ.get('PATH', ''), 'FAKE_LOG': self.log})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(rt, 'POLL_SECONDS', 0.05)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.site = Playlists({'/index.m3u8': MEDIA_PLAYLIST, '/master.m3u8': MASTER_PLAYLIST,
                               '/high/index.m3u8': MEDIA_PLAYLIST, '/low.m3u8': MEDIA_PLAYLIST, '/live.m3u8': '#EXTM3U\n#EXTINF:5,\na.ts\n',
                               '/empty.m3u8': '#EXTM3U\n#EXT-X-ENDLIST\n'})
        self.addCleanup(self.site.close)
        self.dest = os.path.join(self.dir, 'videos', 'Dr. House', 'Dr. House - S01E01 - Pilot.mkv')

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def run_download(self, url='/index.m3u8', mode='ok', **kwargs):
        with mock.patch.dict(os.environ, {'FAKE_FF_MODE': mode}):
            return rt.download_hls(self.site.base + url, self.dest, **kwargs)


class TestDownloadingWithAFakeFfmpeg(FakeFfmpegCase):
    def test_the_file_appears_under_its_name_and_the_dots_in_it_survive(self):
        self.assertEqual(self.run_download(), self.dest)
        self.assertTrue(os.path.isfile(self.dest))
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.dest))), ['Dr. House - S01E01 - Pilot.mkv'])  # no .part left

    def test_ffmpeg_writes_a_part_file_and_copies_the_streams(self):
        self.run_download(headers={'Referer': 'https://embed.example/', 'User-Agent': 'UA'})
        command = self.calls()[0]
        self.assertEqual(command[:4], ['-nostdin', '-y', '-loglevel', 'error'])
        self.assertEqual(command[command.index('-user_agent') + 1], 'UA')
        self.assertEqual(command[command.index('-headers') + 1], 'Referer: https://embed.example/\r\n')
        self.assertEqual(command[command.index('-i') + 1], self.site.base + '/index.m3u8')
        self.assertEqual(command[command.index('-c') + 1], 'copy')  # audio included: no re-encoding
        self.assertNotIn('aac', command)
        self.assertEqual(command[command.index('-f') + 1], 'matroska')
        self.assertEqual(command[command.index('-progress') + 1], 'pipe:1')
        self.assertEqual(command[-1], self.dest + '.part')
        self.assertEqual(command.count('-map'), 2)

    def test_a_master_playlist_is_read_and_the_best_variant_downloaded(self):
        self.run_download('/master.m3u8', headers={'X-Test': '1'})
        command = self.calls()[0]
        self.assertEqual(command[command.index('-i') + 1], self.site.base + '/high/index.m3u8?token=b')
        self.assertEqual(self.site.paths, ['/master.m3u8', '/high/index.m3u8?token=b'])
        self.assertTrue(all(h.get('X-Test') == '1' for h in self.site.header_log))  # the playlists were asked for the same way

    def test_the_containers(self):
        for extension, name in (('.ts', 'mpegts'), ('.mkv', 'matroska'), ('.mp4', 'mp4'), ('.MKV', 'matroska')):
            self.dest = os.path.join(self.dir, 'out', 'name' + extension)
            self.run_download()
            command = self.calls()[-1]
            self.assertEqual(command[command.index('-f') + 1], name, extension)
            self.assertEqual('-bsf:a' in command, extension == '.mp4', extension)  # mp4 wants the AAC header turned around

    def test_other_extensions_are_refused_before_anything_runs(self):
        for extension in ('.avi', '', '.m3u8'):
            with self.assertRaises(ValueError, msg=extension):
                rt.download_hls(self.site.base + '/index.m3u8', os.path.join(self.dir, 'x' + extension))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.site.paths, [])

    def test_progress_is_in_seconds_against_the_length_of_the_playlist(self):
        seen = []
        self.run_download(progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen[-1], (25, 25))  # finished: exactly the length
        self.assertTrue(all(total == 25 for _d, total in seen))
        self.assertEqual([d for d, _t in seen], sorted(d for d, _t in seen))
        self.assertGreater(len(seen), 2)

    def test_a_playlist_without_a_length_has_no_total(self):
        seen = []
        self.site.routes['/empty.m3u8'] = '#EXTM3U\n#EXT-X-ENDLIST\n'
        self.run_download('/empty.m3u8', progress=lambda done, total: seen.append((done, total)))
        self.assertTrue(seen)
        self.assertTrue(all(total is None for _d, total in seen))

    def test_unknown_values_from_ffmpeg_are_ignored(self):
        seen = []
        self.run_download(mode='nan', progress=lambda done, total: seen.append((done, total)))
        self.assertTrue(os.path.isfile(self.dest))
        self.assertTrue(all(done >= 0 for done, _t in seen))

    def test_a_stream_that_is_still_being_recorded_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.run_download('/live.m3u8')
        self.assertIn('live or not finished', str(caught.exception))
        self.assertEqual(self.calls(), [])

    def test_a_failure_gives_ffmpegs_reason_and_leaves_nothing(self):
        with self.assertRaises(ValueError) as caught:
            self.run_download(mode='fail')
        self.assertEqual(str(caught.exception), 'Invalid data found when processing input')
        self.assertEqual(os.listdir(os.path.dirname(self.dest)), [])

    def test_ffmpeg_that_saves_nothing_is_an_error(self):
        with self.assertRaises(ValueError) as caught:
            self.run_download(mode='empty')
        self.assertIn('without saving anything', str(caught.exception))
        self.assertFalse(os.path.exists(self.dest))
        self.assertEqual(os.listdir(os.path.dirname(self.dest)), [])

    def test_a_server_that_refuses_the_playlist_is_reported(self):
        self.site.referer = 'https://embed.example/'
        with self.assertRaises(urllib.error.HTTPError):
            self.run_download()
        self.assertEqual(self.calls(), [])
        self.run_download(headers={'Referer': 'https://embed.example/'})  # and the right header gets through
        self.assertTrue(os.path.isfile(self.dest))

    def test_a_stream_that_stops_delivering_is_given_up_on(self):
        started = time.monotonic()
        with self.assertRaises(ValueError) as caught:
            self.run_download(mode='stall', stall_seconds=0.6)
        self.assertIn('stopped delivering', str(caught.exception))
        self.assertLess(time.monotonic() - started, 10)  # the fake would have slept for a minute
        self.assertEqual(os.listdir(os.path.dirname(self.dest)), [])

    def test_progress_that_keeps_moving_is_not_a_stall(self):
        self.run_download(stall_seconds=0.4)  # the fake reports every 0.12 s for about 0.4 s
        self.assertTrue(os.path.isfile(self.dest))

    def test_cancelling_stops_ffmpeg_and_removes_the_partial_file(self):
        started = time.monotonic()
        calls = []

        def cancelled():
            calls.append(1)
            return len(calls) > 2
        with self.assertRaises(rt.DownloadCancelled):
            self.run_download(mode='slow', cancelled=cancelled)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(os.listdir(os.path.dirname(self.dest)), [])

    def test_a_great_deal_of_output_does_not_hold_ffmpeg_up(self):
        started = time.monotonic()
        self.run_download(mode='noisy')
        self.assertLess(time.monotonic() - started, 10)  # a reader that stopped reading would block ffmpeg for ever
        self.assertTrue(os.path.isfile(self.dest))

    def test_an_error_in_the_progress_callback_stops_ffmpeg_too(self):
        def boom(done, total):
            raise RuntimeError('ui went away')
        with self.assertRaises(RuntimeError):
            self.run_download(mode='slow', progress=boom)
        self.assertEqual(os.listdir(os.path.dirname(self.dest)), [])

    def test_without_ffmpeg_it_says_how_to_get_it(self):
        with mock.patch.dict(os.environ, {'PATH': tempfile.gettempdir() + '/nowhere'}), mock.patch.object(rt.os.path, 'isdir', return_value=False):
            with self.assertRaises(ValueError) as caught:
                rt.download_hls(self.site.base + '/index.m3u8', self.dest)
        self.assertIn('ffmpeg is not installed', str(caught.exception))
        self.assertEqual(self.site.paths, [])


class RealStream:
    """A short HLS stream made by ffmpeg and served locally, only to clients that send the right Referer."""

    REFERER = 'https://embed.example/'

    def __init__(self):
        self.dir = tempfile.mkdtemp()
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=duration=12:size=160x90:rate=10',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=12', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                        '-g', '10', '-c:a', 'aac', '-shortest', '-f', 'hls', '-hls_time', '2', '-hls_list_size', '0', '-hls_playlist_type', 'vod',
                        os.path.join(self.dir, 'index.m3u8')], check=True)
        routes = {}
        for name in os.listdir(self.dir):
            with open(os.path.join(self.dir, name), 'rb') as f:
                routes['/' + name] = f.read()
        routes['/master.m3u8'] = ('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100,RESOLUTION=80x45\nnot-there.m3u8\n'
                                  '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=160x90\nindex.m3u8?token=abc\n')
        self.site = Playlists(routes, referer=self.REFERER)
        self.base = self.site.base

    def close(self):
        self.site.close()
        shutil.rmtree(self.dir, ignore_errors=True)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg is not installed')
class TestWithTheRealFfmpeg(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stream = RealStream()
        cls.addClassCleanup(cls.stream.close)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def duration(self, path):
        out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', path],
                             capture_output=True, text=True).stdout
        return float(out)

    def test_a_stream_behind_a_referer_is_downloaded_whole(self):
        dest = os.path.join(self.dir, 'Dr. House', 'Dr. House - S01E01 - Pilot.mkv')
        seen = []
        rt.download_hls(self.stream.base + '/master.m3u8', dest, headers={'Referer': RealStream.REFERER},
                        progress=lambda done, total: seen.append((done, total)))
        self.assertAlmostEqual(self.duration(dest), 12, delta=1)
        self.assertEqual(seen[-1][0], seen[-1][1])
        self.assertAlmostEqual(seen[-1][1], 12, delta=1)  # the total came from the playlist
        self.assertEqual(os.listdir(os.path.dirname(dest)), ['Dr. House - S01E01 - Pilot.mkv'])
        streams = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name', '-of', 'csv=p=0', dest],
                                 capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(streams), ['aac', 'h264'])  # copied, not re-encoded

    def test_the_other_containers_work_too(self):
        for extension in ('.ts', '.mp4'):
            dest = os.path.join(self.dir, 'x' + extension)
            rt.download_hls(self.stream.base + '/index.m3u8', dest, headers={'Referer': RealStream.REFERER})
            self.assertAlmostEqual(self.duration(dest), 12, delta=1, msg=extension)

    def test_without_the_referer_the_download_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError):
            rt.download_hls(self.stream.base + '/master.m3u8', os.path.join(self.dir, 'x.mkv'))

    def test_cancelling_a_real_download_leaves_nothing(self):
        dest = os.path.join(self.dir, 'x.mkv')
        with self.assertRaises(rt.DownloadCancelled):
            rt.download_hls(self.stream.base + '/index.m3u8', dest, headers={'Referer': RealStream.REFERER}, cancelled=lambda: True)
        self.assertEqual(os.listdir(self.dir), [])


if __name__ == '__main__':
    unittest.main()
