import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from facade import rt
from helpers import TmpConfigCase


WORDPRESS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>Site</title><link>https://s.example</link>
<item><title>Match report</title><link>https://s.example/1</link><dc:creator><![CDATA[José Müller]]></dc:creator>
<category><![CDATA[News]]></category><category><![CDATA[winter sports]]></category>
<category><![CDATA[sports]]></category><category><![CDATA[sports archive]]></category></item>
<item><title>Winter sports preview</title><link>https://s.example/2</link><dc:creator><![CDATA[Other Author]]></dc:creator>
<category><![CDATA[News]]></category><category><![CDATA[winter sports]]></category></item>
<item><title>Garden tips</title><link>https://s.example/3</link><dc:creator><![CDATA[Other Author]]></dc:creator>
<category><![CDATA[News]]></category><category><![CDATA[plants]]></category></item>
</channel></rss>"""


class TestMute(TmpConfigCase):
    def test_phrases_lowercased_and_pipe_split(self):
        self.conf('[mute]\nSponsored | Giveaway\nAd\n||\n')
        self.assertEqual(rt.load_mute_filters(), ['sponsored', 'giveaway', 'ad'])

    def test_is_muted_case_insensitive_substring(self):
        self.assertTrue(rt.is_muted('Big GIVEAWAY today', ['giveaway']))

    def test_is_muted_no_match_or_no_phrases(self):
        self.assertFalse(rt.is_muted('Hello', ['giveaway']))
        self.assertFalse(rt.is_muted('Hello', []))


class TestEntryHelpers(unittest.TestCase):
    def test_id_preferred_over_link(self):
        self.assertEqual(rt.entry_id({'id': 'x', 'link': 'y'}, 'u'), rt.entry_id({'id': 'x', 'link': 'z'}, 'u'))

    def test_link_fallback(self):
        self.assertEqual(rt.entry_id({'link': 'y'}, 'u'), rt.entry_id({'link': 'y', 'title': 't'}, 'u'))

    def test_title_and_published_fallback(self):
        a = rt.entry_id({'title': 't', 'published': '1'}, 'u')
        b = rt.entry_id({'title': 't', 'published': '2'}, 'u')
        self.assertNotEqual(a, b)
        self.assertEqual(len(a), 40)

    def test_age_none_without_date(self):
        self.assertIsNone(rt.entry_age_seconds({}))

    def test_age_from_published_parsed(self):
        entry = {'published_parsed': time.gmtime(time.time() - 3600)}
        self.assertAlmostEqual(rt.entry_age_seconds(entry), 3600, delta=5)


class TestEntryIdFeedScoping(unittest.TestCase):
    def test_fallback_scoped_by_feed_url(self):
        entry = {'title': 't', 'published': 'd'}
        a = rt.entry_id(entry, 'https://a')
        b = rt.entry_id(entry, 'https://b')
        self.assertNotEqual(a, b)

    def test_id_or_link_present_ignores_feed_url(self):
        entry = {'id': 'x'}
        self.assertEqual(rt.entry_id(entry, 'https://a'), rt.entry_id(entry, 'https://b'))


class TestFeedMuteRules(TmpConfigCase):
    def entries(self):
        return rt.feedparser.parse(WORDPRESS_FEED).entries

    def muted(self, rules_text):
        rules = rt.parse_mute_rules(rules_text)
        return [e.title for e in self.entries() if rt.entry_matches_mute_rules(e, rules)]

    def test_parse_rules(self):
        self.assertEqual(rt.parse_mute_rules('category=Sports; Author = Some Name '),
                         [('category', 'sports'), ('author', 'some name')])

    def test_commas_list_alternatives_for_one_field(self):
        self.assertEqual(rt.parse_mute_rules('category=cat1,cat2, Cat 3 ; author=a,b'),
                         [('category', 'cat1'), ('category', 'cat2'), ('category', 'cat 3'),
                          ('author', 'a'), ('author', 'b')])

    def test_an_escaped_comma_is_a_literal_comma(self):
        self.assertEqual(rt.parse_mute_rules(r'title=*smith\, john*,other'), [('title', '*smith, john*'), ('title', 'other')])

    def test_empty_alternatives_are_skipped(self):
        self.assertEqual(rt.parse_mute_rules('category=a,,b,'), [('category', 'a'), ('category', 'b')])

    def test_comma_separated_categories_mute_any_of_them(self):
        self.assertEqual(self.muted('category=sports,plants'), ['Match report', 'Garden tips'])
        self.assertEqual(self.muted('category=nothing,plants'), ['Garden tips'])

    def test_bad_pieces_are_ignored(self):
        self.assertEqual(rt.parse_mute_rules('nonsense; =x; field=; ;category=ok'), [('category', 'ok')])
        self.assertEqual(rt.parse_mute_rules(''), [])

    def test_a_category_rule_matches_one_of_the_items_several_categories(self):
        self.assertEqual(self.muted('category=sports'), ['Match report'])

    def test_the_value_is_an_exact_match_unless_wildcards_are_used(self):
        self.assertEqual(self.muted('category=spor'), [])
        self.assertEqual(self.muted('category=*sports*'), ['Match report', 'Winter sports preview'])
        self.assertEqual(self.muted('category=sports*'), ['Match report'])   # 'sports archive'
        self.assertEqual(self.muted('category=plant?'), ['Garden tips'])

    def test_case_and_accents_are_ignored_on_both_sides(self):
        self.assertEqual(self.muted('category=SPÓRTS'), ['Match report'])
        self.assertEqual(self.muted('author=jose muller'), ['Match report'])

    def test_aliases_tag_and_tags_read_the_same_values(self):
        self.assertEqual(self.muted('tag=plants'), ['Garden tips'])
        self.assertEqual(self.muted('tags=plants'), ['Garden tips'])

    def test_any_rule_may_match(self):
        self.assertEqual(self.muted('category=plants; author=jose muller'), ['Match report', 'Garden tips'])

    def test_title_and_other_entry_fields_work_too(self):
        self.assertEqual(self.muted('title=*garden*'), ['Garden tips'])
        self.assertEqual(self.muted('link=https://s.example/2'), ['Winter sports preview'])

    def test_unknown_field_matches_nothing(self):
        self.assertEqual(self.muted('colour=red'), [])

    def test_rules_come_from_the_fourth_feed_field(self):
        self.conf('[feeds]\nhttps://a.example/feed|A|5|category=sports; author=x\nhttps://b.example/feed\n'
                  'https://c.example/feed|C||category=plants\n')
        self.assertEqual(rt.load_feed_mute_rules(), {
            'https://a.example/feed': [('category', 'sports'), ('author', 'x')],
            'https://c.example/feed': [('category', 'plants')]})
        self.assertEqual(len(rt.load_feeds()), 3)                       # the fourth field doesn't disturb the others
        self.assertEqual(rt.load_feeds()[0], ('https://a.example/feed', 'A', 300))

    def test_check_feeds_hides_matching_items_only_for_that_feed(self):
        self.conf('[feeds]\nhttps://a.example/feed|||category=sports\nhttps://b.example/feed\n')

        class Stub:
            check_feeds = rt.RssTray.check_feeds

            def __init__(self):
                self.online = True
                self.lock = rt.threading.Lock()
                self.state = {'seen': {}, 'unread': [], 'last_checked': {}}
                self.on_new_items = mock.Mock()
                self.note_request_failure = mock.Mock()
        stub = Stub()
        real_parse = rt.feedparser.parse   # grabbed before it is patched
        # each feed's items need their own links, or the app (rightly) sees them as the same articles
        with mock.patch.object(rt.feedparser, 'parse',
                               side_effect=lambda url: real_parse(WORDPRESS_FEED.replace('s.example', url.split('/')[2]))), \
                mock.patch.object(rt, 'entry_age_seconds', return_value=0), \
                mock.patch.object(rt, 'save_state'), mock.patch.object(rt.GLib, 'idle_add'):
            stub.check_feeds(force=True)
        by_feed = {}
        for item in stub.state['unread']:
            by_feed.setdefault(item['feed_url'], []).append(item['title'])
        self.assertEqual(sorted(by_feed['https://a.example/feed']), ['Garden tips', 'Winter sports preview'])
        self.assertEqual(len(by_feed['https://b.example/feed']), 3)       # the other feed is not affected
        self.assertEqual(len(stub.state['seen']), 6)                       # muted items still count as seen

    def test_global_phrase_mute_still_applies_alongside(self):
        self.conf('[feeds]\nhttps://a.example/feed|||category=plants\n[mute]\nwinter\n')

        class Stub:
            check_feeds = rt.RssTray.check_feeds

            def __init__(self):
                self.online = True
                self.lock = rt.threading.Lock()
                self.state = {'seen': {}, 'unread': [], 'last_checked': {}}
                self.on_new_items = mock.Mock()
                self.note_request_failure = mock.Mock()
        stub = Stub()
        with mock.patch.object(rt.feedparser, 'parse', return_value=rt.feedparser.parse(WORDPRESS_FEED)), \
                mock.patch.object(rt, 'entry_age_seconds', return_value=0), \
                mock.patch.object(rt, 'save_state'), mock.patch.object(rt.GLib, 'idle_add'):
            stub.check_feeds(force=True)
        self.assertEqual([i['title'] for i in stub.state['unread']], ['Match report'])


class TestBreakingHighlight(TmpConfigCase):
    def test_default_word_is_breaking(self):
        self.assertEqual(rt.load_highlight_words(), ('breaking',))

    def test_words_from_config_one_per_line_or_pipe_separated(self):
        self.conf('[highlight]\nBreaking|URGENT\nalerta Ro\u0219ie\n')
        self.assertEqual(rt.load_highlight_words(), ('breaking', 'urgent', 'alerta rosie'))

    def test_whole_words_only_ignoring_case_and_accents(self):
        words = ('breaking',)
        for title in ('Breaking: markets fall', 'BREAKING NEWS', 'Live - breaking!', 'Update (Breaking)', 'a breaking story'):
            self.assertTrue(rt.title_is_highlighted(title, words), title)
        for title in ('Breakingly slow', 'Outbreaking', 'Nothing special', ''):
            self.assertFalse(rt.title_is_highlighted(title, words), title)

    def test_phrases_work_and_accents_fold(self):
        self.assertTrue(rt.title_is_highlighted('Ultimă oră: incendiu', ('ultima ora',)))
        self.assertFalse(rt.title_is_highlighted('Ultima orasului', ('ultima ora',)))


class TestMiddleClickFeedTitle(unittest.TestCase):
    class Stub:
        on_listbox_button_press = rt.RssTray.on_listbox_button_press

        def __init__(self, online=True, links=None):
            self.online = online
            self.lock = rt.threading.Lock()
            self.state = {'feed_links': links} if links is not None else {}
            self.popup = mock.MagicMock()

    def press(self, stub, row, button=2):
        listbox = mock.Mock()
        listbox.get_row_at_y.return_value = row
        with mock.patch.object(rt.webbrowser, 'open') as opened:
            handled = stub.on_listbox_button_press(listbox, mock.Mock(button=button, y=5.0))
        return handled, opened

    def header(self, url='https://a.example/feed.xml'):
        return mock.Mock(spec=['header_feed_url'], header_feed_url=url)

    def test_middle_click_opens_the_site_the_feed_declared(self):
        stub = self.Stub(links={'https://a.example/feed.xml': 'https://www.a.example/'})
        handled, opened = self.press(stub, self.header())
        self.assertTrue(handled)
        opened.assert_called_once_with('https://www.a.example/')

    def test_without_a_declared_link_it_opens_the_feed_origin(self):
        handled, opened = self.press(self.Stub(), self.header('https://news.example/rss/all.xml'))
        opened.assert_called_once_with('https://news.example/')

    def test_offline_it_does_nothing_but_still_swallows_the_click(self):
        handled, opened = self.press(self.Stub(online=False), self.header())
        self.assertTrue(handled)
        opened.assert_not_called()

    def test_middle_click_elsewhere_is_ignored(self):
        news_row = mock.Mock(spec=['entry_id', 'link'], entry_id='1', link='l')
        handled, opened = self.press(self.Stub(), news_row)
        self.assertFalse(handled)
        opened.assert_not_called()
        handled, opened = self.press(self.Stub(), None)
        self.assertFalse(handled)

    def test_right_click_behaviour_is_unchanged(self):
        stub = self.Stub()
        stub.on_mark_read_clicked = mock.Mock()
        news_row = mock.Mock(spec=['entry_id', 'link'], entry_id='1', link='l')
        handled, _ = self.press(stub, news_row, button=3)
        self.assertTrue(handled)
        stub.on_mark_read_clicked.assert_called_once_with(None, '1')

    def test_source_url_helper(self):
        self.assertEqual(rt.feed_source_url('https://x.example/a/feed', {'https://x.example/a/feed': 'http://x.example/home'}),
                         'http://x.example/home')
        self.assertEqual(rt.feed_source_url('https://x.example/a/feed', {'https://x.example/a/feed': 'javascript:alert(1)'}),
                         'https://x.example/')
        self.assertEqual(rt.feed_source_url('https://x.example:8080/f?x=1', None), 'https://x.example:8080/')
        self.assertEqual(rt.feed_source_url('not a url', None), 'not a url')

    def test_check_feeds_remembers_each_feeds_website(self):
        class CheckStub:
            check_feeds = rt.RssTray.check_feeds

            def __init__(self):
                self.online = True
                self.lock = rt.threading.Lock()
                self.state = {'seen': {}, 'unread': [], 'last_checked': {}}
                self.on_new_items = mock.Mock()
                self.note_request_failure = mock.Mock()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        config = os.path.join(d, 'config.conf')
        with open(config, 'w') as f:
            f.write('[feeds]\nhttps://a.example/feed.xml\n')
        parsed = mock.Mock(bozo=False, entries=[])
        parsed.feed = {'link': 'https://www.a.example/'}
        stub = CheckStub()
        with mock.patch.object(rt, 'CONFIG_FILE', config), mock.patch.object(rt.feedparser, 'parse', return_value=parsed), \
                mock.patch.object(rt, 'save_state'):
            stub.check_feeds(force=True)
        self.assertEqual(stub.state['feed_links'], {'https://a.example/feed.xml': 'https://www.a.example/'})


class TestFeedTermsAndMenuHelpers(TmpConfigCase):
    def entries(self):
        return rt.feedparser.parse(WORDPRESS_FEED).entries

    def test_tag_terms_and_author(self):
        first = self.entries()[0]
        self.assertEqual(rt.entry_tag_terms(first), ['News', 'winter sports', 'sports', 'sports archive'])
        self.assertEqual(rt.entry_author_name(first), 'José Müller')
        self.assertEqual(rt.entry_tag_terms({'tags': [{'term': 'A'}, {'term': 'a'}, 'B', {'term': ' '}, None]}), ['A', 'B'])
        self.assertEqual(rt.entry_author_name({}), '')

    def test_counts_are_most_common_first_and_capped(self):
        counts = rt.count_feed_terms(self.entries())
        self.assertEqual(counts['category'][0], ['News', 3])
        self.assertEqual(counts['category'][1], ['winter sports', 2])
        self.assertEqual(counts['author'], [['Other Author', 2], ['José Müller', 1]])
        many = [{'tags': [{'term': f't{i}'}]} for i in range(rt.FEED_TERMS_LIMIT + 10)]
        self.assertEqual(len(rt.count_feed_terms(many)['category']), rt.FEED_TERMS_LIMIT)

    def test_item_detail_text(self):
        self.assertEqual(rt.item_detail_text({'tags': ['a', 'b'], 'author': 'X'}), 'Categories: a, b\nAuthor: X')
        self.assertEqual(rt.item_detail_text({'author': 'X'}), 'Author: X')
        self.assertEqual(rt.item_detail_text({'title': 'old item without details'}), '')

    def test_escaped_values_match_exactly_what_they_came_from(self):
        for value in ('R&D', 'a,b', 'wild*card?', 'x [y] z', 'a;b|c', 'Café', 'sports'):
            rules = rt.parse_mute_rules(f'category={rt.escape_mute_value(value)}')
            self.assertEqual(len(rules), 1, value)
            self.assertTrue(rt.entry_matches_mute_rules({'tags': [value]}, rules), value)
            self.assertFalse(rt.entry_matches_mute_rules({'tags': [value + ' more']}, rules), value)
        self.assertFalse(rt.entry_matches_mute_rules({'tags': ['wildXcardY']}, rt.parse_mute_rules('category=' + rt.escape_mute_value('wild*card?'))))

    def test_add_rule_to_a_feed_line_without_a_fourth_field(self):
        self.conf('[feeds]\n# my feeds\nhttps://a.example/feed\nhttps://b.example/feed|B|5\n')
        self.assertTrue(rt.add_feed_mute_rule('https://a.example/feed', 'category', 'sports'))
        self.assertTrue(rt.add_feed_mute_rule('https://b.example/feed', 'author', 'José Müller'))
        with open(rt.CONFIG_FILE) as f:
            text = f.read()
        self.assertIn('# my feeds', text)
        self.assertIn('https://a.example/feed|||category=sports', text)
        self.assertIn('https://b.example/feed|B|5|author=José Müller', text)
        self.assertEqual(rt.load_feed_mute_rules()['https://a.example/feed'], [('category', 'sports')])

    def test_add_rule_extends_existing_rules_and_skips_duplicates(self):
        self.conf('[feeds]\nhttps://a.example/feed|A|10|category=news\n')
        self.assertTrue(rt.add_feed_mute_rule('https://a.example/feed', 'category', 'sports'))
        self.assertFalse(rt.add_feed_mute_rule('https://a.example/feed', 'category', 'Sports'))   # same rule
        self.assertEqual(rt.load_feed_mute_rules()['https://a.example/feed'], [('category', 'news'), ('category', 'sports')])

    def test_a_second_value_for_the_same_field_joins_the_existing_rule(self):
        self.conf('[feeds]\nhttps://a.example/feed|A\n')
        rt.add_feed_mute_rule('https://a.example/feed', 'category', 'cat1')
        rt.add_feed_mute_rule('https://a.example/feed', 'category', 'cat2')
        rt.add_feed_mute_rule('https://a.example/feed', 'category', 'cat3')
        with open(rt.CONFIG_FILE) as f:
            self.assertIn('https://a.example/feed|A||category=cat1, cat2, cat3\n', f.read())
        self.assertEqual(rt.load_feed_mute_rules()['https://a.example/feed'],
                         [('category', 'cat1'), ('category', 'cat2'), ('category', 'cat3')])

    def test_another_field_gets_its_own_rule_and_each_field_merges_separately(self):
        self.conf('[feeds]\nhttps://a.example/feed|A||category=cat1\n')
        rt.add_feed_mute_rule('https://a.example/feed', 'author', 'Ann')
        rt.add_feed_mute_rule('https://a.example/feed', 'category', 'cat2')
        rt.add_feed_mute_rule('https://a.example/feed', 'author', 'Bob')
        with open(rt.CONFIG_FILE) as f:
            self.assertIn('|category=cat1, cat2; author=Ann, Bob\n', f.read())

    def test_hand_written_rules_are_extended_in_place_and_left_alone_otherwise(self):
        self.conf('[feeds]\nhttps://a.example/feed|A||author=Ann;category=news;title=*x*\n')
        rt.add_feed_mute_rule('https://a.example/feed', 'category', 'sports')
        with open(rt.CONFIG_FILE) as f:
            self.assertIn('|author=Ann;category=news, sports;title=*x*\n', f.read())

    def test_the_same_value_is_not_added_twice_in_a_merged_rule(self):
        self.conf('[feeds]\nhttps://a.example/feed|A||category=cat1, cat2\n')
        self.assertFalse(rt.add_feed_mute_rule('https://a.example/feed', 'category', 'CAT2'))

    def test_a_value_with_a_comma_stays_one_alternative_after_merging(self):
        self.conf('[feeds]\nhttps://a.example/feed|A||category=cat1\n')
        rt.add_feed_mute_rule('https://a.example/feed', 'category', 'Smith, John')
        rules = rt.load_feed_mute_rules()['https://a.example/feed']
        self.assertEqual(rules, [('category', 'cat1'), ('category', 'smith, john')])

    def test_unread_terms_only_count_what_is_still_unread_for_that_feed(self):
        unread = [{'feed_url': 'u1', 'tags': ['a', 'b'], 'author': 'X'},
                  {'feed_url': 'u1', 'tags': ['a'], 'author': 'Y'},
                  {'feed_url': 'u2', 'tags': ['z'], 'author': 'Z'},
                  {'feed_url': 'u1', 'title': 'old item without details'}]
        counts = rt.unread_feed_terms(unread, 'u1')
        self.assertEqual(counts['category'], [['a', 2], ['b', 1]])
        self.assertEqual(counts['author'], [['X', 1], ['Y', 1]])
        del unread[0]    # one item dismissed: its categories no longer count
        counts = rt.unread_feed_terms(unread, 'u1')
        self.assertEqual(counts['category'], [['a', 1]])
        self.assertEqual(counts['author'], [['Y', 1]])
        self.assertEqual(rt.unread_feed_terms([], 'u1'), {'category': [], 'author': []})

    def test_add_rule_for_an_unknown_feed_changes_nothing(self):
        self.conf('[feeds]\nhttps://a.example/feed\n')
        before = open(rt.CONFIG_FILE).read()
        self.assertFalse(rt.add_feed_mute_rule('https://zzz.example/feed', 'category', 'x'))
        self.assertEqual(open(rt.CONFIG_FILE).read(), before)

    def test_only_the_matching_feed_line_is_touched_and_other_sections_survive(self):
        self.conf('[feeds]\nhttps://a.example/feed\nhttps://a.example/feed2\n[twitch]\nchan\n')
        rt.add_feed_mute_rule('https://a.example/feed', 'category', 'x')
        self.assertEqual(sorted(rt.load_feed_mute_rules()), ['https://a.example/feed'])
        self.assertEqual(rt.load_twitch_channels(), ['chan'])
        self.assertEqual(len(rt.load_feeds()), 2)

    def test_check_feeds_stores_item_details(self):
        self.conf('[feeds]\nhttps://a.example/feed\n')

        class Stub:
            check_feeds = rt.RssTray.check_feeds

            def __init__(self):
                self.online = True
                self.lock = rt.threading.Lock()
                self.state = {'seen': {}, 'unread': [], 'last_checked': {}}
                self.on_new_items = mock.Mock()
                self.note_request_failure = mock.Mock()
        stub = Stub()
        with mock.patch.object(rt.feedparser, 'parse', return_value=rt.feedparser.parse(WORDPRESS_FEED)), \
                mock.patch.object(rt, 'entry_age_seconds', return_value=0), \
                mock.patch.object(rt, 'save_state'), mock.patch.object(rt.GLib, 'idle_add'):
            stub.check_feeds(force=True)
        first = next(i for i in stub.state['unread'] if i['title'] == 'Match report')
        self.assertEqual(first['tags'], ['News', 'winter sports', 'sports', 'sports archive'])
        self.assertEqual(first['author'], 'José Müller')
        self.assertNotIn('feed_terms', stub.state)   # the menu counts the unread items itself


if __name__ == '__main__':
    unittest.main()
