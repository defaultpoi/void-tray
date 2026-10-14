"""Gives the tests one namespace over the whole void_tray package.

The suite was written against a single module and patches names on it with
mock.patch.object(rt, 'name', ...). The code now lives in many modules, and a
name imported from another module is a separate binding there, so patching only
the defining module would not reach the code under test. Reading `rt.name`
returns the value from the module that defines it; setting `rt.name` (which is
what mock.patch does) rebinds it in every module that holds it.
"""
import ast
import importlib
import pathlib
import pkgutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import void_tray  # noqa: E402


def _load_modules():
    mods = [void_tray]
    for info in pkgutil.walk_packages(void_tray.__path__, 'void_tray.'):
        if info.name.endswith('.__main__'):
            continue  # the entry point, not part of the namespace
        mods.append(importlib.import_module(info.name))
    return mods


def _imported_names(module):
    """Names a module only borrows through `from x import name`."""
    path = getattr(module, '__file__', None)
    if not path:
        return set()
    tree = ast.parse(pathlib.Path(path).read_text())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
    return names


class _View:
    """What mock.patch sees as the namespace's __dict__, so that it restores
    the original value on exit instead of deleting the attribute."""

    def __init__(self, namespace):
        self._namespace = namespace

    def __getitem__(self, name):
        try:
            return getattr(self._namespace, name)
        except AttributeError:
            raise KeyError(name)


class _Namespace:
    __slots__ = ('_mods', '_borrowed')

    def __init__(self):
        mods = _load_modules()
        object.__setattr__(self, '_mods', mods)
        object.__setattr__(self, '_borrowed', {m: _imported_names(m) for m in mods})

    @property
    def __dict__(self):
        return _View(self)

    def _holders(self, name):
        return [m for m in self._mods if name in vars(m)]

    def __getattr__(self, name):
        holders = self._holders(name)
        if not holders:
            raise AttributeError(name)
        for m in holders:
            if name not in self._borrowed[m]:
                return vars(m)[name]
        return vars(holders[0])[name]

    def __setattr__(self, name, value):
        holders = self._holders(name)
        if not holders:
            raise AttributeError(name)
        for m in holders:
            setattr(m, name, value)

    def __delattr__(self, name):
        raise AttributeError('cannot delete %s through the test namespace' % name)


rt = _Namespace()
