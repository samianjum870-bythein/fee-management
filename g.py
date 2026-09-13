#!/usr/bin/env python3
"""
axis_patcher.py
===============

LEAVE_ADMIN_APPROVE_FIX_01

Fixes the "Approve / Reject button does nothing" issue on the tenant
admin panel's Leave Management page
(`templates/tenant/leave_management.html`).

Root cause
----------
The current JS uses this pattern:

    function postJson(url, body) {
        return fetch(url, {...})
            .then(r => r.json().then(d => ({ ok: r.ok, data: d })));
    }

    window.approveLeave = function(id) {
        const remarks = prompt('Optional remarks for approval:', '');
        if (remarks === null) return;
        postJson('/portal/' + SCHEMA + '/leave/' + id + '/approve/', { remarks: remarks })
            .then(function(res) {
                if (!res.ok || !res.data.ok) { alert(...); return; }
                location.reload();
            });
        // <- no .catch() !
    };

If the server ever returns anything other than JSON — a 302 redirect
to the login page, a 500 HTML error page, a 403 CSRF failure page —
`r.json()` rejects, the whole promise chain rejects, and because there
is no `.catch()`, nothing happens. The user sees no error, no reload,
no change: exactly "the button doesn't work."

Also, `fetch()` is not given `credentials: 'same-origin'`. In most
browsers the default is fine, but making it explicit eliminates a
whole class of "session cookie not sent" failures.

Fix
---
  1. `postJson()` now reads the response as TEXT first, then tries to
     JSON.parse it. Non-JSON responses produce a descriptive Error that
     includes the HTTP status and the first 200 characters of the body,
     so the user can see what actually came back.

  2. `postJson()` explicitly sends `credentials: 'same-origin'` and an
     `X-Requested-With: XMLHttpRequest` header.

  3. `approveLeave()`, `rejectLeave()` and `savePolicy()` all get a
     `.catch()` handler that pops an alert with the underlying error
     and logs the full error to the browser console.

  4. Backwards-compatible: the success path (200 + {"ok": true}) is
     unchanged, so nothing else in the page needs to change.

Idempotent: re-running this patcher after the fix is a no-op (it
detects the new postJson body and skips).

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


TARGET_REL_PATH = Path('templates') / 'tenant' / 'leave_management.html'


# ------------------------------------------------------------------
# OLD — literal snippets taken verbatim from the current template.
# ------------------------------------------------------------------

OLD_POSTJSON = """    function postJson(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': CSRF
            },
            body: JSON.stringify(body || {})
        }).then(r => r.json().then(d => ({ ok: r.ok, data: d })));
    }
"""


OLD_APPROVE = """    window.approveLeave = function(id) {
        const remarks = prompt('Optional remarks for approval:', '');
        if (remarks === null) return;
        postJson('/portal/' + SCHEMA + '/leave/' + id + '/approve/', { remarks: remarks }).then(function(res) {
            if (!res.ok || !res.data.ok) {
                alert(res.data.error || 'Failed to approve.');
                return;
            }
            location.reload();
        });
    };
"""


OLD_REJECT = """    window.rejectLeave = function(id) {
        const remarks = prompt('Reason for rejection:', '');
        if (remarks === null) return;
        postJson('/portal/' + SCHEMA + '/leave/' + id + '/reject/', { remarks: remarks }).then(function(res) {
            if (!res.ok || !res.data.ok) {
                alert(res.data.error || 'Failed to reject.');
                return;
            }
            location.reload();
        });
    };
"""


OLD_SAVEPOLICY = """    window.savePolicy = function() {
        const body = {
            max_leaves_per_month: parseInt(document.getElementById('polMonth').value, 10) || 1,
            max_leaves_per_week: parseInt(document.getElementById('polWeek').value, 10) || 1,
            max_consecutive_days: parseInt(document.getElementById('polConsec').value, 10) || 1,
            allow_backdated: document.getElementById('polBack').checked
        };
        postJson('/portal/' + SCHEMA + '/leave/policy/save/', body).then(function(res) {
            if (!res.ok || !res.data.ok) {
                alert(res.data.error || 'Failed to save policy.');
                return;
            }
            closePolicy();
            location.reload();
        });
    };
"""


# ------------------------------------------------------------------
# NEW — robust replacements.
# ------------------------------------------------------------------

NEW_POSTJSON = """    function postJson(url, body) {
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


NEW_APPROVE = """    window.approveLeave = function(id) {
        const remarks = prompt('Optional remarks for approval:', '');
        if (remarks === null) return;
        postJson('/portal/' + SCHEMA + '/leave/' + id + '/approve/', { remarks: remarks })
            .then(function(res) {
                if (!res.ok || !res.data.ok) {
                    alert(res.data.error || 'Failed to approve.');
                    return;
                }
                location.reload();
            })
            .catch(function(err) {
                console.error('[approveLeave]', err);
                alert('Could not approve leave:\\n\\n' + err.message);
            });
    };
"""


NEW_REJECT = """    window.rejectLeave = function(id) {
        const remarks = prompt('Reason for rejection:', '');
        if (remarks === null) return;
        postJson('/portal/' + SCHEMA + '/leave/' + id + '/reject/', { remarks: remarks })
            .then(function(res) {
                if (!res.ok || !res.data.ok) {
                    alert(res.data.error || 'Failed to reject.');
                    return;
                }
                location.reload();
            })
            .catch(function(err) {
                console.error('[rejectLeave]', err);
                alert('Could not reject leave:\\n\\n' + err.message);
            });
    };
"""


NEW_SAVEPOLICY = """    window.savePolicy = function() {
        const body = {
            max_leaves_per_month: parseInt(document.getElementById('polMonth').value, 10) || 1,
            max_leaves_per_week: parseInt(document.getElementById('polWeek').value, 10) || 1,
            max_consecutive_days: parseInt(document.getElementById('polConsec').value, 10) || 1,
            allow_backdated: document.getElementById('polBack').checked
        };
        postJson('/portal/' + SCHEMA + '/leave/policy/save/', body)
            .then(function(res) {
                if (!res.ok || !res.data.ok) {
                    alert(res.data.error || 'Failed to save policy.');
                    return;
                }
                closePolicy();
                location.reload();
            })
            .catch(function(err) {
                console.error('[savePolicy]', err);
                alert('Could not save policy:\\n\\n' + err.message);
            });
    };
"""


# Marker used for idempotency check.
FIX_MARKER = "LEAVE_ADMIN_APPROVE_FIX_01"


# ------------------------------------------------------------------
# Patch
# ------------------------------------------------------------------

def _replace_once(content, old, new, label, verbose):
    """Return (new_content, changed). Skips if `new` already appears."""
    if old not in content:
        # If the `new` version is already there, no-op.
        if new.strip() and new.strip() in content:
            if verbose:
                log(f"  SKIP (already updated): {label}")
            return content, False
        log(f"  WARN: {label} — old snippet not found; leaving it alone.")
        return content, False
    content = content.replace(old, new, 1)
    if verbose:
        log(f"  patched: {label}")
    return content, True


def patch_template(path, dry_run, verbose):
    try:
        content = path.read_text(encoding='utf-8')
    except Exception as exc:
        log(f"ERROR reading {path}: {exc}")
        return False

    if FIX_MARKER in content:
        log(f"SKIP (already fixed): {path}")
        return True

    original = content
    changed_any = False

    log("  applying postJson()")
    content, c1 = _replace_once(content, OLD_POSTJSON, NEW_POSTJSON, "postJson", verbose)
    changed_any = changed_any or c1

    log("  applying approveLeave()")
    content, c2 = _replace_once(content, OLD_APPROVE, NEW_APPROVE, "approveLeave", verbose)
    changed_any = changed_any or c2

    log("  applying rejectLeave()")
    content, c3 = _replace_once(content, OLD_REJECT, NEW_REJECT, "rejectLeave", verbose)
    changed_any = changed_any or c3

    log("  applying savePolicy()")
    content, c4 = _replace_once(content, OLD_SAVEPOLICY, NEW_SAVEPOLICY, "savePolicy", verbose)
    changed_any = changed_any or c4

    if not changed_any:
        log(f"NO CHANGE: {path} (nothing to patch)")
        return True

    # Sanity: the marker should now be present (via NEW_POSTJSON comment).
    if FIX_MARKER not in content:
        # Embed it as a comment to make future runs idempotent even if
        # someone edits the code around it. We inject just above the
        # closing </script> of the block we touched.
        marker_comment = f"    // {FIX_MARKER}\n"
        # Put it right before the final `})();` — if we can find it.
        anchor = "    renderLeaves();\n})();"
        if anchor in content:
            content = content.replace(
                anchor,
                marker_comment + anchor,
                1,
            )

    if content == original:
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


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            'Make the Approve / Reject buttons on the tenant admin '
            'Leave Management page actually surface their errors and '
            'send credentials explicitly.'
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

    target = root / TARGET_REL_PATH
    if not target.is_file():
        log(f"ERROR: file not found: {target}")
        return 1

    log(f"Target: {target}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")

    ok = patch_template(target, args.dry_run, args.verbose)
    if not ok:
        return 2

    log("Done.")
    if args.dry_run:
        log("Re-run without --dry-run to apply.")
    else:
        log("Next steps:")
        log("  1. Hard-refresh the Leave Management page (Ctrl+Shift+R).")
        log("  2. Click Approve on a pending request.")
        log("     - If it still fails, the alert will now show the exact")
        log("       HTTP status and the beginning of the response body")
        log("       (e.g. 'HTTP 302' for a login redirect, or a Django")
        log("       500 traceback page).")
        log("     - Also check the browser console: the full error is")
        log("       logged under '[approveLeave]' / '[rejectLeave]'.")
        log("  3. If the alert says 'non-JSON response (HTTP 302)', your")
        log("     admin session expired — log out and back in.")
        log("  4. If it says 'HTTP 500', copy the snippet from the alert")
        log("     and check the Railway deploy logs for the traceback.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
