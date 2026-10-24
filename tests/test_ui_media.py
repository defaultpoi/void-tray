import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from facade import rt


URL = 'https://archive.org/details/show'
POD = 'https://example.com/pod.xml'


def ep(episode_id, number=None, date='', title=None):
    return {'id': episode_id, 'title': title or f'Show S01E0{number or 1} {episode_id}', 'date': date or f'2020-01-0{number or 1}',
            'season': 1 if number else None, 'episode': number}


def entry_with(*episodes, watched=(), new=(), files=None, title='Show', last=None):
    entry = rt.new_media_entry()
    entry.update(title=title, baselined=True, episodes=list(episodes), known=[e['id'] for e in episodes],
                 watched=list(watched), new=list(new), files=dict(files or {}), last_watched=last)
    return entry


class FakeDriver:
    """What the registry hands the mixin: a name, a default order, listing, and the
    optional resolve and download."""

    def __init__(self, name, order, download=None, resolve=None):
        self.name, self.order, self.download, self.resolve = name, order, download, resolve
        self.list_episodes = mock.Mock(return_value=(name, []))


class FakeRegistry:
    def __init__(self):
        self.by_source = {URL: FakeDriver('archive', 'oldest'), POD: FakeDriver('feed', 'newest')}
        self.by_episode_name = {}
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1

    def listing(self, source, result):
        driver = self.by_source[source]
        if isinstance(result, Exception):
            driver.list_episodes = mock.Mock(side_effect=result)
        else:
            driver.list_episodes = mock.Mock(return_value=result)

    def driver_for(self, source):
        if source not in self.by_source:
            raise rt.NoDriver(f'no source handler for {source}')
        return self.by_source[source], source

    def driver_of(self, episode):
        name = episode.get('driver') or ('archive' if str(episode.get('kind', '')).startswith('archive') else 'feed')
        for driver in self.by_source.values():
            if driver.name == name:
                return driver
        raise rt.NoDriver(f'the {name} source plugin is not loaded')


class Stub:
    _effective_order = rt.RssTray._effective_order
    _check_media_guarded = rt.RssTray._check_media_guarded
    start_media_check = rt.RssTray.start_media_check
    _check_media = rt.RssTray._check_media
    _on_media_new = rt.RssTray._on_media_new
    _media_file_ready = staticmethod(rt.RssTray._media_file_ready)
    _plan_media_downloads = rt.RssTray._plan_media_downloads
    _enqueue_media = rt.RssTray._enqueue_media
    _media_worker = rt.RssTray._media_worker
    _media_display_name = rt.RssTray._media_display_name
    _download_media_episode = rt.RssTray._download_media_episode
    _media_source_order = rt.RssTray._media_source_order
    on_media_row_clicked = rt.RssTray.on_media_row_clicked
    _on_media_played = rt.RssTray._on_media_played
    mark_media_watched = rt.RssTray.mark_media_watched
    media_source_urls = rt.RssTray.media_source_urls
    build_media_row = rt.RssTray.build_media_row
    _update_media_row = rt.RssTray._update_media_row
    _refresh_media_rows = rt.RssTray._refresh_media_rows

    def __init__(self):
        self.media = {'sources': {}}
        self.media_status = {}
        self.media_errors = {}
        self.media_registry = FakeRegistry()
        self._media_check_lock = threading.Lock()
        self._media_data_lock = threading.Lock()
        self._media_queue_lock = threading.Lock()
        self._media_queue = []
        self._media_current = None
        self._media_worker_running = False
        self.popup = mock.MagicMock()
        self.popup.get_visible.return_value = False
        self.listbox = mock.MagicMock()
        self.note_request_failure = mock.Mock()
        self.on_new_items = mock.Mock()


class MediaCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.sources = [(URL, None, None)]
        self.settings = {'path': self.dir, 'interval': 3600, 'ahead': 2}
        self.idle = []
        for name, value in (('load_media_sources', mock.Mock(side_effect=lambda: list(self.sources))),
                            ('load_media_settings', mock.Mock(side_effect=lambda: dict(self.settings))),
                            ('save_media', mock.Mock())):
            patcher = mock.patch.object(rt, name, value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(rt.GLib, 'idle_add', side_effect=lambda f, *a: self.idle.append((f, a)))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(rt.GLib, 'markup_escape_text',
                                    side_effect=lambda t: t.replace('&', '&amp;').replace('<', '&lt;'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def queued(self, stub, name):
        return [a for f, a in self.idle if f == getattr(stub, name)]

    def touch(self, name):
        path = os.path.join(self.dir, name)
        with open(path, 'w') as f:
            f.write('x')
        return path


class TestMediaRowView(unittest.TestCase):
    def view(self, entry, order='oldest', downloaded=False, status=None, name='Show'):
        return rt.media_row_view(name, entry, order, downloaded, status)

    def test_a_ready_episode(self):
        entry = entry_with(ep('a', 1), ep('b', 2))
        self.assertEqual(self.view(entry, downloaded=True)[:3], ('Show', 'S01E01 a', 'ready'))
        self.assertEqual(self.view(entry, downloaded=True)[4], 'ready')

    def test_the_next_episode_is_the_first_unwatched_in_order(self):
        entry = entry_with(ep('a', 1), ep('b', 2), watched=['a'])
        self.assertEqual(self.view(entry)[1], 'S01E02 b')
        newest = entry_with(ep('a', 1), ep('b', 2), watched=['b'])
        self.assertEqual(self.view(newest, order='newest')[1], 'S01E01 a')

    def test_the_title_is_cleaned_of_the_series_name_and_number(self):
        entry = entry_with(ep('x', 1, title='Show S01E01 Pilot'))
        self.assertEqual(self.view(entry)[1], 'S01E01 Pilot')
        self.assertEqual(self.view(entry, name='My Name')[1], 'S01E01 Pilot')  # the feed's own title is cut too

    def test_an_episode_without_a_number_shows_its_date(self):
        entry = entry_with(ep('p', None, date='2025-09-03', title='Pod episode 3'))
        self.assertEqual(self.view(entry)[1], '2025-09-03 Pod episode 3')

    def test_one_that_is_not_on_disk(self):
        _, _, stats, tooltip, color = self.view(entry_with(ep('a', 1)))
        self.assertEqual((stats, color), ('not downloaded', 'idle'))
        self.assertIn('Click to download', tooltip)

    def test_progress(self):
        _, _, stats, tooltip, color = self.view(entry_with(ep('a', 1)), status={'state': 'downloading', 'percent': 43})
        self.assertEqual((stats, color), ('43%', 'busy'))
        self.assertIn('Downloading', tooltip)

    def test_queued(self):
        _, _, stats, _t, color = self.view(entry_with(ep('a', 1)), status={'state': 'queued'})
        self.assertEqual((stats, color), ('queued', 'busy'))

    def test_a_failure_shows_why_in_the_tooltip(self):
        _, _, stats, tooltip, color = self.view(entry_with(ep('a', 1)), status={'state': 'failed', 'error': 'HTTP Error 503'})
        self.assertEqual((stats, color), ('failed', 'failed'))
        self.assertIn('HTTP Error 503', tooltip)

    def test_a_failure_without_a_message_is_still_explained(self):
        self.assertIn('unknown error', self.view(entry_with(ep('a', 1)), status={'state': 'failed'})[3])

    def test_new_episodes_are_counted_after_the_status(self):
        entry = entry_with(ep('a', 1), ep('b', 2), ep('c', 3), new=['b', 'c'])
        self.assertEqual(self.view(entry, downloaded=True)[2], 'ready - 2 new')
        self.assertEqual(self.view(entry, status={'state': 'downloading', 'percent': 5})[2], '5% - 2 new')

    def test_watched_new_episodes_are_not_counted(self):
        entry = entry_with(ep('a', 1), ep('b', 2), new=['b'], watched=['b'])
        self.assertEqual(self.view(entry, downloaded=True)[2], 'ready')

    def test_everything_watched(self):
        entry = entry_with(ep('a', 1), watched=['a'], last={'id': 'a', 'title': 'Pilot'})
        name, text, stats, tooltip, color = self.view(entry)
        self.assertEqual((text, stats, color), ('all watched', '', 'idle'))
        self.assertEqual(tooltip, 'Last watched: Pilot')

    def test_new_episodes_are_still_counted_when_nothing_is_left_to_watch(self):
        entry = entry_with(ep('a', 1), watched=['a'], new=['a'])
        self.assertEqual(self.view(entry)[2], '')  # watched ones are not new

    def test_a_source_with_no_episodes(self):
        self.assertEqual(self.view(entry_with())[1:3], ('no episodes yet', ''))

    def test_a_source_that_cannot_be_read_shows_why_and_offers_a_retry(self):
        name, text, stats, tooltip, color = rt.media_row_view('Show', entry_with(), 'oldest', False, None, 'yt-dlp is not installed')
        self.assertEqual((text, stats, color), ('cannot be read', '', 'failed'))
        self.assertEqual(tooltip.split('\n'), ['yt-dlp is not installed', 'Click to try again'])

    def test_a_source_with_episodes_that_failed_its_last_check_keeps_its_row_and_notes_it(self):
        entry = entry_with(ep('a', 1))
        _, text, stats, tooltip, color = rt.media_row_view('Show', entry, 'oldest', True, None, 'OSError: offline')
        self.assertEqual((text, stats, color), ('S01E01 a', 'ready', 'ready'))
        self.assertIn('Last check failed: OSError: offline', tooltip)

    def test_the_error_is_in_the_tooltip_when_everything_is_watched_too(self):
        entry = entry_with(ep('a', 1), watched=['a'])
        self.assertEqual(rt.media_row_view('Show', entry, 'oldest', False, None, 'x')[3], 'Last check failed: x')

    def test_the_tooltip_has_the_episode_the_hint_the_count_and_the_last_watched(self):
        entry = entry_with(ep('a', 1), ep('b', 2), ep('c', 3), watched=['a'], last={'id': 'a', 'title': 'A title'})
        tooltip = self.view(entry, downloaded=True)[3]
        self.assertEqual(tooltip.split('\n'), ['S01E02 b', 'Click to play', '2 unwatched', 'Last watched: A title'])

    def test_a_last_watched_without_a_title_shows_its_id(self):
        entry = entry_with(ep('a', 1), watched=['a'], last={'id': 'a', 'title': ''})
        self.assertEqual(self.view(entry)[3], 'Last watched: a')


class TestChecking(MediaCase):
    def check(self, stub, **listings):
        """Runs a check with the listings given as {source url: result} on the fake drivers."""
        for source, result in listings.items():
            stub.media_registry.listing(source, result)
        stub._check_media()

    def test_the_first_check_sets_the_baseline_and_announces_nothing(self):
        stub = Stub()
        stub._plan_media_downloads = mock.Mock()
        stub.media_registry.listing(URL, ('Show', [ep('a', 1), ep('b', 2)]))
        stub._check_media()
        entry = stub.media['sources'][URL]
        self.assertTrue(entry['baselined'])
        self.assertEqual(entry['watched'], [])  # the archive driver's default is a series: from the start
        self.assertEqual(self.queued(stub, '_on_media_new'), [])
        self.assertEqual(len(self.queued(stub, '_refresh_media_rows')), 1)
        stub._plan_media_downloads.assert_called_once_with()
        self.save_media.assert_called_with(stub.media)
        self.assertEqual(stub.media_registry.refreshed, 1)  # plugins are looked at before every check

    def test_a_feed_is_a_podcast_and_starts_at_its_latest(self):
        self.sources = [(POD, 'Pod', None)]
        stub = Stub()
        stub._plan_media_downloads = mock.Mock()
        stub.media_registry.listing(POD, ('Pod', [ep('a', 1), ep('b', 2), ep('c', 3)]))
        stub._check_media()
        self.assertEqual(sorted(stub.media['sources'][POD]['watched']), ['a', 'b'])

    def test_the_configured_order_wins_over_the_drivers(self):
        self.sources = [(POD, 'Pod', 'oldest')]
        stub = Stub()
        stub._plan_media_downloads = mock.Mock()
        stub.media_registry.listing(POD, ('Pod', [ep('a', 1), ep('b', 2)]))
        stub._check_media()
        self.assertEqual(stub.media['sources'][POD]['watched'], [])

    def test_a_new_episode_is_announced(self):
        stub = Stub()
        stub._plan_media_downloads = mock.Mock()
        stub.media['sources'][URL] = entry_with(ep('a', 1))
        stub.media_registry.listing(URL, ('Show', [ep('a', 1), ep('b', 2)]))
        stub._check_media()
        self.assertEqual(len(self.queued(stub, '_on_media_new')), 1)
        self.assertEqual(stub.media['sources'][URL]['new'], ['b'])

    def test_a_source_that_cannot_be_read_keeps_what_is_known_and_the_others_go_on(self):
        self.sources = [(URL, None, None), (POD, None, None)]
        stub = Stub()
        stub._plan_media_downloads = mock.Mock()
        old = entry_with(ep('a', 1))
        stub.media['sources'][URL] = old
        stub.media_registry.listing(URL, OSError('offline'))
        stub.media_registry.listing(POD, ('Pod', [ep('p', 1)]))
        stub._check_media()
        stub.note_request_failure.assert_called_once_with()  # a network error may mean the connection is down
        self.assertIs(stub.media['sources'][URL], old)
        self.assertEqual(old['episodes'], [ep('a', 1)])
        self.assertIn(POD, stub.media['sources'])
        self.assertEqual(stub.media_errors, {URL: 'OSError: offline'})
        stub._plan_media_downloads.assert_called_once_with()

    def test_a_drivers_own_complaint_is_shown_as_it_is_and_is_not_a_network_failure(self):
        stub = Stub()
        stub._plan_media_downloads = mock.Mock()
        stub.media_registry.listing(URL, ValueError('yt-dlp is not installed (sudo xbps-install yt-dlp)'))
        stub._check_media()
        self.assertEqual(stub.media_errors, {URL: 'yt-dlp is not installed (sudo xbps-install yt-dlp)'})
        stub.note_request_failure.assert_not_called()

    def test_a_bug_in_a_plugin_is_shown_with_its_kind(self):
        stub = Stub()
        stub._plan_media_downloads = mock.Mock()
        stub.media_registry.listing(URL, KeyError('episodes'))
        stub._check_media()
        self.assertEqual(stub.media_errors[URL], "KeyError: 'episodes'")

    def test_a_source_nobody_handles_says_so(self):
        self.sources = [('ftp://example.com/x', None, None)]
        stub = Stub()
        stub._plan_media_downloads = mock.Mock()
        stub._check_media()
        self.assertEqual(stub.media_errors, {'ftp://example.com/x': 'no source handler for ftp://example.com/x'})
        stub.note_request_failure.assert_not_called()

    def test_a_source_that_reads_again_loses_its_error(self):
        stub = Stub()
        stub._plan_media_downloads = mock.Mock()
        stub.media_errors[URL] = 'old problem'
        stub.media_registry.listing(URL, ('Show', [ep('a', 1)]))
        stub._check_media()
        self.assertNotIn(URL, stub.media_errors)

    def test_removed_sources_are_not_read(self):
        self.sources = []
        stub = Stub()
        stub._plan_media_downloads = mock.Mock()
        stub._check_media()
        for driver in stub.media_registry.by_source.values():
            driver.list_episodes.assert_not_called()

    def test_overlapping_checks_are_skipped(self):
        stub = Stub()
        stub._media_check_lock.acquire()
        with mock.patch.object(rt.threading, 'Thread') as thread:
            stub.start_media_check()
        thread.assert_not_called()

    def test_a_check_runs_in_a_thread_and_releases_its_lock(self):
        stub = Stub()
        stub._check_media = mock.Mock()
        with mock.patch.object(rt.threading, 'Thread') as thread:
            stub.start_media_check()
            self.assertEqual(thread.call_args.kwargs['target'], stub._check_media_guarded)
        stub._check_media_guarded()
        stub._check_media.assert_called_once_with()
        self.assertTrue(stub._media_check_lock.acquire(blocking=False))

    def test_a_check_that_blows_up_still_releases_its_lock(self):
        stub = Stub()
        stub._check_media = mock.Mock(side_effect=RuntimeError('boom'))
        stub._media_check_lock.acquire()
        with self.assertRaises(RuntimeError):
            stub._check_media_guarded()
        self.assertTrue(stub._media_check_lock.acquire(blocking=False))

    def test_new_episodes_open_a_closed_popup_like_a_channel_going_live(self):
        stub = Stub()
        stub._refresh_media_rows = mock.Mock()
        self.assertFalse(stub._on_media_new())
        stub.on_new_items.assert_called_once_with()
        stub._refresh_media_rows.assert_not_called()

    def test_with_the_popup_open_the_rows_just_update(self):
        stub = Stub()
        stub.popup.get_visible.return_value = True
        stub._refresh_media_rows = mock.Mock()
        stub._on_media_new()
        stub._refresh_media_rows.assert_called_once_with()
        stub.on_new_items.assert_not_called()


class TestDescribeError(unittest.TestCase):
    def test_a_message_from_a_driver_is_kept(self):
        self.assertEqual(rt.describe_media_error(ValueError('could not read x')), 'could not read x')
        self.assertEqual(rt.describe_media_error(rt.NoDriver('no source handler for y')), 'no source handler for y')

    def test_other_errors_name_their_kind(self):
        self.assertEqual(rt.describe_media_error(OSError('boom')), 'OSError: boom')
        self.assertEqual(rt.describe_media_error(TimeoutError()), 'TimeoutError')

    def test_an_empty_value_error_still_says_something(self):
        self.assertEqual(rt.describe_media_error(ValueError()), 'ValueError')

    def test_long_messages_are_cut(self):
        self.assertEqual(len(rt.describe_media_error(ValueError('x' * 1000))), 300)


class TestPeriodicCheck(MediaCase):
    def test_it_checks_and_rearms_with_the_interval_now_in_the_config(self):
        stub = Stub()
        stub.start_media_check = mock.Mock()
        self.settings['interval'] = 1800
        with mock.patch.object(rt.GLib, 'timeout_add_seconds') as timer:
            self.assertFalse(stub_periodic(stub))
        stub.start_media_check.assert_called_once_with()
        timer.assert_called_once_with(1800, stub.periodic_media_check)

    def test_the_first_check_waits_for_the_network_without_blocking(self):
        stub = Stub()
        stub.start_media_check = mock.Mock()
        with mock.patch.object(rt, 'run_when_online') as run, mock.patch.object(rt, 'is_online') as probe:
            self.assertFalse(rt.RssTray.initial_media_check(stub))
        run.assert_called_once_with(stub.start_media_check)
        probe.assert_not_called()


def stub_periodic(stub):
    stub.periodic_media_check = rt.RssTray.periodic_media_check.__get__(stub)
    return stub.periodic_media_check()


class TestEffectiveOrder(MediaCase):
    def test_the_configured_order_wins(self):
        stub = Stub()
        self.assertEqual(stub._effective_order(URL, 'newest'), 'newest')
        self.assertEqual(stub._effective_order(POD, 'oldest'), 'oldest')

    def test_otherwise_the_drivers_default_applies(self):
        stub = Stub()
        self.assertEqual(stub._effective_order(URL, None), 'oldest')
        self.assertEqual(stub._effective_order(POD, None), 'newest')
        self.assertEqual(stub._effective_order(URL, 'sideways'), 'oldest')

    def test_a_source_nobody_handles_is_read_as_a_podcast(self):
        self.assertEqual(Stub()._effective_order('ftp://x', None), 'newest')

    def test_the_source_order_looks_up_the_configured_one(self):
        self.sources = [(URL, None, 'newest'), (POD, None, None)]
        stub = Stub()
        self.assertEqual(stub._media_source_order(URL), 'newest')
        self.assertEqual(stub._media_source_order(POD), 'newest')


class TestPlanning(MediaCase):
    def stub(self, entry, url=URL):
        stub = Stub()
        stub.media['sources'][url] = entry
        stub._enqueue_media = mock.Mock()
        return stub

    def test_the_next_unwatched_episodes_up_to_ahead_are_wanted(self):
        stub = self.stub(entry_with(ep('a', 1), ep('b', 2), ep('c', 3), ep('d', 4), watched=['a']))
        stub._plan_media_downloads()
        stub._enqueue_media.assert_called_once_with([(URL, 'b'), (URL, 'c')])

    def test_episodes_already_on_disk_are_not_wanted_again(self):
        path = self.touch('a.mp4')
        stub = self.stub(entry_with(ep('a', 1), ep('b', 2), ep('c', 3), files={'a': {'video': path, 'subs': []}}))
        stub._plan_media_downloads()
        stub._enqueue_media.assert_called_once_with([(URL, 'b')])  # ahead counts the one on disk

    def test_a_file_that_was_deleted_is_wanted_again(self):
        stub = self.stub(entry_with(ep('a', 1), files={'a': {'video': os.path.join(self.dir, 'gone.mp4'), 'subs': []}}))
        stub._plan_media_downloads()
        stub._enqueue_media.assert_called_once_with([(URL, 'a')])

    def test_ahead_zero_downloads_nothing(self):
        self.settings['ahead'] = 0
        stub = self.stub(entry_with(ep('a', 1)))
        stub._plan_media_downloads()
        stub._enqueue_media.assert_called_once_with([])

    def test_a_podcast_wants_its_newest_unwatched_first(self):
        self.sources = [(POD, 'Pod', None)]
        stub = self.stub(entry_with(ep('a', 1), ep('b', 2), ep('c', 3), watched=['c']), url=POD)
        stub._plan_media_downloads()
        stub._enqueue_media.assert_called_once_with([(POD, 'b'), (POD, 'a')])

    def test_sources_not_read_yet_or_no_longer_configured_are_ignored(self):
        self.sources = [(URL, None, None), (POD, None, None)]
        stub = self.stub(entry_with(ep('x', 1)), url='https://gone.example/feed')
        stub._plan_media_downloads()
        stub._enqueue_media.assert_called_once_with([])


class TestQueue(MediaCase):
    def test_items_are_queued_with_a_status_and_a_worker_starts(self):
        stub = Stub()
        with mock.patch.object(rt.threading, 'Thread') as thread:
            stub._enqueue_media([(URL, 'a'), (URL, 'b')])
        self.assertEqual(stub._media_queue, [(URL, 'a'), (URL, 'b')])
        self.assertEqual(stub.media_status[(URL, 'a')], {'state': 'queued'})
        self.assertTrue(stub._media_worker_running)
        self.assertEqual(thread.call_args.kwargs['target'], stub._media_worker)
        thread.return_value.start.assert_called_once_with()
        self.assertEqual(len(self.queued(stub, '_refresh_media_rows')), 1)

    def test_a_running_worker_is_not_started_twice(self):
        stub = Stub()
        stub._media_worker_running = True
        with mock.patch.object(rt.threading, 'Thread') as thread:
            stub._enqueue_media([(URL, 'a')])
        thread.assert_not_called()

    def test_what_is_queued_or_downloading_is_not_queued_again(self):
        stub = Stub()
        stub._media_queue = [(URL, 'a')]
        stub._media_current = (URL, 'b')
        stub._media_worker_running = True
        stub._enqueue_media([(URL, 'a'), (URL, 'b'), (URL, 'c')])
        self.assertEqual(stub._media_queue, [(URL, 'a'), (URL, 'c')])
        self.assertNotIn((URL, 'b'), stub.media_status)  # the current download keeps its own status

    def test_a_click_goes_to_the_front(self):
        stub = Stub()
        stub._media_queue = [(URL, 'a')]
        stub._media_worker_running = True
        stub._enqueue_media([(URL, 'z')], front=True)
        self.assertEqual(stub._media_queue, [(URL, 'z'), (URL, 'a')])

    def test_nothing_new_means_no_refresh_and_no_worker(self):
        stub = Stub()
        with mock.patch.object(rt.threading, 'Thread') as thread:
            stub._enqueue_media([])
        thread.assert_not_called()
        self.assertEqual(self.idle, [])

    def test_the_worker_goes_through_the_queue_and_then_stops(self):
        stub = Stub()
        stub._media_queue = [(URL, 'a'), (URL, 'b')]
        stub._media_worker_running = True
        done = []

        def download(url, episode_id):
            done.append((episode_id, stub._media_current))
            if episode_id == 'a':
                stub._media_queue.append((URL, 'late'))  # queued while working
        stub._download_media_episode = download
        stub._media_worker()
        self.assertEqual(done, [('a', (URL, 'a')), ('b', (URL, 'b')), ('late', (URL, 'late'))])
        self.assertFalse(stub._media_worker_running)
        self.assertIsNone(stub._media_current)


class TestDownloading(MediaCase):
    def stub(self, *episodes, title='Show'):
        stub = Stub()
        stub.media['sources'][URL] = entry_with(*episodes, title=title)
        return stub

    def files(self):
        return {'video': {'url': 'http://h/a.mp4', 'ext': '.mp4'}, 'subs': []}

    def test_a_finished_download_is_recorded_and_its_status_cleared(self):
        stub = self.stub(ep('a', 1))
        saved = {'video': os.path.join(self.dir, 'Show', 'a.mp4'), 'subs': []}
        seen = []

        def download(paths, files, progress=None, cancelled=None):
            seen.append(dict(stub.media_status[(URL, 'a')]))
            progress(50, 100)
            seen.append(dict(stub.media_status[(URL, 'a')]))
            progress(100, 100)
            seen.append(dict(stub.media_status[(URL, 'a')]))
            return saved
        with mock.patch.object(rt, 'resolve_files', return_value=self.files()), \
                mock.patch.object(rt, 'download_episode', side_effect=download):
            stub._download_media_episode(URL, 'a')
        self.assertEqual([s['percent'] for s in seen], [0, 50, 100])
        self.assertEqual(stub.media['sources'][URL]['files']['a'], saved)
        self.assertNotIn((URL, 'a'), stub.media_status)
        self.save_media.assert_called_with(stub.media)
        self.assertGreaterEqual(len(self.queued(stub, '_refresh_media_rows')), 3)

    def test_progress_is_only_reported_when_the_percent_moves(self):
        stub = self.stub(ep('a', 1))

        def download(paths, files, progress=None, cancelled=None):
            for done in range(0, 1001, 1):  # a thousand blocks of a hundred percent
                progress(done, 1000)
            return {'video': 'v', 'subs': []}
        with mock.patch.object(rt, 'resolve_files', return_value=self.files()), \
                mock.patch.object(rt, 'download_episode', side_effect=download):
            stub._download_media_episode(URL, 'a')
        self.assertLessEqual(len(self.queued(stub, '_refresh_media_rows')), 103)

    def test_an_unknown_total_does_not_divide_by_zero(self):
        stub = self.stub(ep('a', 1))

        def download(paths, files, progress=None, cancelled=None):
            progress(5000, None)
            return {'video': 'v', 'subs': []}
        with mock.patch.object(rt, 'resolve_files', return_value=self.files()), \
                mock.patch.object(rt, 'download_episode', side_effect=download):
            stub._download_media_episode(URL, 'a')
        self.assertIn('a', stub.media['sources'][URL]['files'])

    def test_files_go_where_the_settings_say_named_after_the_display_name(self):
        self.sources = [(URL, 'My Show', None)]
        stub = self.stub(ep('a', 1, title='Real Name S01E01 Pilot'), title='Real Name')
        with mock.patch.object(rt, 'resolve_files', return_value=self.files()), \
                mock.patch.object(rt, 'episode_paths', return_value={'video': 'v', 'subs': []}) as paths, \
                mock.patch.object(rt, 'download_episode', return_value={'video': 'v', 'subs': []}):
            stub._download_media_episode(URL, 'a')
        args, kwargs = paths.call_args
        self.assertEqual(args[:2], (self.dir, 'My Show'))
        self.assertEqual(kwargs['names'], ('Real Name',))

    def test_names_that_other_files_already_use_are_passed_on(self):
        stub = self.stub(ep('a', 1), ep('b', 2))
        stub.media['sources'][URL]['files']['a'] = {'video': '/v/a.mp4', 'subs': []}
        with mock.patch.object(rt, 'resolve_files', return_value=self.files()), \
                mock.patch.object(rt, 'episode_paths', return_value={'video': 'v', 'subs': []}) as paths, \
                mock.patch.object(rt, 'download_episode', return_value={'video': 'v', 'subs': []}):
            stub._download_media_episode(URL, 'b')
        self.assertEqual(paths.call_args.args[4], {'/v/a.mp4'})

    def test_a_deleted_episode_comes_back_under_its_own_name(self):
        stub = self.stub(ep('a', 1))
        stub.media['sources'][URL]['files']['a'] = {'video': '/v/Show/a.mp4', 'subs': []}  # recorded, but gone from the disk
        with mock.patch.object(rt, 'resolve_files', return_value=self.files()), \
                mock.patch.object(rt, 'episode_paths', return_value={'video': 'v', 'subs': []}) as paths, \
                mock.patch.object(rt, 'download_episode', return_value={'video': 'v', 'subs': []}):
            stub._download_media_episode(URL, 'a')
        self.assertEqual(paths.call_args.args[4], set())

    def driver_stub(self, download=None, resolve=None, **kwargs):
        stub = self.stub(dict(ep('a', 1, title='Show S01E01 Pilot'), driver='archive'), **kwargs)  # listed by the URL's driver
        driver = stub.media_registry.by_source[URL]
        driver.download, driver.resolve = download, resolve
        return stub, driver

    def test_a_driver_that_downloads_itself_gets_the_base_path_and_the_progress_callback(self):
        saved = self.touch('video.mkv')
        sub = self.touch('video.en.vtt')
        calls = []

        def download(episode, base, progress, cancelled):
            calls.append((episode['id'], base, cancelled()))
            progress(50, 100)
            return {'video': saved, 'subs': [sub, os.path.join(self.dir, 'missing.srt')]}
        stub, _driver = self.driver_stub(download=download)
        with mock.patch.object(rt, 'download_episode') as standard, mock.patch.object(rt, 'resolve_files') as resolve:
            stub._download_media_episode(URL, 'a')
        self.assertEqual(calls, [('a', os.path.join(self.dir, 'Show', 'Show - S01E01 - Pilot'), False)])
        standard.assert_not_called()
        resolve.assert_not_called()
        self.assertEqual(stub.media['sources'][URL]['files']['a'], {'video': saved, 'subs': [sub]})  # only files that exist
        self.assertNotIn((URL, 'a'), stub.media_status)

    def test_the_base_path_avoids_names_other_episodes_use(self):
        saved = self.touch('video.mkv')
        seen = []
        stub, _driver = self.driver_stub(download=lambda e, base, p, c: seen.append(base) or {'video': saved, 'subs': []})
        stub.media['sources'][URL]['files']['other'] = {'video': os.path.join(self.dir, 'Show', 'Show - S01E01 - Pilot.mkv'), 'subs': []}
        stub._download_media_episode(URL, 'a')
        self.assertEqual(seen, [os.path.join(self.dir, 'Show', 'Show - S01E01 - Pilot (2)')])

    def test_a_driver_that_claims_a_download_it_did_not_make_fails_the_episode(self):
        stub, _driver = self.driver_stub(download=lambda e, base, p, c: {'video': os.path.join(self.dir, 'nothing.mkv'), 'subs': []})
        stub._download_media_episode(URL, 'a')
        self.assertEqual(stub.media_status[(URL, 'a')]['state'], 'failed')
        self.assertIn('did not leave the video file', stub.media_status[(URL, 'a')]['error'])
        self.assertEqual(stub.media['sources'][URL]['files'], {})

    def test_a_driver_that_returns_nonsense_fails_the_episode(self):
        for result in (None, 'path', {}, {'video': 5}):
            stub, _driver = self.driver_stub(download=lambda e, base, p, c, r=result: r)
            stub._download_media_episode(URL, 'a')
            self.assertEqual(stub.media_status[(URL, 'a')]['state'], 'failed', result)

    def test_an_error_in_a_drivers_download_is_shown_with_its_kind(self):
        def download(episode, base, progress, cancelled):
            raise ValueError('yt-dlp: Video unavailable')
        stub, _driver = self.driver_stub(download=download)
        stub._download_media_episode(URL, 'a')
        self.assertEqual(stub.media_status[(URL, 'a')], {'state': 'failed', 'error': 'yt-dlp: Video unavailable'})

    def test_a_drivers_own_resolve_replaces_the_standard_lookup(self):
        files = {'video': {'url': 'http://h/a.mp4', 'ext': '.mp4'}, 'subs': []}
        resolved = mock.Mock(return_value=files)
        stub, _driver = self.driver_stub(resolve=resolved)
        with mock.patch.object(rt, 'resolve_files') as standard, \
                mock.patch.object(rt, 'download_episode', return_value={'video': 'v', 'subs': []}) as fetch:
            stub._download_media_episode(URL, 'a')
        resolved.assert_called_once()
        standard.assert_not_called()
        self.assertEqual(fetch.call_args.args[1], {'video': {'url': 'http://h/a.mp4', 'ext': '.mp4'}, 'subs': []})  # checked on the way

    def test_an_episode_whose_driver_is_gone_fails_with_the_reason(self):
        stub = self.stub(dict(ep('a', 1), driver='gone-plugin'))
        stub._download_media_episode(URL, 'a')
        self.assertEqual(stub.media_status[(URL, 'a')]['state'], 'failed')
        self.assertIn('gone-plugin source plugin is not loaded', stub.media_status[(URL, 'a')]['error'])

    def test_a_failure_is_shown_and_nothing_is_recorded(self):
        stub = self.stub(ep('a', 1))
        with mock.patch.object(rt, 'resolve_files', return_value=self.files()), \
                mock.patch.object(rt, 'download_episode', side_effect=OSError('connection reset')):
            stub._download_media_episode(URL, 'a')
        self.assertEqual(stub.media_status[(URL, 'a')], {'state': 'failed', 'error': 'OSError: connection reset'})
        self.assertEqual(stub.media['sources'][URL]['files'], {})
        self.save_media.assert_not_called()

    def test_a_failure_without_a_message_names_the_error(self):
        stub = self.stub(ep('a', 1))
        with mock.patch.object(rt, 'resolve_files', return_value=self.files()), \
                mock.patch.object(rt, 'download_episode', side_effect=TimeoutError()):
            stub._download_media_episode(URL, 'a')
        self.assertEqual(stub.media_status[(URL, 'a')]['error'], 'TimeoutError')

    def test_an_episode_that_cannot_be_resolved_fails_too(self):
        stub = self.stub(ep('a', 1))
        with mock.patch.object(rt, 'resolve_files', side_effect=ValueError('no media file in this item')):
            stub._download_media_episode(URL, 'a')
        self.assertEqual(stub.media_status[(URL, 'a')]['error'], 'no media file in this item')

    def test_an_episode_that_left_the_list_is_forgotten(self):
        stub = self.stub(ep('a', 1))
        stub.media_status[(URL, 'gone')] = {'state': 'queued'}
        with mock.patch.object(rt, 'resolve_files') as resolve:
            stub._download_media_episode(URL, 'gone')
            stub._download_media_episode('https://nowhere.example/', 'x')
        resolve.assert_not_called()
        self.assertNotIn((URL, 'gone'), stub.media_status)

    def test_the_display_name_is_the_configured_one_else_the_title_else_the_url(self):
        stub = Stub()
        self.sources = [(URL, 'Mine', None)]
        self.assertEqual(stub._media_display_name(URL, {'title': 'Theirs'}), 'Mine')
        self.sources = [(URL, None, None)]
        self.assertEqual(stub._media_display_name(URL, {'title': 'Theirs'}), 'Theirs')
        self.assertEqual(stub._media_display_name(URL, {'title': ''}), URL)


class TestPlaying(MediaCase):
    def stub(self, *episodes, **kwargs):
        stub = Stub()
        stub.media['sources'][URL] = entry_with(*episodes, **kwargs)
        stub._enqueue_media = mock.Mock()
        return stub

    def test_a_ready_episode_plays_with_the_popup_out_of_the_way(self):
        path = self.touch('a.mp4')
        stub = self.stub(ep('a', 1, title='Show S01E01 Pilot'), files={'a': {'video': path, 'subs': []}})
        with mock.patch.object(rt, 'play_media_file') as play:
            stub.on_media_row_clicked(URL)
        stub.popup.hide.assert_called_once_with()
        self.assertEqual(play.call_args.args, (path, 'Show \u00b7 S01E01 Pilot'))
        stub._enqueue_media.assert_not_called()

    def test_the_window_title_uses_the_configured_name(self):
        self.sources = [(URL, 'My Show', None)]
        path = self.touch('a.mp4')
        stub = self.stub(ep('a', 1, title='Show S01E01 Pilot'), files={'a': {'video': path, 'subs': []}})
        with mock.patch.object(rt, 'play_media_file') as play:
            stub.on_media_row_clicked(URL)
        self.assertEqual(play.call_args.args[1], 'My Show \u00b7 S01E01 Pilot')

    def test_it_is_the_next_unwatched_episode_that_plays(self):
        a, b = self.touch('a.mp4'), self.touch('b.mp4')
        stub = self.stub(ep('a', 1), ep('b', 2), watched=['a'],
                         files={'a': {'video': a, 'subs': []}, 'b': {'video': b, 'subs': []}})
        with mock.patch.object(rt, 'play_media_file') as play:
            stub.on_media_row_clicked(URL)
        self.assertEqual(play.call_args.args[0], b)

    def test_the_end_of_playback_comes_back_through_the_main_loop(self):
        path = self.touch('a.mp4')
        stub = self.stub(ep('a', 1), files={'a': {'video': path, 'subs': []}})
        with mock.patch.object(rt, 'play_media_file') as play:
            stub.on_media_row_clicked(URL)
        play.call_args.kwargs['on_finished'](95.0, 3.0)  # called from the watcher thread
        self.assertEqual(self.queued(stub, '_on_media_played'), [(URL, 'a', 95.0, 3.0)])

    def test_an_episode_that_is_not_downloaded_is_queued_at_the_front_not_played(self):
        stub = self.stub(ep('a', 1))
        with mock.patch.object(rt, 'play_media_file') as play:
            stub.on_media_row_clicked(URL)
        play.assert_not_called()
        stub._enqueue_media.assert_called_once_with([(URL, 'a')], front=True)
        stub.popup.hide.assert_not_called()

    def test_a_download_that_is_already_going_is_left_alone(self):
        for state in ('queued', 'downloading'):
            stub = self.stub(ep('a', 1))
            stub.media_status[(URL, 'a')] = {'state': state}
            stub.on_media_row_clicked(URL)
            stub._enqueue_media.assert_not_called()

    def test_a_failed_download_is_retried_by_a_click(self):
        stub = self.stub(ep('a', 1))
        stub.media_status[(URL, 'a')] = {'state': 'failed', 'error': 'x'}
        stub.on_media_row_clicked(URL)
        stub._enqueue_media.assert_called_once_with([(URL, 'a')], front=True)

    def test_a_file_that_was_deleted_is_downloaded_again(self):
        stub = self.stub(ep('a', 1), files={'a': {'video': os.path.join(self.dir, 'gone.mp4'), 'subs': []}})
        with mock.patch.object(rt, 'play_media_file') as play:
            stub.on_media_row_clicked(URL)
        play.assert_not_called()
        stub._enqueue_media.assert_called_once_with([(URL, 'a')], front=True)

    def test_nothing_left_to_watch_or_an_unknown_source_does_nothing(self):
        stub = self.stub(ep('a', 1), watched=['a'])
        with mock.patch.object(rt, 'play_media_file') as play:
            stub.on_media_row_clicked(URL)
            stub.on_media_row_clicked('https://nowhere.example/')
        play.assert_not_called()
        stub._enqueue_media.assert_not_called()

    def test_a_podcast_plays_its_newest_unwatched(self):
        self.sources = [(POD, 'Pod', None)]
        a, c = self.touch('a.mp3'), self.touch('c.mp3')
        stub = Stub()
        stub.media['sources'][POD] = entry_with(ep('a', 1), ep('c', 3),
                                                files={'a': {'video': a, 'subs': []}, 'c': {'video': c, 'subs': []}})
        with mock.patch.object(rt, 'play_media_file') as play:
            stub.on_media_row_clicked(POD)
        self.assertEqual(play.call_args.args[0], c)

    def test_watched_enough_marks_the_episode(self):
        stub = Stub()
        stub.mark_media_watched = mock.Mock()
        self.assertFalse(stub._on_media_played(URL, 'a', 96.0, 2.0))
        stub.mark_media_watched.assert_called_once_with(URL, 'a')

    def test_stopping_part_way_does_not(self):
        stub = Stub()
        stub.mark_media_watched = mock.Mock()
        stub._on_media_played(URL, 'a', 30.0, 700.0)
        stub._on_media_played(URL, 'a', None, None)
        stub.mark_media_watched.assert_not_called()


class TestWatched(MediaCase):
    def stub(self, *episodes, **kwargs):
        stub = Stub()
        stub.media['sources'][URL] = entry_with(*episodes, **kwargs)
        stub._plan_media_downloads = mock.Mock()
        stub._refresh_media_rows = mock.Mock()
        return stub

    def test_marking_remembers_the_episode_and_when(self):
        stub = self.stub(ep('a', 1, title='Show S01E01 Pilot'), ep('b', 2), new=['a', 'b'])
        with mock.patch.object(rt.time_module, 'time', return_value=1234.5):
            stub.mark_media_watched(URL, 'a')
        entry = stub.media['sources'][URL]
        self.assertEqual(entry['watched'], ['a'])
        self.assertEqual(entry['last_watched'], {'id': 'a', 'title': 'Show S01E01 Pilot', 'at': 1234.5})
        self.assertEqual(entry['new'], ['b'])
        self.save_media.assert_called_with(stub.media)

    def test_it_keeps_the_next_ones_ready_and_updates_the_rows(self):
        stub = self.stub(ep('a', 1))
        stub.mark_media_watched(URL, 'a')
        stub._plan_media_downloads.assert_called_once_with()
        stub._refresh_media_rows.assert_called_once_with()

    def test_watching_twice_lists_it_once(self):
        stub = self.stub(ep('a', 1))
        stub.mark_media_watched(URL, 'a')
        stub.mark_media_watched(URL, 'a')
        self.assertEqual(stub.media['sources'][URL]['watched'], ['a'])

    def test_an_episode_that_left_the_list_is_still_remembered(self):
        stub = self.stub(ep('a', 1))
        stub.mark_media_watched(URL, 'vanished')
        self.assertEqual(stub.media['sources'][URL]['last_watched']['title'], 'vanished')

    def test_an_unknown_source_is_ignored(self):
        stub = self.stub(ep('a', 1))
        stub.mark_media_watched('https://nowhere.example/', 'a')
        stub._plan_media_downloads.assert_not_called()

    def test_marking_moves_the_next_episode_on(self):
        stub = self.stub(ep('a', 1), ep('b', 2))
        stub._plan_media_downloads = rt.RssTray._plan_media_downloads.__get__(stub)
        stub._enqueue_media = mock.Mock()
        stub.mark_media_watched(URL, 'a')
        stub._enqueue_media.assert_called_once_with([(URL, 'b')])


class TestRows(MediaCase):
    def row(self, url=URL):
        row = mock.Mock(media_source=url)
        return row

    def stub(self, *episodes, **kwargs):
        stub = Stub()
        stub.media['sources'][URL] = entry_with(*episodes, **kwargs)
        return stub

    def test_the_sources_listed_are_the_configured_ones_that_were_read(self):
        self.sources = [(URL, None, None), (POD, None, None), ('https://c.example/x', None, None)]
        stub = Stub()
        stub.media['sources'][POD] = entry_with(ep('a', 1))
        stub.media['sources'][URL] = entry_with(ep('a', 1))
        stub.media['sources']['https://c.example/x'] = entry_with()  # read, but nothing in it yet
        stub.media['sources']['https://removed.example/'] = entry_with(ep('a', 1))
        self.assertEqual(stub.media_source_urls(), [URL, POD])  # config order, no removed sources, no empty ones

    def test_a_source_that_could_not_be_read_is_listed_too(self):
        self.sources = [(URL, None, None), (POD, None, None)]
        stub = Stub()
        stub.media_errors[POD] = 'no source handler for x'
        self.assertEqual(stub.media_source_urls(), [POD])

    def test_a_row_for_a_source_that_never_read_shows_its_error(self):
        stub = Stub()
        stub.media_errors[URL] = 'yt-dlp is not installed'
        row = self.row()
        stub._update_media_row(row, {URL: ('My Show', None)})
        self.assertEqual(row.media_label.set_markup.call_args.args[0],
                         '<span foreground="#000000"><b>My Show</b> - cannot be read</span>')
        self.assertIn('#c0392b', row.media_marker.set_markup.call_args.args[0])
        self.assertIn('yt-dlp is not installed', row.set_tooltip_text.call_args.args[0])
        self.assertNotIn(URL, stub.media['sources'])  # showing it creates no state

    def test_a_row_for_a_source_with_neither_state_nor_error_is_left_alone(self):
        stub = Stub()
        row = self.row()
        stub._update_media_row(row, {})
        row.media_label.set_markup.assert_not_called()

    def test_clicking_an_unreadable_source_checks_it_again(self):
        stub = Stub()
        stub.media_errors[URL] = 'OSError: offline'
        stub.start_media_check = mock.Mock()
        stub.on_media_row_clicked(URL)
        stub.start_media_check.assert_called_once_with()

    def test_clicking_a_source_that_just_has_nothing_to_watch_does_not_recheck(self):
        stub = Stub()
        stub.media['sources'][URL] = entry_with(ep('a', 1), watched=['a'])
        stub.media_errors[URL] = 'OSError: offline'  # but it has episodes, so no retry from the click
        stub.start_media_check = mock.Mock()
        stub.on_media_row_clicked(URL)
        stub.start_media_check.assert_not_called()

    def test_a_row_shows_the_name_the_episode_and_the_stats(self):
        path = self.touch('a.mp4')
        stub = self.stub(ep('a', 1, title='Show S01E01 Pilot'), ep('b', 2), new=['b'], files={'a': {'video': path, 'subs': []}})
        row = self.row()
        stub._update_media_row(row, {})
        row.media_label.set_markup.assert_called_with('<span foreground="#000000"><b>Show</b> - S01E01 Pilot</span>')
        row.media_stats.set_markup.assert_called_with('<span foreground="#000000">- ready - 1 new</span>')
        row.media_marker.set_markup.assert_called_with('<span foreground="#2e8b57">\u25cf</span>')
        self.assertIn('Click to play', row.set_tooltip_text.call_args.args[0])

    def test_the_configured_name_and_order_are_used(self):
        stub = Stub()
        stub.media['sources'][POD] = entry_with(ep('a', 1, title='Old'), ep('b', 2, title='New'), title='Feed Title')
        row = self.row(POD)
        stub._update_media_row(row, {POD: ('My Pod', 'newest')})
        self.assertIn('<b>My Pod</b> - S01E02 New', row.media_label.set_markup.call_args.args[0])

    def test_the_feed_title_is_used_without_a_configured_name(self):
        stub = self.stub(ep('a', 1), title='Feed Title')
        row = self.row()
        stub._update_media_row(row, {})
        self.assertIn('<b>Feed Title</b>', row.media_label.set_markup.call_args.args[0])

    def test_status_colours(self):
        stub = self.stub(ep('a', 1))
        row = self.row()
        for status, color in (({'state': 'downloading', 'percent': 3}, '#d08000'), ({'state': 'queued'}, '#d08000'),
                              ({'state': 'failed', 'error': 'x'}, '#c0392b'), (None, '#808080')):
            stub.media_status.clear()
            if status:
                stub.media_status[(URL, 'a')] = status
            stub._update_media_row(row, {})
            self.assertIn(color, row.media_marker.set_markup.call_args.args[0])

    def test_markup_in_names_and_titles_is_escaped(self):
        stub = self.stub(ep('a', 1, title='Tom & <Jerry>'), title='A <b>')
        row = self.row()
        stub._update_media_row(row, {})
        markup = row.media_label.set_markup.call_args.args[0]
        self.assertIn('A &lt;b>', markup)
        self.assertIn('Tom &amp; &lt;Jerry>', markup)
        self.assertNotIn('<Jerry>', markup)

    def test_no_stats_means_an_empty_stats_label(self):
        stub = self.stub(ep('a', 1), watched=['a'])
        row = self.row()
        stub._update_media_row(row, {})
        row.media_stats.set_markup.assert_called_with('')

    def test_a_source_that_vanished_leaves_its_row_alone(self):
        stub = Stub()
        row = self.row()
        stub._update_media_row(row, {})
        row.media_label.set_markup.assert_not_called()

    def test_refreshing_updates_only_the_media_rows_in_place(self):
        stub = Stub()
        media_a, media_b, other = self.row(), self.row(POD), mock.Mock(spec=['set_tooltip_text'])
        stub.listbox.get_children.return_value = [media_a, other, media_b]
        stub._update_media_row = mock.Mock()
        self.sources = [(URL, 'Mine', 'oldest')]
        self.assertFalse(stub._refresh_media_rows())
        self.assertEqual([c.args[0] for c in stub._update_media_row.call_args_list], [media_a, media_b])
        self.assertEqual(stub._update_media_row.call_args.args[1], {URL: ('Mine', 'oldest')})
        stub.listbox.remove.assert_not_called()  # never rebuilt: that would collapse a media-only popup

    def test_without_a_popup_there_is_nothing_to_refresh(self):
        stub = Stub()
        stub.popup = None
        stub._update_media_row = mock.Mock()
        stub._refresh_media_rows()
        stub._update_media_row.assert_not_called()

    def test_a_row_is_clickable_and_remembers_its_source(self):
        stub = self.stub(ep('a', 1))
        stub._update_media_row = mock.Mock()
        with mock.patch.object(rt, 'Gtk', mock.MagicMock()) as gtk:
            row = stub.build_media_row(URL)
        self.assertEqual(row.media_source, URL)
        row.set_activatable.assert_called_with(True)
        row.set_selectable.assert_called_with(False)
        stub._update_media_row.assert_called_once_with(row, {URL: (None, None)})
        self.assertIs(row, gtk.ListBoxRow.return_value)


if __name__ == '__main__':
    unittest.main()
