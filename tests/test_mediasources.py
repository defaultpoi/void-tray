import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from facade import rt


class TestNormalizeEpisodes(unittest.TestCase):
    def norm(self, episodes, title='Show', name='drv'):
        return rt.normalize_episodes(name, (title, episodes))

    def test_an_episode_is_checked_and_stamped_with_its_driver(self):
        title, eps = self.norm([{'id': 'a', 'title': 'A', 'date': '2020-01-02', 'url': 'http://h/a.mp4', 'ext': '.mp4', 'ref': 'r'}])
        self.assertEqual(title, 'Show')
        self.assertEqual(eps, [{'id': 'a', 'title': 'A', 'date': '2020-01-02', 'url': 'http://h/a.mp4', 'ext': '.mp4',
                                'ref': 'r', 'driver': 'drv', 'subs': []}])  # extra keys are kept for resolve()

    def test_ids_become_strings_and_must_be_there_and_unique(self):
        _, eps = self.norm([{'id': 5, 'title': 'five'}, {'id': 5, 'title': 'again'}, {'id': 0, 'title': 'zero'},
                            {'id': ''}, {'title': 'no id'}, {'id': None}, 'text', None, 7])
        self.assertEqual([(e['id'], e['title']) for e in eps], [('5', 'five'), ('0', 'zero')])

    def test_a_missing_title_is_the_id(self):
        self.assertEqual(self.norm([{'id': 'a'}, {'id': 'b', 'title': None}])[1][0]['title'], 'a')
        self.assertEqual(self.norm([{'id': 'a'}, {'id': 'b', 'title': None}])[1][1]['title'], 'b')

    def test_dates_are_year_month_day_or_nothing(self):
        dates = [e['date'] for e in self.norm([
            {'id': '1', 'date': '2020-01-02T10:00:00Z'}, {'id': '2', 'date': 'yesterday'}, {'id': '3', 'date': 20200102},
            {'id': '4'}, {'id': '5', 'date': None}, {'id': '6', 'date': '2020-1-2'}])[1]]
        self.assertEqual(dates, ['2020-01-02', '', '', '', '', ''])

    def test_the_extension_comes_from_the_url_when_not_given_or_not_valid(self):
        eps = self.norm([{'id': '1', 'url': 'http://h/a.mkv?x=1'}, {'id': '2', 'url': 'http://h/stream'},
                         {'id': '3', 'url': 'http://h/a.mp3', 'ext': 'mp3'}, {'id': '4', 'url': 'http://h/a.mp3', 'ext': '.ogg'}])[1]
        self.assertEqual([e['ext'] for e in eps], ['.mkv', '.mp4', '.mp3', '.ogg'])

    def test_an_episode_without_a_url_is_left_to_resolve(self):
        eps = self.norm([{'id': '1', 'ext': '.mp4', 'url': ''}, {'id': '2', 'url': None}, {'id': '3', 'url': 5}])[1]
        for episode in eps:
            self.assertNotIn('url', episode)
            self.assertNotIn('ext', episode)

    def test_subtitles_are_checked(self):
        subs = self.norm([{'id': 'a', 'url': 'http://h/a.mp4', 'subs': [
            {'url': 'http://h/a.en.srt', 'ext': '.srt', 'lang': 'en'}, {'url': 'http://h/a.vtt'}, {'ext': '.srt'},
            {'url': ''}, 'text', {'url': 'http://h/b', 'ext': 'srt', 'lang': ''}]}])[1][0]['subs']
        self.assertEqual(subs, [{'url': 'http://h/a.en.srt', 'ext': '.srt', 'lang': 'en'},
                                {'url': 'http://h/a.vtt', 'ext': '.vtt', 'lang': None},
                                {'url': 'http://h/b', 'ext': '.srt', 'lang': None}])

    def test_season_and_episode_are_kept_when_they_are_whole_numbers(self):
        eps = self.norm([{'id': 'a', 'season': 1, 'episode': 3}, {'id': 'b', 'season': '1', 'episode': 3.0},
                         {'id': 'c', 'season': True, 'episode': None}, {'id': 'd'}])[1]
        self.assertEqual([(e.get('season'), e.get('episode')) for e in eps], [(1, 3), (None, None), (None, None), (None, None)])

    def test_subtitle_headers_are_kept_when_valid(self):
        subs = self.norm([{'id': 'a', 'subs': [{'url': 'https://h/s.vtt', 'headers': {'Referer': 'https://e/'}},
                                               {'url': 'https://h/t.vtt', 'headers': 'nope'}]}])[1][0]['subs']
        self.assertEqual(subs[0]['headers'], {'Referer': 'https://e/'})
        self.assertNotIn('headers', subs[1])

    def test_subtitles_that_are_not_a_list_are_none(self):
        self.assertEqual(self.norm([{'id': 'a', 'subs': 'x'}])[1][0]['subs'], [])

    def test_the_input_is_not_modified(self):
        original = {'id': 5, 'title': None}
        self.norm([original])
        self.assertEqual(original, {'id': 5, 'title': None})

    def test_any_iterable_of_episodes_will_do(self):
        self.assertEqual(len(self.norm(iter([{'id': 'a'}, {'id': 'b'}]))[1]), 2)

    def test_a_missing_title_is_empty_and_a_result_of_the_wrong_shape_raises(self):
        self.assertEqual(self.norm([], title=None)[0], '')
        for bad in (None, 'text', 5, ('only a title',), ('t', 5), ('t', [], 'extra')):
            with self.assertRaises(ValueError, msg=bad):
                rt.normalize_episodes('mine', bad)
        with self.assertRaises(ValueError) as caught:
            rt.normalize_episodes('mine', None)
        self.assertIn('mine: list_episodes must return (title, [episodes])', str(caught.exception))


class TestNormalizeFiles(unittest.TestCase):
    def test_a_file_with_everything_given(self):
        files = {'video': {'url': 'https://h/a.mp4', 'ext': '.mp4', 'headers': {'Referer': 'https://e/'}},
                 'subs': [{'url': 'https://h/a.vtt', 'ext': '.vtt', 'lang': 'ro', 'headers': {'Referer': 'https://e/'}}]}
        self.assertEqual(rt.normalize_files(files), files)

    def test_the_extension_comes_from_the_url_when_missing(self):
        self.assertEqual(rt.normalize_files({'video': {'url': 'https://h/a.mkv?x=1'}})['video']['ext'], '.mkv')
        self.assertEqual(rt.normalize_files({'video': {'url': 'https://h/stream', 'ext': 'mp4'}})['video']['ext'], '.mp4')

    def test_an_hls_video_defaults_to_mkv_and_keeps_ts_or_mp4_when_asked(self):
        for given, expected in ((None, '.mkv'), ('.ts', '.ts'), ('.mp4', '.mp4'), ('.mkv', '.mkv'), ('.avi', '.mkv'), ('ts', '.mkv')):
            video = {'url': 'https://h/master.m3u8?t=1'}
            if given:
                video['ext'] = given
            self.assertEqual(rt.normalize_files({'video': video})['video']['ext'], expected, given)

    def test_headers_are_checked(self):
        clean = rt.normalize_files({'video': {'url': 'https://h/a.mp4', 'headers': {'Referer': 'x', 'Bad\nName': 'y', 'N': 'a\nb', 5: 'z', 'Num': 7}}})
        self.assertEqual(clean['video']['headers'], {'Referer': 'x'})  # nothing that could add a header line of its own
        self.assertNotIn('headers', rt.normalize_files({'video': {'url': 'https://h/a.mp4', 'headers': 'x'}})['video'])
        self.assertNotIn('headers', rt.normalize_files({'video': {'url': 'https://h/a.mp4', 'headers': {}}})['video'])

    def test_subtitles_are_cleaned_like_an_episodes(self):
        files = rt.normalize_files({'video': {'url': 'https://h/a.mp4'}, 'subs': [{'url': 'https://h/s.vtt'}, {'ext': '.srt'}, 'x']})
        self.assertEqual(files['subs'], [{'url': 'https://h/s.vtt', 'ext': '.vtt', 'lang': None}])
        self.assertEqual(rt.normalize_files({'video': {'url': 'https://h/a.mp4'}})['subs'], [])

    def test_extra_keys_a_driver_added_are_dropped(self):
        files = rt.normalize_files({'video': {'url': 'https://h/a.mp4', 'note': 1}, 'subs': [], '_embed_url': 'https://e/'})
        self.assertEqual(files, {'video': {'url': 'https://h/a.mp4', 'ext': '.mp4'}, 'subs': []})

    def test_without_a_video_url_it_is_an_error(self):
        for bad in (None, 'x', {}, {'video': None}, {'video': {}}, {'video': {'url': ''}}, {'video': {'url': 5}}, {'subs': []}):
            with self.assertRaises(ValueError, msg=bad):
                rt.normalize_files(bad)


class TestNormalizeSaved(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def file(self, name):
        path = os.path.join(self.dir, name)
        open(path, 'w').close()
        return path

    def test_existing_files_pass(self):
        video, sub = self.file('a.mp4'), self.file('a.en.srt')
        self.assertEqual(rt.normalize_saved({'video': video, 'subs': [sub]}), {'video': video, 'subs': [sub]})

    def test_subtitles_that_are_not_there_or_not_paths_are_dropped(self):
        video, sub = self.file('a.mp4'), self.file('a.srt')
        saved = rt.normalize_saved({'video': video, 'subs': [sub, os.path.join(self.dir, 'gone.srt'), 5, None]})
        self.assertEqual(saved['subs'], [sub])

    def test_missing_subs_are_an_empty_list(self):
        video = self.file('a.mp4')
        self.assertEqual(rt.normalize_saved({'video': video}), {'video': video, 'subs': []})
        self.assertEqual(rt.normalize_saved({'video': video, 'subs': 'x'}), {'video': video, 'subs': []})

    def test_a_video_that_is_not_there_is_an_error(self):
        for bad in (None, 'a.mp4', {}, {'video': 5}, {'video': os.path.join(self.dir, 'gone.mp4')}, {'video': self.dir}):
            with self.assertRaises(ValueError, msg=bad):
                rt.normalize_saved(bad)


class PluginCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.registry = rt.MediaSources(self.dir)
        self.addCleanup(self.cleanup_modules)

    def cleanup_modules(self):
        for name in [n for n in sys.modules if n.startswith('void_tray_media_plugin_')]:
            del sys.modules[name]

    def plugin(self, name, source, bump=False):
        path = os.path.join(self.dir, name)
        with open(path, 'w') as f:
            f.write(textwrap.dedent(source))
        if bump:  # a later modification time, as a real edit would have
            stat = os.stat(path)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        return path

    GOOD = '''
        NAME = 'mysite'
        ORDER = 'oldest'

        def matches(url):
            return url.startswith('https://mysite.example/')

        def list_episodes(source):
            return 'My Show', [{'id': 1, 'title': 'One', 'date': '2021-02-03T00:00:00', 'url': 'http://h/1.mp4'}]
    '''

    def names(self):
        return [d.name for d in self.registry.drivers()]


class TestLoadingPlugins(PluginCase):
    def test_without_a_plugin_folder_only_the_built_in_drivers_are_there(self):
        self.registry = rt.MediaSources(os.path.join(self.dir, 'nope'))
        self.registry.refresh()
        self.assertEqual(self.names(), ['archive', 'yt-dlp', 'feed'])
        self.assertEqual(self.registry.errors, {})

    def test_a_plugin_is_loaded_and_comes_before_the_built_in_drivers(self):
        self.plugin('mysite.py', self.GOOD)
        self.registry.refresh()
        self.assertEqual(self.names(), ['mysite', 'archive', 'yt-dlp', 'feed'])
        driver = self.registry.by_name('mysite')
        self.assertEqual(driver.order, 'oldest')
        self.assertTrue(driver.matches('https://mysite.example/show'))
        self.assertFalse(driver.matches('https://other.example/'))

    def test_the_name_defaults_to_the_file_name_and_the_order_to_newest(self):
        self.plugin('another.py', 'def list_episodes(source):\n    return "T", []\n')
        self.registry.refresh()
        driver = self.registry.by_name('another')
        self.assertEqual(driver.order, 'newest')
        self.assertFalse(driver.matches('anything'))  # no matches(): only reachable as `another:...`

    def test_a_declared_order_that_is_not_one_is_ignored(self):
        self.plugin('odd.py', 'ORDER = "sideways"\ndef list_episodes(source):\n    return "T", []\n')
        self.registry.refresh()
        self.assertEqual(self.registry.by_name('odd').order, 'newest')

    def test_plugins_are_loaded_in_name_order(self):
        for name in ('b.py', 'c.py', 'a.py'):
            self.plugin(name, 'def list_episodes(source):\n    return "T", []\n')
        self.registry.refresh()
        self.assertEqual(self.names()[:3], ['a', 'b', 'c'])

    def test_files_that_are_not_plugins_are_ignored(self):
        for name in ('_helper.py', '.hidden.py', 'notes.txt', 'plugin.py~', 'data.json'):
            self.plugin(name, 'raise RuntimeError("must not be imported")\n')
        os.mkdir(os.path.join(self.dir, 'sub.py'))  # a directory
        self.registry.refresh()
        self.assertEqual(self.names(), ['archive', 'yt-dlp', 'feed'])
        self.assertEqual(self.registry.errors, {})

    def test_a_plugin_that_breaks_is_reported_and_the_others_still_load(self):
        self.plugin('good.py', self.GOOD)
        self.plugin('syntax.py', 'def list_episodes(:\n')
        self.plugin('raises.py', 'raise RuntimeError("no network at import")\n')
        self.plugin('nolist.py', 'NAME = "nolist"\n')
        self.plugin('notfunc.py', 'matches = 5\ndef list_episodes(source):\n    return "T", []\n')
        self.plugin('badresolve.py', 'resolve = "x"\ndef list_episodes(source):\n    return "T", []\n')
        self.registry.refresh()
        self.assertIn('mysite', self.names())
        self.assertEqual(sorted(self.registry.errors), ['badresolve.py', 'nolist.py', 'notfunc.py', 'raises.py', 'syntax.py'])
        self.assertIn('SyntaxError', self.registry.errors['syntax.py'])
        self.assertIn('RuntimeError: no network at import', self.registry.errors['raises.py'])
        self.assertIn('no list_episodes(source) function', self.registry.errors['nolist.py'])
        self.assertIn('matches is not a function', self.registry.errors['notfunc.py'])
        self.assertIn('resolve is not a function', self.registry.errors['badresolve.py'])

    def test_a_declared_name_that_cannot_be_used_falls_back_to_the_file_name(self):
        self.plugin('fine.py', 'NAME = "Not Valid!"\ndef list_episodes(source):\n    return "T", []\n')
        self.registry.refresh()
        self.assertIsNotNone(self.registry.by_name('fine'))

    def test_a_file_name_that_cannot_be_a_name_is_reported(self):
        self.plugin('My Site.py', 'def list_episodes(source):\n    return "T", []\n')
        self.registry.refresh()
        self.assertIn('cannot be used as a name', self.registry.errors['My Site.py'])

    def test_an_unchanged_plugin_is_not_loaded_again_but_an_edited_one_is(self):
        self.plugin('mysite.py', self.GOOD)
        self.registry.refresh()
        first = self.registry.by_name('mysite')
        self.registry.refresh()
        self.assertIs(self.registry.by_name('mysite'), first)
        self.plugin('mysite.py', self.GOOD.replace("'My Show'", "'Renamed Show'"), bump=True)
        self.registry.refresh()
        edited = self.registry.by_name('mysite')
        self.assertIsNot(edited, first)
        self.assertEqual(edited.list_episodes('x')[0], 'Renamed Show')

    def test_fixing_a_broken_plugin_clears_its_error(self):
        self.plugin('mysite.py', 'def list_episodes(:\n')
        self.registry.refresh()
        self.assertIn('mysite.py', self.registry.errors)
        self.plugin('mysite.py', self.GOOD, bump=True)
        self.registry.refresh()
        self.assertEqual(self.registry.errors, {})
        self.assertIsNotNone(self.registry.by_name('mysite'))

    def test_breaking_a_working_plugin_removes_it_and_says_why(self):
        self.plugin('mysite.py', self.GOOD)
        self.registry.refresh()
        self.plugin('mysite.py', 'def list_episodes(:\n', bump=True)
        self.registry.refresh()
        self.assertIsNone(self.registry.by_name('mysite'))
        self.assertIn('mysite.py', self.registry.errors)

    def test_a_removed_plugin_file_removes_the_driver_and_its_error(self):
        self.plugin('mysite.py', self.GOOD)
        self.plugin('broken.py', 'def list_episodes(:\n')
        self.registry.refresh()
        os.remove(os.path.join(self.dir, 'mysite.py'))
        os.remove(os.path.join(self.dir, 'broken.py'))
        self.registry.refresh()
        self.assertEqual(self.names(), ['archive', 'yt-dlp', 'feed'])
        self.assertEqual(self.registry.errors, {})

    def test_a_plugin_may_use_dataclasses_and_the_apps_own_helpers(self):
        self.plugin('fancy.py', '''
            from dataclasses import dataclass
            from void_tray.media import season_episode

            @dataclass
            class Item:
                id: str
                title: str

            def list_episodes(source):
                item = Item('a', 'Show S01E02')
                return 'Fancy', [{'id': item.id, 'title': item.title, 'extra': season_episode(item.title)}]
        ''')
        self.registry.refresh()
        _, episodes = self.registry.by_name('fancy').list_episodes('x')
        self.assertEqual(episodes[0]['extra'], (1, 2))

    def test_two_plugins_do_not_see_each_others_names(self):
        self.plugin('one.py', 'VALUE = 1\ndef list_episodes(source):\n    return str(VALUE), []\n')
        self.plugin('two.py', 'VALUE = 2\ndef list_episodes(source):\n    return str(VALUE), []\n')
        self.registry.refresh()
        self.assertEqual(self.registry.by_name('one').list_episodes('x')[0], '1')
        self.assertEqual(self.registry.by_name('two').list_episodes('x')[0], '2')

    def test_a_plugin_that_cannot_be_read_is_skipped(self):
        path = self.plugin('mysite.py', self.GOOD)
        real_stat = os.stat

        def stat(p, *a, **k):
            if p == path:
                raise OSError('vanished')
            return real_stat(p, *a, **k)
        with mock.patch.object(rt.os, 'stat', side_effect=stat):
            self.registry.refresh()
        self.assertIsNone(self.registry.by_name('mysite'))


class TestListing(PluginCase):
    def test_what_a_plugin_lists_is_checked_and_stamped(self):
        self.plugin('mysite.py', self.GOOD)
        self.registry.refresh()
        title, episodes = self.registry.by_name('mysite').list_episodes('https://mysite.example/show')
        self.assertEqual(title, 'My Show')
        self.assertEqual([(e['id'], e['date'], e['driver'], e['ext']) for e in episodes], [('1', '2021-02-03', 'mysite', '.mp4')])

    def test_a_plugin_that_returns_the_wrong_shape_is_an_error(self):
        self.plugin('mysite.py', 'def list_episodes(source):\n    return [{"id": "a"}]\n')
        self.registry.refresh()
        with self.assertRaises(ValueError) as caught:
            self.registry.by_name('mysite').list_episodes('x')
        self.assertIn('mysite: list_episodes must return', str(caught.exception))

    def test_an_error_inside_a_plugin_reaches_the_caller(self):
        self.plugin('mysite.py', 'def list_episodes(source):\n    raise KeyError("episodes")\n')
        self.registry.refresh()
        with self.assertRaises(KeyError):
            self.registry.by_name('mysite').list_episodes('x')

    def test_the_source_is_handed_over_as_given(self):
        self.plugin('mysite.py', 'def list_episodes(source):\n    return source, []\n')
        self.registry.refresh()
        self.assertEqual(self.registry.by_name('mysite').list_episodes('some thing')[0], 'some thing')


class TestChoosingADriver(PluginCase):
    def test_the_built_in_drivers_by_url(self):
        self.registry.refresh()
        cases = {'https://archive.org/details/x': 'archive', 'https://www.youtube.com/@StarTalk/podcasts': 'yt-dlp',
                 'https://youtu.be/abc': 'yt-dlp', 'https://example.com/feed.xml': 'feed', 'http://example.com/pod': 'feed'}
        for source, expected in cases.items():
            driver, given = self.registry.driver_for(source)
            self.assertEqual((driver.name, given), (expected, source), source)

    def test_a_name_prefix_picks_the_driver_and_is_cut_off(self):
        self.registry.refresh()
        driver, given = self.registry.driver_for('yt-dlp:https://vimeo.com/channels/staffpicks')
        self.assertEqual((driver.name, given), ('yt-dlp', 'https://vimeo.com/channels/staffpicks'))
        driver, given = self.registry.driver_for('archive:some-identifier')
        self.assertEqual((driver.name, given), ('archive', 'some-identifier'))
        driver, given = self.registry.driver_for('feed:https://example.com/x')
        self.assertEqual((driver.name, given), ('feed', 'https://example.com/x'))

    def test_the_prefix_is_case_insensitive(self):
        self.registry.refresh()
        self.assertEqual(self.registry.driver_for('YT-DLP:https://example.com/')[0].name, 'yt-dlp')

    def test_a_plugin_is_reached_by_its_prefix_and_gets_what_follows_it(self):
        self.plugin('mysite.py', self.GOOD)
        self.registry.refresh()
        driver, given = self.registry.driver_for('mysite:show-1')
        self.assertEqual((driver.name, given), ('mysite', 'show-1'))

    def test_a_plugin_that_matches_a_url_takes_it_before_the_built_in_drivers(self):
        self.plugin('mysite.py', self.GOOD)
        self.registry.refresh()
        self.assertEqual(self.registry.driver_for('https://mysite.example/show')[0].name, 'mysite')  # it would be a feed otherwise

    def test_a_plugin_can_take_over_a_site_the_built_in_drivers_handle(self):
        self.plugin('mytube.py', 'def matches(u):\n    return "youtube.com" in u\ndef list_episodes(s):\n    return "T", []\n')
        self.registry.refresh()
        self.assertEqual(self.registry.driver_for('https://www.youtube.com/@x')[0].name, 'mytube')

    def test_a_plugin_named_like_a_built_in_driver_replaces_it_for_the_prefix(self):
        self.plugin('feed.py', 'def list_episodes(s):\n    return "mine", []\n')
        self.registry.refresh()
        driver, _ = self.registry.driver_for('feed:https://example.com/x')
        self.assertEqual(driver.list_episodes('x')[0], 'mine')

    def test_http_is_never_taken_for_a_driver_name(self):
        self.plugin('http.py', 'def list_episodes(s):\n    return "T", []\n')
        self.plugin('https.py', 'def list_episodes(s):\n    return "T", []\n')
        self.registry.refresh()
        self.assertEqual(self.registry.driver_for('https://example.com/feed.xml')[0].name, 'feed')

    def test_a_source_nobody_handles_raises_no_driver(self):
        self.registry.refresh()
        for source in ('ftp://example.com/x', 'nosuchplugin:thing', 'just words'):
            with self.assertRaises(rt.NoDriver, msg=source) as caught:
                self.registry.driver_for(source)
            self.assertEqual(str(caught.exception), f'no source handler for {source}')

    def test_the_message_says_which_plugins_did_not_load(self):
        self.plugin('mysite.py', 'def list_episodes(:\n')
        self.registry.refresh()
        with self.assertRaises(rt.NoDriver) as caught:
            self.registry.driver_for('mysite:show')  # the plugin that would have handled it is broken
        message = str(caught.exception)
        self.assertIn('no source handler for mysite:show', message)
        self.assertIn('plugins not loaded: mysite.py: SyntaxError', message)

    def test_a_plugin_whose_matches_raises_does_not_stop_the_others(self):
        self.plugin('a_buggy.py', 'def matches(u):\n    raise RuntimeError("bug")\ndef list_episodes(s):\n    return "T", []\n')
        self.plugin('b_fine.py', 'def matches(u):\n    return True\ndef list_episodes(s):\n    return "T", []\n')
        self.registry.refresh()
        self.assertEqual(self.registry.driver_for('anything:goes')[0].name, 'b_fine')

    def test_matches_returning_a_truthy_value_is_enough(self):
        self.plugin('loose.py', 'def matches(u):\n    return "yes" if "loose" in u else None\ndef list_episodes(s):\n    return "T", []\n')
        self.registry.refresh()
        self.assertEqual(self.registry.driver_for('https://loose.example/')[0].name, 'loose')
        self.assertEqual(self.registry.driver_for('https://other.example/')[0].name, 'feed')

    def test_the_whole_source_line_is_what_matches_sees(self):
        self.plugin('seen.py', 'SEEN = []\ndef matches(u):\n    SEEN.append(u)\n    return False\ndef list_episodes(s):\n    return "T", []\n')
        self.registry.refresh()
        self.registry.driver_for('https://example.com/feed.xml')
        self.assertEqual(self.registry.by_name('seen').module.SEEN, ['https://example.com/feed.xml'])


class TestDriverOfAnEpisode(PluginCase):
    def test_the_driver_that_listed_it(self):
        self.plugin('mysite.py', self.GOOD)
        self.registry.refresh()
        self.assertEqual(self.registry.driver_of({'id': 'a', 'driver': 'mysite'}).name, 'mysite')
        self.assertEqual(self.registry.driver_of({'id': 'a', 'driver': 'yt-dlp'}).name, 'yt-dlp')

    def test_episodes_stored_before_drivers_existed_are_read_by_their_kind(self):
        self.registry.refresh()
        self.assertEqual(self.registry.driver_of({'id': 'a', 'kind': 'archive'}).name, 'archive')
        self.assertEqual(self.registry.driver_of({'id': 'a', 'kind': 'archive-file'}).name, 'archive')
        self.assertEqual(self.registry.driver_of({'id': 'a', 'kind': 'feed'}).name, 'feed')
        self.assertEqual(self.registry.driver_of({'id': 'a'}).name, 'feed')

    def test_a_driver_that_is_gone_says_so(self):
        self.registry.refresh()
        with self.assertRaises(rt.NoDriver) as caught:
            self.registry.driver_of({'id': 'a', 'driver': 'removed-plugin'})
        self.assertIn('the removed-plugin source plugin is not loaded', str(caught.exception))

    def test_the_resolve_and_download_of_a_plugin_are_found(self):
        self.plugin('full.py', '''
            def list_episodes(s):
                return "T", []
            def resolve(episode):
                return {"video": {"url": "http://h/x.mp4", "ext": ".mp4"}, "subs": []}
            def download(episode, base, progress, cancelled):
                return {"video": base + ".mp4", "subs": []}
        ''')
        self.plugin('plain.py', 'def list_episodes(s):\n    return "T", []\n')
        self.registry.refresh()
        full, plain = self.registry.by_name('full'), self.registry.by_name('plain')
        self.assertTrue(callable(full.resolve) and callable(full.download))
        self.assertIsNone(plain.resolve)
        self.assertIsNone(plain.download)
        self.assertIsNone(self.registry.by_name('archive').download)
        self.assertTrue(callable(self.registry.by_name('yt-dlp').download))


class TestBuiltInDrivers(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.registry = rt.MediaSources(self.dir)

    def test_the_ytdlp_driver_passes_the_configured_extra_arguments(self):
        with mock.patch.object(rt, 'load_media_settings', return_value={'ytdlp': ['-S', 'res:720']}), \
                mock.patch.object(rt, 'list_ytdlp', return_value=('Chan', [{'id': 'x', 'title': 'X', 'url': 'https://www.youtube.com/watch?v=x'}])) as read:
            title, episodes = self.registry.by_name('yt-dlp').list_episodes('https://www.youtube.com/@c/videos')
        read.assert_called_once_with('https://www.youtube.com/@c/videos', ['-S', 'res:720'])
        self.assertEqual((title, episodes[0]['driver']), ('Chan', 'yt-dlp'))

    def test_the_ytdlp_driver_downloads_with_the_extra_arguments_too(self):
        with mock.patch.object(rt, 'load_media_settings', return_value={'ytdlp': ['--proxy', 'p'], 'screen': (1366, 768)}), \
                mock.patch.object(rt, 'download_ytdlp', return_value={'video': 'v', 'subs': []}) as fetch:
            progress, cancelled = mock.Mock(), mock.Mock()
            result = self.registry.by_name('yt-dlp').download({'id': 'x', 'url': 'u'}, '/v/S/base', progress, cancelled)
        fetch.assert_called_once_with({'id': 'x', 'url': 'u'}, '/v/S/base', progress, cancelled, ['--proxy', 'p'], (1366, 768))  # and the screen
        self.assertEqual(result, {'video': 'v', 'subs': []})

    def test_archive_and_feed_drivers_read_their_sources(self):
        with mock.patch.object(rt, 'list_archive', return_value=('Coll', [])) as archive:
            self.registry.by_name('archive').list_episodes('https://archive.org/details/some-id')
            self.registry.by_name('archive').list_episodes('some-id')  # `archive:some-id`
        self.assertEqual([c.args for c in archive.call_args_list], [('some-id',), ('some-id',)])
        with mock.patch.object(rt, 'list_feed', return_value=('Pod', [])) as feed:
            self.registry.by_name('feed').list_episodes('https://example.com/feed.xml')
        feed.assert_called_once_with('https://example.com/feed.xml')

    def test_default_orders(self):
        self.assertEqual({d.name: d.order for d in self.registry.drivers()}, {'archive': 'oldest', 'yt-dlp': 'newest', 'feed': 'newest'})


if __name__ == '__main__':
    unittest.main()
