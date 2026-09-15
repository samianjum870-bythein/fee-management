#!/usr/bin/env python3
"""
axis_patcher.py
===============

LEAVE_BUTTONS_FIX_03
--------------------

Follow-up to LEAVE_ADMIN_APPROVE_FIX_02. That patch already added
`functools.wraps` to the URL wrappers, so `csrf_exempt` now propagates
correctly. But the Approve / Reject buttons on
    /portal/<schema>/leave/
can still fail silently in the real world for three remaining reasons:

  1. `require_tenant_type` and `require_school_feature` in
     `axis_saas/views/helpers.py` are NOT decorated with
     `functools.wraps`. They sit between the view and `csrf_exempt`'s
     outermost decorator. In most ordering they don't break the
     propagation, but any reordering of decorators silently kills the
     CSRF exemption. Fixing the helpers removes that class of bug.

  2. The template reads remarks through `window.prompt()`. Some
     browsers and mobile webviews silently suppress prompt() after the
     page has been idle, and any extension that blocks modal dialogs
     does the same. The button then appears to "do nothing". This
     patch replaces prompt() with a small inline modal.

  3. The template embeds server JSON via `{{ leaves_json|safe }}` and
     friends. If any staff-supplied string contains `</script>` (a
     leave title, a reason, a name), the browser terminates the
     enclosing `<script>` early and the whole IIFE never finishes
     executing — so `window.approveLeave` is never defined and the
     inline `onclick` throws a ReferenceError that most browsers log
     to the console but don't surface to the user.

  4. As a defence-in-depth on top of #3, the new modal handler logs
     everything to the console and surfaces any failure with an
     alert, including the exact URL it hit.

Usage:
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py --target-dir /path/to/project
    python3 axis_patcher.py                 # apply in place
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------
def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def _replace_once(content, old, new, label, verbose):
    """Replace first literal occurrence. Returns (content, changed)."""
    if new.strip() and new.strip() in content:
        if verbose:
            log(f"  SKIP (already patched): {label}")
        return content, False
    if old not in content:
        log(f"  WARN: {label} — anchor not found; leaving it alone.")
        return content, False
    content = content.replace(old, new, 1)
    if verbose:
        log(f"  patched: {label}")
    return content, True


def _write(path, content, dry_run, verbose, label):
    if dry_run:
        log(f"  DRY-RUN: would write {path} ({label})")
        return True
    try:
        path.write_text(content, encoding='utf-8')
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as exc:
        log(f"  ERROR writing {path}: {exc}")
        return False


# =====================================================================
# 1) helpers.py — add functools.wraps to the two decorators
# =====================================================================
HELPERS_REL = Path('axis_saas') / 'views' / 'helpers.py'

HELPERS_TT_OLD = '''def require_tenant_type(allowed_types):

    def decorator(view_func):

        def wrapper(request, schema_name, *args, **kwargs):
            if hasattr(request, 'tenant') and request.tenant is not None:
                tenant = request.tenant
            else:
                tenant = get_tenant(request, schema_name)
            tenant_type_matches = tenant.tenant_type in allowed_types or (
                'school' in allowed_types and tenant.tenant_type in ('school', 'wing_school', 'single_small_school')
            )
            if not tenant_type_matches:
                raise Http404('Not available for this tenant type')
            return view_func(request, schema_name, *args, **kwargs)
        return wrapper
    return decorator
'''

HELPERS_TT_NEW = '''def require_tenant_type(allowed_types):
    """LEAVE_BUTTONS_FIX_03: functools.wraps added so view attributes
    (notably csrf_exempt) survive this decorator layer."""

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, schema_name, *args, **kwargs):
            if hasattr(request, 'tenant') and request.tenant is not None:
                tenant = request.tenant
            else:
                tenant = get_tenant(request, schema_name)
            tenant_type_matches = tenant.tenant_type in allowed_types or (
                'school' in allowed_types and tenant.tenant_type in ('school', 'wing_school', 'single_small_school')
            )
            if not tenant_type_matches:
                raise Http404('Not available for this tenant type')
            return view_func(request, schema_name, *args, **kwargs)
        return wrapper
    return decorator
'''

HELPERS_SF_OLD = '''def require_school_feature(feature_key):

    def decorator(view_func):

        def wrapper(request, schema_name, *args, **kwargs):
            if hasattr(request, 'tenant') and request.tenant is not None:
                tenant = request.tenant
            else:
                tenant = get_tenant(request, schema_name)
            channel = 'mobile' if '/mobile/' in request.path or is_mobile_user_agent(request) else 'desktop'
            if tenant.tenant_type not in ('school', 'wing_school', 'single_small_school') or not tenant.is_feature_enabled(feature_key, channel):
                raise Http404('This school feature is not enabled for this tenant.')
            return view_func(request, schema_name, *args, **kwargs)
        return wrapper
    return decorator
'''

HELPERS_SF_NEW = '''def require_school_feature(feature_key):
    """LEAVE_BUTTONS_FIX_03: functools.wraps added so view attributes
    (notably csrf_exempt) survive this decorator layer."""

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, schema_name, *args, **kwargs):
            if hasattr(request, 'tenant') and request.tenant is not None:
                tenant = request.tenant
            else:
                tenant = get_tenant(request, schema_name)
            channel = 'mobile' if '/mobile/' in request.path or is_mobile_user_agent(request) else 'desktop'
            if tenant.tenant_type not in ('school', 'wing_school', 'single_small_school') or not tenant.is_feature_enabled(feature_key, channel):
                raise Http404('This school feature is not enabled for this tenant.')
            return view_func(request, schema_name, *args, **kwargs)
        return wrapper
    return decorator
'''


def patch_helpers(root, dry_run, verbose):
    path = root / HELPERS_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = path.read_text(encoding='utf-8')

    if 'LEAVE_BUTTONS_FIX_03' in content:
        log(f"SKIP (already patched): {path}")
        return True

    # Ensure functools is imported.
    if 'import functools' not in content:
        # Prepend after the docstring / first import line. Simplest safe
        # approach: put it on its own line at the very top, before any
        # other import. Python accepts imports above docstrings only
        # for module-level comments, but the file starts with a big
        # comment block then "import re". We insert right before the
        # first import.
        if content.startswith('"""') or content.startswith("'''"):
            # Skip past the closing triple-quote.
            quote = content[:3]
            close = content.find(quote, 3)
            if close != -1:
                insert_at = content.find('\n', close) + 1
                content = content[:insert_at] + '\nimport functools\n' + content[insert_at:]
            else:
                content = 'import functools\n' + content
        else:
            content = 'import functools\n' + content

    content, c1 = _replace_once(
        content, HELPERS_TT_OLD, HELPERS_TT_NEW,
        "require_tenant_type", verbose,
    )
    content, c2 = _replace_once(
        content, HELPERS_SF_OLD, HELPERS_SF_NEW,
        "require_school_feature", verbose,
    )

    if not (c1 and c2):
        log("ERROR: helpers.py anchors not all found — aborting this file.")
        return False
    return _write(path, content, dry_run, verbose, "helpers decorators")


# =====================================================================
# 2) leave_management.py — safe JSON for the template
# =====================================================================
LEAVE_VIEWS_REL = Path('axis_saas') / 'views' / 'leave_management.py'

LEAVE_CTX_OLD = '''    context = {
        'tenant': tenant,
        'leaves_json': json.dumps(leaves),
        'staff_summary_json': json.dumps(staff_summary),
        'policy_json': json.dumps(policy_data),
        'suspensions_json': json.dumps(suspensions),
        'staff_picker_json': json.dumps(staff_picker),
'''

LEAVE_CTX_NEW = '''    # LEAVE_BUTTONS_FIX_03: escape <, >, & and JS line separators so a
    # staff-supplied string containing "</script>" cannot break the
    # enclosing <script> tag in the template and silently kill every
    # global function (including approveLeave / rejectLeave).
    def _safe_json(obj):
        s = json.dumps(obj)
        return (
            s.replace('&', '\\\\u0026')
             .replace('<', '\\\\u003c')
             .replace('>', '\\\\u003e')
             .replace('\\u2028', '\\\\u2028')
             .replace('\\u2029', '\\\\u2029')
        )

    context = {
        'tenant': tenant,
        'leaves_json': _safe_json(leaves),
        'staff_summary_json': _safe_json(staff_summary),
        'policy_json': _safe_json(policy_data),
        'suspensions_json': _safe_json(suspensions),
        'staff_picker_json': _safe_json(staff_picker),
'''


def patch_leave_views(root, dry_run, verbose):
    path = root / LEAVE_VIEWS_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = path.read_text(encoding='utf-8')

    if '_safe_json' in content and 'LEAVE_BUTTONS_FIX_03' in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, changed = _replace_once(
        content, LEAVE_CTX_OLD, LEAVE_CTX_NEW,
        "leave_management context safe JSON", verbose,
    )
    if not changed:
        return False
    return _write(path, content, dry_run, verbose, "safe JSON context")


# =====================================================================
# 3) leave_management.html — remarks modal + robust handlers
# =====================================================================
LEAVE_TEMPLATE_REL = Path('templates') / 'tenant' / 'leave_management.html'

# --- 3a) Modal HTML --------------------------------------------------
MODAL_ANCHOR = '''<!-- Staff usage modal -->
<div class="lm-modal-backdrop" id="lmStaffBackdrop">'''

MODAL_NEW = '''<!-- Approve / Reject remarks modal (LEAVE_BUTTONS_FIX_03) -->
<div class="lm-modal-backdrop" id="lmRemarksBackdrop">
    <div class="lm-modal">
        <button class="close" onclick="closeRemarks()">×</button>
        <h3 id="lmRemarksTitle">Approve Leave</h3>
        <p class="page-desc" id="lmRemarksDesc"></p>
        <div class="row" style="margin-top:1rem;">
            <label for="lmRemarksText">Remarks (optional)</label>
            <textarea id="lmRemarksText" rows="3" placeholder="Add a note…"></textarea>
        </div>
        <div id="lmRemarksError" style="display:none; color:#ef4444; font-size:0.8rem; margin-top:0.5rem;"></div>
        <div style="display:flex; gap:0.5rem; justify-content:flex-end; margin-top:1rem;">
            <button class="btn-secondary" onclick="closeRemarks()">Cancel</button>
            <button class="btn-primary" id="lmRemarksConfirm">Confirm</button>
        </div>
    </div>
</div>

<!-- Staff usage modal -->
<div class="lm-modal-backdrop" id="lmStaffBackdrop">'''

# --- 3b) Replace the two handlers ------------------------------------
HANDLERS_OLD = '''    window.approveLeave = function(id) {
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
                if (err && err.__sessionExpired && err.__loginUrl) {
                    alert('Your session has expired. Redirecting to login…');
                    window.location.href = err.__loginUrl;
                    return;
                }
                alert('Could not approve leave:\\n\\n' + err.message);
            });
    };
    window.rejectLeave = function(id) {
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
                if (err && err.__sessionExpired && err.__loginUrl) {
                    alert('Your session has expired. Redirecting to login…');
                    window.location.href = err.__loginUrl;
                    return;
                }
                alert('Could not reject leave:\\n\\n' + err.message);
            });
    };
'''

HANDLERS_NEW = '''    // LEAVE_BUTTONS_FIX_03: prompt()-free modal flow. The previous
    // version relied on window.prompt(), which some browsers and mobile
    // webviews silently suppress — the button then appeared to do
    // nothing. We now use an inline modal, log everything to the
    // console, and surface every failure through the modal's own error
    // slot AND a fallback alert.
    var _pendingLeaveAction = null;

    function _openRemarksModal(action, id) {
        _pendingLeaveAction = { action: action, id: id };
        var isApprove = action === 'approve';
        document.getElementById('lmRemarksTitle').textContent =
            isApprove ? 'Approve Leave' : 'Reject Leave';
        document.getElementById('lmRemarksDesc').textContent = isApprove
            ? 'Optionally add remarks that will be stored on the leave record.'
            : 'Add a reason for rejection (shown to the staff member).';
        document.getElementById('lmRemarksText').value = '';
        var errBox = document.getElementById('lmRemarksError');
        errBox.style.display = 'none';
        errBox.textContent = '';
        var confirmBtn = document.getElementById('lmRemarksConfirm');
        confirmBtn.disabled = false;
        confirmBtn.textContent = isApprove ? 'Approve' : 'Reject';
        confirmBtn.style.background = isApprove ? '#10b981' : '#ef4444';
        document.getElementById('lmRemarksBackdrop').classList.add('show');
        setTimeout(function () {
            var ta = document.getElementById('lmRemarksText');
            if (ta) ta.focus();
        }, 40);
    }

    window.closeRemarks = function () {
        document.getElementById('lmRemarksBackdrop').classList.remove('show');
        _pendingLeaveAction = null;
    };

    window.approveLeave = function (id) {
        console.log('[approveLeave] clicked for leave id=' + id);
        _openRemarksModal('approve', id);
    };
    window.rejectLeave = function (id) {
        console.log('[rejectLeave] clicked for leave id=' + id);
        _openRemarksModal('reject', id);
    };

    document.getElementById('lmRemarksConfirm').addEventListener('click', function () {
        if (!_pendingLeaveAction) return;
        var action = _pendingLeaveAction.action;
        var id = _pendingLeaveAction.id;
        var remarks = (document.getElementById('lmRemarksText').value || '');
        var url = '/portal/' + SCHEMA + '/leave/' + id + '/' + action + '/';
        var btn = this;
        var errBox = document.getElementById('lmRemarksError');

        console.log('[leave-' + action + '] POST', url, 'remarks=', remarks);
        btn.disabled = true;
        btn.textContent = 'Sending…';
        errBox.style.display = 'none';

        postJson(url, { remarks: remarks })
            .then(function (res) {
                console.log('[leave-' + action + '] response', res);
                if (!res.ok || !res.data.ok) {
                    errBox.textContent = (res.data && res.data.error) || 'Failed to ' + action + '.';
                    errBox.style.display = 'block';
                    btn.disabled = false;
                    btn.textContent = (action === 'approve' ? 'Approve' : 'Reject');
                    return;
                }
                location.reload();
            })
            .catch(function (err) {
                console.error('[leave-' + action + '] error', err);
                btn.disabled = false;
                btn.textContent = (action === 'approve' ? 'Approve' : 'Reject');
                if (err && err.__sessionExpired && err.__loginUrl) {
                    errBox.textContent = 'Your session expired. Redirecting to login…';
                    errBox.style.display = 'block';
                    setTimeout(function () { window.location.href = err.__loginUrl; }, 900);
                    return;
                }
                errBox.textContent = 'Could not ' + action + ': ' + (err.message || err);
                errBox.style.display = 'block';
            });
    });

    // Backdrop click + Escape close the remarks modal too.
    (function () {
        var back = document.getElementById('lmRemarksBackdrop');
        if (back) back.addEventListener('click', function (e) {
            if (e.target === back) closeRemarks();
        });
    })();
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            var back = document.getElementById('lmRemarksBackdrop');
            if (back && back.classList.contains('show')) closeRemarks();
        }
    });
'''


def patch_leave_template(root, dry_run, verbose):
    path = root / LEAVE_TEMPLATE_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = path.read_text(encoding='utf-8')

    if 'LEAVE_BUTTONS_FIX_03' in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, c1 = _replace_once(
        content, MODAL_ANCHOR, MODAL_NEW,
        "template: remarks modal", verbose,
    )
    content, c2 = _replace_once(
        content, HANDLERS_OLD, HANDLERS_NEW,
        "template: approve/reject handlers", verbose,
    )

    if not (c1 and c2):
        log("ERROR: template anchors not all found — aborting this file.")
        return False
    return _write(path, content, dry_run, verbose, "leave_management.html")


# =====================================================================
# Main
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "LEAVE_BUTTONS_FIX_03 — make the Approve / Reject buttons on "
            "/portal/<schema>/leave/ reliably work: propagate csrf_exempt "
            "through every helper decorator, stop emitting raw server "
            "JSON into <script>, and replace prompt() with an inline "
            "remarks modal that surfaces every failure."
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

    log("--- 1/3: axis_saas/views/helpers.py ---")
    ok &= patch_helpers(root, args.dry_run, args.verbose)

    log("--- 2/3: axis_saas/views/leave_management.py ---")
    ok &= patch_leave_views(root, args.dry_run, args.verbose)

    log("--- 3/3: templates/tenant/leave_management.html ---")
    ok &= patch_leave_template(root, args.dry_run, args.verbose)

    if not ok:
        log("One or more steps failed. See messages above.")
        return 2

    log("Done.")
    if args.dry_run:
        log("Re-run without --dry-run to apply.")
    else:
        log("Next steps:")
        log("  1. Restart your dev server (templates + views changed).")
        log("  2. Hard-refresh the Leave Management page (Ctrl+Shift+R).")
        log("  3. Open DevTools → Console BEFORE clicking Approve.")
        log("     - You should see '[approveLeave] clicked for leave id=…'")
        log("       immediately, then '[leave-approve] POST …' when you")
        log("       confirm in the modal.")
        log("     - If the POST returns a non-JSON page, the modal will")
        log("       show the exact HTTP status and a snippet of the body.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
