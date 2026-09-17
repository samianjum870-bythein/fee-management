#!/usr/bin/env python3
"""
axis_patcher.py — ATTENDANCE_PERMS_UNIVERSAL_V1
=================================================

Adds a "Universal Class Teacher Permissions" block to the top of the
existing Class Teacher Permissions modal, so the admin can set the
same default attendance authority for EVERY class in one click, while
still being able to override individual classes below.

What this patcher does
----------------------
1.  Adds a new bulk-save endpoint to `axis_saas/views/admin_attendence.py`:

        POST /portal/<schema>/api/attendance/class-teacher-permissions/bulk-save/

    Body:
        {
          "backdate_access":     "none" | "read" | "read_write",
          "max_edits_per_date":  int,
          "view_history_days":   int,
          "edit_history_days":   int
        }

    It applies the same permission row to every active class that has
    an assigned class teacher, and creates the row when one does not
    exist yet.  Returns {ok, updated, classes: [...ids]}.

2.  Registers the URL route in `axis_saas/public_urls.py`.

3.  Enhances `templates/tenant/attendence.html`:

        * Inserts a "Universal / Default Settings" card ABOVE the
          per-class table inside the existing `attPermModal`.
        * The card has the four fields + an
          "Apply to All Classes" primary button + a
          "Copy from an existing class" quick-fill (dropdown).
        * The card is collapsible so the admin can still jump straight
          to the per-class overrides.

Idempotent. Safe to re-run. Never deletes or overwrites unrelated code.

Usage
-----
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py
    python3 axis_patcher.py --target-dir /srv/fee_management
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


MARKER = "ATTENDANCE_PERMS_UNIVERSAL_V1"


# ------------------------------------------------------------------ utils

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log(f"  ERROR: not found: {path}")
        return None
    except Exception as e:
        log(f"  ERROR reading {path}: {e}")
        return None


def write_file(path, content, dry_run=False, label=""):
    if dry_run:
        log(f"  DRY-RUN: would write {path} ({label})")
        return True
    try:
        path.write_text(content, encoding="utf-8")
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as e:
        log(f"  ERROR writing {path}: {e}")
        return False


def replace_once(content, old, new, label=""):
    """Replace the first occurrence of `old`. Returns (new_content, ok)."""
    if old not in content:
        return content, False
    return content.replace(old, new, 1), True


# =====================================================================
# 1. admin_attendence.py — add bulk-save endpoint
# =====================================================================

BULK_SAVE_VIEW = '''

@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_class_teacher_permissions_bulk_save_api(
    request, schema_name,
):
    """ATTENDANCE_PERMS_UNIVERSAL_V1

    Apply the same attendance permission settings to EVERY active
    class that has an assigned class teacher, in one shot.

    Body:
        {
          "backdate_access":    "none" | "read" | "read_write",
          "max_edits_per_date": int,
          "view_history_days":  int,
          "edit_history_days":  int
        }

    Returns:
        {
          "ok": True,
          "updated":  N,
          "classes":  [class_id, class_id, ...]
        }

    Individual per-class overrides are still possible through the
    existing `/save/` endpoint; this endpoint only writes the universal
    default that the admin chooses.
    """
    from ..models import ClassTeacherAttendancePermission

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    def _int(key, default, lo=0, hi=None):
        try:
            v = int(body.get(key, default))
        except (TypeError, ValueError):
            v = default
        v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    backdate_access = (body.get('backdate_access') or 'none').strip()
    if backdate_access not in ('none', 'read', 'read_write'):
        backdate_access = 'none'

    admin_name = request.session.get('school_admin_username', 'admin')

    with schema_context(schema_name):
        with transaction.atomic():
            classes = list(
                SchoolClass.objects
                .filter(is_active=True, class_teacher__isnull=False)
                .only('id')
            )
            updated_ids = []
            for cls in classes:
                p = ClassTeacherAttendancePermission.for_class(cls)
                p.backdate_access = backdate_access
                p.max_edits_per_date = _int(
                    'max_edits_per_date', p.max_edits_per_date, 0, 50,
                )
                p.view_history_days = _int(
                    'view_history_days', p.view_history_days, 0, 730,
                )
                p.edit_history_days = _int(
                    'edit_history_days', p.edit_history_days, 0, 730,
                )
                p.updated_by = admin_name
                p.save()
                updated_ids.append(cls.id)

    return JsonResponse({
        'ok': True,
        'updated': len(updated_ids),
        'classes': updated_ids,
        'backdate_access': backdate_access,
    })
'''


def patch_admin_attendance(root, args):
    path = root / "axis_saas" / "views" / "admin_attendence.py"
    content = read_file(path)
    if content is None:
        return False

    if "admin_attendance_class_teacher_permissions_bulk_save_api" in content:
        log(f"  SKIP (already applied): bulk-save view in {path}")
        return True

    # Append at end of file (after the last view).
    content = content.rstrip() + "\n" + BULK_SAVE_VIEW
    return write_file(path, content, args.dry_run,
                      "add bulk-save endpoint")


# =====================================================================
# 2. public_urls.py — register the new route
# =====================================================================

def patch_public_urls(root, args):
    path = root / "axis_saas" / "public_urls.py"
    content = read_file(path)
    if content is None:
        return False

    if "admin_attendance_class_teacher_permissions_bulk_save_api" in content:
        log(f"  SKIP (already applied): bulk-save route in {path}")
        return True

    # --- extend the import block -------------------------------------
    old_import = (
        "    admin_attendance_class_teacher_permissions_save_api,\n"
        "    admin_attendance_daily_logs_api,\n"
    )
    new_import = (
        "    admin_attendance_class_teacher_permissions_save_api,\n"
        "    # ATTENDANCE_PERMS_UNIVERSAL_V1\n"
        "    admin_attendance_class_teacher_permissions_bulk_save_api,\n"
        "    admin_attendance_daily_logs_api,\n"
    )
    content, ok = replace_once(content, old_import, new_import,
                               label="import block")
    if not ok:
        log(f"  WARN: could not extend import block in {path}")
        return False

    # --- add the route ------------------------------------------------
    old_route = (
        "    path('portal/<slug:schema_name>/api/attendance/"
        "class-teacher-permissions/save/', "
        "portal_wrapper(login_required_for_schema("
        "admin_attendance_class_teacher_permissions_save_api)), "
        "name='admin_attendance_class_teacher_permissions_save_api'),\n"
    )
    new_route = old_route + (
        "    # ATTENDANCE_PERMS_UNIVERSAL_V1\n"
        "    path('portal/<slug:schema_name>/api/attendance/"
        "class-teacher-permissions/bulk-save/', "
        "portal_wrapper(login_required_for_schema("
        "admin_attendance_class_teacher_permissions_bulk_save_api)), "
        "name='admin_attendance_class_teacher_permissions_bulk_save_api'),\n"
    )
    content, ok = replace_once(content, old_route, new_route,
                               label="route block")
    if not ok:
        log(f"  WARN: could not add route block in {path}")
        return False

    return write_file(path, content, args.dry_run,
                      "register bulk-save route")


# =====================================================================
# 3. templates/tenant/attendence.html — universal block in modal
# =====================================================================

# ---------------------------------------------------------------------
# The universal block markup that gets injected just above the
# `#attPermList` container.  Uses the existing modal CSS classes so
# the look is consistent.
# ---------------------------------------------------------------------

UNIVERSAL_BLOCK = r'''
            <!-- ============ ATTENDANCE_PERMS_UNIVERSAL_V1 ============ -->
            <div id="attPermUniversal"
                 style="border-bottom:1px solid var(--border);
                        background:var(--surface-alt);">
                <div style="display:flex; justify-content:space-between;
                            align-items:center; gap:.5rem;
                            padding:.85rem 1.2rem; cursor:pointer;"
                     onclick="AXIS_ADMIN_ATT_PERMS.toggleUniversal()">
                    <div>
                        <div style="font-weight:800; font-size:.95rem;">
                            🌐 Universal Settings
                            <span style="font-weight:600; color:var(--muted);
                                         font-size:.75rem; margin-left:.4rem;">
                                (Apply the same defaults to every class)
                            </span>
                        </div>
                        <div style="font-size:.72rem; color:var(--muted);
                                    margin-top:.15rem;">
                            Set once here, then override individual classes
                            below if you need to.
                        </div>
                    </div>
                    <button type="button" id="attPermUniversalToggle"
                            style="background:transparent; border:1px solid var(--border);
                                   color:var(--text); border-radius:.5rem;
                                   padding:.35rem .7rem; font-weight:700;
                                   font-size:.78rem; cursor:pointer;">
                        Show ▾
                    </button>
                </div>

                <div id="attPermUniversalBody" style="display:none;
                        padding:.4rem 1.2rem 1.1rem;">
                    <div style="display:grid;
                                grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));
                                gap:.75rem; align-items:end;">
                        <div>
                            <div style="font-size:.7rem; font-weight:800;
                                        color:var(--muted); text-transform:uppercase;
                                        letter-spacing:.05em; margin-bottom:.25rem;">
                                Backdate Access
                            </div>
                            <select id="attPermUniBackdate"
                                    style="width:100%; padding:.5rem .6rem;
                                           border-radius:.5rem;
                                           border:1px solid var(--border);
                                           background:var(--surface);
                                           color:var(--text);">
                                <option value="none">Today only</option>
                                <option value="read">Read only</option>
                                <option value="read_write">Read &amp; Write</option>
                            </select>
                        </div>
                        <div>
                            <div style="font-size:.7rem; font-weight:800;
                                        color:var(--muted); text-transform:uppercase;
                                        letter-spacing:.05em; margin-bottom:.25rem;">
                                Max Edits / Date
                            </div>
                            <input type="number" id="attPermUniMaxEdits"
                                   min="0" max="50" value="1"
                                   style="width:100%; padding:.5rem .6rem;
                                          border-radius:.5rem;
                                          border:1px solid var(--border);
                                          background:var(--surface);
                                          color:var(--text);">
                        </div>
                        <div>
                            <div style="font-size:.7rem; font-weight:800;
                                        color:var(--muted); text-transform:uppercase;
                                        letter-spacing:.05em; margin-bottom:.25rem;">
                                View History Days
                            </div>
                            <input type="number" id="attPermUniViewDays"
                                   min="0" max="730" value="30"
                                   style="width:100%; padding:.5rem .6rem;
                                          border-radius:.5rem;
                                          border:1px solid var(--border);
                                          background:var(--surface);
                                          color:var(--text);">
                        </div>
                        <div>
                            <div style="font-size:.7rem; font-weight:800;
                                        color:var(--muted); text-transform:uppercase;
                                        letter-spacing:.05em; margin-bottom:.25rem;">
                                Edit History Days
                            </div>
                            <input type="number" id="attPermUniEditDays"
                                   min="0" max="730" value="5"
                                   style="width:100%; padding:.5rem .6rem;
                                          border-radius:.5rem;
                                          border:1px solid var(--border);
                                          background:var(--surface);
                                          color:var(--text);">
                        </div>
                    </div>

                    <div style="display:flex; flex-wrap:wrap; gap:.5rem;
                                align-items:center; margin-top:.9rem;">
                        <label style="font-size:.72rem; font-weight:800;
                                      color:var(--muted); text-transform:uppercase;
                                      letter-spacing:.05em;">
                            Copy from
                        </label>
                        <select id="attPermUniCopyFrom"
                                style="padding:.4rem .55rem;
                                       border-radius:.5rem;
                                       border:1px solid var(--border);
                                       background:var(--surface);
                                       color:var(--text); font-size:.82rem;
                                       min-width:180px;">
                            <option value="">— pick an existing class —</option>
                        </select>
                        <button type="button"
                                onclick="AXIS_ADMIN_ATT_PERMS.fillFromClass()"
                                style="background:var(--surface-alt);
                                       color:var(--text);
                                       border:1px solid var(--border);
                                       border-radius:.5rem;
                                       padding:.4rem .8rem;
                                       font-weight:700; font-size:.78rem;
                                       cursor:pointer;">
                            Fill
                        </button>
                        <span style="flex:1;"></span>
                        <button type="button"
                                onclick="AXIS_ADMIN_ATT_PERMS.resetUniversal()"
                                style="background:var(--surface);
                                       color:var(--text);
                                       border:1px solid var(--border);
                                       border-radius:.55rem;
                                       padding:.55rem 1rem;
                                       font-weight:700; font-size:.82rem;
                                       cursor:pointer;">
                            Reset
                        </button>
                        <button type="button"
                                onclick="AXIS_ADMIN_ATT_PERMS.applyUniversal()"
                                style="background:var(--primary);
                                       color:#fff;
                                       border:1px solid var(--primary);
                                       border-radius:.55rem;
                                       padding:.55rem 1.1rem;
                                       font-weight:700; font-size:.82rem;
                                       cursor:pointer;">
                            ✔ Apply to All Classes
                        </button>
                    </div>

                    <div id="attPermUniMsg"
                         style="margin-top:.6rem; font-size:.82rem;
                                color:var(--muted); min-height:1.1rem;">
                    </div>
                </div>
            </div>
            <!-- ============ /ATTENDANCE_PERMS_UNIVERSAL_V1 ============ -->

'''

# The JS hooks that get appended to the existing AXIS_ADMIN_ATT_PERMS
# IIFE.  They are inserted just before the `return { ... };` line so
# they share the same SCHEMA / q / esc / csrf closures.
PERMS_JS_HOOKS = r'''
    // ---------- ATTENDANCE_PERMS_UNIVERSAL_V1 ----------

    function toggleUniversal() {
        var body = q('#attPermUniversalBody');
        var btn  = q('#attPermUniversalToggle');
        if (!body) return;
        var isHidden = body.style.display === 'none' ||
                       body.style.display === '';
        body.style.display = isHidden ? 'block' : 'none';
        if (btn) btn.textContent = isHidden ? 'Hide ▴' : 'Show ▾';
    }

    function resetUniversal() {
        q('#attPermUniBackdate').value  = 'none';
        q('#attPermUniMaxEdits').value  = 1;
        q('#attPermUniViewDays').value  = 30;
        q('#attPermUniEditDays').value  = 5;
        setUniMsg('', '');
    }

    function setUniMsg(text, cls) {
        var m = q('#attPermUniMsg');
        if (!m) return;
        m.textContent = text || '';
        m.style.color = cls === 'ok'  ? '#10b981'
                      : cls === 'err' ? '#ef4444'
                      : 'var(--muted)';
        m.style.fontWeight = (cls === 'ok' || cls === 'err') ? 700 : 400;
    }

    function populateUniversalCopyFrom() {
        var sel = q('#attPermUniCopyFrom');
        if (!sel) return;
        // Keep the first option
        while (sel.options.length > 1) sel.remove(1);
        permCache.forEach(function(p) {
            var o = document.createElement('option');
            o.value = p.class_id;
            o.textContent = p.class_display || ('Class #' + p.class_id);
            sel.appendChild(o);
        });
    }

    function fillFromClass() {
        var sel = q('#attPermUniCopyFrom');
        if (!sel) return;
        var cid = sel.value;
        if (!cid) { setUniMsg('Pick a class first.', 'err'); return; }
        var row = permCache.filter(function(p) {
            return String(p.class_id) === String(cid);
        })[0];
        if (!row) { setUniMsg('Class not found in list.', 'err'); return; }
        q('#attPermUniBackdate').value = row.backdate_access || 'none';
        q('#attPermUniMaxEdits').value = row.max_edits_per_date;
        q('#attPermUniViewDays').value = row.view_history_days;
        q('#attPermUniEditDays').value = row.edit_history_days;
        setUniMsg('Copied from ' + (row.class_display || cid) + '.', 'ok');
    }

    function applyUniversal() {
        var payload = {
            backdate_access:    q('#attPermUniBackdate').value,
            max_edits_per_date: parseInt(q('#attPermUniMaxEdits').value, 10) || 0,
            view_history_days:  parseInt(q('#attPermUniViewDays').value, 10) || 0,
            edit_history_days:  parseInt(q('#attPermUniEditDays').value, 10) || 0,
        };
        if (!confirm(
            'Apply these settings to ALL classes that have a class teacher?\n\n' +
            'This will overwrite any per-class overrides you have saved.'
        )) { return; }

        setUniMsg('Applying to all classes…', '');
        fetch('/portal/' + SCHEMA +
              '/api/attendance/class-teacher-permissions/bulk-save/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrf(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify(payload),
        })
            .then(function(r) { return r.json(); })
            .then(function(j) {
                if (!j.ok) {
                    setUniMsg(j.error || 'Failed.', 'err');
                    return;
                }
                setUniMsg('Applied to ' + j.updated + ' class(es). Refreshing…', 'ok');
                // Refresh the per-class table so the numbers match.
                setTimeout(function() { loadPermissions(); }, 400);
            })
            .catch(function() {
                setUniMsg('Network error.', 'err');
            });
    }

'''


def patch_admin_template(root, args):
    path = root / "templates" / "tenant" / "attendence.html"
    content = read_file(path)
    if content is None:
        return False

    if "ATTENDANCE_PERMS_UNIVERSAL_V1" in content:
        log(f"  SKIP (already applied): universal block in {path}")
        return True

    # ---- 1. Insert the universal block just above #attPermList ------
    anchor = (
        '        <div class="att-modal-body" id="attPermList" '
        'style="padding:1rem 1.2rem;">'
    )
    if anchor not in content:
        log(f"  WARN: could not find #attPermList anchor in {path}")
        return False

    content = content.replace(
        anchor,
        UNIVERSAL_BLOCK + anchor,
        1,
    )
    log(f"  Inserted universal block before #attPermList")

    # ---- 2. Call populateUniversalCopyFrom when permissions load ----
    #         (permCache is populated inside loadPermissions() right
    #          before renderPermissions() is called).
    old_load = (
        "                permCache = j.permissions || [];\n"
        "                renderPermissions();"
    )
    new_load = (
        "                permCache = j.permissions || [];\n"
        "                renderPermissions();\n"
        "                // ATTENDANCE_PERMS_UNIVERSAL_V1\n"
        "                if (typeof populateUniversalCopyFrom === 'function') {\n"
        "                    populateUniversalCopyFrom();\n"
        "                }"
    )
    if old_load in content:
        content = content.replace(old_load, new_load, 1)
        log(f"  Hooked populateUniversalCopyFrom into loadPermissions")
    else:
        log(f"  WARN: loadPermissions body not matched; universal copy-"
            f"from dropdown will be empty until modal is re-opened")

    # ---- 3. Insert JS hooks just before the return statement --------
    return_anchor = (
        "    return {\n"
        "        openPermissions: openPermissions,\n"
        "        closePermissions: closePermissions,\n"
        "        savePermission: savePermission,\n"
        "        openLogs: openLogs,\n"
        "        closeLogs: closeLogs,\n"
        "    };"
    )
    new_return = (
        "    // ---------- ATTENDANCE_PERMS_UNIVERSAL_V1 exports ----------\n"
        "    return {\n"
        "        openPermissions: openPermissions,\n"
        "        closePermissions: closePermissions,\n"
        "        savePermission: savePermission,\n"
        "        openLogs: openLogs,\n"
        "        closeLogs: closeLogs,\n"
        "        toggleUniversal: toggleUniversal,\n"
        "        resetUniversal: resetUniversal,\n"
        "        applyUniversal: applyUniversal,\n"
        "        fillFromClass: fillFromClass,\n"
        "    };"
    )
    if return_anchor not in content:
        log(f"  WARN: return-anchor not found in {path}; "
            f"could not add JS hooks cleanly")
        return False

    # Insert the JS hooks just before the return statement.
    content = content.replace(
        return_anchor,
        PERMS_JS_HOOKS + new_return,
        1,
    )
    log(f"  Inserted universal JS hooks before IIFE return")

    return write_file(path, content, args.dry_run,
                      "insert universal block + JS")


# =====================================================================
# MAIN
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            f"{MARKER} — add a Universal Settings block to the Class "
            f"Teacher Permissions modal so the admin can apply the same "
            f"attendance authority to every class in one click, while "
            f"still overriding individual classes below."
        )
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--target-dir", default=".")
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not (root / "manage.py").is_file():
        log(f"ERROR: manage.py not found in {root}")
        return 1

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")
    log(f"Patch:  {MARKER}")

    steps = [
        ("Admin views: bulk-save endpoint",  patch_admin_attendance),
        ("URLs: register bulk-save route",   patch_public_urls),
        ("Template: universal block + JS",   patch_admin_template),
    ]

    results = []
    for label, fn in steps:
        log(f"--- {label} ---")
        try:
            ok = fn(root, args)
        except Exception as exc:
            log(f"  EXCEPTION: {exc.__class__.__name__}: {exc}")
            ok = False
        results.append((label, ok))

    log("=" * 65)
    for label, ok in results:
        log(f"  {'OK  ' if ok else 'FAIL'}  {label}")

    all_ok = all(ok for _, ok in results)
    if all_ok:
        log("All steps completed successfully.")
        if args.dry_run:
            log("Re-run without --dry-run to apply.")
        else:
            log("Restart the Django server to pick up the new endpoint.")
            log("")
            log("What you get:")
            log("  • The Class Teacher Permissions modal now opens with a")
            log("    collapsible 🌐 Universal Settings card at the top.")
            log("  • Fill the four fields (or 'Copy from' an existing")
            log("    class) and click 'Apply to All Classes' to set every")
            log("    class at once.")
            log("  • The per-class table below is unchanged — individual")
            log("    overrides still work exactly as before.")
        return 0
    log("One or more steps failed. See messages above.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
