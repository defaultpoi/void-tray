import json
import os
import unittest

from facade import rt
from helpers import TmpConfigCase


class TestState(TmpConfigCase):
    def test_missing_file_gives_defaults(self):
        s = rt.load_state()
        self.assertEqual(s['seen'], {})
        self.assertEqual(s['unread'], [])
        self.assertEqual(s['available_updates'], [])
        self.assertEqual(s['live_channels'], [])
        self.assertEqual(s['last_checked'], {})

    def test_orphans_default_to_an_empty_list(self):
        self.assertEqual(rt.load_state()['orphans'], [])

    def test_orphans_must_be_a_list_of_names(self):
        self.state_text(json.dumps({'orphans': 'libfoo'}))
        self.assertEqual(rt.load_state()['orphans'], [])
        self.state_text(json.dumps({'orphans': ['libfoo', 5, None, 'libbar']}))
        self.assertEqual(rt.load_state()['orphans'], ['libfoo', 'libbar'])

    def test_read_later_defaults_to_an_empty_list(self):
        self.assertEqual(rt.load_state()['read_later'], [])

    def test_read_later_must_be_a_list_of_complete_items(self):
        self.state_text(json.dumps({'read_later': 'oops'}))
        self.assertEqual(rt.load_state()['read_later'], [])
        good = {'id': '1', 'title': 'T', 'link': 'https://x.example/1', 'feed_url': 'u', 'saved': 5.0, 'tags': ['a']}
        self.state_text(json.dumps({'read_later': [good, {'id': '2'}, 'text', None, {'id': 3, 'title': 'T', 'link': 'l'}]}))
        self.assertEqual(rt.load_state()['read_later'], [good])

    def test_corrupt_json_gives_defaults(self):
        self.state_text('{not json')
        self.assertEqual(rt.load_state()['unread'], [])

    def test_non_dict_json_gives_defaults(self):
        self.state_text('[1, 2]')
        self.assertEqual(rt.load_state()['seen'], {})

    def test_wrong_shaped_keys_reset(self):
        self.state_text(json.dumps({
            'seen': [], 'unread': 'x', 'available_updates': {},
            'live_channels': 5, 'last_checked': [],
        }))
        s = rt.load_state()
        self.assertEqual(s['seen'], {})
        self.assertEqual(s['unread'], [])
        self.assertEqual(s['available_updates'], [])
        self.assertEqual(s['live_channels'], [])
        self.assertEqual(s['last_checked'], {})

    def test_valid_keys_preserved(self):
        self.state_text(json.dumps({'unread': [{'id': 'a'}], 'seen': {'a': 1}}))
        s = rt.load_state()
        self.assertEqual(s['unread'], [{'id': 'a'}])
        self.assertEqual(s['seen'], {'a': 1})

    def test_save_roundtrip_leaves_no_tmp_file(self):
        rt.save_state({'a': 1})
        with open(rt.STATE_FILE) as f:
            self.assertEqual(json.load(f), {'a': 1})
        self.assertFalse(os.path.exists(rt.STATE_FILE + '.tmp'))


if __name__ == '__main__':
    unittest.main()
