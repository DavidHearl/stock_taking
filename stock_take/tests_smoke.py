"""
Refactor smoke tests.

These do NOT test behaviour. They are a safety net for large structural changes
— specifically splitting ``views.py`` into per-feature ``*_views.py`` modules.
They assert that:

  1. Every route in the URLconf resolves to an importable, callable view.
  2. Every ``stock_take`` view module imports cleanly.
  3. Every named URL that takes no arguments can be reversed.

If a view is moved to a new module and a reference is missed, or a module gains
an import error, these fail immediately and point at the offending route/module
— across the whole site at once, without needing per-view coverage.

Run with: python manage.py test stock_take.tests_smoke
"""
import importlib
import pkgutil

from django.test import SimpleTestCase
from django.urls import NoReverseMatch, get_resolver, reverse
from django.urls.resolvers import URLResolver


def _iter_url_patterns(resolver):
    """Yield every leaf URLPattern under a resolver, recursing into includes."""
    for entry in getattr(resolver, 'url_patterns', []):
        if isinstance(entry, URLResolver):
            yield from _iter_url_patterns(entry)
        else:
            yield entry


class URLConfSmokeTests(SimpleTestCase):
    """Guards against broken routes after view extraction / refactors."""

    def test_every_route_has_importable_callback(self):
        broken = []
        for pattern in _iter_url_patterns(get_resolver()):
            try:
                callback = pattern.callback  # triggers import of the view
                if not callable(callback):
                    broken.append(f'{pattern.pattern} -> not callable')
            except Exception as exc:  # noqa: BLE001 - we want to report any failure
                broken.append(f'{pattern.pattern} -> {exc!r}')
        self.assertEqual(
            broken, [],
            'Routes pointing at unimportable/uncallable views:\n' + '\n'.join(broken),
        )

    def test_named_urls_without_args_reverse(self):
        failures = []
        for name in sorted(k for k in get_resolver().reverse_dict.keys() if isinstance(k, str)):
            try:
                reverse(name)
            except NoReverseMatch:
                # Route requires arguments — covered by the callback test above.
                continue
            except Exception as exc:  # noqa: BLE001
                failures.append(f'{name} -> {exc!r}')
        self.assertEqual(
            failures, [],
            'Named no-arg URLs that fail to reverse:\n' + '\n'.join(failures),
        )


class ViewModuleImportSmokeTests(SimpleTestCase):
    """Every stock_take view module must import cleanly."""

    def test_all_view_modules_import(self):
        import stock_take

        failures = []
        for module_info in pkgutil.iter_modules(stock_take.__path__):
            name = module_info.name
            if name == 'views' or name.endswith('_views') or name.endswith('_view'):
                try:
                    importlib.import_module(f'stock_take.{name}')
                except Exception as exc:  # noqa: BLE001
                    failures.append(f'stock_take.{name} -> {exc!r}')
        self.assertEqual(
            failures, [],
            'View modules that fail to import:\n' + '\n'.join(failures),
        )
