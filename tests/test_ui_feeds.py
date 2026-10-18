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


class TestSaveForLater(unittest.TestCase):
    class Stub:
        save_for_later = rt.RssTray.save_for_later
        on_save_clicked = rt.RssTray.on_save_clicked
        remove_read_later = rt.RssTray.remove_read_later
        on_read_later_remove_clicked = rt.RssTray.on_read_later_remove_clicked
        open_read_later = rt.RssTray.open_read_later

        def __init__(self, read_later=()):
            self.lock = rt.threading.Lock()
            self.state = {'unread': [
                {'id': '1', 'title': 'one', 'link': 'l1', 'feed_url': 'u'},
                {'id': '2', 'title': 'two', 'link': 'l2', 'feed_url': 'u', 'tags': ['x'], 'author': 'A'},
                {'id': '3', 'title': 'three', 'link': 'l3', 'feed_url': 'u'}],
                'read_later': list(read_later)}
            self.update_icon = mock.Mock()
            self.refresh_list = mock.Mock()

    def save(self, stub, ids, now=100.0):
        with mock.patch.object(rt, 'save_state') as save, mock.patch.object(rt.time_module, 'time', return_value=now):
            stub.save_for_later(ids)
        return save

    def test_a_saved_item_leaves_unread_and_joins_read_later_with_its_details(self):
        stub = self.Stub()
        save = self.save(stub, ['2'])
        self.assertEqual([e['id'] for e in stub.state['unread']], ['1', '3'])
        saved = stub.state['read_later']
        self.assertEqual([e['id'] for e in saved], ['2'])
        self.assertEqual((saved[0]['tags'], saved[0]['author'], saved[0]['saved']), (['x'], 'A', 100.0))
        save.assert_called_once_with(stub.state)
        stub.update_icon.assert_called_once()
        stub.refresh_list.assert_called_once()

    def test_the_icon_in_front_of_an_item_saves_that_item(self):
        stub = self.Stub()
        with mock.patch.object(rt, 'save_state'):
            stub.on_save_clicked(None, '3')
        self.assertEqual([e['id'] for e in stub.state['read_later']], ['3'])

    def test_several_items_keep_their_order_and_go_in_front_of_older_saves(self):
        stub = self.Stub(read_later=[{'id': '0', 'title': 'old', 'link': 'l0', 'saved': 1.0}])
        self.save(stub, ['3', '1'])
        self.assertEqual([e['id'] for e in stub.state['read_later']], ['1', '3', '0'])  # unread order, newest first

    def test_an_item_that_is_already_saved_is_only_taken_off_unread(self):
        stub = self.Stub(read_later=[{'id': '1', 'title': 'one', 'link': 'l1', 'saved': 1.0}])
        self.save(stub, ['1'])
        self.assertEqual([e['id'] for e in stub.state['read_later']], ['1'])
        self.assertEqual(stub.state['read_later'][0]['saved'], 1.0)
        self.assertEqual([e['id'] for e in stub.state['unread']], ['2', '3'])

    def test_unknown_ids_change_nothing(self):
        stub = self.Stub()
        self.save(stub, ['nope'])
        self.assertEqual(len(stub.state['unread']), 3)
        self.assertEqual(stub.state['read_later'], [])

    def test_removing_a_saved_item_takes_it_off_the_list_only(self):
        stub = self.Stub(read_later=[{'id': 'a', 'title': 'A', 'link': 'la'}, {'id': 'b', 'title': 'B', 'link': 'lb'}])
        with mock.patch.object(rt, 'save_state') as save:
            stub.on_read_later_remove_clicked(None, 'a')
        self.assertEqual([e['id'] for e in stub.state['read_later']], ['b'])
        self.assertEqual(len(stub.state['unread']), 3)
        save.assert_called_once()
        stub.refresh_list.assert_called_once()

    def test_opening_a_saved_item_removes_it_and_opens_its_link(self):
        stub = self.Stub(read_later=[{'id': 'a', 'title': 'A', 'link': 'la'}])
        with mock.patch.object(rt, 'save_state'), mock.patch.object(rt.webbrowser, 'open') as opened:
            stub.open_read_later('a', 'la')
        self.assertEqual(stub.state['read_later'], [])
        opened.assert_called_once_with('la')


class TestOpenAllAsTabs(unittest.TestCase):
    class Stub:
        _feed_menu_open_all = rt.RssTray._feed_menu_open_all
        _open_tabs = staticmethod(rt.RssTray._open_tabs)

        def __init__(self):
            self.lock = rt.threading.Lock()
            self.state = {'unread': [
                {'id': '1', 'feed_url': 'u1', 'title': 'a', 'link': 'https://x/1'},
                {'id': '2', 'feed_url': 'u2', 'title': 'b', 'link': 'https://x/2'},
                {'id': '3', 'feed_url': 'u1', 'title': 'c', 'link': 'https://x/3'},
                {'id': '4', 'feed_url': 'u1', 'title': 'd', 'link': ''}]}
            self.update_icon = mock.Mock()
            self.refresh_list = mock.Mock()

    def open_all(self, stub, feed='u1'):
        popover = mock.Mock()
        with mock.patch.object(rt, 'save_state') as save, mock.patch.object(rt.threading, 'Thread') as thread:
            stub._feed_menu_open_all(None, popover, feed)
        return popover, save, thread

    def test_every_unread_item_of_that_feed_is_opened_in_the_background_in_order(self):
        stub = self.Stub()
        popover, _, thread = self.open_all(stub)
        popover.popdown.assert_called_once()
        thread.assert_called_once_with(target=stub._open_tabs, args=(['https://x/1', 'https://x/3'],), daemon=True)
        thread.return_value.start.assert_called_once()

    def test_opened_items_count_as_read_and_other_feeds_are_untouched(self):
        stub = self.Stub()
        _, save, _ = self.open_all(stub)
        self.assertEqual([e['id'] for e in stub.state['unread']], ['2'])  # the link-less one counts as read too
        save.assert_called_once_with(stub.state)
        stub.update_icon.assert_called_once()
        stub.refresh_list.assert_called_once()

    def test_a_feed_with_nothing_to_open_starts_no_thread(self):
        stub = self.Stub()
        stub.state['unread'] = [{'id': '4', 'feed_url': 'u1', 'title': 'd', 'link': ''}]
        _, _, thread = self.open_all(stub)
        thread.assert_not_called()

    def test_tabs_are_opened_one_after_another_with_a_short_pause(self):
        events = []
        with mock.patch.object(rt.webbrowser, 'open_new_tab', side_effect=lambda l: events.append(('tab', l))), \
                mock.patch.object(rt.time_module, 'sleep', side_effect=lambda s: events.append(('sleep', s))):
            rt.RssTray._open_tabs(['a', 'b'])
        self.assertEqual(events, [('tab', 'a'), ('sleep', rt.OPEN_TABS_DELAY_SECONDS), ('tab', 'b'), ('sleep', rt.OPEN_TABS_DELAY_SECONDS)])


class TestFeedMenuReadLater(unittest.TestCase):
    class Stub:
        _feed_menu_later = rt.RssTray._feed_menu_later
        save_for_later = mock.Mock()

        def __init__(self):
            self.lock = rt.threading.Lock()
            self.state = {'unread': [
                {'id': '1', 'feed_url': 'u1', 'title': 'a', 'tags': ['sports']},
                {'id': '2', 'feed_url': 'u1', 'title': 'b', 'tags': ['news'], 'author': 'X'},
                {'id': '3', 'feed_url': 'u2', 'title': 'c', 'tags': ['sports']},
                {'id': '4', 'feed_url': 'u1', 'title': 'no stored tags'}]}
            self.save_for_later = mock.Mock()

    def choose(self, stub, field, value, added=True):
        popover = mock.Mock()
        with mock.patch.object(rt, 'add_feed_readlater_rule', return_value=added) as add:
            stub._feed_menu_later(None, popover, 'u1', field, value)
        return popover, add

    def test_choosing_a_category_writes_the_rule_and_files_the_matching_unread_items_of_that_feed(self):
        stub = self.Stub()
        popover, add = self.choose(stub, 'category', 'sports')
        add.assert_called_once_with('u1', 'category', 'sports')
        popover.popdown.assert_called_once()
        stub.save_for_later.assert_called_once_with(['1'])  # not feed u2's item

    def test_choosing_an_author(self):
        stub = self.Stub()
        self.choose(stub, 'author', 'X')
        stub.save_for_later.assert_called_once_with(['2'])

    def test_a_rule_that_was_already_there_still_files_what_matches_now(self):
        stub = self.Stub()
        self.choose(stub, 'category', 'sports', added=False)
        stub.save_for_later.assert_called_once_with(['1'])

    def test_nothing_matching_files_nothing(self):
        stub = self.Stub()
        self.choose(stub, 'category', 'cooking')
        stub.save_for_later.assert_not_called()

    def test_matching_ignores_case_like_the_rules_do(self):
        stub = self.Stub()
        self.choose(stub, 'category', 'SPORTS')
        stub.save_for_later.assert_called_once_with(['1'])


class TestFeedMenuLayout(unittest.TestCase):
    class Stub:
        _show_feed_menu = rt.RssTray._show_feed_menu
        _feed_menu_room = rt.RssTray._feed_menu_room

        def __init__(self, online=True):
            self.online = online
            self.popup = None  # no window to measure: the menu takes its default room
            self.lock = rt.threading.Lock()
            self.state = {'unread': [
                {'id': '1', 'feed_url': 'u1', 'title': 'a', 'link': 'l1', 'tags': ['Sports'], 'author': 'Ana'},
                {'id': '2', 'feed_url': 'u1', 'title': 'b', 'link': 'l2', 'tags': ['Sports', 'News']},
                {'id': '3', 'feed_url': 'u2', 'title': 'c', 'link': 'l3', 'tags': ['Other']}],
                'feed_links': {'u1': 'https://site.example/'}}
            self.on_launcher_closed = mock.Mock()
            self._launcher_open = False
            for name in ('_feed_menu_open_all', '_feed_menu_open', '_feed_menu_mute', '_feed_menu_later'):
                setattr(self, name, mock.Mock(name=name))

    def build(self, stub):
        buttons, labels = [], []

        def make_button(label=None):
            button = mock.MagicMock()
            button.text = label
            buttons.append(button)
            return button

        def make_label(label=None):
            lab = mock.MagicMock()
            lab.text = label
            lab.set_text.side_effect = lambda text: setattr(lab, 'text', text)
            lab.set_markup.side_effect = lambda text: setattr(lab, 'text', text)
            labels.append(lab)
            return lab

        boxes = []

        def make_box(orientation=None, spacing=0):
            box = mock.MagicMock()
            box.orientation = orientation
            boxes.append(box)
            return box

        gtk = mock.MagicMock()
        gtk.Button.side_effect = make_button
        gtk.Label.side_effect = make_label
        gtk.Box.side_effect = make_box
        gtk.boxes = boxes
        row = mock.Mock(header_feed_url='u1')
        row.get_allocation.return_value = mock.Mock(x=0, height=20)
        with mock.patch.object(rt, 'Gtk', gtk), mock.patch.object(rt, 'Gdk', mock.MagicMock()):
            stub._show_feed_menu(row, mock.Mock(x=5.0))
        return buttons, labels, gtk

    def test_the_menu_starts_with_open_all_counting_that_feeds_unread_items(self):
        stub = self.Stub()
        buttons, _, _ = self.build(stub)
        first = buttons[0]
        self.assertEqual(first.text, 'Open all 2 as tabs')
        first.set_sensitive.assert_called_with(True)
        args = first.connect.call_args[0]
        self.assertEqual((args[0], args[1], args[3]), ('clicked', stub._feed_menu_open_all, 'u1'))

    def test_the_count_follows_the_unread_items_of_that_feed(self):
        stub = self.Stub()
        stub.state['unread'] = stub.state['unread'][:1]
        buttons, _, _ = self.build(stub)
        self.assertEqual(buttons[0].text, 'Open all 1 as tabs')

    def test_open_all_is_disabled_offline_or_with_nothing_to_open(self):
        buttons, _, _ = self.build(self.Stub(online=False))
        buttons[0].set_sensitive.assert_called_with(False)
        stub = self.Stub()
        stub.state['unread'] = [e for e in stub.state['unread'] if e['feed_url'] != 'u1']
        buttons, _, _ = self.build(stub)
        self.assertEqual(buttons[0].text, 'Open all 0 as tabs')
        buttons[0].set_sensitive.assert_called_with(False)

    def test_website_and_xml_stay_in_the_menu(self):
        stub = self.Stub()
        buttons, _, _ = self.build(stub)
        self.assertEqual([b.text for b in buttons[1:3]], ['Website', 'XML'])
        self.assertEqual(buttons[1].connect.call_args[0][3], 'https://site.example/')
        self.assertEqual(buttons[2].connect.call_args[0][3], 'u1')

    def test_every_category_and_author_has_a_mute_and_a_later_button(self):
        stub = self.Stub()
        buttons, labels, _ = self.build(stub)
        texts = [b.text for b in buttons[3:]]
        self.assertEqual(texts, ['Mute', 'Later'] * 3)  # Sports, News, Ana
        names = [l.text for l in labels if l.text and '(' in l.text]
        self.assertEqual(sorted(names), ['Ana (1)', 'News (1)', 'Sports (2)'])

    def test_the_buttons_call_the_right_handlers_with_the_field_and_name(self):
        stub = self.Stub()
        buttons, labels, _ = self.build(stub)
        mutes = [b for b in buttons if b.text == 'Mute']
        laters = [b for b in buttons if b.text == 'Later']
        for button, handler in [(b, stub._feed_menu_mute) for b in mutes] + [(b, stub._feed_menu_later) for b in laters]:
            args = button.connect.call_args[0]
            self.assertEqual(args[0], 'clicked')
            self.assertIs(args[1], handler)
            self.assertEqual(args[3], 'u1')
        called = {(b.connect.call_args[0][4], b.connect.call_args[0][5]) for b in laters}
        self.assertEqual(called, {('category', 'Sports'), ('category', 'News'), ('author', 'Ana')})

    def test_other_feeds_terms_are_not_offered(self):
        stub = self.Stub()
        _, labels, _ = self.build(stub)
        self.assertFalse([l for l in labels if l.text and 'Other' in l.text])

    def test_a_feed_without_categories_or_authors_says_so(self):
        stub = self.Stub()
        stub.state['unread'] = [{'id': '9', 'feed_url': 'u1', 'title': 'z', 'link': 'l'}]
        buttons, labels, _ = self.build(stub)
        self.assertEqual(len([l for l in labels if l.text == 'none among the unread items']), 2)
        self.assertEqual([b.text for b in buttons[3:]], [])

    def test_the_three_open_actions_share_one_row_to_keep_the_menu_short(self):
        stub = self.Stub()
        buttons, _, gtk = self.build(stub)
        horizontal = [b for b in gtk.boxes if b.orientation == gtk.Orientation.HORIZONTAL]
        action_row = horizontal[0]  # the first horizontal box is the row of open actions
        packed = [call.args[0] for call in action_row.pack_start.call_args_list]
        self.assertEqual(packed, buttons[:3])

    def test_menu_text_is_forced_dark_because_the_menu_sits_on_a_white_scroller(self):
        stub = self.Stub()
        _, labels, _ = self.build(stub)
        text_labels = [l for l in labels if l.text and ('(' in l.text or 'Categories' in l.text or 'Authors' in l.text)]
        self.assertTrue(text_labels)
        for label in text_labels:
            label.get_style_context.return_value.add_class.assert_called_with('menu-text')

    def test_the_popup_is_kept_open_under_the_menu(self):
        stub = self.Stub()
        _, _, gtk = self.build(stub)
        self.assertTrue(stub._launcher_open)
        gtk.Popover.new.return_value.popup.assert_called_once()


class TestFeedMenuRoom(unittest.TestCase):
    class Stub:
        _feed_menu_room = rt.RssTray._feed_menu_room

        def __init__(self, window_height=400):
            self.popup = mock.MagicMock()
            self.popup.get_allocated_height.return_value = window_height

    def row(self, top, height=30):
        row = mock.Mock()
        row.translate_coordinates.return_value = (0, top)
        row.get_allocation.return_value = mock.Mock(height=height)
        return row

    def test_a_title_near_the_top_opens_downwards_with_the_room_left_below_it(self):
        position, height = self.Stub(window_height=400)._feed_menu_room(self.row(top=40))
        self.assertEqual(position, rt.Gtk.PositionType.BOTTOM)
        self.assertEqual(height, 280)  # 400 - 70 - 30 = 300, capped at the usual maximum

    def test_little_room_below_means_a_smaller_scrolling_menu(self):
        position, height = self.Stub(window_height=250)._feed_menu_room(self.row(top=40))
        self.assertEqual(position, rt.Gtk.PositionType.BOTTOM)
        self.assertEqual(height, 250 - 70 - rt.FEED_MENU_EDGE_PX)

    def test_a_title_near_the_bottom_opens_upwards(self):
        position, height = self.Stub(window_height=400)._feed_menu_room(self.row(top=300))
        self.assertEqual(position, rt.Gtk.PositionType.TOP)
        self.assertEqual(height, 270)  # 300 - 30

    def test_the_menu_is_never_squeezed_below_a_minimum(self):
        _, height = self.Stub(window_height=100)._feed_menu_room(self.row(top=40))
        self.assertEqual(height, rt.FEED_MENU_MIN_HEIGHT_PX)

    def test_without_a_popup_or_a_position_the_default_applies(self):
        stub = self.Stub()
        stub.popup = None
        self.assertEqual(stub._feed_menu_room(self.row(top=40)), (rt.Gtk.PositionType.BOTTOM, rt.FEED_MENU_MAX_HEIGHT_PX))
        stub = self.Stub()
        row = self.row(top=40)
        row.translate_coordinates.return_value = None
        self.assertEqual(stub._feed_menu_room(row), (rt.Gtk.PositionType.BOTTOM, rt.FEED_MENU_MAX_HEIGHT_PX))

    def test_the_menu_uses_the_position_and_height_it_was_given(self):
        stub = TestFeedMenuLayout.Stub()
        stub._feed_menu_room = lambda row: ('the-side', 123)
        gtk = mock.MagicMock()
        row = mock.Mock(header_feed_url='u1')
        row.get_allocation.return_value = mock.Mock(x=0, height=20)
        with mock.patch.object(rt, 'Gtk', gtk), mock.patch.object(rt, 'Gdk', mock.MagicMock()):
            stub._show_feed_menu(row, mock.Mock(x=5.0))
        gtk.Popover.new.return_value.set_position.assert_called_once_with('the-side')
        gtk.ScrolledWindow.return_value.set_max_content_height.assert_called_once_with(123)


if __name__ == '__main__':
    unittest.main()
