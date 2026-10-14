import unittest

from facade import rt
from helpers import TmpConfigCase


class TestConfigEncoding(TmpConfigCase):
    def test_non_utf8_bytes_do_not_crash_the_loaders(self):
        with open(rt.CONFIG_FILE, 'wb') as f:
            f.write(b'[feeds]\nhttps://a.example/feed|Caf\xe9\n')
        feeds = rt.load_feeds()
        self.assertEqual(feeds[0][0], 'https://a.example/feed')


if __name__ == '__main__':
    unittest.main()
