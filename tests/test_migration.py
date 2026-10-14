import os
import tempfile
import unittest
from unittest import mock

from facade import rt


class TestLegacyConfigMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.legacy = os.path.join(self.tmp.name, 'rss-tray')
        self.new = os.path.join(self.tmp.name, 'void-tray')
        self.new_config = os.path.join(self.new, 'config.conf')
        for target, value in (('LEGACY_CONFIG_DIR', self.legacy), ('CONFIG_DIR', self.new),
                              ('CONFIG_FILE', self.new_config)):
            patcher = mock.patch.object(rt, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write_legacy(self, **files):
        os.makedirs(self.legacy, exist_ok=True)
        for name, text in files.items():
            with open(os.path.join(self.legacy, name), 'w') as f:
                f.write(text)

    def test_copies_config_state_and_sound_and_keeps_the_old_files(self):
        self._write_legacy(**{'config.conf': '[feeds]\nhttps://a.example/rss\n',
                              'state.json': '{"seen": {"x": 1}}', 'notification.wav': 'RIFF'})
        self.assertTrue(rt.migrate_legacy_config())
        for name in ('config.conf', 'state.json', 'notification.wav'):
            self.assertTrue(os.path.isfile(os.path.join(self.new, name)), name)
            self.assertTrue(os.path.isfile(os.path.join(self.legacy, name)), name)
        with open(self.new_config) as f:
            self.assertIn('https://a.example/rss', f.read())

    def test_copies_only_what_exists(self):
        self._write_legacy(**{'config.conf': '[feeds]\n'})
        self.assertTrue(rt.migrate_legacy_config())
        self.assertFalse(os.path.exists(os.path.join(self.new, 'state.json')))

    def test_never_overwrites_an_existing_config(self):
        self._write_legacy(**{'config.conf': 'old'})
        os.makedirs(self.new)
        with open(self.new_config, 'w') as f:
            f.write('new')
        self.assertFalse(rt.migrate_legacy_config())
        with open(self.new_config) as f:
            self.assertEqual(f.read(), 'new')

    def test_nothing_to_copy_without_a_legacy_directory(self):
        self.assertFalse(rt.migrate_legacy_config())
        self.assertFalse(os.path.exists(self.new))

    def test_a_copy_error_is_logged_not_raised(self):
        self._write_legacy(**{'config.conf': 'x'})
        with mock.patch.object(rt.shutil, 'copy2', side_effect=OSError('disk full')), \
                mock.patch.object(rt, '_log') as log:
            self.assertFalse(rt.migrate_legacy_config())
        self.assertIn('disk full', log.call_args[0][0])


class TestDebugEnvironmentNames(unittest.TestCase):
    def _output(self, env):
        with mock.patch.dict(rt.os.environ, env, clear=True), mock.patch('builtins.print') as p:
            rt._log('hello')
        return p.call_args[0][0] if p.called else None

    def test_new_name_prints_with_the_app_prefix(self):
        self.assertEqual(self._output({'VOID_TRAY_DEBUG': '1'}), 'void-tray: hello')

    def test_old_name_still_works(self):
        self.assertEqual(self._output({'RSS_TRAY_DEBUG': '1'}), 'void-tray: hello')

    def test_silent_when_neither_is_set(self):
        self.assertIsNone(self._output({}))


if __name__ == '__main__':
    unittest.main()
