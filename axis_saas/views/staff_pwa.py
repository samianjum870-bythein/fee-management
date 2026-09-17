"""AXIS Staff Portal PWA — manifest + service worker.

STAFF_PWA_V1
------------
Adds PWA support to the staff portal, kept deliberately separate from
the school-admin PWA endpoints in ``axis_saas/pwa_views.py`` so the
two surfaces never interfere.

Key design notes
----------------
* Both endpoints are schema-independent. The staff portal is mounted
  at ``/portal/staff/`` (see ``axis_saas.staff_urls``) and resolves
  the tenant from the authenticated staff session, not from the URL.
  The manifest and service worker therefore live at stable paths:

      /portal/staff/manifest.json
      /portal/staff/sw.js

* The service worker scope is limited to ``/portal/staff/`` so it
  cannot control the school-admin pages or any other tenant surface.

* The service worker uses a *network-first* strategy for navigations
  (so staff always see fresh data when online, and can fall back to
  a cached copy when offline) and a *cache-first* strategy for static
  assets. API and auth endpoints are never cached.
"""
from django.http import HttpResponse, JsonResponse


def _manifest_icons():
    """Icon set for the staff PWA.

    SVG is used for both sizes — it scales cleanly and keeps the repo
    light. The 'maskable' variant uses the same artwork because the
    icon is already designed with a safe-zone-friendly composition
    (centered mark on a full-bleed background).
    """
    return [
        {
            'src': '/static/pwa/staff-icon-192.svg',
            'sizes': '192x192',
            'type': 'image/svg+xml',
            'purpose': 'any',
        },
        {
            'src': '/static/pwa/staff-icon-192.svg',
            'sizes': '192x192',
            'type': 'image/svg+xml',
            'purpose': 'maskable',
        },
        {
            'src': '/static/pwa/staff-icon-512.svg',
            'sizes': '512x512',
            'type': 'image/svg+xml',
            'purpose': 'any',
        },
        {
            'src': '/static/pwa/staff-icon-512.svg',
            'sizes': '512x512',
            'type': 'image/svg+xml',
            'purpose': 'maskable',
        },
    ]


def staff_manifest(request):
    """Serve the staff-portal Web App Manifest.

    ``start_url`` points at the staff dashboard; ``scope`` is limited
    to ``/portal/staff/`` so installing the staff PWA never hijacks
    URLs outside the staff portal.
    """
    data = {
        'name': 'AXIS Staff Portal',
        'short_name': 'AXIS Staff',
        'description': (
            'AXIS staff portal — dashboard, classes, attendance, '
            'leave, profile.'
        ),
        'start_url': '/portal/staff/dashboard/',
        'scope': '/portal/staff/',
        'display': 'standalone',
        'orientation': 'portrait',
        'background_color': '#f2f7f6',
        'theme_color': '#0b6e64',
        'categories': ['education', 'productivity'],
        'lang': 'en',
        'icons': _manifest_icons(),
    }
    response = JsonResponse(data)
    response['Cache-Control'] = (
        'no-store, no-cache, must-revalidate, max-age=0'
    )
    return response


def staff_service_worker(request):
    """Serve the staff-portal service worker.

    Served from ``/portal/staff/sw.js`` so its default scope is
    ``/portal/staff/``. The ``Service-Worker-Allowed`` header is set
    defensively in case a proxy rewrites the path.
    """
    sw_js = r"""// AXIS Staff Portal Service Worker (STAFF_PWA_V1)
const CACHE_NAME = 'axis-staff-pwa-v1';
const PRECACHE_STATIC = [
    '/static/pwa/staff-icon-192.svg',
    '/static/pwa/staff-icon-512.svg',
];

self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(function (cache) {
                return cache.addAll(PRECACHE_STATIC).catch(function (err) {
                    console.warn('[AXIS Staff SW] pre-cache failed:', err);
                });
            })
            .then(function () { return self.skipWaiting(); })
    );
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(keys.map(function (key) {
                if (key !== CACHE_NAME && key.indexOf('axis-staff-pwa-') === 0) {
                    return caches.delete(key);
                }
                return null;
            }));
        }).then(function () { return self.clients.claim(); })
    );
});

self.addEventListener('fetch', function (event) {
    var request = event.request;
    if (request.method !== 'GET' && request.method !== 'HEAD') {
        return;
    }
    var url;
    try {
        url = new URL(request.url);
    } catch (e) {
        return;
    }
    if (url.origin !== self.location.origin) {
        return;
    }

    // Never intercept API or auth traffic — the staff portal must
    // always reflect live server state for those.
    if (url.pathname.indexOf('/portal/staff/api/') !== -1
        || url.pathname.indexOf('/portal/staff/login') !== -1
        || url.pathname.indexOf('/portal/staff/logout') !== -1
        || url.pathname.indexOf('/portal/staff/biometric/') !== -1) {
        return;
    }

    // Network-first for page navigations, so staff see fresh data
    // when online, and a cached copy when offline.
    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).catch(function () {
                return caches.match(request);
            })
        );
        return;
    }

    // Cache-first for everything else (static assets, stylesheets,
    // images). Cache successful same-origin GET responses.
    event.respondWith(
        caches.match(request).then(function (cached) {
            if (cached) {
                return cached;
            }
            return fetch(request).then(function (response) {
                if (response
                    && response.status === 200
                    && response.type === 'basic'
                    && url.pathname.indexOf('/portal/staff/') === 0) {
                    var clone = response.clone();
                    caches.open(CACHE_NAME).then(function (cache) {
                        cache.put(request, clone);
                    });
                }
                return response;
            });
        })
    );
});
"""
    response = HttpResponse(sw_js, content_type='application/javascript')
    response['Cache-Control'] = (
        'no-store, no-cache, must-revalidate, max-age=0'
    )
    response['Pragma'] = 'no-cache'
    response['Service-Worker-Allowed'] = '/portal/staff/'
    return response
