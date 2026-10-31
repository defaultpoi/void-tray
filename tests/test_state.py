import json
import os
import unittest
from unittest import mock

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

    def corrupt_copy(self):
        with open(rt.STATE_FILE + '.corrupt') as f:
            return f.read()

    def test_a_corrupt_file_is_set_aside_and_not_overwritten_by_the_next_save(self):
        self.state_text('{"seen": {"a": 1}, "unread": [')  # cut off in the middle
        state = rt.load_state()
        self.assertEqual(state['unread'], [])
        self.assertEqual(self.corrupt_copy(), '{"seen": {"a": 1}, "unread": [')
        self.assertFalse(os.path.exists(rt.STATE_FILE))
        rt.save_state(state)  # what the app does a moment later
        self.assertEqual(self.corrupt_copy(), '{"seen": {"a": 1}, "unread": [')
        with open(rt.STATE_FILE) as f:
            self.assertEqual(json.load(f)['unread'], [])

    def test_json_that_is_not_an_object_is_set_aside(self):
        self.state_text('[1, 2]')
        self.assertEqual(rt.load_state()['seen'], {})
        self.assertEqual(self.corrupt_copy(), '[1, 2]')
        self.assertFalse(os.path.exists(rt.STATE_FILE))

    def test_a_file_that_is_not_text_is_set_aside_instead_of_crashing_the_start(self):
        with open(rt.STATE_FILE, 'wb') as f:
            f.write(b'\xff\xfe\x00{')
        self.assertEqual(rt.load_state()['unread'], [])
        with open(rt.STATE_FILE + '.corrupt', 'rb') as f:
            self.assertEqual(f.read(), b'\xff\xfe\x00{')

    def test_a_good_or_missing_file_is_not_set_aside(self):
        rt.load_state()
        self.state_text(json.dumps({'unread': [{'id': 'a'}]}))
        self.assertEqual(rt.load_state()['unread'], [{'id': 'a'}])
        self.state_text(json.dumps({'seen': [], 'unread': 'x'}))  # wrong-shaped keys are reset one by one
        rt.load_state()
        self.assertTrue(os.path.exists(rt.STATE_FILE))
        self.assertFalse(os.path.exists(rt.STATE_FILE + '.corrupt'))

    def test_a_later_corrupt_file_replaces_the_earlier_copy(self):
        self.state_text('{first')
        rt.load_state()
        self.state_text('{second')
        rt.load_state()
        self.assertEqual(self.corrupt_copy(), '{second')

    def test_a_failed_move_still_starts_with_defaults(self):
        self.state_text('{not json')
        with mock.patch.object(rt.os, 'replace', side_effect=OSError('read-only')):
            self.assertEqual(rt.load_state()['unread'], [])

    def test_a_file_that_cannot_be_read_is_not_called_corrupt(self):
        os.mkdir(rt.STATE_FILE)  # exists, but opening it fails with an OSError
        self.assertEqual(rt.load_state()['unread'], [])
        self.assertTrue(os.path.isdir(rt.STATE_FILE))
        self.assertFalse(os.path.exists(rt.STATE_FILE + '.corrupt'))

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

    def test_a_saved_countdown_end_is_kept_only_when_it_is_a_finite_number(self):
        self.state_text(json.dumps({'timer_end': 1760000000.5}))
        self.assertEqual(rt.load_state()['timer_end'], 1760000000.5)
        for bad in ('soon', None, True, [5], {'a': 1}):
            self.state_text(json.dumps({'timer_end': bad}))
            self.assertNotIn('timer_end', rt.load_state())
        self.state_text('{"timer_end": NaN}')  # Python's json accepts these
        self.assertNotIn('timer_end', rt.load_state())
        self.state_text('{"timer_end": Infinity}')
        self.assertNotIn('timer_end', rt.load_state())

    def test_save_roundtrip_leaves_no_tmp_file(self):
        rt.save_state({'a': 1})
        with open(rt.STATE_FILE) as f:
            self.assertEqual(json.load(f), {'a': 1})
        self.assertFalse(os.path.exists(rt.STATE_FILE + '.tmp'))



class TestMediaState(TmpConfigCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(rt, 'MEDIA_FILE', os.path.join(self.dir, 'media.json'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, data):
        with open(rt.MEDIA_FILE, 'w') as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_a_missing_file_starts_empty(self):
        self.assertEqual(rt.load_media(), {'sources': {}})

    def test_garbage_starts_empty(self):
        for text in ('', '{not json', '[1, 2]', 'null', '{"sources": []}'):
            self.write(text)
            self.assertEqual(rt.load_media(), {'sources': {}}, text)

    def test_garbage_is_set_aside_not_overwritten_by_the_next_save(self):
        for text in ('{"sources": {"https://x.example/f": {"title": "T", "episodes": [', '[1, 2]', '{"sources": []}'):
            self.write(text)
            media = rt.load_media()
            rt.save_media(media)  # the next save, which used to destroy it
            with open(rt.MEDIA_FILE + '.corrupt') as f:
                self.assertEqual(f.read(), text)
            self.assertEqual(rt.load_media(), {'sources': {}})

    def test_a_file_that_is_not_text_is_set_aside_too(self):
        with open(rt.MEDIA_FILE, 'wb') as f:
            f.write(b'\xff\xfe\x00{')
        self.assertEqual(rt.load_media(), {'sources': {}})
        self.assertTrue(os.path.exists(rt.MEDIA_FILE + '.corrupt'))

    def test_a_missing_empty_or_good_file_is_not_set_aside(self):
        rt.load_media()
        self.write({})  # a JSON object with nothing in it holds nothing to lose
        rt.load_media()
        self.write({'sources': {'https://x.example/f': {'title': 'T'}}})
        self.assertEqual(rt.load_media()['sources']['https://x.example/f']['title'], 'T')
        self.assertFalse(os.path.exists(rt.MEDIA_FILE + '.corrupt'))
        self.assertTrue(os.path.exists(rt.MEDIA_FILE))

    def test_a_file_that_cannot_be_read_is_left_alone(self):
        os.mkdir(rt.MEDIA_FILE)
        self.assertEqual(rt.load_media(), {'sources': {}})
        self.assertTrue(os.path.isdir(rt.MEDIA_FILE))
        self.assertFalse(os.path.exists(rt.MEDIA_FILE + '.corrupt'))

    def test_what_is_saved_is_loaded(self):
        entry = rt.new_media_entry()
        entry.update(title='Show', baselined=True, watched=['a'], known=['a', 'b'], new=['b'],
                     episodes=[{'id': 'a', 'title': 'A'}, {'id': 'b', 'title': 'B'}],
                     files={'a': {'video': '/v/a.mp4', 'subs': ['/v/a.en.srt']}},
                     last_watched={'id': 'a', 'title': 'A', 'at': 1.5})
        rt.save_media({'sources': {'https://x.example/feed': entry}})
        self.assertEqual(rt.load_media(), {'sources': {'https://x.example/feed': entry}})
        self.assertFalse(os.path.exists(rt.MEDIA_FILE + '.tmp'))

    def test_a_new_entry_has_every_field_the_code_reads(self):
        self.assertEqual(rt.new_media_entry(), {'title': '', 'episodes': [], 'known': [], 'new': [], 'watched': [],
                                                'files': {}, 'last_watched': None, 'baselined': False})

    def test_fields_of_the_wrong_type_are_repaired(self):
        self.write({'sources': {'https://x.example/f': {
            'title': 5, 'episodes': 'many', 'known': {'a': 1}, 'new': None, 'watched': 'a', 'files': [], 'last_watched': 'a',
            'baselined': 'yes'}}})
        self.assertEqual(rt.load_media()['sources']['https://x.example/f'], rt.new_media_entry())

    def test_wrong_items_inside_lists_are_dropped(self):
        self.write({'sources': {'https://x.example/f': {
            'episodes': [{'id': 'a'}, {'title': 'no id'}, 'text', {'id': 5}], 'watched': ['a', 5, None, 'b'],
            'files': {'a': {'video': '/v/a.mp4'}, 'b': {'subs': []}, 'c': 'x', 'd': {'video': 5}},
            'last_watched': {'title': 'no id'}}}})
        entry = rt.load_media()['sources']['https://x.example/f']
        self.assertEqual(entry['episodes'], [{'id': 'a'}])
        self.assertEqual(entry['watched'], ['a', 'b'])
        self.assertEqual(entry['files'], {'a': {'video': '/v/a.mp4'}})
        self.assertIsNone(entry['last_watched'])

    def test_a_source_that_is_not_an_object_is_dropped(self):
        self.write({'sources': {'https://x.example/f': 'x', 'https://y.example/f': {}}})
        self.assertEqual(list(rt.load_media()['sources']), ['https://y.example/f'])

    def test_the_state_file_is_untouched(self):
        rt.save_media({'sources': {}})
        self.assertFalse(os.path.exists(rt.STATE_FILE))


if __name__ == '__main__':
    unittest.main()
