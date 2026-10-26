import unittest
from unittest import mock

from facade import rt


class FakeStreamlinkProc:
    def __init__(self, output_lines):
        self.stdout = iter(output_lines)
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class TestLiveRowHelpers(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, rt, '_playing', rt._playing)

    def test_window_title_is_bullet_separated_and_skips_empties(self):
        self.assertEqual(rt.stream_window_title('Twitch', 'user', 'My title'), 'Twitch \u00b7 user \u00b7 My title')
        self.assertEqual(rt.stream_window_title('YouTube', 'user', None), 'YouTube \u00b7 user')
        self.assertEqual(rt.stream_window_title('YouTube', 'user', ''), 'YouTube \u00b7 user')

    def test_marker_is_dot_or_text_form_triangle(self):
        self.assertIn('\u25cf', rt.live_marker_markup('#123456', False))
        playing = rt.live_marker_markup('#123456', True)
        self.assertIn('\u25b6\ufe0e', playing)
        self.assertNotIn('\u25cf', playing)
        self.assertIn('#123456', playing)

    def test_is_stream_playing_tracks_site_channel_and_process(self):
        proc = FakeStreamlinkProc([])
        rt._playing = (proc, 'Twitch', 'chan')
        self.assertTrue(rt.is_stream_playing('Twitch', 'chan'))
        self.assertFalse(rt.is_stream_playing('YouTube', 'chan'))
        self.assertFalse(rt.is_stream_playing('Twitch', 'other'))
        proc.terminated = True  # streamlink exited -> no longer playing
        self.assertFalse(rt.is_stream_playing('Twitch', 'chan'))

    def test_nothing_playing(self):
        rt._playing = None
        self.assertFalse(rt.is_stream_playing('Twitch', 'chan'))

    def test_playing_is_recorded_after_successful_load_with_bullet_title(self):
        rt._streamlink_proc = None
        proc = FakeStreamlinkProc(['access with one of:\n'])
        titles = []
        with mock.patch.object(rt.subprocess, 'Popen', return_value=proc), \
                mock.patch.object(rt, '_load_in_mpv', side_effect=lambda u, t: titles.append(t) or True), \
                mock.patch.object(rt.threading, 'Thread'), mock.patch.object(rt.threading, 'Timer'):
            rt._play_in_shared_mpv('twitch.tv/x', 'best', 'Twitch', 'x', 'https://twitch.tv/x', 'Stream title')
        self.assertEqual(titles, ['Twitch \u00b7 x \u00b7 Stream title'])
        self.assertTrue(rt.is_stream_playing('Twitch', 'x'))
        rt._streamlink_proc = None


class TestOpenLiveStream(unittest.TestCase):
    def _which_ok(self):
        return mock.patch.object(rt.shutil, 'which', side_effect=lambda x: '/usr/bin/' + x)

    def test_twitch_starts_worker_thread_with_url_and_quality(self):
        with self._which_ok(), mock.patch.object(rt.threading, 'Thread') as thread, \
                mock.patch.object(rt.webbrowser, 'open') as wb_open:
            rt.open_twitch_stream('somechan', '720p60')
        thread.assert_called_once()
        self.assertIs(thread.call_args.kwargs['target'], rt._play_in_shared_mpv)
        self.assertEqual(thread.call_args.kwargs['args'], (
            'twitch.tv/somechan', '720p60', 'Twitch', 'somechan', 'https://twitch.tv/somechan', None, None))
        thread.return_value.start.assert_called_once()
        wb_open.assert_not_called()

    def test_twitch_defaults_to_best_quality(self):
        with self._which_ok(), mock.patch.object(rt.threading, 'Thread') as thread, \
                mock.patch.object(rt.webbrowser, 'open'):
            rt.open_twitch_stream('somechan')
        self.assertEqual(thread.call_args.kwargs['args'][1], 'best')

    def test_twitch_falls_back_to_browser_when_streamlink_missing(self):
        with mock.patch.object(rt.shutil, 'which', return_value=None), \
                mock.patch.object(rt.threading, 'Thread') as thread, \
                mock.patch.object(rt.webbrowser, 'open') as wb_open:
            rt.open_twitch_stream('somechan')
        thread.assert_not_called()
        wb_open.assert_called_once_with('https://twitch.tv/somechan')

    def test_twitch_falls_back_to_browser_when_thread_start_raises(self):
        with self._which_ok(), mock.patch.object(rt.threading, 'Thread', side_effect=RuntimeError), \
                mock.patch.object(rt.webbrowser, 'open') as wb_open:
            rt.open_twitch_stream('somechan')
        wb_open.assert_called_once_with('https://twitch.tv/somechan')

    def test_youtube_starts_worker_thread(self):
        with self._which_ok(), mock.patch.object(rt.threading, 'Thread') as thread, \
                mock.patch.object(rt.webbrowser, 'open') as wb_open:
            rt.open_youtube_stream('@somehandle', '720p60')
        self.assertEqual(thread.call_args.kwargs['args'], (
            'https://www.youtube.com/@somehandle/live', '720p60', 'YouTube',
            '@somehandle', 'https://www.youtube.com/@somehandle', None, None))
        wb_open.assert_not_called()

    def test_a_display_name_travels_with_the_title_to_the_worker(self):
        with self._which_ok(), mock.patch.object(rt.threading, 'Thread') as thread:
            rt.open_twitch_stream('somechan', '720p60', title='Stream title', name='Some Name')
            twitch_args = thread.call_args.kwargs['args']
            rt.open_youtube_stream('@somehandle', '720p60', title='Stream title', name='Other Name')
            youtube_args = thread.call_args.kwargs['args']
        self.assertEqual(twitch_args[-2:], ('Stream title', 'Some Name'))
        self.assertEqual(youtube_args[-2:], ('Stream title', 'Other Name'))

    def test_youtube_falls_back_to_channel_page_when_mpv_missing(self):
        with mock.patch.object(rt.shutil, 'which', side_effect=lambda x: None if x == 'mpv' else '/x'), \
                mock.patch.object(rt.webbrowser, 'open') as wb_open:
            rt.open_youtube_stream('@somehandle')
        wb_open.assert_called_once_with('https://www.youtube.com/@somehandle')


class TestDisplayNameInTheWindowTitle(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, rt, '_playing', rt._playing)
        self.addCleanup(setattr, rt, '_streamlink_proc', rt._streamlink_proc)

    def play(self, name):
        rt._streamlink_proc = None
        proc = FakeStreamlinkProc(['access with one of:\n'])
        titles = []
        with mock.patch.object(rt.subprocess, 'Popen', return_value=proc), \
                mock.patch.object(rt, '_load_in_mpv', side_effect=lambda u, t: titles.append(t) or True), \
                mock.patch.object(rt.threading, 'Thread') as thread, mock.patch.object(rt.threading, 'Timer'):
            rt._play_in_shared_mpv('twitch.tv/x', 'best', 'Twitch', 'x', 'https://twitch.tv/x', 'Stream title', name)
        return titles, thread

    def test_the_window_is_titled_with_the_display_name_instead_of_the_channel(self):
        titles, _ = self.play('Some Name')
        self.assertEqual(titles, ['Twitch \u00b7 Some Name \u00b7 Stream title'])

    def test_without_a_display_name_the_channel_is_used_as_before(self):
        titles, _ = self.play(None)
        self.assertEqual(titles, ['Twitch \u00b7 x \u00b7 Stream title'])

    def test_the_name_is_handed_on_to_the_title_refresh(self):
        _, thread = self.play('Some Name')
        refresh = [c for c in thread.call_args_list if c.kwargs.get('target') is rt._apply_stream_title][0]
        self.assertEqual(refresh.kwargs['args'][-1], 'Some Name')

    def refined(self, name, author):
        proc = FakeStreamlinkProc([])
        result = mock.Mock(stdout='{"metadata": {"author": %s, "title": "Live now"}}' % ('"%s"' % author if author else 'null'))
        with mock.patch.object(rt.subprocess, 'run', return_value=result), \
                mock.patch.object(rt, '_is_current_streamlink', return_value=True), \
                mock.patch.object(rt, '_mpv_ipc') as ipc:
            rt._apply_stream_title(proc, 'twitch.tv/x', 'Twitch', 'x', name)
        return ipc.call_args.args[0][0][2]

    def test_the_refined_title_keeps_the_configured_name_over_the_platforms_author(self):
        self.assertEqual(self.refined('Some Name', 'SomeAuthor'), 'Twitch \u00b7 Some Name \u00b7 Live now')

    def test_without_a_name_the_platforms_author_is_used_as_before(self):
        self.assertEqual(self.refined(None, 'SomeAuthor'), 'Twitch \u00b7 SomeAuthor \u00b7 Live now')
        self.assertEqual(self.refined(None, None), 'Twitch \u00b7 x \u00b7 Live now')


class TestSharedMpv(unittest.TestCase):
    def setUp(self):
        rt._streamlink_proc = None
        self.addCleanup(setattr, rt, '_streamlink_proc', None)

    def test_streamlink_serves_locally_then_loads_into_mpv(self):
        proc = FakeStreamlinkProc(['[cli][info] Starting server, access with one of:\n', ' http://127.0.0.1:1/\n'])
        with mock.patch.object(rt.subprocess, 'Popen', return_value=proc) as popen, \
                mock.patch.object(rt, '_load_in_mpv', return_value=True) as load, \
                mock.patch.object(rt.threading, 'Thread'), \
                mock.patch.object(rt.threading, 'Timer'), \
                mock.patch.object(rt.webbrowser, 'open') as wb_open:
            rt._play_in_shared_mpv('twitch.tv/x', '720p60', 'Twitch', 'x', 'https://twitch.tv/x')
        args = popen.call_args[0][0]
        self.assertEqual(args[:5], ['streamlink', '--player-external-http',
                                    '--player-external-http-interface', '127.0.0.1',
                                    '--player-external-http-port'])
        self.assertEqual(args[6:], ['twitch.tv/x', '720p60'])
        # streamlink's log goes to stdout or stderr depending on version: merge them
        self.assertIs(popen.call_args.kwargs['stderr'], rt.subprocess.STDOUT)
        self.assertIs(popen.call_args.kwargs['stdout'], rt.subprocess.PIPE)
        load.assert_called_once_with('http://127.0.0.1:%s/' % args[5], 'Twitch \u00b7 x')
        wb_open.assert_not_called()
        self.assertFalse(proc.terminated)

    def test_never_ready_failure_is_logged_with_last_output(self):
        proc = FakeStreamlinkProc(['error: boom\n'])
        with mock.patch.object(rt.subprocess, 'Popen', return_value=proc), \
                mock.patch.object(rt.webbrowser, 'open'), \
                mock.patch.object(rt, '_log') as log:
            rt._play_in_shared_mpv('twitch.tv/x', 'best', 'Twitch', 'x', 'https://twitch.tv/x')
        self.assertIn('error: boom', log.call_args[0][0])

    def test_browser_fallback_when_streamlink_never_ready(self):
        proc = FakeStreamlinkProc(['error: no plugin\n'])
        with mock.patch.object(rt.subprocess, 'Popen', return_value=proc), \
                mock.patch.object(rt, '_load_in_mpv') as load, \
                mock.patch.object(rt.webbrowser, 'open') as wb_open:
            rt._play_in_shared_mpv('twitch.tv/x', 'best', 'Twitch', 'x', 'https://twitch.tv/x')
        load.assert_not_called()
        wb_open.assert_called_once_with('https://twitch.tv/x')
        self.assertTrue(proc.terminated)

    def test_browser_fallback_when_mpv_load_fails(self):
        proc = FakeStreamlinkProc(['access with one of:\n'])
        with mock.patch.object(rt.subprocess, 'Popen', return_value=proc), \
                mock.patch.object(rt, '_load_in_mpv', return_value=False), \
                mock.patch.object(rt.webbrowser, 'open') as wb_open:
            rt._play_in_shared_mpv('twitch.tv/x', 'best', 'Twitch', 'x', 'https://twitch.tv/x')
        wb_open.assert_called_once_with('https://twitch.tv/x')

    def test_browser_fallback_when_streamlink_launch_raises(self):
        with mock.patch.object(rt.subprocess, 'Popen', side_effect=OSError), \
                mock.patch.object(rt.webbrowser, 'open') as wb_open:
            rt._play_in_shared_mpv('twitch.tv/x', 'best', 'Twitch', 'x', 'https://twitch.tv/x')
        wb_open.assert_called_once_with('https://twitch.tv/x')

    def test_new_click_stops_previous_stream_only_after_mpv_took_the_new_one(self):
        old = FakeStreamlinkProc([])
        rt._streamlink_proc = old
        new = FakeStreamlinkProc(['access with one of:\n'])
        seen = {}

        def load(url, title):
            seen['old_alive_during_load'] = not old.terminated
            return True
        with mock.patch.object(rt.subprocess, 'Popen', return_value=new), \
                mock.patch.object(rt, '_load_in_mpv', side_effect=load), \
                mock.patch.object(rt.threading, 'Thread'), \
                mock.patch.object(rt.threading, 'Timer'):
            rt._play_in_shared_mpv('twitch.tv/y', 'best', 'Twitch', 'y', 'https://twitch.tv/y')
        self.assertTrue(seen['old_alive_during_load'])  # else mpv would quit at EOF
        self.assertTrue(old.terminated)
        self.assertFalse(new.terminated)

    def test_failed_new_click_leaves_previous_stream_playing(self):
        old = FakeStreamlinkProc([])
        rt._streamlink_proc = old
        new = FakeStreamlinkProc(['no marker\n'])
        with mock.patch.object(rt.subprocess, 'Popen', return_value=new), \
                mock.patch.object(rt.webbrowser, 'open'), mock.patch.object(rt, '_log'):
            rt._play_in_shared_mpv('twitch.tv/y', 'best', 'Twitch', 'y', 'https://twitch.tv/y')
        self.assertFalse(old.terminated)
        self.assertTrue(new.terminated)
        self.assertIs(rt._streamlink_proc, old)

    def test_superseded_failure_does_not_open_browser(self):
        def lines():
            rt._streamlink_proc = FakeStreamlinkProc([])  # a newer click took over
            yield 'no marker\n'
        proc = FakeStreamlinkProc([])
        proc.stdout = lines()
        with mock.patch.object(rt.subprocess, 'Popen', return_value=proc), \
                mock.patch.object(rt.webbrowser, 'open') as wb_open:
            rt._play_in_shared_mpv('twitch.tv/x', 'best', 'Twitch', 'x', 'https://twitch.tv/x')
        wb_open.assert_not_called()

    def test_load_in_mpv_sends_title_then_loadfile(self):
        with mock.patch.object(rt, '_ensure_mpv', return_value=True), \
                mock.patch.object(rt, '_mpv_ipc', return_value=True) as ipc:
            self.assertTrue(rt._load_in_mpv('http://127.0.0.1:9/', 'T'))
        ipc.assert_called_once_with([['set_property', 'force-media-title', 'T'],
                                     ['loadfile', 'http://127.0.0.1:9/', 'replace']])

    def test_load_in_mpv_fails_when_mpv_cannot_start(self):
        with mock.patch.object(rt, '_ensure_mpv', return_value=False):
            self.assertFalse(rt._load_in_mpv('u', 'T'))

    def test_load_in_mpv_retries_once(self):
        with mock.patch.object(rt, '_ensure_mpv', return_value=True), \
                mock.patch.object(rt, '_mpv_ipc', side_effect=[False, True]), \
                mock.patch.object(rt.time_module, 'sleep'):
            self.assertTrue(rt._load_in_mpv('u', 'T'))

    def test_ensure_mpv_reuses_running_instance(self):
        with mock.patch.object(rt, '_mpv_ipc', return_value=True), \
                mock.patch.object(rt.subprocess, 'Popen') as popen:
            self.assertTrue(rt._ensure_mpv())
        popen.assert_not_called()

    def test_ensure_mpv_launches_maximized_idle_with_ipc(self):
        mpv = mock.Mock()
        mpv.poll.return_value = None
        with mock.patch.object(rt, '_mpv_ipc', side_effect=[False, True]), \
                mock.patch.object(rt.subprocess, 'Popen', return_value=mpv) as popen, \
                mock.patch.object(rt.time_module, 'sleep'):
            self.assertTrue(rt._ensure_mpv())
        args = popen.call_args[0][0]
        self.assertEqual(args[0], 'mpv')
        self.assertIn('--window-maximized=yes', args)
        self.assertIn('--idle=once', args)
        self.assertIn(f'--input-ipc-server={rt.MPV_IPC_SOCKET}', args)

    def test_ensure_mpv_false_when_mpv_dies_at_startup(self):
        mpv = mock.Mock()
        mpv.poll.return_value = 1
        with mock.patch.object(rt, '_mpv_ipc', return_value=False), \
                mock.patch.object(rt.subprocess, 'Popen', return_value=mpv):
            self.assertFalse(rt._ensure_mpv())



class FakeMpvSocket:
    """A unix socket speaking enough of mpv's JSON IPC to answer get_property."""

    def __init__(self, path, properties, hang_up=False):
        import socket
        import threading
        self.path = path
        self.properties = properties
        self.hang_up = hang_up
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(1)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        import json
        conn, _ = self.sock.accept()
        with conn:
            buf = b''
            while True:
                chunk = conn.recv(4096)
                if not chunk or self.hang_up:
                    return
                buf += chunk
                *lines, buf = buf.split(b'\n')
                for line in lines:
                    request = json.loads(line)
                    name = request['command'][1]
                    conn.sendall(b'{"event": "property-change"}\n')  # events come between replies
                    if name in self.properties:
                        reply = {'data': self.properties[name], 'request_id': request['request_id'], 'error': 'success'}
                    else:
                        reply = {'request_id': request['request_id'], 'error': 'property unavailable'}
                    conn.sendall((json.dumps(reply) + '\n').encode())

    def close(self):
        self.sock.close()


class TestMpvProperties(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = self.dir + '/mpv.sock'
        patcher = mock.patch.object(rt, 'MPV_IPC_SOCKET', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def serve(self, properties, **kwargs):
        server = FakeMpvSocket(self.path, properties, **kwargs)
        self.addCleanup(server.close)

    def test_properties_come_back_by_name(self):
        self.serve({'path': '/v/a.mp4', 'percent-pos': 41.5})
        self.assertEqual(rt._mpv_get_properties(['path', 'percent-pos']), {'path': '/v/a.mp4', 'percent-pos': 41.5})

    def test_a_property_mpv_cannot_give_is_none(self):
        self.serve({'path': '/v/a.mp4'})
        self.assertEqual(rt._mpv_get_properties(['path', 'time-remaining']), {'path': '/v/a.mp4', 'time-remaining': None})

    def test_an_mpv_that_is_not_running_gives_none(self):
        self.assertIsNone(rt._mpv_get_properties(['path']))

    def test_an_mpv_that_hangs_up_gives_none(self):
        self.serve({}, hang_up=True)
        self.assertIsNone(rt._mpv_get_properties(['path']))


class TestCountsAsWatched(unittest.TestCase):
    def test_through_to_the_credits(self):
        self.assertTrue(rt.counts_as_watched(95.0, 60.0))
        self.assertTrue(rt.counts_as_watched(90.0, None))

    def test_the_last_seconds_of_something_mostly_played(self):
        self.assertTrue(rt.counts_as_watched(75.0, 12.0))
        self.assertFalse(rt.counts_as_watched(75.0, 300.0))

    def test_a_clip_that_was_barely_started_is_not_watched(self):
        self.assertFalse(rt.counts_as_watched(10.0, 5.0))

    def test_never_seen_playing_is_not_watched(self):
        self.assertFalse(rt.counts_as_watched(None, None))
        self.assertFalse(rt.counts_as_watched(None, 3.0))

    def test_stopped_part_way(self):
        self.assertFalse(rt.counts_as_watched(40.0, 900.0))


class TestWatchLocalPlayback(unittest.TestCase):
    def watch(self, script, clock=None, total=None):
        """Runs the watcher over scripted poll results; returns what on_finished got."""
        results = iter(script)
        done = []
        self.asked = []
        with mock.patch.object(rt, '_mpv_get_properties', side_effect=lambda names: self.asked.append(list(names)) or next(results)), \
                mock.patch.object(rt.time_module, 'sleep'), \
                mock.patch.object(rt.time_module, 'monotonic', side_effect=clock or (lambda: 0.0)):
            rt._watch_local_playback('/v/a.mp4', lambda percent, remaining: done.append((percent, remaining)), total_seconds=total)
        return done

    @staticmethod
    def at(percent, remaining, path='/v/a.mp4'):
        return {'path': path, 'percent-pos': percent, 'time-remaining': remaining}

    def test_it_reports_the_furthest_point_when_mpv_quits(self):
        self.assertEqual(self.watch([self.at(5, 95), self.at(50, 50), self.at(96, 4), None]), [(96, 4)])

    def test_it_keeps_the_best_even_if_the_person_seeks_back(self):
        self.assertEqual(self.watch([self.at(80, 20), self.at(10, 90), None]), [(80, 20)])

    def test_it_waits_for_the_file_to_load(self):
        idle = {'path': None, 'percent-pos': None, 'time-remaining': None}
        self.assertEqual(self.watch([idle, idle, self.at(30, 70), None]), [(30, 70)])

    def test_another_file_taking_its_place_ends_the_watch(self):
        other = self.at(1, 99, path='/v/b.mp4')
        self.assertEqual(self.watch([self.at(60, 40), other, self.at(99, 1)]), [(60, 40)])

    def test_a_file_that_never_loads_gives_up(self):
        idle = {'path': '/v/other.mp4', 'percent-pos': 1, 'time-remaining': 9}
        ticks = iter([0.0, 1.0, rt.WATCH_LOAD_TIMEOUT_SECONDS + 1])
        self.assertEqual(self.watch([idle, idle, idle], clock=lambda: next(ticks)), [(None, None)])

    def test_mpv_gone_before_anything_loaded(self):
        self.assertEqual(self.watch([None]), [(None, None)])

    def test_against_a_known_length_how_far_it_got_comes_from_the_time_not_mpvs_percent(self):
        # mpv's own percent for a file that is still growing is against its length so far: it says 95 % here
        growing = {'path': '/v/a.mp4', 'percent-pos': 95.0, 'time-remaining': 3.0, 'time-pos': 600.0}
        self.assertEqual(self.watch([growing, None], total=2400), [(25.0, 1800.0)])

    def test_it_asks_for_the_time_only_when_it_has_a_length(self):
        self.watch([None])
        self.assertEqual(self.asked[0], ['path', 'percent-pos', 'time-remaining'])
        self.watch([None], total=100)
        self.assertEqual(self.asked[0], ['path', 'percent-pos', 'time-remaining', 'time-pos'])

    def test_the_furthest_point_and_the_least_remaining_are_kept_against_a_known_length(self):
        at = lambda pos: {'path': '/v/a.mp4', 'percent-pos': 1, 'time-remaining': 1, 'time-pos': pos}
        self.assertEqual(self.watch([at(100), at(2300), at(50), None], total=2400), [(2300 * 100 / 2400, 100.0)])

    def test_watching_past_the_estimated_end_is_a_hundred_percent_at_most(self):
        self.assertEqual(self.watch([{'path': '/v/a.mp4', 'percent-pos': 1, 'time-remaining': 1, 'time-pos': 3000.0}, None], total=2400), [(100.0, 0.0)])

    def test_a_missing_time_is_ignored_against_a_known_length(self):
        self.assertEqual(self.watch([{'path': '/v/a.mp4', 'percent-pos': 50, 'time-remaining': 5, 'time-pos': None}, None], total=100), [(None, None)])

    def test_finishing_a_partial_file_counts_as_watched_by_the_playlists_length(self):
        percent, remaining = self.watch([{'path': '/v/a.mp4', 'percent-pos': 1, 'time-remaining': 1, 'time-pos': 2300.0}, None], total=2400)[0]
        self.assertTrue(rt.counts_as_watched(percent, remaining))
        percent, remaining = self.watch([{'path': '/v/a.mp4', 'percent-pos': 99, 'time-remaining': 1, 'time-pos': 600.0}, None], total=2400)[0]
        self.assertFalse(rt.counts_as_watched(percent, remaining))  # mpv said 99 %, the playlist says a quarter

    def test_values_that_are_not_numbers_are_ignored(self):
        bad = {'path': '/v/a.mp4', 'percent-pos': 'x', 'time-remaining': None}
        self.assertEqual(self.watch([bad, self.at(20, 80), None]), [(20, 80)])


class TestPlayMediaFile(unittest.TestCase):
    def test_a_file_is_loaded_with_its_title_and_watched(self):
        done = mock.Mock()
        with mock.patch.object(rt.shutil, 'which', return_value='/usr/bin/mpv'), \
                mock.patch.object(rt, '_load_in_mpv', return_value=True) as load, \
                mock.patch.object(rt.threading, 'Thread') as thread:
            self.assertTrue(rt.play_media_file('/v/a.mp4', 'Show \u00b7 Pilot', done))
        load.assert_called_once_with('/v/a.mp4', 'Show \u00b7 Pilot')
        self.assertEqual(thread.call_args.kwargs['target'], rt._watch_local_playback)
        self.assertEqual(thread.call_args.kwargs['args'], ('/v/a.mp4', done))
        self.assertEqual(thread.call_args.kwargs['kwargs'], {'total_seconds': None})
        thread.return_value.start.assert_called_once_with()

    def test_the_length_of_an_unfinished_download_is_handed_to_the_watcher(self):
        with mock.patch.object(rt.shutil, 'which', return_value='/usr/bin/mpv'), \
                mock.patch.object(rt, '_load_in_mpv', return_value=True), \
                mock.patch.object(rt.threading, 'Thread') as thread:
            rt.play_media_file('/v/a.ts.part', 'T', mock.Mock(), total_seconds=2501)
        self.assertEqual(thread.call_args.kwargs['kwargs'], {'total_seconds': 2501})

    def test_without_a_callback_nothing_is_watched(self):
        with mock.patch.object(rt.shutil, 'which', return_value='/usr/bin/mpv'), \
                mock.patch.object(rt, '_load_in_mpv', return_value=True), \
                mock.patch.object(rt.threading, 'Thread') as thread:
            self.assertTrue(rt.play_media_file('/v/a.mp4', 'T'))
        thread.assert_not_called()

    def test_an_mpv_that_cannot_start_means_false_and_no_watch(self):
        with mock.patch.object(rt.shutil, 'which', return_value='/usr/bin/mpv'), \
                mock.patch.object(rt, '_load_in_mpv', return_value=False), \
                mock.patch.object(rt.threading, 'Thread') as thread:
            self.assertFalse(rt.play_media_file('/v/a.mp4', 'T', mock.Mock()))
        thread.assert_not_called()

    def test_without_mpv_the_desktop_default_player_gets_the_file(self):
        with mock.patch.object(rt.shutil, 'which', return_value=None), \
                mock.patch.object(rt.subprocess, 'Popen') as popen, \
                mock.patch.object(rt, '_load_in_mpv') as load:
            self.assertTrue(rt.play_media_file('/v/a.mp4', 'T', mock.Mock()))
        self.assertEqual(popen.call_args.args[0], ['xdg-open', '/v/a.mp4'])
        load.assert_not_called()

    def test_without_mpv_or_xdg_open_it_says_so(self):
        with mock.patch.object(rt.shutil, 'which', return_value=None), \
                mock.patch.object(rt.subprocess, 'Popen', side_effect=OSError):
            self.assertFalse(rt.play_media_file('/v/a.mp4', 'T'))


if __name__ == '__main__':
    unittest.main()
