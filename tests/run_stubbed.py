"""Runs the suite with GTK replaced by mocks, for machines without PyGObject.

Usage: python3 tests/run_stubbed.py
The normal run (python3 -m unittest discover -s tests) uses the real bindings;
both must pass.
"""
import pathlib
import sys
import unittest
from unittest import mock

for name in ('gi', 'gi.repository', 'cairo'):
    sys.modules[name] = mock.MagicMock()

here = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(here))
suite = unittest.defaultTestLoader.discover(str(here), pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
