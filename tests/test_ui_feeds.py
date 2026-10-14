import unittest
from unittest import mock

from facade import rt
from helpers import TmpConfigCase


class TestFeedCheckRobustness(TmpConfigCase):
    class Stub:
        check_feeds = rt.RssTray.check_feeds

        def __init__(self, online=True):
            self.online = online
            self.lock = rt.threading.Lock()
            self.state = {'seen': {}, 'unread': [], 'last_checked': {}}
            self.on_new_items = mock.Mock()
            self.note_request_failure = mock.Mock()

    def test_offline_unforced_check_does_nothing(self):
        self.conf('[feeds]\nhttps://a.example/feed\n')
        stub = self.Stub(online=False)
        with mock.patch.object(rt.feedparser, 'parse') as parse, mock.patch.object(rt, 'save_state'):
            stub.check_feeds()
        parse.assert_not_called()
        self.assertEqual(stub.state['last_checked'], {})

    def test_forced_check_still_runs_offline(self):
        self.conf('[feeds]\nhttps://a.example/feed\n')
        stub = self.Stub(online=False)
        parsed = mock.Mock(bozo=False, entries=[])
        with mock.patch.object(rt.feedparser, 'parse', return_value=parsed) as parse, mock.patch.object(rt, 'save_state'):
            stub.check_feeds(force=True)
        parse.assert_called_once()

    def test_failed_fetch_is_retried_soon_not_after_a_full_interval(self):
        self.conf('[feeds]\nhttps://a.example/feed||30\n')  # 30 minute interval
        stub = self.Stub()
        failed = mock.Mock(bozo=True, entries=[])
        with mock.patch.object(rt.feedparser, 'parse', return_value=failed), \
                mock.patch.object(rt.time_module, 'time', return_value=1_000_000.0), \
                mock.patch.object(rt, 'save_state'):
            stub.check_feeds()
        due_again_in = 30 * 60 - (1_000_000.0 - stub.state['last_checked']['https://a.example/feed'])
        self.assertAlmostEqual(due_again_in, rt.FEED_RETRY_SECONDS, delta=1)

    def test_successful_fetch_counts_as_checked_now(self):
        self.conf('[feeds]\nhttps://a.example/feed\n')
        stub = self.Stub()
        ok = mock.Mock(bozo=False, entries=[])
        with mock.patch.object(rt.feedparser, 'parse', return_value=ok), \
                mock.patch.object(rt.time_module, 'time', return_value=1_000_000.0), \
                mock.patch.object(rt, 'save_state'):
            stub.check_feeds()
        self.assertEqual(stub.state['last_checked']['https://a.example/feed'], 1_000_000.0)


class TestUnreadCap(TmpConfigCase):
    def test_oldest_unread_beyond_the_cap_are_dropped_but_stay_seen(self):
        self.conf('[feeds]\nhttps://a.example/feed\n')

        class Stub:
            check_feeds = rt.RssTray.check_feeds

            def __init__(self):
                self.online = True
                self.lock = rt.threading.Lock()
                old = [{'id': f'old{i}', 'title': 't', 'link': '', 'feed_url': 'u'} for i in range(rt.MAX_UNREAD_ITEMS)]
                self.state = {'seen': {}, 'unread': old, 'last_checked': {}}
                self.on_new_items = mock.Mock()
        stub = Stub()
        entries = [{'id': f'new{i}', 'title': 'x', 'link': f'l{i}'} for i in range(3)]
        parsed = mock.Mock(bozo=False, entries=entries)
        with mock.patch.object(rt.feedparser, 'parse', return_value=parsed), \
                mock.patch.object(rt, 'save_state'), mock.patch.object(rt.GLib, 'idle_add'):
            stub.check_feeds()
        unread = stub.state['unread']
        self.assertEqual(len(unread), rt.MAX_UNREAD_ITEMS)
        self.assertEqual([e['title'] for e in unread[:3]], ['x', 'x', 'x'])     # newest kept, at the front
        self.assertNotIn(f'old{rt.MAX_UNREAD_ITEMS - 1}', [e['id'] for e in unread])  # oldest dropped
        self.assertEqual(len(stub.state['seen']), 3)                             # and never resurfaces


class TestFeedMenuActions(unittest.TestCase):
    class Stub:
        _feed_menu_mute = rt.RssTray._feed_menu_mute
        _feed_menu_open = rt.RssTray._feed_menu_open

        def __init__(self):
            self.lock = rt.threading.Lock()
            self.state = {'unread': [
                {'id': '1', 'feed_url': 'u1', 'title': 'a', 'tags': ['sports']},
                {'id': '2', 'feed_url': 'u1', 'title': 'b', 'tags': ['news'], 'author': 'X'},
                {'id': '3', 'feed_url': 'u2', 'title': 'c', 'tags': ['sports']},
                {'id': '4', 'feed_url': 'u1', 'title': 'old item without stored tags'}]}
            self.update_icon = mock.Mock()
            self.refresh_list = mock.Mock()

    def test_muting_a_category_writes_the_rule_and_hides_matching_unread_items_of_that_feed_only(self):
        stub = self.Stub()
        popover = mock.Mock()
        with mock.patch.object(rt, 'add_feed_mute_rule', return_value=True) as add, mock.patch.object(rt, 'save_state'):
            stub._feed_menu_mute(None, popover, 'u1', 'category', 'sports')
        add.assert_called_once_with('u1', 'category', 'sports')
        popover.popdown.assert_called_once()
        self.assertEqual([e['id'] for e in stub.state['unread']], ['2', '3', '4'])
        stub.update_icon.assert_called_once()
        stub.refresh_list.assert_called_once()

    def test_muting_an_author(self):
        stub = self.Stub()
        with mock.patch.object(rt, 'add_feed_mute_rule', return_value=True), mock.patch.object(rt, 'save_state'):
            stub._feed_menu_mute(None, mock.Mock(), 'u1', 'author', 'X')
        self.assertEqual([e['id'] for e in stub.state['unread']], ['1', '3', '4'])

    def test_nothing_changes_when_the_rule_could_not_be_added(self):
        stub = self.Stub()
        with mock.patch.object(rt, 'add_feed_mute_rule', return_value=False):
            stub._feed_menu_mute(None, mock.Mock(), 'u1', 'category', 'sports')
        self.assertEqual(len(stub.state['unread']), 4)
        stub.refresh_list.assert_not_called()

    def test_open_closes_the_menu_and_opens_the_url(self):
        popover = mock.Mock()
        with mock.patch.object(rt.webbrowser, 'open') as opened:
            self.Stub()._feed_menu_open(None, popover, 'https://x.example/')
        popover.popdown.assert_called_once()
        opened.assert_called_once_with('https://x.example/')


if __name__ == '__main__':
    unittest.main()
