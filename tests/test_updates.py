import unittest

from facade import rt


class TestParseXbpsUpdates(unittest.TestCase):
    def test_real_line(self):
        out = 'cryptsetup-2.8.8_1 update x86_64 https://repo-default.voidlinux.org/current 3203607 568523\n'
        self.assertEqual(rt.parse_xbps_updates(out), ['cryptsetup'])

    def test_hyphenated_pkgname(self):
        out = 'python3-feedparser-6.0.11_1 update x86_64 https://r 1 2\n'
        self.assertEqual(rt.parse_xbps_updates(out), ['python3-feedparser'])

    def test_non_update_actions_and_junk_ignored(self):
        out = ('foo-1.0_1 install x86_64 https://r 1 2\n'
               'bar-1.0_1 remove x86_64 https://r 1 2\n'
               '\nshort\n'
               'baz-2.0_1 update x86_64 https://r 1 2\n')
        self.assertEqual(rt.parse_xbps_updates(out), ['baz'])

    def test_empty(self):
        self.assertEqual(rt.parse_xbps_updates(''), [])


if __name__ == '__main__':
    unittest.main()
