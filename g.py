#!/usr/bin/env python3
"""
g4.py — TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX
================================================

The models, migrations and save-writes were correctly refactored to use
a ForeignKey. But six *read* sites were missed and still treat
`DaySchedule.label` / `PeriodsTimetable.label` as strings.

Each miss is one of these two patterns:

    (obj.label or '').strip()          # AttributeError: no .strip()
    (obj.label or '')                  # truthy FK object -> renders as
                                       # "<ScheduleLabel: Senior>"
                                       # or crashes json.dumps

This patcher rewrites each one to `obj.label.name if obj.label_id else ''`.

Files touched
-------------
  M  axis_saas/views/timetable.py              (1 site)
  M  axis_saas/views/periods.py                (1 site)
  M  axis_saas/views/assign_teachers.py        (2 sites)
  M  axis_saas/views/single_class_detailed.py  (2 sites)
  M  axis_saas/views/wing_class_detailed.py    (2 sites)
  M  templates/tenant/timetable_assignments.html  (2 sites, 3 substitutions)

Idempotent — each anchor is checked before replacement.

USAGE
-----
    python3 g4.py --dry-run
    python3 g4.py
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


def read(path):
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log(f"  WARN: {path} not found")
        return None


def write(path, content, dry):
    if dry:
        log(f"  DRY-RUN would write {path} ({len(content)} bytes)")
        return True
    path.write_text(content, encoding="utf-8")
    log(f"  wrote {path} ({len(content)} bytes)")
    return True


def fix(path, pairs, dry, label):
    log(f"FIX: {label} ({path.name})")
    content = read(path)
    if content is None:
        return False
    touched = False
    for old, new, tag in pairs:
        if old not in content:
            log(f"  - already fixed (or anchor missing): {tag}")
            continue
        content = content.replace(old, new, 1)
        log(f"  + patched: {tag}")
        touched = True
    if not touched:
        log("  - nothing to change")
        return True
    return write(path, content, dry)


# ---------------------------------------------------------------------------
# views/timetable.py — timetable_management()
# ---------------------------------------------------------------------------

TT_OLD = """\
            _lbl = (ds.label or '').strip()
            if _lbl:
"""

TT_NEW = """\
            # TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX: label is a FK now.
            _lbl = ds.label.name if ds.label_id else ''
            if _lbl:
"""


# ---------------------------------------------------------------------------
# views/periods.py — _reconcile_timetables() delete-notification payload
# ---------------------------------------------------------------------------

PERIODS_OLD = """\
                _deleted_info.append({
                    'title': _tt.title or '(untitled)',
                    'label': _tt.label or '',
                    'assigned': _assigned,
                })
"""

PERIODS_NEW = """\
                _deleted_info.append({
                    'title': _tt.title or '(untitled)',
                    # TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX: label is a FK now.
                    'label': _tt.label.name if _tt.label_id else '',
                    'assigned': _assigned,
                })
"""


# ---------------------------------------------------------------------------
# views/assign_teachers.py — two sites
# ---------------------------------------------------------------------------

ASSIGN_OLD_1 = """\
                'timetable_title': a.timetable.title,
                'timetable_label': a.timetable.label or '',
"""

ASSIGN_NEW_1 = """\
                'timetable_title': a.timetable.title,
                # TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX: label is a FK now.
                'timetable_label': a.timetable.label.name if a.timetable.label_id else '',
"""

ASSIGN_OLD_2 = """\
            'timetable_title': tt.title,
            'timetable_label': tt.label or '',
"""

ASSIGN_NEW_2 = """\
            'timetable_title': tt.title,
            # TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX: label is a FK now.
            'timetable_label': tt.label.name if tt.label_id else '',
"""


# ---------------------------------------------------------------------------
# views/single_class_detailed.py — two sites
# ---------------------------------------------------------------------------

SCD_OLD_1 = """\
            assigned_timetable = {
                'id': _tt.id,
                'title': _tt.title,
                'label': _tt.label or '',
                'break_duration': _tt.break_duration or 0,
                'days': _tt.days or [],
            }
"""

SCD_NEW_1 = """\
            assigned_timetable = {
                'id': _tt.id,
                'title': _tt.title,
                # TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX: label is a FK now.
                'label': _tt.label.name if _tt.label_id else '',
                'break_duration': _tt.break_duration or 0,
                'days': _tt.days or [],
            }
"""

SCD_OLD_2 = """\
        _edit_timetables = []
        for _tt in PeriodsTimetable.objects.order_by('id'):
            _edit_timetables.append({
                'id': _tt.id,
                'title': _tt.title,
                'label': _tt.label or '',
                'break_duration': _tt.break_duration or 0,
                'days': _tt.days or [],
            })
"""

SCD_NEW_2 = """\
        _edit_timetables = []
        for _tt in PeriodsTimetable.objects.order_by('id'):
            _edit_timetables.append({
                'id': _tt.id,
                'title': _tt.title,
                # TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX: label is a FK now.
                'label': _tt.label.name if _tt.label_id else '',
                'break_duration': _tt.break_duration or 0,
                'days': _tt.days or [],
            })
"""


# ---------------------------------------------------------------------------
# views/wing_class_detailed.py — same two sites, same shapes
# ---------------------------------------------------------------------------

WCD_OLD_1 = SCD_OLD_1
WCD_NEW_1 = SCD_NEW_1
WCD_OLD_2 = SCD_OLD_2
WCD_NEW_2 = SCD_NEW_2


# ---------------------------------------------------------------------------
# templates/tenant/timetable_assignments.html
# ---------------------------------------------------------------------------

TPL_OLD_1 = """\
                    <td>
                        <span class="tag tag-blue">{{ a.timetable.title }}</span>
                        {% if a.timetable.label %}
                        <span class="text-muted" style="font-size:0.8rem;"> · {{ a.timetable.label }}</span>
                        {% endif %}
                    </td>
"""

TPL_NEW_1 = """\
                    <td>
                        <span class="tag tag-blue">{{ a.timetable.title }}</span>
                        {% if a.timetable.label_id %}
                        <span class="text-muted" style="font-size:0.8rem;"> · {{ a.timetable.label.name }}</span>
                        {% endif %}
                    </td>
"""

TPL_OLD_2 = """\
                    {% for tt in timetables %}
                    <option value="{{ tt.id }}">{{ tt.title }}{% if tt.label %} ({{ tt.label }}){% endif %}</option>
                    {% endfor %}
"""

TPL_NEW_2 = """\
                    {% for tt in timetables %}
                    <option value="{{ tt.id }}">{{ tt.title }}{% if tt.label_id %} ({{ tt.label.name }}){% endif %}</option>
                    {% endfor %}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--target-dir", default=".")
    args = ap.parse_args()

    root = Path(args.target_dir).resolve()
    log(f"Target: {root}")
    log(f"Dry-run: {args.dry_run}")
    print("-" * 60)

    ok = True
    ok &= fix(
        root / "axis_saas" / "views" / "timetable.py",
        [(TT_OLD, TT_NEW, "timetable_management: slots_by_label key")],
        args.dry_run, "views/timetable.py",
    )
    print("-" * 60)
    ok &= fix(
        root / "axis_saas" / "views" / "periods.py",
        [(PERIODS_OLD, PERIODS_NEW, "_reconcile_timetables: deleted_info")],
        args.dry_run, "views/periods.py",
    )
    print("-" * 60)
    ok &= fix(
        root / "axis_saas" / "views" / "assign_teachers.py",
        [
            (ASSIGN_OLD_1, ASSIGN_NEW_1, "timetable_assign_teachers: class_rows"),
            (ASSIGN_OLD_2, ASSIGN_NEW_2, "api_get_teacher_assignments: response"),
        ],
        args.dry_run, "views/assign_teachers.py",
    )
    print("-" * 60)
    ok &= fix(
        root / "axis_saas" / "views" / "single_class_detailed.py",
        [
            (SCD_OLD_1, SCD_NEW_1, "assigned_timetable dict"),
            (SCD_OLD_2, SCD_NEW_2, "_edit_timetables loop"),
        ],
        args.dry_run, "views/single_class_detailed.py",
    )
    print("-" * 60)
    ok &= fix(
        root / "axis_saas" / "views" / "wing_class_detailed.py",
        [
            (WCD_OLD_1, WCD_NEW_1, "assigned_timetable dict"),
            (WCD_OLD_2, WCD_NEW_2, "_edit_timetables loop"),
        ],
        args.dry_run, "views/wing_class_detailed.py",
    )
    print("-" * 60)
    ok &= fix(
        root / "templates" / "tenant" / "timetable_assignments.html",
        [
            (TPL_OLD_1, TPL_NEW_1, "assignment list row: label rendering"),
            (TPL_OLD_2, TPL_NEW_2, "assign modal: label in <option>"),
        ],
        args.dry_run, "templates/tenant/timetable_assignments.html",
    )
    print("-" * 60)

    if not ok:
        log("Completed with errors.")
        return 1

    log("Read-site fix applied.")
    if not args.dry_run:
        print()
        log("NEXT STEPS:")
        log("")
        log("  1. Restart the dev server (it will pick up the file changes")
        log("     automatically, but a clean restart clears any stale state):")
        log("       python manage.py runserver")
        log("")
        log("  2. Load the pages that were crashing:")
        log("       /portal/ey/timetable/         <- the original crash")
        log("       /portal/ey/timetable/periods/ <- already worked")
        log("       /portal/ey/timetable/assign/  <- template fix")
        log("       /portal/ey/my-classes/<id>/   <- single_class_detailed fix")
        log("")
        log("  3. If /portal/ey/my-classes/ still errors, look for one more")
        log("     '.label or' site we missed — but the six above cover every")
        log("     instance I can see in your current source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
