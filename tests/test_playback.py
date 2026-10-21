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


if __name__ == '__main__':
    unittest.main()
