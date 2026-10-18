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

    def test_accents_in_the_title_are_ignored(self):
        self.assertTrue(rt.is_muted('\u0218tiri sportive azi', ['stiri sportive']))

    def test_accents_in_the_phrase_are_ignored(self):
        self.assertTrue(rt.is_muted('Stiri sportive azi', ['\u0219tiri sportive']))

    def test_comma_below_and_cedilla_forms_match_each_other(self):
        # s/t with comma below (U+0219, U+021B) vs the older cedilla forms (U+015F, U+0163)
        self.assertTrue(rt.is_muted('\u015etiri \u0163\u0103ri', ['\u0219tiri \u021b\u0103ri']))
        self.assertTrue(rt.is_muted('\u0218tiri \u021b\u0103ri', ['\u015ftiri \u0163\u0103ri']))

    def test_composed_and_decomposed_accents_match(self):
        self.assertTrue(rt.is_muted('Caf\u00e9 news', ['cafe\u0301']))

    def test_accents_do_not_cause_false_matches(self):
        self.assertFalse(rt.is_muted('Stiri sportive', ['stiri politice']))
        self.assertFalse(rt.is_muted('Sport', ['\u0219tiri']))

    def test_a_phrase_with_nothing_left_after_folding_never_mutes(self):
        self.assertFalse(rt.is_muted('Hello', ['\u0301']))
        self.assertFalse(rt.is_muted('Hello', ['   ']))

    def test_loader_folds_accents_and_case(self):
        self.conf('[mute]\n\u0218tiri Sportive | CAF\u00c9\n\u0301\n')
        self.assertEqual(rt.load_mute_filters(), ['stiri sportive', 'cafe'])

    def test_configured_phrases_mute_across_accent_forms(self):
        self.conf('[mute]\n\u0219tiri sportive\n')
        phrases = rt.load_mute_filters()
        self.assertTrue(rt.is_muted('STIRI SPORTIVE: derby', phrases))
        self.assertTrue(rt.is_muted('\u0218tiri sportive: derby', phrases))
        self.assertFalse(rt.is_muted('Stiri politice', phrases))


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

    def test_read_later_rules_come_from_the_fifth_feed_field(self):
        self.conf('[feeds]\nhttps://a.example/feed|A|5|category=sports|author=x, y\nhttps://b.example/feed|B\n'
                  'https://c.example/feed||||category=plants\nhttps://d.example/feed|D|5|category=news\n')
        self.assertEqual(rt.load_feed_readlater_rules(), {
            'https://a.example/feed': [('author', 'x'), ('author', 'y')],
            'https://c.example/feed': [('category', 'plants')]})
        self.assertEqual(rt.load_feed_mute_rules()['https://a.example/feed'], [('category', 'sports')])  # the columns stay apart
        self.assertEqual(len(rt.load_feeds()), 4)

    class LaterStub:
        check_feeds = rt.RssTray.check_feeds

        def __init__(self):
            self.online = True
            self.lock = rt.threading.Lock()
            self.state = {'seen': {}, 'unread': [], 'last_checked': {}}
            self.on_new_items = mock.Mock()
            self.note_request_failure = mock.Mock()
            self._read_later_changed = mock.Mock()

    def check_with(self, conf, stub=None):
        self.conf(conf)
        stub = stub or self.LaterStub()
        with mock.patch.object(rt.feedparser, 'parse', return_value=rt.feedparser.parse(WORDPRESS_FEED)), \
                mock.patch.object(rt, 'entry_age_seconds', return_value=0), \
                mock.patch.object(rt.time_module, 'time', return_value=5000.0), \
                mock.patch.object(rt, 'save_state'), mock.patch.object(rt.GLib, 'idle_add') as idle:
            stub.check_feeds(force=True)
        return stub, idle

    def test_items_matching_a_read_later_rule_go_to_the_read_later_list_not_unread(self):
        stub, _ = self.check_with('[feeds]\nhttps://a.example/feed||||category=plants\n')
        saved = stub.state['read_later']
        self.assertEqual([e['title'] for e in saved], ['Garden tips'])
        self.assertEqual(saved[0]['saved'], 5000.0)
        self.assertEqual(saved[0]['feed_url'], 'https://a.example/feed')
        self.assertIn('plants', [t.lower() for t in saved[0]['tags']])
        self.assertNotIn('Garden tips', [e['title'] for e in stub.state['unread']])
        self.assertEqual(len(stub.state['unread']), 2)
        self.assertEqual(len(stub.state['seen']), 3)  # they count as seen like any other item

    def test_such_items_raise_no_new_item_alert_only_a_quiet_refresh(self):
        stub, idle = self.check_with('[feeds]\nhttps://a.example/feed||||category=plants\n')
        idle.assert_any_call(stub.on_new_items)  # the two other items still alert
        idle.assert_any_call(stub._read_later_changed)
        stub2, idle2 = self.check_with('[feeds]\nhttps://a.example/feed||||category=*\n')
        self.assertEqual(stub2.state['unread'], [])
        idle2.assert_called_once_with(stub2._read_later_changed)  # all of them went to Read later: nothing to alert about
        stub2.on_new_items.assert_not_called()

    def test_a_mute_rule_wins_over_a_read_later_rule(self):
        stub, _ = self.check_with('[feeds]\nhttps://a.example/feed|||category=plants|category=plants\n')
        self.assertEqual(stub.state.get('read_later', []), [])
        self.assertNotIn('Garden tips', [e['title'] for e in stub.state['unread']])

    def test_too_old_items_are_not_filed_under_read_later(self):
        self.conf('[feeds]\nhttps://a.example/feed||||category=plants\n')
        stub = self.LaterStub()
        with mock.patch.object(rt.feedparser, 'parse', return_value=rt.feedparser.parse(WORDPRESS_FEED)), \
                mock.patch.object(rt, 'entry_age_seconds', return_value=3 * 24 * 3600), \
                mock.patch.object(rt, 'save_state'), mock.patch.object(rt.GLib, 'idle_add'):
            stub.check_feeds(force=True)
        self.assertEqual(stub.state.get('read_later', []), [])

    def test_an_item_already_saved_is_not_added_twice_and_new_ones_go_in_front(self):
        stub = self.LaterStub()
        stub.state['read_later'] = [{'id': 'older', 'title': 'Old save', 'link': 'l', 'saved': 1.0}]
        stub, _ = self.check_with('[feeds]\nhttps://a.example/feed||||category=plants\n', stub)
        self.assertEqual([e['title'] for e in stub.state['read_later']], ['Garden tips', 'Old save'])

    def test_rules_of_one_feed_do_not_touch_another(self):
        self.conf('[feeds]\nhttps://a.example/feed\nhttps://b.example/feed||||category=plants\n')
        stub = self.LaterStub()
        real_parse = rt.feedparser.parse
        with mock.patch.object(rt.feedparser, 'parse',
                               side_effect=lambda url: real_parse(WORDPRESS_FEED.replace('s.example', url.split('/')[2]))), \
                mock.patch.object(rt, 'entry_age_seconds', return_value=0), \
                mock.patch.object(rt, 'save_state'), mock.patch.object(rt.GLib, 'idle_add'):
            stub.check_feeds(force=True)
        self.assertEqual({e['feed_url'] for e in stub.state['read_later']}, {'https://b.example/feed'})
        self.assertEqual(len([e for e in stub.state['unread'] if e['feed_url'] == 'https://a.example/feed']), 3)

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

    WORDS = ('breaking', 'ultima ora')

    def test_item_highlighted_by_its_title(self):
        self.assertTrue(rt.item_is_highlighted({'title': 'Breaking: flood', 'tags': ['Weather']}, self.WORDS))

    def test_item_highlighted_by_a_category_alone(self):
        self.assertTrue(rt.item_is_highlighted({'title': 'Flood in the valley', 'tags': ['Weather', 'Breaking News']}, self.WORDS))
        self.assertTrue(rt.item_is_highlighted({'title': 'Flood', 'tags': ['BREAKING']}, self.WORDS))

    def test_category_matching_is_whole_word_ignoring_case_and_accents(self):
        self.assertTrue(rt.item_is_highlighted({'title': 'x', 'tags': ['Ultim\u0103 Or\u0103']}, self.WORDS))
        self.assertFalse(rt.item_is_highlighted({'title': 'x', 'tags': ['Outbreaking', 'Breakingly']}, self.WORDS))

    def test_item_without_a_match_or_without_tags_is_not_highlighted(self):
        self.assertFalse(rt.item_is_highlighted({'title': 'Quiet day', 'tags': ['Sport']}, self.WORDS))
        self.assertFalse(rt.item_is_highlighted({'title': 'Quiet day'}, self.WORDS))
        self.assertFalse(rt.item_is_highlighted({'title': 'Quiet day', 'tags': None}, self.WORDS))

    def test_non_text_tags_are_ignored_and_no_words_highlights_nothing(self):
        self.assertFalse(rt.item_is_highlighted({'title': 'x', 'tags': [None, 5, {}]}, self.WORDS))
        self.assertFalse(rt.item_is_highlighted({'title': 'Breaking', 'tags': ['Breaking']}, ()))


class TestHeaderClicks(unittest.TestCase):
    class Stub:
        on_listbox_button_press = rt.RssTray.on_listbox_button_press

        def __init__(self):
            self.popup = mock.MagicMock()
            self._show_feed_menu = mock.Mock()
            self.on_mark_read_clicked = mock.Mock()
            self.remove_read_later = mock.Mock()

    def press(self, stub, row, button):
        listbox = mock.Mock()
        listbox.get_row_at_y.return_value = row
        with mock.patch.object(rt.webbrowser, 'open') as opened, mock.patch.object(rt.webbrowser, 'open_new_tab') as tab:
            handled = stub.on_listbox_button_press(listbox, mock.Mock(button=button, y=5.0))
        return handled, opened, tab

    def header(self, url='https://a.example/feed.xml'):
        return mock.Mock(spec=['header_feed_url'], header_feed_url=url)

    def test_middle_click_on_a_feed_title_no_longer_opens_anything(self):
        stub = self.Stub()
        handled, opened, tab = self.press(stub, self.header(), button=2)
        self.assertFalse(handled)  # not swallowed: it is simply not a gesture any more
        opened.assert_not_called()
        tab.assert_not_called()
        stub._show_feed_menu.assert_not_called()

    def test_right_click_on_a_feed_title_opens_the_feed_menu(self):
        stub = self.Stub()
        row = self.header()
        handled, _, _ = self.press(stub, row, button=3)
        self.assertTrue(handled)
        stub._show_feed_menu.assert_called_once()
        self.assertIs(stub._show_feed_menu.call_args[0][0], row)

    def test_right_click_on_a_news_item_still_marks_it_read(self):
        stub = self.Stub()
        row = mock.Mock(spec=['entry_id', 'link'], entry_id='1', link='l')
        handled, _, _ = self.press(stub, row, button=3)
        self.assertTrue(handled)
        stub.on_mark_read_clicked.assert_called_once_with(None, '1')

    def test_right_click_on_a_saved_item_takes_it_off_the_list_without_opening_it(self):
        stub = self.Stub()
        row = mock.Mock(spec=['readlater_id', 'link'], readlater_id='7', link='l')
        handled, opened, tab = self.press(stub, row, button=3)
        self.assertTrue(handled)
        stub.remove_read_later.assert_called_once_with('7')
        stub.on_mark_read_clicked.assert_not_called()
        opened.assert_not_called()


class TestFeedRuleWriting(TmpConfigCase):
    URL = 'https://a.example/feed'

    def feeds_lines(self):
        with open(rt.CONFIG_FILE) as f:
            text = f.read()
        return [l for l in text.splitlines() if l.startswith('https://')]

    def test_a_mute_rule_lands_in_the_fourth_field(self):
        self.conf(f'[feeds]\n{self.URL}\n')
        self.assertTrue(rt.add_feed_mute_rule(self.URL, 'category', 'sports'))
        self.assertEqual(self.feeds_lines(), [f'{self.URL}|||category=sports'])

    def test_a_read_later_rule_lands_in_the_fifth_field(self):
        self.conf(f'[feeds]\n{self.URL}\n')
        self.assertTrue(rt.add_feed_readlater_rule(self.URL, 'author', 'Ana'))
        self.assertEqual(self.feeds_lines(), [f'{self.URL}||||author=Ana'])
        self.assertEqual(len(rt.load_feeds()), 1)
        self.assertEqual(rt.load_feed_readlater_rules(), {self.URL: [('author', 'ana')]})  # patterns are stored folded

    def test_the_other_fields_are_kept_as_they_were(self):
        self.conf(f'[feeds]\n{self.URL}|My Blog|5|category=sports|author=Bob\n')
        rt.add_feed_readlater_rule(self.URL, 'category', 'long reads')
        rt.add_feed_mute_rule(self.URL, 'category', 'ads')
        self.assertEqual(self.feeds_lines(), [f'{self.URL}|My Blog|5|category=sports, ads|author=Bob; category=long reads'])

    def test_a_value_joins_a_rule_for_the_same_field(self):
        self.conf(f'[feeds]\n{self.URL}||||category=a\n')
        self.assertTrue(rt.add_feed_readlater_rule(self.URL, 'category', 'b'))
        self.assertEqual(self.feeds_lines(), [f'{self.URL}||||category=a, b'])

    def test_a_value_that_is_already_there_is_not_added_again(self):
        self.conf(f'[feeds]\n{self.URL}||||category=a, b\n')
        self.assertFalse(rt.add_feed_readlater_rule(self.URL, 'category', 'B'))
        self.assertEqual(self.feeds_lines(), [f'{self.URL}||||category=a, b'])

    def test_a_feed_that_is_not_in_the_config_changes_nothing(self):
        self.conf(f'[feeds]\n{self.URL}\n')
        self.assertFalse(rt.add_feed_readlater_rule('https://other.example/feed', 'category', 'x'))
        self.assertEqual(self.feeds_lines(), [self.URL])

    def test_names_with_commas_survive_the_round_trip(self):
        self.conf(f'[feeds]\n{self.URL}\n')
        rt.add_feed_readlater_rule(self.URL, 'author', 'Smith, John')
        rules = rt.load_feed_readlater_rules()[self.URL]
        self.assertTrue(rt.entry_matches_mute_rules({'author': 'Smith, John'}, rules))
        self.assertFalse(rt.entry_matches_mute_rules({'author': 'John'}, rules))

    def test_other_feeds_sections_and_comments_are_left_alone(self):
        self.conf(f'# my feeds\n[feeds]\n{self.URL}\nhttps://b.example/feed|B\n\n[mute]\nspam\n')
        rt.add_feed_readlater_rule(self.URL, 'category', 'x')
        with open(rt.CONFIG_FILE) as f:
            text = f.read()
        self.assertIn('# my feeds', text)
        self.assertIn('https://b.example/feed|B\n', text)
        self.assertIn('[mute]\nspam', text)


if __name__ == '__main__':
    unittest.main()
