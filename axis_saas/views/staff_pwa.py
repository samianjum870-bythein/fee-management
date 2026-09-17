"""AXIS Staff Portal PWA — manifest + service worker + offline page.

STAFF_PWA_V1
------------
Adds PWA support to the staff portal, deliberately kept separate from
the school-admin PWA endpoints in ``axis_saas.pwa_views`` so the two
surfaces never interfere.

STAFF_PWA_V1_HOTFIX_2 — review fixes
------------------------------------
* The service worker caches **static assets only**. Everything under
  ``/portal/staff/`` that is not the offline page is fetched from the
  network every time. The previous version cached the entire
  ``/portal/staff/`` tree, including API responses, which meant a
  second staff member signing in on the same browser could read the
  previous user's data from cache.
* Navigations use network-first with a **precached offline fallback
  page**.
* ``Promise.allSettled`` replaces ``cache.addAll`` for precache, so a
  single missing icon does not abort the whole batch.
* Cache name bumped to ``axis-staff-pwa-v2`` so the old (over-caching)
  worker is retired on first load after deploy.
* Manifest served with ``application/manifest+json``.
* Manifest advertises PNG icons only — Chrome / Edge will not fire
  ``beforeinstallprompt`` without a 192x192 and a 512x512 PNG.

STAFF_PWA_TESTS_AND_CLEANUP_V1
------------------------------
* The offline fallback page no longer uses an inline ``onclick``
  handler — that would be blocked by any strict Content-Security-Policy
  header. The reload is bound via ``addEventListener`` from a small
  inline script block.
* The service worker's ``isStaticAsset`` helper now carries a comment
  explaining why its ``/static/`` prefix check can never match under
  the current scope — so the next reader does not chase a bug that is
  not there.
"""
from django.http import HttpResponse, JsonResponse


_STAFF_OFFLINE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0b6e64">
<title>AXIS Staff - Offline</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    background:
      radial-gradient(900px 380px at 100% -10%,
        rgba(15, 157, 143, 0.18), transparent 62%),
      radial-gradient(760px 340px at -15% 0%,
        rgba(201, 162, 39, 0.14), transparent 58%),
      linear-gradient(180deg, #f4f9f8 0%, #eef4f2 45%, #f8fbfa 100%);
    color: #0d1f1c;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    -webkit-font-smoothing: antialiased;
  }
  .wrap {
    max-width: 380px;
    width: 100%;
    background: #ffffff;
    border: 1px solid #e2ebe8;
    border-radius: 24px;
    box-shadow: 0 18px 38px rgba(11, 110, 100, 0.12);
    padding: 28px 22px;
    text-align: center;
  }
  .brand {
    width: 64px;
    height: 64px;
    margin: 0 auto 18px;
    border-radius: 20px;
    background: linear-gradient(140deg, #12b3a2 0%, #0b6e64 60%, #084c46 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    color: #ffffff;
    font-weight: 800;
    font-size: 1.7rem;
    box-shadow: 0 10px 20px rgba(11, 110, 100, 0.28);
  }
  h1 {
    font-size: 1.35rem;
    font-weight: 800;
    letter-spacing: -0.01em;
    margin-bottom: 8px;
  }
  p {
    color: #6b807a;
    font-size: 0.9rem;
    line-height: 1.5;
    margin-bottom: 20px;
  }
  button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    background: linear-gradient(135deg, #12b3a2 0%, #0b6e64 100%);
    color: #ffffff;
    border: none;
    border-radius: 14px;
    padding: 13px 22px;
    font-family: inherit;
    font-size: 0.95rem;
    font-weight: 800;
    cursor: pointer;
    box-shadow: 0 14px 24px rgba(11, 110, 100, 0.24);
    width: 100%;
    transition: transform 0.15s ease;
  }
  button:active { transform: scale(0.975); }
  .hint {
    margin-top: 14px;
    font-size: 0.75rem;
    color: #9aa9a4;
  }
</style>
</head>
<body>
  <main class="wrap">
    <div class="brand">A</div>
    <h1>You are offline</h1>
    <p>AXIS Staff Portal needs an internet connection to load fresh
       data. Please check your network and try again.</p>
    <button type="button" id="staffOfflineRetry">Retry</button>
    <div class="hint">This page is cached by the app so it appears
      instantly when you lose connection.</div>
  </main>
  <script>
    // STAFF_PWA_TESTS_AND_CLEANUP_V1: bound via addEventListener so a
    // strict Content-Security-Policy that blocks inline event
    // handlers does not break the Retry button.
    (function () {
      var btn = document.getElementById('staffOfflineRetry');
      if (btn) {
        btn.addEventListener('click', function () {
          window.location.reload();
        });
      }
    })();
  </script>
</body>
</html>
"""


def _manifest_icons():
    """Icon set for the staff PWA.

    PNG only. Chrome / Edge require at least one PNG icon at 192x192
    and one at 512x512 before considering the app installable.
    """
    return [
        {
            'src': '/static/pwa/staff-icon-192.png',
            'sizes': '192x192',
            'type': 'image/png',
            'purpose': 'any',
        },
        {
            'src': '/static/pwa/staff-icon-192.png',
            'sizes': '192x192',
            'type': 'image/png',
            'purpose': 'maskable',
        },
        {
            'src': '/static/pwa/staff-icon-512.png',
            'sizes': '512x512',
            'type': 'image/png',
            'purpose': 'any',
        },
        {
            'src': '/static/pwa/staff-icon-512.png',
            'sizes': '512x512',
            'type': 'image/png',
            'purpose': 'maskable',
        },
    ]


def staff_manifest(request):
    """Serve the staff-portal Web App Manifest.

    Content-Type is ``application/manifest+json`` — the media type
    named by the W3C spec. Chrome accepts ``application/json`` too,
    but strict clients and future audit tooling prefer the correct
    type.
    """
    data = {
        'name': 'AXIS Staff Portal',
        'short_name': 'AXIS Staff',
        'description': (
            'AXIS staff portal - dashboard, classes, attendance, '
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
    response = JsonResponse(
        data, content_type='application/manifest+json',
    )
    response['Cache-Control'] = (
        'no-store, no-cache, must-revalidate, max-age=0'
    )
    return response


def staff_service_worker(request):
    """Serve the staff-portal service worker.

    Static assets only — the fetch handler returns early for any
    request that is not under ``/static/`` and does not have a known
    asset extension. ``/portal/staff/`` GETs go to the network.
    Navigations fall back to the precached offline page.
    """
    sw_js = r"""// AXIS Staff Portal Service Worker (STAFF_PWA_V1_HOTFIX_2)
//
// Design rules (read before editing):
//   1. NEVER cache anything under /portal/staff/ except the offline
//      fallback page. The portal returns per-user, per-session data;
//      a second staff member signing in on the same browser must not
//      be able to read the first user's cached responses.
//   2. Cache ONLY assets served from /static/ or with a known asset
//      file extension.
//   3. All API / auth / manifest / SW requests bypass the worker.
//   4. Navigations use network-first with an offline fallback page.

const CACHE_NAME = 'axis-staff-pwa-v2';
const OFFLINE_URL = '/portal/staff/offline/';
const PRECACHE_STATIC = [
    '/static/pwa/staff-icon-192.png',
    '/static/pwa/staff-icon-512.png',
    '/static/pwa/staff-icon-180.png',
];

var STATIC_EXT_RE = /[.](?:css|js|mjs|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|otf|eot)$/i;

// STAFF_PWA_TESTS_AND_CLEANUP_V1:
//   The service worker scope is /portal/staff/. Browsers only
//   dispatch `fetch` events for URLs that fall inside the scope,
//   so /static/* requests never reach this handler in practice.
//   The first line of isStaticAsset() is kept for forward-
//   compatibility in case the scope is ever widened. The only
//   assets this SW actually caches are the ones listed in
//   PRECACHE_STATIC (icons + the offline page) — those are cached
//   during the install handler, which is scope-independent.
function isStaticAsset(url) {
    if (url.pathname.indexOf('/static/') === 0) return true;
    return STATIC_EXT_RE.test(url.pathname);
}

function isBypass(url) {
    if (url.pathname === '/portal/staff/manifest.json') return true;
    if (url.pathname === '/portal/staff/sw.js') return true;
    if (url.pathname.indexOf('/portal/staff/api/') === 0) return true;
    if (url.pathname.indexOf('/portal/staff/login') === 0) return true;
    if (url.pathname.indexOf('/portal/staff/logout') === 0) return true;
    if (url.pathname.indexOf('/portal/staff/biometric/') === 0) return true;
    return false;
}

self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return Promise.allSettled(
                PRECACHE_STATIC.concat([OFFLINE_URL]).map(function (url) {
                    return cache.add(url);
                })
            );
        }).then(function () { return self.skipWaiting(); })
    );
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(keys.map(function (key) {
                if (key !== CACHE_NAME
                    && key.indexOf('axis-staff-pwa-') === 0) {
                    return caches.delete(key);
                }
                return null;
            }));
        }).then(function () { return self.clients.claim(); })
    );
});

self.addEventListener('fetch', function (event) {
    var request = event.request;
    if (request.method !== 'GET' && request.method !== 'HEAD') return;

    var url;
    try { url = new URL(request.url); } catch (e) { return; }

    if (url.origin !== self.location.origin) return;
    if (isBypass(url)) return;

    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).catch(function () {
                return caches.match(OFFLINE_URL).then(function (cached) {
                    if (cached) return cached;
                    return new Response(
                        'You are offline and the offline page could not be loaded.',
                        {
                            status: 503,
                            headers: { 'Content-Type': 'text/plain; charset=utf-8' },
                        }
                    );
                });
            })
        );
        return;
    }

    if (!isStaticAsset(url)) return;

    event.respondWith(
        caches.match(request).then(function (cached) {
            if (cached) return cached;
            return fetch(request).then(function (response) {
                if (response
                    && response.status === 200
                    && response.type === 'basic') {
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


def staff_offline(request):
    """Serve the self-contained offline fallback page.

    Precached by the staff service worker so the user gets a branded
    page instead of the browser's generic offline error.
    """
    response = HttpResponse(
        _STAFF_OFFLINE_HTML, content_type='text/html; charset=utf-8',
    )
    response['Cache-Control'] = (
        'no-store, no-cache, must-revalidate, max-age=0'
    )
    return response
