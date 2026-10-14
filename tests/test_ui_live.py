import unittest
from unittest import mock

from facade import rt


class TestLiveScheduleTick(unittest.TestCase):
    class Stub:
        _live_schedule_tick = rt.RssTray._live_schedule_tick
        _current_scheduled_active = staticmethod(rt.RssTray._current_scheduled_active)

        def __init__(self, before):
            self._scheduled_active = before
            self.twitch_checks = 0
            self.youtube_checks = 0

        def start_twitch_check(self):
            self.twitch_checks += 1

        def start_youtube_check(self):
            self.youtube_checks += 1

    def _tick(self, before, now_active):
        stub = self.Stub(before)
        with mock.patch.object(rt.RssTray, '_current_scheduled_active', staticmethod(lambda now=None: now_active)):
            stub._current_scheduled_active = rt.RssTray._current_scheduled_active
            self.assertTrue(stub._live_schedule_tick())
        return stub

    def test_window_opening_triggers_one_immediate_check_for_that_platform(self):
        stub = self._tick({'twitch': set(), 'youtube': set()}, {'twitch': {'weekly'}, 'youtube': set()})
        self.assertEqual((stub.twitch_checks, stub.youtube_checks), (1, 0))
        self.assertEqual(stub._scheduled_active['twitch'], {'weekly'})

    def test_staying_inside_or_leaving_a_window_does_not_trigger(self):
        stub = self._tick({'twitch': {'weekly'}, 'youtube': {'yt'}}, {'twitch': {'weekly'}, 'youtube': set()})
        self.assertEqual((stub.twitch_checks, stub.youtube_checks), (0, 0))


if __name__ == '__main__':
    unittest.main()
