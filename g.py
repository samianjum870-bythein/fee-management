#!/usr/bin/env python3
"""
axis_patcher.py
===============

STAFF_DASHBOARD_QSET_COMBINE_FIX_01

Fixes the production crash on /portal/staff/dashboard/:

    TypeError: Cannot combine a unique query with a non-unique query.

Root cause
----------
In `axis_saas/views/staff_portal.py` → `staff_dashboard()`:

    class_teacher_classes = (
        SchoolClass.objects
        .filter(class_teacher=staff, is_active=True)
        .annotate(student_count=Count('students'))
        .order_by('name', 'section')
    )

    subject_teacher_classes = (
        SchoolClass.objects
        .filter(class_subjects__teacher=staff, is_active=True)
        .distinct()                                 # <-- this marks the query
        .annotate(student_count=Count('students'))   #     as "unique"
        .order_by('name', 'section')
    )

    all_classes = class_teacher_classes | subject_teacher_classes   # BOOM

Django's `QuerySet.__or__` refuses to combine a query that has been
flagged `unique=True` (via `.distinct()`) with one that has not. The
`subject_teacher_classes` side goes through the M2M join
(`class_subjects__teacher`), which produces duplicate rows for classes
where the staff teaches more than one subject — hence the `.distinct()`.
The `class_teacher` side is a plain FK filter, so `.distinct()` was
never added there.

The fix is to stop using `|`. Build a single queryset with `Q()` instead:

    all_classes = (
        SchoolClass.objects
        .filter(
            Q(class_teacher=staff) | Q(class_subjects__teacher=staff),
            is_active=True,
        )
        .distinct()                                  # dedupe M2M rows
    )

This produces identical results in a single SQL query and avoids the
"unique vs non-unique" combine entirely. The intermediate
`class_teacher_classes` / `subject_teacher_classes` lists are still
needed for the template's two separate sections ("Class Teacher:" and
"Subject Teacher:"), so they are left untouched.

Files modified:
    axis_saas/views/staff_portal.py

Idempotent: re-running this patcher after the fix is a no-op (it
detects the new Q()-based block and skips).

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


TARGET_REL_PATH = Path('axis_saas') / 'views' / 'staff_portal.py'


# ---------------------------------------------------------------- old ---
# Exact snippet as it exists in the buggy file. Kept as a literal so the
# replacement is unambiguous (no regex, no accidental matches).
OLD_BLOCK = """        # Combined for total counts
        all_classes = class_teacher_classes | subject_teacher_classes
        student_count = Student.objects.filter(school_class__in=all_classes).count()
        today = timezone.localdate()
        attendance_today = StudentAttendance.objects.filter(date=today, school_class__in=all_classes).count()
"""


# ---------------------------------------------------------------- new ---
NEW_BLOCK = """        # Combined for total counts.
        #
        # STAFF_DASHBOARD_QSET_COMBINE_FIX_01:
        #   The previous implementation combined two annotated querysets
        #   with `|`:
        #
        #       all_classes = class_teacher_classes | subject_teacher_classes
        #
        #   `subject_teacher_classes` carries `.distinct()` because the
        #   `class_subjects__teacher` M2M join fans out per subject, so
        #   it is flagged as a "unique" query. `class_teacher_classes`
        #   is not. Django's QuerySet.__or__ refuses to merge a unique
        #   query with a non-unique one and raises:
        #
        #       TypeError: Cannot combine a unique query with a
        #                  non-unique query.
        #
        #   We now build a single queryset with Q() and `.distinct()`
        #   on the merged query. Same result set, one SQL query, no
        #   combine step. The two intermediate lists (used by the
        #   template for the "Class Teacher" / "Subject Teacher"
        #   sections) are untouched above.
        all_classes = (
            SchoolClass.objects
            .filter(
                Q(class_teacher=staff) | Q(class_subjects__teacher=staff),
                is_active=True,
            )
            .distinct()
        )
        student_count = Student.objects.filter(school_class__in=all_classes).count()
        today = timezone.localdate()
        attendance_today = StudentAttendance.objects.filter(date=today, school_class__in=all_classes).count()
"""


# Marker string used for idempotency: if the file already contains this
# comment we assume the fix has already been applied.
FIX_MARKER = "STAFF_DASHBOARD_QSET_COMBINE_FIX_01"


def patch_file(path, dry_run, verbose):
    try:
        content = path.read_text(encoding='utf-8')
    except Exception as exc:
        log(f"ERROR reading {path}: {exc}")
        return False

    if FIX_MARKER in content:
        log(f"SKIP (already fixed): {path}")
        return True

    if OLD_BLOCK not in content:
        # Be helpful: report whether the old `|` combine is even present
        if "all_classes = class_teacher_classes | subject_teacher_classes" in content:
            log(
                "WARN: found the buggy `|` line but not the exact "
                "expected block. Manual review required."
            )
        else:
            log(
                "WARN: expected block not found in "
                f"{path}. Nothing patched."
            )
        return False

    new_content = content.replace(OLD_BLOCK, NEW_BLOCK, 1)

    if new_content == content:
        log(f"NO CHANGE: {path}")
        return True

    if dry_run:
        log(
            f"DRY-RUN: would replace "
            f"{len(OLD_BLOCK)} -> {len(NEW_BLOCK)} bytes in {path}"
        )
        return True

    try:
        path.write_text(new_content, encoding='utf-8')
    except Exception as exc:
        log(f"ERROR writing {path}: {exc}")
        return False

    log(f"PATCHED: {path}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Fix the 'Cannot combine a unique query with a non-unique "
            "query' crash in staff_dashboard()."
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

    ok = patch_file(target, args.dry_run, args.verbose)
    if not ok:
        return 2

    log("Done.")
    if args.dry_run:
        log("Re-run without --dry-run to apply.")
    else:
        log("Commit + push and let Railway redeploy.")
        log("")
        log("Heads-up on two unrelated items visible in the deploy log:")
        log("  1) 'Ignoring invalid WebAuthn RP ID localhost ...' —")
        log("     Set WEBAUTHN_RP_ID and WEBAUTHN_ORIGIN env vars on")
        log("     Railway to the production domain (no scheme for RP_ID,")
        log("     with https:// scheme for ORIGIN).")
        log("  2) 'Your models in app(s): axis_saas have changes that are")
        log("     not yet reflected in a migration' — run")
        log("     `python manage.py makemigrations axis_saas` locally")
        log("     and commit the generated migration file.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
