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
import types

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


_PLAIN_DATA = (str, bytes, int, float, bool, tuple, frozenset, type(None))


def _same_binding(a, b):
    """The same object, or two equal plain values (two modules that each define
    `TIMEOUT = 5` do not disagree about it)."""
    return a is b or (type(a) is type(b) and isinstance(a, _PLAIN_DATA) and a == b)


def _describe(value):
    if isinstance(value, types.ModuleType):
        return f'module {value.__name__}'
    qualname = getattr(value, '__qualname__', None)
    if not qualname:
        return type(value).__name__
    return f'class {qualname}' if isinstance(value, type) else f'{type(value).__name__} {qualname}'


def _find_clashes(mods):
    """{name: [(module, what the name is bound to there), ...]} for every name
    that two modules bind to different objects.

    The namespace below answers `rt.name` from one module and rebinds `name` in
    every module that has it, so a name that means two things (an
    `import datetime` in one module and `from datetime import datetime` in
    another) silently gives one of them the wrong thing, and tests of unrelated
    code break or, worse, pass. A package's attribute for its own submodule
    (`void_tray.live`, put there by the import system) is structural and only
    counts when something else also uses the name."""
    regular, structural = {}, {}
    for module in mods:
        for name, value in vars(module).items():
            if name.startswith('__') and name.endswith('__'):
                continue
            submodule = isinstance(value, types.ModuleType) and value.__name__ == f'{module.__name__}.{name}'
            (structural if submodule else regular).setdefault(name, []).append((module.__name__, value))
    clashes = {}
    for name, holders in regular.items():
        everyone = holders + structural.get(name, [])
        distinct = []
        for _, value in everyone:
            if not any(_same_binding(value, kept) for kept in distinct):
                distinct.append(value)
        if len(distinct) > 1:
            clashes[name] = [(module, _describe(value)) for module, value in everyone]
    return clashes


def _clash_message(clashes):
    lines = ['void_tray modules bind the same name to different things, which the test namespace (rt) cannot '
             'tell apart: rt.<name> answers from one of them and mock.patch.object(rt, "<name>") rebinds all of '
             'them, so unrelated tests break or pass for the wrong reason. Rename one, or import both the same way:']
    for name, where in sorted(clashes.items()):
        lines.append(f'  {name}: ' + '; '.join(f'{module} = {what}' for module, what in where))
    return '\n'.join(lines)


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
        clashes = _find_clashes(mods)
        if clashes:
            raise RuntimeError(_clash_message(clashes))
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
