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
