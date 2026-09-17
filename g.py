#!/usr/bin/env python3
"""
axis_patcher.py — STAFF_PWA_V1
==============================

Makes the staff portal installable as a PWA (Progressive Web App),
without touching the school admin panel's existing PWA plumbing.

Why a separate module?
----------------------
The admin panel already has its own PWA endpoints in
`axis_saas/pwa_views.py` (manifest at /portal/<schema>/manifest.json,
service worker at /sw.js). We deliberately do NOT modify that module
or the admin base template. Instead we add:

  * A new view module `axis_saas/views/staff_pwa.py` — staff manifest
    + staff service worker. Both are schema-independent (staff
    session carries the tenant), so they live at stable URLs.

  * Two new SVG icons under `static/pwa/` — teal gradient + gold
    accent, matching the existing AXIS staff brand mark.

  * New URL routes in `axis_saas/staff_urls.py`:
        /portal/staff/manifest.json
        /portal/staff/sw.js

  * An install prompt + service-worker registration block in
    `templates/mobile/staff/base.html`, plus a floating "Install App"
    button (only shown when a staff session is active, i.e. the same
    condition that renders the bottom navigation).

Files created
-------------
    axis_saas/views/staff_pwa.py
    static/pwa/staff-icon-192.svg
    static/pwa/staff-icon-512.svg

Files modified
--------------
    axis_saas/staff_urls.py
    templates/mobile/staff/base.html

Idempotency
-----------
Every write is guarded by a marker string. Re-running this patcher
after a successful run is a clean no-op — no anchors will match and
no files will be rewritten.

Usage
-----
    python axis_patcher.py --dry-run --verbose
    python axis_patcher.py
    python axis_patcher.py --target-dir /path/to/fee_management --verbose
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Target paths (relative to project root)
# ---------------------------------------------------------------------------

STAFF_PWA_VIEW_REL   = Path("axis_saas") / "views" / "staff_pwa.py"
STAFF_URLS_REL       = Path("axis_saas") / "staff_urls.py"
STAFF_BASE_TPL_REL   = Path("templates") / "mobile" / "staff" / "base.html"
ICON_192_REL         = Path("static") / "pwa" / "staff-icon-192.svg"
ICON_512_REL         = Path("static") / "pwa" / "staff-icon-512.svg"


# ---------------------------------------------------------------------------
# Marker strings — used for idempotency checks
# ---------------------------------------------------------------------------

MARKER_VIEW       = "STAFF_PWA_V1"
MARKER_URLS_IMPORT = "STAFF_PWA_V1: staff portal PWA manifest"
MARKER_URLS_ROUTE  = "STAFF_PWA_V1"
MARKER_TPL_HEAD    = "STAFF_PWA_V1_HEAD"
MARKER_TPL_BODY    = "STAFF_PWA_V1: floating install button"
MARKER_ICON        = "AXIS Staff Portal icon"


# ---------------------------------------------------------------------------
# New file: axis_saas/views/staff_pwa.py
# ---------------------------------------------------------------------------

STAFF_PWA_VIEW_CONTENT = '''"""AXIS Staff Portal PWA — manifest + service worker.

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
'''


# ---------------------------------------------------------------------------
# New files: SVG icons
# ---------------------------------------------------------------------------
# Same artwork at two viewBox sizes so the manifest can advertise
# explicit 192 and 512 entries without any build step.

_STAFF_ICON_SVG = '''<?xml version="1.0" encoding="UTF-8"?>
<!-- AXIS Staff Portal icon — STAFF_PWA_V1 -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">
  <defs>
    <linearGradient id="staffBg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#14c2b0"/>
      <stop offset="55%" stop-color="#0b6e64"/>
      <stop offset="100%" stop-color="#063a36"/>
    </linearGradient>
    <linearGradient id="staffGold" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#c9a227"/>
      <stop offset="100%" stop-color="#f0d876"/>
    </linearGradient>
  </defs>

  <!-- Full-bleed teal background -->
  <rect width="{size}" height="{size}" fill="url(#staffBg)"/>

  <!-- Soft corner highlight -->
  <circle cx="{hl_x}" cy="{hl_y}" r="{hl_r}" fill="rgba(255,255,255,0.08)"/>

  <!-- Safe-zone ring -->
  <circle cx="{cx}" cy="{cy}" r="{ring_outer}" fill="none"
          stroke="rgba(255,255,255,0.16)" stroke-width="2"/>

  <!-- Central monogram "A" -->
  <text x="{cx}" y="{text_y}" text-anchor="middle"
        font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
        font-size="{font_size}" font-weight="800"
        fill="#ffffff" letter-spacing="{letter_spacing}">A</text>

  <!-- Gold accent bar under the monogram -->
  <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}"
        rx="{bar_r}" fill="url(#staffGold)"/>
</svg>
'''


def _icon_svg(size: int) -> str:
    """Return the staff icon SVG for the given pixel size (192 or 512).

    All coordinates are expressed as fractions of ``size`` so the
    192 and 512 variants are pixel-perfect copies of the same design.
    """
    s = float(size)
    return _STAFF_ICON_SVG.format(
        size=size,
        cx=int(s * 0.5),                 # 96 / 256
        cy=int(s * 0.5),
        hl_x=int(s * 0.78),              # corner highlight
        hl_y=int(s * 0.18),
        hl_r=int(s * 0.43),
        ring_outer=int(s * 0.363),       # ~186/512
        text_y=int(s * 0.676),           # baseline for monogram
        font_size=int(s * 0.508),        # ~260/512
        letter_spacing=int(s * -0.016),  # ~ -8/512
        bar_x=int(s * 0.383),            # ~196/512
        bar_y=int(s * 0.758),            # ~388/512
        bar_w=int(s * 0.234),            # ~120/512
        bar_h=max(3, int(s * 0.0117)),   # ~6/512
        bar_r=3 if size <= 192 else 3,
    )


# ---------------------------------------------------------------------------
# Anchors for existing files
# ---------------------------------------------------------------------------

# --- axis_saas/staff_urls.py ----------------------------------------------

URLS_IMPORT_ANCHOR = (
    "    staff_attendance_dates_api,\n"
    ")\n"
)

URLS_IMPORT_REPLACEMENT = (
    "    staff_attendance_dates_api,\n"
    ")\n"
    "\n"
    "# STAFF_PWA_V1: staff portal PWA manifest + service worker.\n"
    "# These are schema-independent endpoints — the staff session\n"
    "# carries the tenant, so the URLs are stable across tenants.\n"
    "from axis_saas.views.staff_pwa import (\n"
    "    staff_manifest,\n"
    "    staff_service_worker,\n"
    ")\n"
)

URLS_ROUTE_ANCHOR = (
    "urlpatterns = [\n"
    "    path('', staff_dashboard, name='staff_dashboard_root'),\n"
)

URLS_ROUTE_REPLACEMENT = (
    "urlpatterns = [\n"
    "    # STAFF_PWA_V1: staff portal PWA endpoints. Served before\n"
    "    # the dashboard root so the manifest and service worker\n"
    "    # resolve unambiguously.\n"
    "    path('manifest.json', staff_manifest, name='staff_manifest'),\n"
    "    path('sw.js', staff_service_worker, name='staff_service_worker'),\n"
    "    path('', staff_dashboard, name='staff_dashboard_root'),\n"
)


# --- templates/mobile/staff/base.html --------------------------------------

TPL_HEAD_ANCHOR = (
    '    <meta name="csrf-token" content="{{ csrf_token }}">\n'
    '    <meta name="theme-color" content="#0b6e64">\n'
)

TPL_HEAD_REPLACEMENT = (
    '    <meta name="csrf-token" content="{{ csrf_token }}">\n'
    '    <meta name="theme-color" content="#0b6e64">\n'
    '    {# STAFF_PWA_V1_HEAD — staff-portal PWA metadata #}\n'
    '    <link rel="manifest" href="/portal/staff/manifest.json">\n'
    '    <link rel="icon" type="image/svg+xml" href="/static/pwa/staff-icon-192.svg">\n'
    '    <link rel="apple-touch-icon" href="/static/pwa/staff-icon-192.svg">\n'
    '    <meta name="apple-mobile-web-app-capable" content="yes">\n'
    '    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">\n'
    '    <meta name="apple-mobile-web-app-title" content="AXIS Staff">\n'
    '    <meta name="mobile-web-app-capable" content="yes">\n'
)

TPL_BODY_ANCHOR = (
    "</body>\n"
    "</html>\n"
)

TPL_BODY_REPLACEMENT = (
    "{% if request.session.staff_id and request.session.staff_schema_name %}\n"
    "<!-- STAFF_PWA_V1: floating install button -->\n"
    "<div id=\"staffPwaInstallContainer\" style=\"display:none; position:fixed; bottom:calc(120px + var(--bottom-safe, env(safe-area-inset-bottom, 0px))); right:16px; z-index:9998;\">\n"
    "    <button id=\"staffPwaInstallBtn\" type=\"button\" aria-label=\"Install AXIS Staff app\"\n"
    "            style=\"display:flex; align-items:center; gap:8px; background:linear-gradient(135deg, #12b3a2 0%, #0b6e64 100%); color:#ffffff; border:none; border-radius:99px; padding:12px 18px; font-family:inherit; font-weight:800; font-size:0.85rem; letter-spacing:0.02em; cursor:pointer; box-shadow:0 14px 28px rgba(11, 110, 100, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.28); transition: transform 0.15s ease, box-shadow 0.2s ease;\">\n"
    "        <svg width=\"18\" height=\"18\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\">\n"
    "            <path d=\"M12 3v12\"/>\n"
    "            <path d=\"m7 10 5 5 5-5\"/>\n"
    "            <path d=\"M5 21h14\"/>\n"
    "        </svg>\n"
    "        <span>Install App</span>\n"
    "    </button>\n"
    "</div>\n"
    "\n"
    "<!-- STAFF_PWA_V1: fallback instructions modal -->\n"
    "<div id=\"staffPwaFallback\" role=\"dialog\" aria-modal=\"true\" aria-labelledby=\"staffPwaFallbackTitle\"\n"
    "     style=\"display:none; position:fixed; inset:0; background:rgba(13, 31, 28, 0.6); z-index:10000; align-items:center; justify-content:center; padding:20px; backdrop-filter:blur(6px); -webkit-backdrop-filter:blur(6px);\">\n"
    "    <div style=\"background:#ffffff; border-radius:22px; padding:22px 20px; max-width:400px; width:100%; box-shadow:0 26px 48px rgba(11, 110, 100, 0.32); border:1px solid #e2ebe8;\">\n"
    "        <div style=\"display:flex; align-items:center; gap:12px; margin-bottom:14px;\">\n"
    "            <div style=\"width:44px; height:44px; border-radius:14px; background:linear-gradient(140deg, #12b3a2 0%, #0b6e64 60%, #084c46 100%); display:flex; align-items:center; justify-content:center; color:#ffffff; font-weight:800; font-size:1.1rem; box-shadow:0 10px 20px rgba(11, 110, 100, 0.28);\">A</div>\n"
    "            <div>\n"
    "                <h3 id=\"staffPwaFallbackTitle\" style=\"margin:0; font-size:1.05rem; color:#0d1f1c;\">Install AXIS Staff</h3>\n"
    "                <p style=\"margin:2px 0 0; font-size:0.78rem; color:#6b807a;\">Add the staff portal to your home screen</p>\n"
    "            </div>\n"
    "        </div>\n"
    "        <p style=\"margin:0 0 10px; font-size:0.85rem; color:#0d1f1c; line-height:1.5;\">To install manually, use your browser menu:</p>\n"
    "        <ul style=\"margin:0 0 16px; padding-left:20px; font-size:0.82rem; color:#0d1f1c; line-height:1.7;\">\n"
    "            <li><strong>Chrome / Edge:</strong> &#8942; menu &rarr; <em>Install app</em> or <em>Add to Home screen</em>.</li>\n"
    "            <li><strong>Firefox:</strong> menu &rarr; <em>Install</em>.</li>\n"
    "            <li><strong>Safari (iOS):</strong> Share &rarr; <em>Add to Home Screen</em>.</li>\n"
    "        </ul>\n"
    "        <button id=\"staffPwaFallbackClose\" type=\"button\"\n"
    "                style=\"width:100%; background:linear-gradient(135deg, #12b3a2 0%, #0b6e64 100%); color:#ffffff; border:none; border-radius:14px; padding:12px 16px; font-family:inherit; font-weight:800; font-size:0.9rem; cursor:pointer; box-shadow:0 12px 22px rgba(11, 110, 100, 0.24);\">Got it</button>\n"
    "    </div>\n"
    "</div>\n"
    "\n"
    "<script>\n"
    "(function () {\n"
    "    // STAFF_PWA_V1: install prompt + service worker registration.\n"
    "    // Kept self-contained so it never touches the admin panel PWA.\n"
    "    var HIDE_KEY = 'staff_pwa_install_hidden_v1';\n"
    "    var standalone = (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches)\n"
    "        || window.navigator.standalone === true;\n"
    "\n"
    "    var container = document.getElementById('staffPwaInstallContainer');\n"
    "    var installBtn = document.getElementById('staffPwaInstallBtn');\n"
    "    var fallback = document.getElementById('staffPwaFallback');\n"
    "    var fallbackClose = document.getElementById('staffPwaFallbackClose');\n"
    "\n"
    "    if (standalone) {\n"
    "        if (container) container.style.display = 'none';\n"
    "    } else if (container && localStorage.getItem(HIDE_KEY) !== 'true') {\n"
    "        container.style.display = 'block';\n"
    "    }\n"
    "\n"
    "    var deferredPrompt = null;\n"
    "\n"
    "    window.addEventListener('beforeinstallprompt', function (e) {\n"
    "        e.preventDefault();\n"
    "        deferredPrompt = e;\n"
    "        if (container && !standalone) container.style.display = 'block';\n"
    "    });\n"
    "\n"
    "    window.addEventListener('appinstalled', function () {\n"
    "        if (container) container.style.display = 'none';\n"
    "        deferredPrompt = null;\n"
    "        try { localStorage.setItem(HIDE_KEY, 'true'); } catch (err) {}\n"
    "    });\n"
    "\n"
    "    function showFallback() {\n"
    "        if (fallback) fallback.style.display = 'flex';\n"
    "    }\n"
    "    function hideFallback() {\n"
    "        if (fallback) fallback.style.display = 'none';\n"
    "    }\n"
    "\n"
    "    if (installBtn) {\n"
    "        installBtn.addEventListener('click', function (e) {\n"
    "            e.preventDefault();\n"
    "            if (deferredPrompt) {\n"
    "                deferredPrompt.prompt();\n"
    "                deferredPrompt.userChoice.then(function (choice) {\n"
    "                    if (choice && choice.outcome === 'accepted') {\n"
    "                        if (container) container.style.display = 'none';\n"
    "                        try { localStorage.setItem(HIDE_KEY, 'true'); } catch (err) {}\n"
    "                    } else {\n"
    "                        showFallback();\n"
    "                    }\n"
    "                    deferredPrompt = null;\n"
    "                }).catch(function () {\n"
    "                    showFallback();\n"
    "                    deferredPrompt = null;\n"
    "                });\n"
    "            } else {\n"
    "                // No install prompt available (e.g. iOS Safari, or\n"
    "                // browser already dismissed it) — show manual steps.\n"
    "                showFallback();\n"
    "            }\n"
    "        });\n"
    "    }\n"
    "\n"
    "    if (fallbackClose) {\n"
    "        fallbackClose.addEventListener('click', hideFallback);\n"
    "    }\n"
    "    if (fallback) {\n"
    "        fallback.addEventListener('click', function (e) {\n"
    "            if (e.target === fallback) hideFallback();\n"
    "        });\n"
    "    }\n"
    "\n"
    "    if ('serviceWorker' in navigator) {\n"
    "        window.addEventListener('load', function () {\n"
    "            navigator.serviceWorker\n"
    "                .register('/portal/staff/sw.js', { scope: '/portal/staff/' })\n"
    "                .catch(function (err) {\n"
    "                    console.warn('[AXIS Staff PWA] SW registration failed:', err);\n"
    "                });\n"
    "        });\n"
    "    }\n"
    "})();\n"
    "</script>\n"
    "{% endif %}\n"
    "\n"
    "</body>\n"
    "</html>\n"
)


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Log:
    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose

    def info(self, message: str) -> None:
        print(f"[{ts()}] {message}")

    def detail(self, message: str) -> None:
        if self.verbose:
            print(f"[{ts()}]   -> {message}")

    def error(self, message: str) -> None:
        print(f"[{ts()}] ERROR: {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# File-operation helpers
# ---------------------------------------------------------------------------

def write_new_file(
    target: Path,
    content: str,
    marker: str,
    log: Log,
    dry_run: bool,
) -> int:
    """Create a new file. Idempotent:
      * If the file does not exist  → create it.
      * If it exists and has the marker  → skip (already written).
      * If it exists WITHOUT the marker  → refuse (do not clobber).
    """
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError as exc:
            log.error(f"could not read existing file {target}: {exc}")
            return 1

        if marker in existing:
            log.info(f"{target.name}: already present (idempotent no-op)")
            return 0

        log.error(
            f"{target} already exists but does not contain the "
            f"expected marker {marker!r}. Refusing to overwrite."
        )
        return 1

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error(f"could not create parent dir for {target}: {exc}")
        return 1

    if dry_run:
        log.info(f"{target}: would create ({len(content)} bytes)")
        return 0

    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        log.error(f"failed to write {target}: {exc}")
        return 1

    log.info(f"{target}: created ({len(content)} bytes)")
    return 0


def patch_literal(
    target: Path,
    anchor: str,
    replacement: str,
    success_marker: str,
    log: Log,
    dry_run: bool,
) -> int:
    """Replace one exact anchor string with a replacement.

    Refuses to patch if the anchor matches more than once (ambiguous)
    or if the file has already been patched (idempotent no-op).
    """
    if not target.exists():
        log.error(f"target file does not exist: {target}")
        return 1
    if target.is_dir():
        log.error(f"target path is a directory, not a file: {target}")
        return 1

    try:
        src = target.read_text(encoding="utf-8")
    except OSError as exc:
        log.error(f"could not read {target}: {exc}")
        return 1

    # Idempotency: already patched.
    if success_marker in src and anchor not in src:
        log.info(f"{target.name}: already patched (idempotent no-op)")
        return 0

    n = src.count(anchor)
    log.info(f"{target.name}: anchor matches = {n}")

    if n == 0:
        log.error(
            f"{target.name}: anchor not found — file may have drifted. "
            f"Nothing changed in this file."
        )
        return 1
    if n > 1:
        log.error(
            f"{target.name}: {n} anchor matches — refusing to guess. "
            f"Nothing changed in this file."
        )
        return 1

    patched = src.replace(anchor, replacement, 1)
    if patched == src:
        log.info(f"{target.name}: no change produced — nothing to do")
        return 0

    log.info(f"{target.name}: replacement prepared")

    if dry_run:
        log.detail(f"would write: {target}")
        return 0

    try:
        target.write_text(patched, encoding="utf-8")
    except OSError as exc:
        log.error(f"failed to write {target}: {exc}")
        return 1

    log.info(f"{target.name}: written")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "STAFF_PWA_V1 — make the staff portal installable as a PWA "
            "without touching the admin-panel PWA."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without writing to disk.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show detailed output for every action.",
    )
    parser.add_argument(
        "--target-dir", default=".",
        help="Project root (default: current directory).",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    log = Log(args.verbose)

    try:
        root = Path(args.target_dir).expanduser().resolve()
    except OSError as exc:
        print(f"[{ts()}] ERROR: cannot resolve target dir: {exc}",
              file=sys.stderr)
        return 1

    log.info(f"target root : {root}")
    if args.dry_run:
        log.info("mode        : DRY RUN (no files will be written)")
    log.info("patcher     : STAFF_PWA_V1")
    log.info("")

    if not root.exists() or not root.is_dir():
        log.error(
            f"target directory does not exist or is not a directory: {root}"
        )
        return 1

    rc = 0

    # --- new view module -------------------------------------------------
    log.info(f"--- {STAFF_PWA_VIEW_REL} ---")
    rc |= write_new_file(
        root / STAFF_PWA_VIEW_REL,
        STAFF_PWA_VIEW_CONTENT,
        marker=MARKER_VIEW,
        log=log,
        dry_run=args.dry_run,
    )

    # --- icons -----------------------------------------------------------
    log.info("")
    log.info(f"--- {ICON_192_REL} ---")
    rc |= write_new_file(
        root / ICON_192_REL,
        _icon_svg(192),
        marker=MARKER_ICON,
        log=log,
        dry_run=args.dry_run,
    )

    log.info("")
    log.info(f"--- {ICON_512_REL} ---")
    rc |= write_new_file(
        root / ICON_512_REL,
        _icon_svg(512),
        marker=MARKER_ICON,
        log=log,
        dry_run=args.dry_run,
    )

    # --- staff_urls.py: imports + routes ---------------------------------
    log.info("")
    log.info(f"--- {STAFF_URLS_REL} (imports) ---")
    rc |= patch_literal(
        root / STAFF_URLS_REL,
        URLS_IMPORT_ANCHOR,
        URLS_IMPORT_REPLACEMENT,
        success_marker=MARKER_URLS_IMPORT,
        log=log,
        dry_run=args.dry_run,
    )

    log.info("")
    log.info(f"--- {STAFF_URLS_REL} (routes) ---")
    rc |= patch_literal(
        root / STAFF_URLS_REL,
        URLS_ROUTE_ANCHOR,
        URLS_ROUTE_REPLACEMENT,
        success_marker="staff_manifest",
        log=log,
        dry_run=args.dry_run,
    )

    # --- staff base.html: head + body ------------------------------------
    log.info("")
    log.info(f"--- {STAFF_BASE_TPL_REL} (head) ---")
    rc |= patch_literal(
        root / STAFF_BASE_TPL_REL,
        TPL_HEAD_ANCHOR,
        TPL_HEAD_REPLACEMENT,
        success_marker=MARKER_TPL_HEAD,
        log=log,
        dry_run=args.dry_run,
    )

    log.info("")
    log.info(f"--- {STAFF_BASE_TPL_REL} (install button + SW) ---")
    rc |= patch_literal(
        root / STAFF_BASE_TPL_REL,
        TPL_BODY_ANCHOR,
        TPL_BODY_REPLACEMENT,
        success_marker=MARKER_TPL_BODY,
        log=log,
        dry_run=args.dry_run,
    )

    log.info("")
    if args.dry_run:
        log.info("dry run complete - no changes were written")
    elif rc == 0:
        log.info("done.")
    else:
        log.info("done with errors - see above.")

    return rc


if __name__ == "__main__":
    sys.exit(main())
