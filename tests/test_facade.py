import datetime
import types
import unittest
from unittest import mock

import facade


def module(name, **names):
    mod = types.ModuleType(name)
    vars(mod).update(names)
    return mod


def shared():
    return 'one function, imported into several modules'


class TestNameClashes(unittest.TestCase):
    def test_the_package_has_no_clashes(self):
        self.assertEqual(facade._find_clashes(facade._load_modules()), {})

    def test_import_datetime_against_from_datetime_import_datetime_is_reported(self):
        # the mix-up that happened once: a module's `import datetime` shadowed another's class
        clashes = facade._find_clashes([module('pkg.a', datetime=datetime), module('pkg.b', datetime=datetime.datetime)])
        self.assertEqual(clashes, {'datetime': [('pkg.a', 'module datetime'), ('pkg.b', 'class datetime')]})

    def test_two_functions_with_one_name_are_reported(self):
        # and the other one: two modules each defining their own _error_text
        def first(): pass

        def second(): pass
        clashes = facade._find_clashes([module('pkg.a', _error_text=first), module('pkg.b', _error_text=second)])
        self.assertEqual([m for m, _ in clashes['_error_text']], ['pkg.a', 'pkg.b'])

    def test_one_object_in_several_modules_is_not_a_clash(self):
        mods = [module('pkg.a', shared=shared, datetime=datetime), module('pkg.b', shared=shared, datetime=datetime),
                module('pkg.c', shared=shared)]
        self.assertEqual(facade._find_clashes(mods), {})

    def test_equal_plain_values_are_not_a_clash_but_different_ones_are(self):
        same = [module('pkg.a', WAIT=float('2.5')), module('pkg.b', WAIT=float('2.5'))]
        self.assertEqual(facade._find_clashes(same), {})
        different = [module('pkg.a', WAIT=float('2.5')), module('pkg.b', WAIT=float('3.5'))]
        self.assertEqual(list(facade._find_clashes(different)), ['WAIT'])

    def test_dunder_names_are_ignored(self):
        mods = [module('pkg.a', __doc__='one', __file__='a.py'), module('pkg.b', __doc__='two', __file__='b.py')]
        self.assertEqual(facade._find_clashes(mods), {})

    def test_a_package_binding_its_own_submodules_is_not_a_clash(self):
        pkg, ui = module('pkg'), module('pkg.ui')
        pkg.live, ui.live = module('pkg.live'), module('pkg.ui.live')
        self.assertEqual(facade._find_clashes([pkg, ui]), {})

    def test_a_name_that_is_also_a_submodule_is_a_clash_when_something_else_uses_it(self):
        pkg, ui = module('pkg'), module('pkg.ui')
        pkg.live = module('pkg.live')
        ui.live = module('pkg.ui.live')
        other = module('pkg.other', live=shared)
        clashes = facade._find_clashes([pkg, ui, other])
        self.assertEqual(sorted(m for m, _ in clashes['live']), ['pkg', 'pkg.other', 'pkg.ui'])


class TestNamespaceRefusesClashes(unittest.TestCase):
    def test_a_namespace_over_clashing_modules_fails_loudly_and_names_the_clash(self):
        mods = [module('pkg.a', datetime=datetime), module('pkg.b', datetime=datetime.datetime)]
        with mock.patch.object(facade, '_load_modules', return_value=mods):
            with self.assertRaises(RuntimeError) as caught:
                facade._Namespace()
        message = str(caught.exception)
        for part in ('datetime', 'pkg.a', 'pkg.b', 'module datetime', 'class datetime'):
            self.assertIn(part, message)

    def test_a_namespace_over_clean_modules_still_patches_every_holder(self):
        a, b = module('pkg.a', shared=shared), module('pkg.b', shared=shared)
        with mock.patch.object(facade, '_load_modules', return_value=[a, b]):
            namespace = facade._Namespace()
        self.assertIs(namespace.shared, shared)
        with mock.patch.object(namespace, 'shared', len):
            self.assertIs(a.shared, len)
            self.assertIs(b.shared, len)
        self.assertIs(a.shared, shared)


if __name__ == '__main__':
    unittest.main()
