#!/usr/bin/env python3
"""
axis_patcher.py — ATTENDANCE_MODAL_CLICKABLE_STUDENT_V1
========================================================

Makes the student *name* clickable inside both attendance modals:

  1. Admin attendance dashboard modal (``templates/tenant/attendence.html``)
     Rendered by ``AXIS_ADMIN_ATT.reloadStudents()``.
  2. Class-detail "Manage Attendance" modal
     (``templates/tenant/single_class_detailed.html`` and
      ``templates/tenant/wing_class_detailed.html``)
     Rendered by ``CD_ATT.reload()``.

Clicking a student's name navigates to
``/portal/<schema>/students/<student_id>/`` — the student's own profile
page. The click does NOT affect the roll number column, the status
radios, or the leave/auto badges.

What this patch touches
-----------------------
* templates/tenant/attendence.html
* templates/tenant/single_class_detailed.html
* templates/tenant/wing_class_detailed.html

Only:
  * A small CSS block appended to each template's ``<style>``.
  * The ``.student-name`` element changed from ``<div>`` to ``<a>``
    inside the JS that renders each student row.

Idempotent. No models, migrations, views, or URLs touched.

Usage
-----
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py
    python3 axis_patcher.py --target-dir /srv/fee_management
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


MARKER = "ATTENDANCE_MODAL_CLICKABLE_STUDENT_V1"


# --------------------------------------------------------------------- utils

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


# ====================================================== CSS

CSS_BLOCK = r'''
    /* ============ ATTENDANCE_MODAL_CLICKABLE_STUDENT_V1 ============
       Student names inside the attendance modals are now anchors that
       link to the student's profile page.  The anchor inherits the
       surrounding text styling so nothing about the row's layout
       changes; only the cursor + hover colour announce the link. */
    .att-mrow a.student-name,
    .cd-att-row a.student-name {
        color: inherit;
        text-decoration: none;
        font-weight: 600;
        cursor: pointer;
        transition: color .12s ease;
    }
    .att-mrow a.student-name:hover,
    .cd-att-row a.student-name:hover {
        color: var(--primary);
        text-decoration: underline;
    }
    /* ============ /ATTENDANCE_MODAL_CLICKABLE_STUDENT_V1 ============ */
'''


# ====================================================== attendence.html

DASH_JS_OLD = """        modalStudents.forEach(function(s) {
            var leave = s.on_leave
                ? '<span class="leave-tag">ON LEAVE</span>' : '';
            var auto = s.is_auto
                ? '<span class="leave-tag" style="background:#1e40af;">AUTO</span>' : '';
            html += '<div class="att-mrow">'
                  +   '<div class="roll">' + esc(s.roll_number || '—') + '</div>'
                  +   '<div><div class="student-name">' + esc(s.name) + '</div>'
                  +        '<div class="student-meta">' + esc(s.father_name || '') + leave + auto + '</div></div>'
                  +   '<div class="att-mstatus">'"""

DASH_JS_NEW = """        modalStudents.forEach(function(s) {
            var leave = s.on_leave
                ? '<span class="leave-tag">ON LEAVE</span>' : '';
            var auto = s.is_auto
                ? '<span class="leave-tag" style="background:#1e40af;">AUTO</span>' : '';
            // ATTENDANCE_MODAL_CLICKABLE_STUDENT_V1: student name links
            // to the student's profile page.
            html += '<div class="att-mrow">'
                  +   '<div class="roll">' + esc(s.roll_number || '—') + '</div>'
                  +   '<div><a class="student-name" href="/portal/' + SCHEMA + '/students/' + s.id + '/">' + esc(s.name) + '</a>'
                  +        '<div class="student-meta">' + esc(s.father_name || '') + leave + auto + '</div></div>'
                  +   '<div class="att-mstatus">'"""

DASH_CSS_ANCHOR = (
    "    /* ============== /ADMIN_ATTENDANCE_DASHBOARD_V1 "
    "============== */"
)


def patch_dashboard(root, args):
    path = root / "templates" / "tenant" / "attendence.html"
    content = read_file(path)
    if content is None:
        return False

    if "ATTENDANCE_MODAL_CLICKABLE_STUDENT_V1" in content:
        log("  SKIP (already applied): attendence.html")
        return True

    # 1. CSS — insert before the closing dashboard CSS comment.
    if DASH_CSS_ANCHOR not in content:
        log("  WARN: dashboard CSS closing comment not found")
        return False
    content = content.replace(
        DASH_CSS_ANCHOR,
        CSS_BLOCK + "\n" + DASH_CSS_ANCHOR,
        1,
    )

    # 2. JS — change the student-name div into an anchor.
    if DASH_JS_OLD not in content:
        log("  WARN: attendence.html student-row JS block not found")
        return False
    content = content.replace(DASH_JS_OLD, DASH_JS_NEW, 1)

    return write_file(path, content, args.dry_run,
                      "attendence.html (clickable student name)")


# ====================================================== class-detail templates

DETAIL_JS_OLD = """        students.forEach(function (s) {
            var leave = s.on_leave ? '<span class="leave-tag">ON LEAVE</span>' : '';
            var auto = s.is_auto ? '<span class="leave-tag" style="background:#1e40af;">AUTO</span>' : '';
            html += '<div class="cd-att-row">'
                  +   '<div class="roll">' + esc(s.roll_number || '—') + '</div>'
                  +   '<div><div class="student-name">' + esc(s.name) + '</div>'
                  +        '<div class="student-meta">' + esc(s.father_name || '') + leave + auto + '</div></div>'
                  +   '<div class="cd-att-status">'"""

DETAIL_JS_NEW = """        students.forEach(function (s) {
            var leave = s.on_leave ? '<span class="leave-tag">ON LEAVE</span>' : '';
            var auto = s.is_auto ? '<span class="leave-tag" style="background:#1e40af;">AUTO</span>' : '';
            // ATTENDANCE_MODAL_CLICKABLE_STUDENT_V1: student name links
            // to the student's profile page.
            html += '<div class="cd-att-row">'
                  +   '<div class="roll">' + esc(s.roll_number || '—') + '</div>'
                  +   '<div><a class="student-name" href="/portal/' + SCHEMA + '/students/' + s.id + '/">' + esc(s.name) + '</a>'
                  +        '<div class="student-meta">' + esc(s.father_name || '') + leave + auto + '</div></div>'
                  +   '<div class="cd-att-status">'"""

DETAIL_CSS_ANCHOR = (
    "    /* ============ END CLASS_DETAIL_ATTENDANCE_BUTTON_V1 "
    "============ */"
)


def patch_class_detail(root, filename, args):
    path = root / "templates" / "tenant" / filename
    content = read_file(path)
    if content is None:
        return False

    if "ATTENDANCE_MODAL_CLICKABLE_STUDENT_V1" in content:
        log(f"  SKIP (already applied): {filename}")
        return True

    # 1. CSS — insert before the closing CLASS_DETAIL CSS comment.
    if DETAIL_CSS_ANCHOR not in content:
        log(f"  WARN: CLASS_DETAIL_ATTENDANCE_BUTTON_V1 CSS closing "
            f"comment not found in {filename}")
        return False
    content = content.replace(
        DETAIL_CSS_ANCHOR,
        CSS_BLOCK + "\n" + DETAIL_CSS_ANCHOR,
        1,
    )

    # 2. JS — change the student-name div into an anchor.
    if DETAIL_JS_OLD not in content:
        log(f"  WARN: student-row JS block not found in {filename}")
        return False
    content = content.replace(DETAIL_JS_OLD, DETAIL_JS_NEW, 1)

    return write_file(path, content, args.dry_run,
                      f"{filename} (clickable student name)")


# ====================================================== MAIN

def main():
    parser = argparse.ArgumentParser(
        description=(
            "ATTENDANCE_MODAL_CLICKABLE_STUDENT_V1 — make the student "
            "name clickable inside the admin attendance modal and the "
            "class-detail Manage Attendance modal.  Clicking a name "
            "navigates to that student's profile page."
        )
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing.")
    parser.add_argument("--verbose", action="store_true",
                        help="Verbose output.")
    parser.add_argument("--target-dir", default=".",
                        help="Project root (default: current directory).")
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not (root / "manage.py").is_file():
        log(f"ERROR: manage.py not found in {root}")
        return 1

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")
    log(f"Patch:  {MARKER}")

    steps = [
        ("Template: attendence.html",
         patch_dashboard),
        ("Template: single_class_detailed.html",
         lambda r, a: patch_class_detail(r, "single_class_detailed.html", a)),
        ("Template: wing_class_detailed.html",
         lambda r, a: patch_class_detail(r, "wing_class_detailed.html", a)),
    ]

    results = []
    for label, fn in steps:
        log(f"--- {label} ---")
        try:
            ok = fn(root, args)
        except Exception as exc:
            log(f"  EXCEPTION: {exc}")
            ok = False
        results.append((label, ok))

    log("=" * 65)
    for label, ok in results:
        log(f"  {'OK' if ok else 'FAIL'}  {label}")

    all_ok = all(ok for _, ok in results)
    if all_ok:
        log("All steps completed successfully.")
        if args.dry_run:
            log("Re-run without --dry-run to apply.")
        else:
            log("Hard-refresh the pages (Ctrl+Shift+R) to pick up the new JS.")
        return 0
    log("One or more steps failed. See messages above.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
