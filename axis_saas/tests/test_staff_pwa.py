"""Tests for the staff portal PWA (STAFF_PWA_V1).

Covers the manifest, the service worker, the offline fallback page,
the middleware public-path allowlist, the URL configuration, the
on-disk PNG icons, and the admin PWA's isolation from
``/portal/staff/``.

Run:
    python manage.py test axis_saas.tests.test_staff_pwa
"""

import json
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase


# =====================================================================
# /portal/staff/manifest.json
# =====================================================================
class StaffManifestTests(TestCase):
    """The manifest must be installable-compliant.

    Chrome / Edge require PNG icons at 192x192 and 512x512 before they
    fire ``beforeinstallprompt``. The manifest is served with the
    ``application/manifest+json`` media type named by the W3C spec.
    """

    def setUp(self):
        self.client = Client()

    def test_manifest_returns_200_without_session(self):
        response = self.client.get('/portal/staff/manifest.json')
        self.assertEqual(response.status_code, 200)

    def test_manifest_content_type_is_manifest_json(self):
        response = self.client.get('/portal/staff/manifest.json')
        self.assertIn('manifest+json', response['Content-Type'])

    def test_manifest_has_required_web_app_fields(self):
        response = self.client.get('/portal/staff/manifest.json')
        data = json.loads(response.content)
        for key in ('name', 'short_name', 'start_url', 'scope',
                    'display', 'background_color', 'theme_color',
                    'icons'):
            self.assertIn(key, data, f"manifest missing '{key}'")
        self.assertEqual(data['display'], 'standalone')

    def test_manifest_start_url_and_scope_are_staff_scoped(self):
        response = self.client.get('/portal/staff/manifest.json')
        data = json.loads(response.content)
        self.assertEqual(data['start_url'], '/portal/staff/dashboard/')
        self.assertEqual(data['scope'], '/portal/staff/')

    def test_manifest_advertises_png_icons_at_192_and_512(self):
        """The two sizes Chrome / Edge require for installability."""
        response = self.client.get('/portal/staff/manifest.json')
        data = json.loads(response.content)
        png_sizes = {
            icon['sizes'] for icon in data['icons']
            if icon.get('type') == 'image/png'
        }
        self.assertIn('192x192', png_sizes)
        self.assertIn('512x512', png_sizes)

    def test_manifest_icons_include_any_and_maskable_purposes(self):
        response = self.client.get('/portal/staff/manifest.json')
        data = json.loads(response.content)
        purposes = {icon.get('purpose') for icon in data['icons']}
        self.assertIn('any', purposes)
        self.assertIn('maskable', purposes)

    def test_manifest_advertises_only_png_icons(self):
        """SVG icons are silently ignored by Chrome for installability.
        The manifest must not mix SVG into the icon set."""
        response = self.client.get('/portal/staff/manifest.json')
        data = json.loads(response.content)
        for icon in data['icons']:
            self.assertEqual(
                icon.get('type'), 'image/png',
                f"Non-PNG icon in manifest: {icon!r}",
            )


# =====================================================================
# /portal/staff/sw.js
# =====================================================================
class StaffServiceWorkerTests(TestCase):
    """The service worker must be privacy-safe and offline-capable."""

    def setUp(self):
        self.client = Client()
        self._body = None

    @property
    def body(self):
        if self._body is None:
            response = self.client.get('/portal/staff/sw.js')
            self._body = response.content.decode('utf-8')
        return self._body

    def test_sw_returns_200_without_session(self):
        response = self.client.get('/portal/staff/sw.js')
        self.assertEqual(response.status_code, 200)

    def test_sw_content_type_is_javascript(self):
        response = self.client.get('/portal/staff/sw.js')
        self.assertIn('javascript', response['Content-Type'])

    def test_sw_sets_service_worker_allowed_header(self):
        response = self.client.get('/portal/staff/sw.js')
        self.assertEqual(response['Service-Worker-Allowed'], '/portal/staff/')

    def test_sw_bypasses_api_endpoints(self):
        self.assertIn('/portal/staff/api/', self.body)

    def test_sw_bypasses_auth_endpoints(self):
        for path in ('/portal/staff/login',
                     '/portal/staff/logout',
                     '/portal/staff/biometric/'):
            self.assertIn(path, self.body, f"sw missing bypass for {path}")

    def test_sw_uses_promise_allsettled_not_addall(self):
        """Precache must survive a single missing icon without
        aborting the entire install."""
        self.assertIn('Promise.allSettled', self.body)
        self.assertNotIn('cache.addAll(', self.body)

    def test_sw_precaches_the_offline_url(self):
        self.assertIn("'/portal/staff/offline/'", self.body)
        self.assertIn('OFFLINE_URL', self.body)

    def test_sw_uses_network_first_for_navigations(self):
        self.assertIn("request.mode === 'navigate'", self.body)

    def test_sw_ignores_non_get_head_requests(self):
        self.assertIn("request.method !== 'GET'", self.body)

    def test_sw_cache_name_is_v2(self):
        """Cache name bump retired the old (over-caching) worker."""
        self.assertIn("'axis-staff-pwa-v2'", self.body)

    def test_sw_only_caches_when_putting_static_assets(self):
        """The SW must guard its ``cache.put()`` calls so an
        authenticated HTML response can never end up in the cache.
        The guard is the ``isStaticAsset`` / ``isBypass`` pair."""
        self.assertIn('isStaticAsset', self.body)
        self.assertIn('isBypass', self.body)


# =====================================================================
# /portal/staff/offline/
# =====================================================================
class StaffOfflinePageTests(TestCase):
    """The offline fallback page must be self-contained and CSP-safe."""

    def setUp(self):
        self.client = Client()

    def _body(self):
        return self.client.get('/portal/staff/offline/').content.decode('utf-8')

    def test_offline_returns_200_without_session(self):
        response = self.client.get('/portal/staff/offline/')
        self.assertEqual(response.status_code, 200)

    def test_offline_content_type_is_html(self):
        response = self.client.get('/portal/staff/offline/')
        self.assertIn('text/html', response['Content-Type'])

    def test_offline_has_a_retry_button(self):
        body = self._body()
        self.assertIn('Retry', body)
        self.assertIn('staffOfflineRetry', body)

    def test_offline_has_no_inline_event_handlers(self):
        """CSP-safe: no ``onclick=`` anywhere in the page."""
        body = self._body()
        self.assertNotIn('onclick=', body)
        self.assertIn('addEventListener', body)

    def test_offline_is_self_contained(self):
        """No external stylesheet / script / font links."""
        body = self._body()
        self.assertNotIn('<link rel="stylesheet"', body)
        self.assertNotIn('<script src=', body)


# =====================================================================
# Staff portal middleware allowlist
# =====================================================================
class StaffPortalMiddlewareTests(TestCase):
    """Manifest / sw / offline must be public; everything else is not."""

    def setUp(self):
        self.client = Client()

    def test_manifest_reachable_without_staff_session(self):
        self.assertEqual(
            self.client.get('/portal/staff/manifest.json').status_code,
            200,
        )

    def test_service_worker_reachable_without_staff_session(self):
        self.assertEqual(
            self.client.get('/portal/staff/sw.js').status_code,
            200,
        )

    def test_offline_page_reachable_without_staff_session(self):
        self.assertEqual(
            self.client.get('/portal/staff/offline/').status_code,
            200,
        )

    def test_dashboard_redirects_to_login_without_session(self):
        response = self.client.get('/portal/staff/dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/portal/staff/login/', response['Location'])

    def test_login_page_reachable_without_session(self):
        self.assertEqual(
            self.client.get('/portal/staff/login/').status_code,
            200,
        )


# =====================================================================
# URL configuration sanity
# =====================================================================
class StaffUrlConfigTests(TestCase):
    """Static checks on ``axis_saas.staff_urls.urlpatterns``."""

    def test_no_duplicate_route_names(self):
        import axis_saas.staff_urls as mod
        # STAFF_PWA_TESTS_HOTFIX_1: ``urlpatterns`` mixes
        # URLPattern and URLResolver. URLResolver has no
        # ``.name`` attribute on Django < 5.1, so walk with
        # getattr and drop the unnamed entries.
        names = [
            getattr(p, 'name', None) for p in mod.urlpatterns
        ]
        names = [n for n in names if n]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(
            duplicates, [],
            f"Duplicate route names in staff_urls: {duplicates}",
        )

    def test_no_duplicate_route_paths(self):
        import axis_saas.staff_urls as mod
        paths = [str(p.pattern) for p in mod.urlpatterns]
        duplicates = sorted({p for p in paths if paths.count(p) > 1})
        self.assertEqual(
            duplicates, [],
            f"Duplicate route paths in staff_urls: {duplicates}",
        )

    def test_required_pwa_routes_are_registered(self):
        import axis_saas.staff_urls as mod
        # STAFF_PWA_TESTS_HOTFIX_1: same URLResolver guard as
        # the duplicate-names test above.
        names = {
            getattr(p, 'name', None) for p in mod.urlpatterns
        }
        names.discard(None)
        for required in ('staff_manifest',
                         'staff_service_worker',
                         'staff_offline'):
            self.assertIn(required, names)


# =====================================================================
# PNG icons on disk
# =====================================================================
class StaffIconFileTests(TestCase):
    """The three PNG icons must exist with a valid PNG magic header."""

    PNG_MAGIC = b'\x89PNG\r\n\x1a\n'

    def _icon_path(self, filename: str) -> Path:
        return Path(settings.BASE_DIR) / 'static' / 'pwa' / filename

    def _assert_valid_png(self, filename: str):
        path = self._icon_path(filename)
        self.assertTrue(path.exists(), f"{path} does not exist")
        with path.open('rb') as fh:
            header = fh.read(8)
        self.assertEqual(
            header, self.PNG_MAGIC,
            f"{path} does not start with a PNG magic header",
        )

    def test_staff_icon_180_png_exists_and_is_valid(self):
        self._assert_valid_png('staff-icon-180.png')

    def test_staff_icon_192_png_exists_and_is_valid(self):
        self._assert_valid_png('staff-icon-192.png')

    def test_staff_icon_512_png_exists_and_is_valid(self):
        self._assert_valid_png('staff-icon-512.png')


# =====================================================================
# Admin PWA service worker isolation
# =====================================================================
class AdminServiceWorkerIsolationTests(TestCase):
    """The admin PWA worker is served from ``/sw.js`` at scope ``/``
    and must NOT intercept anything under ``/portal/staff/``. Without
    the exclusion guard, an admin PWA already installed on the same
    browser would cache staff API responses and leak them across
    logout / login cycles."""

    def setUp(self):
        self.client = Client()

    def test_admin_sw_excludes_staff_portal(self):
        response = self.client.get('/sw.js')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn(
            "url.pathname.startsWith('/portal/staff/')",
            body,
            "admin /sw.js must exclude /portal/staff/ from its fetch handler",
        )
