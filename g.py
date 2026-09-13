#!/usr/bin/env python3
"""
axis_patcher.py
===============

TIMETABLE_HARDENING_V1_PHASE3
-----------------------------

Fixes the three P0 bugs the review identified. Each is a concrete,
verifiable defect in the current source — not a refactor, not a
rewrite, not a design change.

  #1  views/staff_portal.py — ``timezone.timedelta`` does not exist.

      ``django.utils.timezone`` imports ``timedelta`` internally but
      does NOT re-export it. The brute-force-lockout fallback path
      therefore raises ``AttributeError`` on exactly the 10th failed
      login attempt. It is the one code path you most need to work
      and it has never been exercised in production because
      nobody hits it during testing.

      Fix: add ``timedelta`` to the existing ``from datetime import``
      line, then use bare ``timedelta(minutes=15)``.

  #2  views/staff_portal.py — duplicate ``staff_dashboard`` definition.

      The file defines ``staff_dashboard`` twice. Python keeps the
      last one. That last one has NO ``@require_staff_login``
      decorator (so it is reachable without authentication) and
      returns ``'classes'`` in the template context, while
      ``mobile/staff/dashboard.html`` expects
      ``class_teacher_classes`` and ``subject_teacher_classes``.

      Fix: delete the second (unauthenticated, wrong-context)
      definition so the first, correct one takes effect again.

  #3  views/helpers.py — bare ``except:`` swallows KeyboardInterrupt
      and SystemExit.

      ``get_student_list_context`` wraps a class-id filter in a
      bare ``except: pass``. That catches BaseException (including
      Ctrl-C and SystemExit), making debugging on that path
      impossible.

      Fix: narrow to ``except Exception`` and log a warning.

NOT FIXED (deliberately out of scope for a text-rewriting patcher):

  * ``PeriodsTimetable.days`` JSONField -> relational tables. Requires
    a real data migration, template rewrites (the JSON shape is baked
    into client-side JS across four templates), and a two-phase
    deploy. The review itself calls this a structural migration.
  * ``views/classes.py`` bare ``except:`` — I scanned the current
    source and could not find one. The review may be referencing a
    stale revision. Leaving working code alone.

Files modified
--------------
  M  axis_saas/views/staff_portal.py
  M  axis_saas/views/helpers.py

Idempotent. Safe to run multiple times.

Usage
-----
    python3 axis_patcher.py                       # apply
    python3 axis_patcher.py --dry-run             # preview
    python3 axis_patcher.py --verbose             # detailed output
    python3 axis_patcher.py --target-dir=/path    # custom root
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


MARKER = "TIMETABLE_HARDENING_V1_PHASE3"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _read(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        log(f"  ERROR reading {path}: {exc}")
        return None


def _write(path: Path, content: str, dry_run: bool, verbose: bool) -> bool:
    if dry_run:
        log(f"  DRY-RUN would write {path} ({len(content)} bytes)")
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        log(f"  Wrote {path}" + (f" ({len(content)} bytes)" if verbose else ""))
        return True
    except Exception as exc:
        log(f"  ERROR writing {path}: {exc}")
        return False


# =====================================================================
# STEP 1 — views/staff_portal.py
# =====================================================================

# --- 1a. add ``timedelta`` to the existing datetime import ---
OLD_IMPORT = (
    "import uuid\n"
    "from datetime import datetime\n"
)

NEW_IMPORT = (
    "import uuid\n"
    "from datetime import datetime, timedelta  # TIMETABLE_HARDENING_V1_PHASE3\n"
)


# --- 1b. use ``timedelta`` instead of the nonexistent ``timezone.timedelta`` ---
OLD_TIMEDELTA = (
    "            cache.set(f'{ip_key}_blocked_until', "
    "timezone.now() + timezone.timedelta(minutes=15), 900)"
)

NEW_TIMEDELTA = (
    "            # TIMETABLE_HARDENING_V1_PHASE3: django.utils.timezone does\n"
    "            # not re-export timedelta. Use the stdlib import added at\n"
    "            # the top of this module instead.\n"
    "            cache.set(f'{ip_key}_blocked_until', "
    "timezone.now() + timedelta(minutes=15), 900)"
)


# --- 1c. remove the duplicate (unauthenticated) staff_dashboard ---
# This block sits immediately after the *first* staff_dashboard's closing
# paren, with no blank line between them. The distinguishing features are:
#   * it has only @require_staff_feature (no @require_staff_login)
#   * it defines `classes` instead of `class_teacher_classes` /
#     `subject_teacher_classes`
#   * its return dict contains 'classes': classes
OLD_DUP_DASHBOARD = (
    "    )\n"
    "@require_staff_feature('staff_dashboard')\n"
    "def staff_dashboard(request):\n"
    "    schema_name = request.session['staff_schema_name']\n"
    "    from django_tenants.utils import schema_context\n"
    "    with schema_context(schema_name):\n"
    "        staff = get_object_or_404(Staff, pk=request.session['staff_id'])\n"
    "        classes = SchoolClass.objects.filter(Q(class_teacher=staff) | "
    "Q(class_subjects__teacher=staff)).distinct().order_by('name', 'section')\n"
    "        today = timezone.localdate()\n"
    "        student_count = Student.objects.filter(school_class__in=classes).count()\n"
    "        attendance_today = StudentAttendance.objects.filter(date=today, "
    "school_class__in=classes).count()\n"
    "        notifications = Notification.objects.filter(is_read=False)"
    ".order_by('-created_at')[:5]\n"
    "\n"
    "    return render(\n"
    "        request,\n"
    "        'mobile/staff/dashboard.html',\n"
    "        {\n"
    "            'staff': staff,\n"
    "            'classes': classes,\n"
    "            'student_count': student_count,\n"
    "            'attendance_today': attendance_today,\n"
    "            'notifications': notifications,\n"
    "            'schema_name': schema_name,\n"
    "        },\n"
    "    )\n"
)

NEW_DUP_DASHBOARD = (
    "    )\n"
    "    # TIMETABLE_HARDENING_V1_PHASE3: a second staff_dashboard() lived\n"
    "    # here. Python keeps the last definition, so that one silently\n"
    "    # overrode this one. It had no @require_staff_login decorator\n"
    "    # (reachable without auth) and returned 'classes' in the context\n"
    "    # instead of the 'class_teacher_classes' / 'subject_teacher_classes'\n"
    "    # the template expects. Deleted.\n"
)


def patch_staff_portal(path: Path, dry_run: bool, verbose: bool) -> bool:
    log(f"STEP 1: {path}")
    content = _read(path)
    if content is None:
        return False

    changed = False

    # --- 1a: timedelta import ---
    if "from datetime import datetime, timedelta" in content:
        log("  - import fix already applied")
    elif OLD_IMPORT in content:
        content = content.replace(OLD_IMPORT, NEW_IMPORT, 1)
        log("  + added timedelta to datetime import")
        changed = True
    else:
        log("  WARN: import anchor not found — file may have diverged")

    # --- 1b: timezone.timedelta -> timedelta ---
    if "timezone.timedelta" not in content:
        log("  - timezone.timedelta fix already applied (or never present)")
    elif OLD_TIMEDELTA in content:
        content = content.replace(OLD_TIMEDELTA, NEW_TIMEDELTA, 1)
        log("  + replaced timezone.timedelta with timedelta")
        changed = True
    else:
        log("  WARN: timezone.timedelta found but exact line did not match")

    # --- 1c: duplicate staff_dashboard ---
    # Idempotency: after the fix, the exact OLD_DUP_DASHBOARD block is
    # gone and the marker comment is in its place. Detect via the marker.
    if "TIMETABLE_HARDENING_V1_PHASE3: a second staff_dashboard()" in content:
        log("  - duplicate staff_dashboard already removed")
    elif OLD_DUP_DASHBOARD in content:
        content = content.replace(OLD_DUP_DASHBOARD, NEW_DUP_DASHBOARD, 1)
        log("  + removed duplicate (unauthenticated) staff_dashboard")
        changed = True
    else:
        log("  WARN: duplicate staff_dashboard anchor not found")
        log("        (the review may reference a different revision)")

    if not changed:
        log("  - nothing to change")
        return True
    return _write(path, content, dry_run, verbose)


# =====================================================================
# STEP 2 — views/helpers.py
# =====================================================================

OLD_HELPERS_EXCEPT = (
    "        if class_id:\n"
    "            try:\n"
    "                students = students.filter(school_class_id=class_id)\n"
    "            except:\n"
    "                pass\n"
)

NEW_HELPERS_EXCEPT = (
    "        if class_id:\n"
    "            try:\n"
    "                students = students.filter(school_class_id=class_id)\n"
    "            except Exception as _tt_exc:\n"
    "                # TIMETABLE_HARDENING_V1_PHASE3: bare except swallowed\n"
    "                # KeyboardInterrupt and SystemExit, making the class_id\n"
    "                # filter path impossible to debug. Narrowed and logged.\n"
    "                logger.warning(\n"
    "                    'get_student_list_context: class_id filter failed: %s',\n"
    "                    _tt_exc,\n"
    "                )\n"
)


def patch_helpers(path: Path, dry_run: bool, verbose: bool) -> bool:
    log(f"STEP 2: {path}")
    content = _read(path)
    if content is None:
        return False

    if "TIMETABLE_HARDENING_V1_PHASE3" in content and OLD_HELPERS_EXCEPT not in content:
        log("  - bare except already fixed")
        return True

    if OLD_HELPERS_EXCEPT not in content:
        log("  WARN: exact bare-except anchor not found")
        log("        (whitespace or surrounding code may have changed)")
        return False

    content = content.replace(OLD_HELPERS_EXCEPT, NEW_HELPERS_EXCEPT, 1)
    log("  + replaced bare except with 'except Exception' + logging")
    return _write(path, content, dry_run, verbose)


# =====================================================================
# STEP 3 — report-only scan for remaining bare excepts
# =====================================================================

def scan_bare_excepts(target: Path) -> None:
    """Walk the app and print any remaining bare ``except:`` lines.

    Read-only. Does NOT modify files. Some may be legitimate
    (they aren't — bare excepts are a code smell — but changing them
    without context is riskier than leaving them for a human).
    """
    log("STEP 3: scanning for remaining bare excepts (report only)")
    app_root = target / "axis_saas"
    if not app_root.exists():
        log("  - axis_saas/ not found, skipping scan")
        return

    hits = []
    for py in app_root.rglob("*.py"):
        if any(part in {"migrations", "__pycache__"} for part in py.parts):
            continue
        try:
            for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped == "except:" or stripped.startswith("except:  #"):
                    hits.append((py.relative_to(target), lineno, line.strip()))
        except Exception:
            continue

    if not hits:
        log("  - no bare excepts found")
        return

    log(f"  - {len(hits)} remaining bare except(s) — review manually:")
    for rel, lineno, txt in hits:
        log(f"      {rel}:{lineno}  {txt}")


# =====================================================================
# main
# =====================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "TIMETABLE_HARDENING_V1_PHASE3 — fix three P0 bugs: "
            "timezone.timedelta AttributeError, duplicate staff_dashboard "
            "definition, and a bare except in get_student_list_context."
        )
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--target-dir", default=".")
    args = parser.parse_args()

    target = Path(args.target_dir).resolve()
    log(f"Target dir: {target}")
    log(f"Dry-run:    {args.dry_run}")
    log(f"Verbose:    {args.verbose}")
    print("-" * 60)

    if not (target / "manage.py").exists():
        log("WARNING: manage.py not found at target root. Continuing anyway.")
    print("-" * 60)

    ok = True
    ok &= patch_staff_portal(
        target / "axis_saas" / "views" / "staff_portal.py",
        args.dry_run, args.verbose,
    )
    print("-" * 60)

    ok &= patch_helpers(
        target / "axis_saas" / "views" / "helpers.py",
        args.dry_run, args.verbose,
    )
    print("-" * 60)

    scan_bare_excepts(target)
    print("-" * 60)

    if ok:
        log("DONE.")
        if not args.dry_run:
            log("")
            log("NEXT STEPS:")
            log("")
            log("  1. Run a system check to catch any import errors:")
            log("       python manage.py check")
            log("")
            log("  2. Exercise the brute-force fallback path manually:")
            log("       - attempt 10 bad logins from the same IP")
            log("       - the 10th must NOT raise AttributeError")
            log("       - the account should be locked for 15 minutes")
            log("")
            log("  3. Hit /portal/staff/dashboard/ (after logging in)")
            log("     and confirm the template renders the two class lists")
            log("     (class_teacher_classes + subject_teacher_classes).")
            log("")
            log("  4. Re-run the timetable test suite:")
            log("       python manage.py test axis_saas.tests.test_timetable")
            log("       python manage.py test axis_saas.tests.test_timetable_api")
            log("")
            log("NOT FIXED (structural migrations, not patcher work):")
            log("  - PeriodsTimetable.days JSONField -> relational tables")
            log("  - DaySchedule.label / PeriodsTimetable.label CharField")
            log("    -> ScheduleLabel ForeignKey")
            log("  - views/classes.py bare except: (could not locate one in")
            log("    the current source; the review may be stale there)")
        return 0

    log("FAILED: one or more steps did not complete.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
