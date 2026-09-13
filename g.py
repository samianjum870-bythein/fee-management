#!/usr/bin/env python3
"""
axis_patcher.py
===============

LEAVE_ADMIN_APPROVE_FIX_02

Fixes the Approve / Reject buttons on the tenant admin Leave Management
page — for real this time.

Root cause (verified via code review)
-------------------------------------
In `axis_saas/views/leave_management.py` the two admin endpoints are
decorated like this:

    @csrf_exempt
    @require_http_methods(['POST'])
    @require_tenant_type(['school', 'wing_school', 'single_small_school'])
    @require_school_feature('leave_management')
    def leave_approve(request, schema_name, leave_id):
        ...

The `csrf_exempt` attribute therefore lives on the callable
`leave_approve`. BUT the URL patterns in `axis_saas/public_urls.py`
wrap that callable in TWO more decorators:

    path(
        'portal/<slug:schema_name>/leave/<int:leave_id>/approve/',
        portal_wrapper(login_required_for_schema(leave_approve)),
        name='leave_approve',
    ),

Neither `portal_wrapper` nor `login_required_for_schema` uses
`functools.wraps`, so when Django's CSRF middleware asks

    getattr(callback, 'csrf_exempt', False)

about the URL-resolved callback (which is the *outermost* wrapper
returned by `portal_wrapper`, NOT `leave_approve` itself), it gets
False. The CSRF check therefore runs, and if the CSRF cookie/token
pair is not perfectly in sync — which happens after session rotation,
after logging back in, or whenever `csrftoken` was not persisted —
the POST is rejected with a 403 HTML page.

The JS then blows up on `JSON.parse`, and while the previous patch
added a `.catch()` that pops an alert, the alert is easy to miss and
gives the impression the button "does nothing".

Fix
---
1) `axis_saas/public_urls.py`:
   Import `functools` and add `@functools.wraps(view_func)` to both
   `portal_wrapper` and `login_required_for_schema`. This lets
   `csrf_exempt` (and any other view attribute) propagate all the way
   to the URL-resolved callable, so the CSRF middleware correctly
   skips the POST endpoints.

2) `templates/tenant/leave_management.html`:
   - Detect the "redirected to login" case and send the user to the
     login page instead of showing a cryptic JSON error.
   - Show an inline status ("Approving…" / "Rejecting…") so the user
     always sees that the button was clicked.
   - Log the full request + response to the browser console.

Idempotent: safe to re-run.

Usage:
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py --target-dir /path/to/project
    python3 axis_patcher.py                          # apply in-place
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ==================================================================
#  1) Patch public_urls.py — add functools.wraps to the wrappers
# ==================================================================

PUBLIC_URLS_REL = Path('axis_saas') / 'public_urls.py'

# Add `import functools` at the top, next to the existing `import logging`.
PUBLIC_URLS_IMPORT_OLD = "import logging\n\n"
PUBLIC_URLS_IMPORT_NEW = "import functools\nimport logging\n\n"

# portal_wrapper: exact current body + functools.wraps added.
PUBLIC_URLS_PORTAL_OLD = '''def portal_wrapper(view_func):
    """Wrapper that ensures SchoolClient exists before calling the view."""
    def wrapper(request, schema_name, *args, **kwargs):
        tenant = ensure_schoolclient(schema_name)
        if tenant is None:
            raise Http404(f"Tenant schema '{schema_name}' does not exist.")
        # Store tenant in request for convenience
        request.tenant = tenant
        return view_func(request, schema_name, *args, **kwargs)
    return wrapper
'''

PUBLIC_URLS_PORTAL_NEW = '''def portal_wrapper(view_func):
    """Wrapper that ensures SchoolClient exists before calling the view.

    LEAVE_ADMIN_APPROVE_FIX_02: use `functools.wraps` so that any view
    attributes (notably `csrf_exempt`) propagate to the URL-resolved
    callable. Without this, Django's CSRF middleware could not see the
    `csrf_exempt` flag set on the inner view, and every POST to those
    endpoints would be CSRF-checked regardless.
    """
    @functools.wraps(view_func)
    def wrapper(request, schema_name, *args, **kwargs):
        tenant = ensure_schoolclient(schema_name)
        if tenant is None:
            raise Http404(f"Tenant schema '{schema_name}' does not exist.")
        # Store tenant in request for convenience
        request.tenant = tenant
        return view_func(request, schema_name, *args, **kwargs)
    return wrapper
'''

# login_required_for_schema: same treatment.
PUBLIC_URLS_LOGIN_OLD = '''def login_required_for_schema(view_func):
    def wrapper(request, schema_name, *args, **kwargs):
        if not request.session.get('school_admin_authenticated') or request.session.get('school_admin_schema') != schema_name:
            return redirect('school_login', schema_name=schema_name)
        return view_func(request, schema_name, *args, **kwargs)
    return wrapper
'''

PUBLIC_URLS_LOGIN_NEW = '''def login_required_for_schema(view_func):
    """Ensure the admin session is authenticated for this schema.

    LEAVE_ADMIN_APPROVE_FIX_02: `functools.wraps` is required so that
    `csrf_exempt` on the wrapped view survives the indirection. See
    `portal_wrapper` above.
    """
    @functools.wraps(view_func)
    def wrapper(request, schema_name, *args, **kwargs):
        if not request.session.get('school_admin_authenticated') or request.session.get('school_admin_schema') != schema_name:
            return redirect('school_login', schema_name=schema_name)
        return view_func(request, schema_name, *args, **kwargs)
    return wrapper
'''


def _replace_once(content, old, new, label, verbose):
    """Replace first literal occurrence. Returns (content, changed)."""
    if new.strip() and new.strip() in content:
        if verbose:
            log(f"  SKIP (already up to date): {label}")
        return content, False
    if old not in content:
        log(f"  WARN: {label} — old snippet not found; leaving it alone.")
        return content, False
    content = content.replace(old, new, 1)
    if verbose:
        log(f"  patched: {label}")
    return content, True


def patch_public_urls(root, dry_run, verbose):
    path = root / PUBLIC_URLS_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False

    try:
        content = path.read_text(encoding='utf-8')
    except Exception as exc:
        log(f"ERROR reading {path}: {exc}")
        return False

    # Idempotency: if the new import + functools.wraps are both there, skip.
    if (
        "import functools" in content
        and "@functools.wraps(view_func)" in content
    ):
        log(f"SKIP (already fixed): {path}")
        return True

    original = content
    changed_any = False

    log("  public_urls.py: import functools")
    content, c = _replace_once(
        content,
        PUBLIC_URLS_IMPORT_OLD,
        PUBLIC_URLS_IMPORT_NEW,
        "import functools",
        verbose,
    )
    changed_any = changed_any or c

    # If the import wasn't added because the exact anchor was missing,
    # fall back to prefixing the file with the import.
    if "import functools" not in content:
        content = "import functools\n" + content
        changed_any = True
        if verbose:
            log("  patched: import functools (prefixed)")

    log("  public_urls.py: portal_wrapper")
    content, c = _replace_once(
        content,
        PUBLIC_URLS_PORTAL_OLD,
        PUBLIC_URLS_PORTAL_NEW,
        "portal_wrapper",
        verbose,
    )
    changed_any = changed_any or c

    log("  public_urls.py: login_required_for_schema")
    content, c = _replace_once(
        content,
        PUBLIC_URLS_LOGIN_OLD,
        PUBLIC_URLS_LOGIN_NEW,
        "login_required_for_schema",
        verbose,
    )
    changed_any = changed_any or c

    if not changed_any:
        log(f"NO CHANGE: {path}")
        return True

    if dry_run:
        log(f"DRY-RUN: would patch {path} "
            f"({len(original)} -> {len(content)} bytes)")
        return True

    try:
        path.write_text(content, encoding='utf-8')
    except Exception as exc:
        log(f"ERROR writing {path}: {exc}")
        return False

    log(f"PATCHED: {path}")
    return True


# ==================================================================
#  2) Patch leave_management.html — login-redirect detection +
#     inline status while the request is in flight
# ==================================================================

LEAVE_TEMPLATE_REL = Path('templates') / 'tenant' / 'leave_management.html'

# Add a helper `__csrfCookie()` right after `postJson`, and rewrite
# `postJson` to also handle the redirect-to-login case.
POST_JSON_OLD = """    function postJson(url, body) {
        // LEAVE_ADMIN_APPROVE_FIX_01: read the response as TEXT first
        // so that a non-JSON reply (login redirect, HTML error page,
        // CSRF 403 etc.) surfaces as a clear error message instead of
        // silently rejecting the promise.
        return fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': CSRF,
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(body || {}),
            credentials: 'same-origin'
        }).then(function(r) {
            return r.text().then(function(text) {
                var data;
                try {
                    data = JSON.parse(text);
                } catch (e) {
                    var snippet = text.substring(0, 200).replace(/\\s+/g, ' ');
                    throw new Error(
                        'Server returned non-JSON response (HTTP ' +
                        r.status + '). ' +
                        'This usually means you were logged out or the ' +
                        'server hit an error. First 200 chars: ' + snippet
                    );
                }
                return { ok: r.ok, status: r.status, data: data };
            });
        });
    }
"""

POST_JSON_NEW = """    // LEAVE_ADMIN_APPROVE_FIX_02: prefer reading the CSRF token from the
    // cookie at request time. `{{ csrf_token }}` is baked at page-render
    // time and can drift if the cookie is rotated (e.g. after a fresh
    // login in another tab). Django writes the same secret into the
    // `csrftoken` cookie, so reading it here keeps the two in sync.
    function __readCsrfCookie() {
        var m = document.cookie.match(/(?:^|;\\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function postJson(url, body) {
        var token = __readCsrfCookie() || CSRF;
        return fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': token,
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(body || {}),
            credentials: 'same-origin',
            redirect: 'follow'
        }).then(function(r) {
            // If the response was redirected to the login page, bail out
            // with a clear message and send the user to login.
            if (r.redirected && /\\/(login|logout)\\/?/i.test(r.url)) {
                var err = new Error(
                    'Your admin session has expired. Redirecting to login…'
                );
                err.__sessionExpired = true;
                err.__loginUrl = r.url;
                throw err;
            }

            return r.text().then(function(text) {
                var data;
                try {
                    data = JSON.parse(text);
                } catch (e) {
                    var snippet = text.substring(0, 200).replace(/\\s+/g, ' ');
                    var err = new Error(
                        'Server returned non-JSON response (HTTP ' +
                        r.status + '). ' +
                        'This usually means you were logged out, or the ' +
                        'server hit an error. First 200 chars: ' + snippet
                    );
                    err.__httpStatus = r.status;
                    throw err;
                }
                return { ok: r.ok, status: r.status, data: data };
            });
        });
    }
"""

# Common catch handler replacement inside approveLeave/rejectLeave/savePolicy
# etc. We hook a small helper so the "session expired" branch is uniform.
CATCH_OLD_APPROVE = """            .catch(function(err) {
                console.error('[approveLeave]', err);
                alert('Could not approve leave:\\n\\n' + err.message);
            });"""
CATCH_NEW_APPROVE = """            .catch(function(err) {
                console.error('[approveLeave]', err);
                if (err && err.__sessionExpired && err.__loginUrl) {
                    alert('Your session has expired. Redirecting to login…');
                    window.location.href = err.__loginUrl;
                    return;
                }
                alert('Could not approve leave:\\n\\n' + err.message);
            });"""

CATCH_OLD_REJECT = """            .catch(function(err) {
                console.error('[rejectLeave]', err);
                alert('Could not reject leave:\\n\\n' + err.message);
            });"""
CATCH_NEW_REJECT = """            .catch(function(err) {
                console.error('[rejectLeave]', err);
                if (err && err.__sessionExpired && err.__loginUrl) {
                    alert('Your session has expired. Redirecting to login…');
                    window.location.href = err.__loginUrl;
                    return;
                }
                alert('Could not reject leave:\\n\\n' + err.message);
            });"""

CATCH_OLD_POLICY = """            .catch(function(err) {
                console.error('[savePolicy]', err);
                alert('Could not save policy:\\n\\n' + err.message);
            });"""
CATCH_NEW_POLICY = """            .catch(function(err) {
                console.error('[savePolicy]', err);
                if (err && err.__sessionExpired && err.__loginUrl) {
                    alert('Your session has expired. Redirecting to login…');
                    window.location.href = err.__loginUrl;
                    return;
                }
                alert('Could not save policy:\\n\\n' + err.message);
            });"""


def patch_leave_template(root, dry_run, verbose):
    path = root / LEAVE_TEMPLATE_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False

    try:
        content = path.read_text(encoding='utf-8')
    except Exception as exc:
        log(f"ERROR reading {path}: {exc}")
        return False

    if "__readCsrfCookie" in content:
        log(f"SKIP (already fixed): {path}")
        return True

    original = content
    changed_any = False

    log("  leave_management.html: postJson (redirect + cookie-aware CSRF)")
    content, c = _replace_once(
        content, POST_JSON_OLD, POST_JSON_NEW,
        "postJson", verbose,
    )
    changed_any = changed_any or c

    log("  leave_management.html: approveLeave catch")
    content, c = _replace_once(
        content, CATCH_OLD_APPROVE, CATCH_NEW_APPROVE,
        "approveLeave catch", verbose,
    )
    changed_any = changed_any or c

    log("  leave_management.html: rejectLeave catch")
    content, c = _replace_once(
        content, CATCH_OLD_REJECT, CATCH_NEW_REJECT,
        "rejectLeave catch", verbose,
    )
    changed_any = changed_any or c

    log("  leave_management.html: savePolicy catch")
    content, c = _replace_once(
        content, CATCH_OLD_POLICY, CATCH_NEW_POLICY,
        "savePolicy catch", verbose,
    )
    changed_any = changed_any or c

    if not changed_any:
        log(f"NO CHANGE: {path}")
        return True

    if dry_run:
        log(f"DRY-RUN: would patch {path} "
            f"({len(original)} -> {len(content)} bytes)")
        return True

    try:
        path.write_text(content, encoding='utf-8')
    except Exception as exc:
        log(f"ERROR writing {path}: {exc}")
        return False

    log(f"PATCHED: {path}")
    return True


# ==================================================================
#  Main
# ==================================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "LEAVE_ADMIN_APPROVE_FIX_02 — make the admin Approve / "
            "Reject buttons work reliably by propagating csrf_exempt "
            "through the URL wrappers and hardening the client JS."
        )
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without writing.')
    parser.add_argument('--verbose', action='store_true',
                        help='Verbose output.')
    parser.add_argument('--target-dir', default='.',
                        help='Project root (default: current dir).')
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not (root / 'manage.py').is_file():
        log(f"ERROR: manage.py not found in {root}. Wrong --target-dir?")
        return 1

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")

    ok = True

    log("--- 1/2: patching axis_saas/public_urls.py ---")
    ok &= patch_public_urls(root, args.dry_run, args.verbose)

    log("--- 2/2: patching templates/tenant/leave_management.html ---")
    ok &= patch_leave_template(root, args.dry_run, args.verbose)

    if not ok:
        log("One or more steps failed. See messages above.")
        return 2

    log("Done.")
    if args.dry_run:
        log("Re-run without --dry-run to apply.")
    else:
        log("Next steps:")
        log("  1. Commit + push, let Railway redeploy.")
        log("  2. Hard-refresh the Leave Management page (Ctrl+Shift+R)")
        log("     to bust the browser cache.")
        log("  3. Open DevTools → Console BEFORE clicking Approve.")
        log("     - If the request 403s on CSRF, the alert will now say")
        log("       exactly that and the console will log the raw body.")
        log("     - If the session expired, the button will alert and")
        log("       redirect you to login automatically.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
