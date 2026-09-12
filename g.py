#!/usr/bin/env python3
"""
axis_patcher.py
===============

Adds (v2):

  1. In the class-detailed page modal (wing_class_detailed.html /
     single_class_detailed.html): when NO timetable is assigned to the
     class, show a prominent "Assign Periods Timetable to this Class"
     button that links to /portal/<schema>/timetable/assign/.

  2. In timetable_assignments.html:
     - Make the class name clickable (link -> class detailed page).
     - Add a "View Class" button to the Actions column.

Idempotent. Safe to run multiple times.

Usage:
    python3 axis_patcher.py [--dry-run] [--verbose] [--target-dir=.]
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


PATCH_MARKER_V2 = "MANAGE_PERIODS_TIMETABLE_v2"


def _log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


def _read(path):
    try:
        return path.read_text(encoding='utf-8')
    except Exception as e:
        _log(f"ERROR reading {path}: {e}")
        return None


def _write(path, content, dry_run, verbose):
    try:
        if dry_run:
            _log(f"DRY-RUN would write {path} ({len(content)} bytes)")
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        if verbose:
            _log(f"Wrote {path} ({len(content)} bytes)")
        else:
            _log(f"Wrote {path}")
        return True
    except Exception as e:
        _log(f"ERROR writing {path}: {e}")
        return False


# =====================================================================
# 1) Class-detailed templates — replace "no timetable" empty state
#    with a button that links to the assign page.
# =====================================================================
OLD_EMPTY_STATE = (
    "        if (!ASSIGNED_TIMETABLE || !ASSIGNED_TIMETABLE.id) {\n"
    "            container.innerHTML =\n"
    "                '<div class=\"tt-empty\">' +\n"
    "                    '<strong>No periods timetable is assigned to this class yet.</strong><br>' +\n"
    "                    'Please assign one from the Timetable &rarr; Assign to Classes page.' +\n"
    "                '</div>';\n"
    "            return;\n"
    "        }"
)

NEW_EMPTY_STATE = (
    "        if (!ASSIGNED_TIMETABLE || !ASSIGNED_TIMETABLE.id) {\n"
    "            /* MANAGE_PERIODS_TIMETABLE_v2: no-assignment -> deep link to assign page */\n"
    "            container.innerHTML =\n"
    "                '<div class=\"tt-empty\">' +\n"
    "                    '<strong>No periods timetable is assigned to this class yet.</strong>' +\n"
    "                    '<p style=\"margin:1.25rem 0 0 0;\">' +\n"
    "                        '<a href=\"/portal/' + SCHEMA + '/timetable/assign/\" ' +\n"
    "                           'style=\"display:inline-flex; align-items:center; gap:0.5rem; ' +\n"
    "                                  'padding:0.7rem 1.5rem; border-radius:0.75rem; ' +\n"
    "                                  'background:linear-gradient(135deg, var(--primary), var(--primary-dark)); ' +\n"
    "                                  'color:#fff; font-weight:700; font-size:0.92rem; ' +\n"
    "                                  'text-decoration:none; box-shadow:0 6px 18px -6px rgba(59,130,246,0.55);\">' +\n"
    "                            '&#128197; Assign Periods Timetable to this Class' +\n"
    "                        '</a>' +\n"
    "                    '</p>' +\n"
    "                '</div>';\n"
    "            return;\n"
    "        }"
)


def _patch_class_detailed_template(path, dry_run, verbose):
    _log(f"Patching class-detail template: {path}")
    content = _read(path)
    if content is None:
        return False

    if PATCH_MARKER_V2 in content:
        _log("  - already patched (v2), skipping")
        return True

    if OLD_EMPTY_STATE not in content:
        _log("  WARN: could not find the v1 empty-state block")
        return False

    content = content.replace(OLD_EMPTY_STATE, NEW_EMPTY_STATE, 1)
    _log("  + replaced empty-state with 'Assign Periods Timetable to this Class' button")
    return _write(path, content, dry_run, verbose)


# =====================================================================
# 2) timetable_assignments.html — clickable class name + View Class btn
# =====================================================================
OLD_CLASS_CELL = (
    "                <tr>\n"
    "                    <td><strong>{{ a.class_display_name }}</strong></td>\n"
    "                    <td>\n"
    "                        <span class=\"tag tag-blue\">{{ a.timetable.title }}</span>"
)

NEW_CLASS_CELL = (
    "                <tr>\n"
    "                    <td>\n"
    "                        <a href=\"{% url 'class_detailed' schema_name=tenant.schema_name class_id=a.school_class.id %}\"\n"
    "                           style=\"color:var(--primary); font-weight:600; text-decoration:none;\">\n"
    "                            {{ a.class_display_name }}\n"
    "                        </a>\n"
    "                    </td>\n"
    "                    <td>\n"
    "                        <span class=\"tag tag-blue\">{{ a.timetable.title }}</span>"
)

OLD_ACTIONS_CELL = (
    "                    <td style=\"text-align:right; white-space:nowrap;\">\n"
    "                        <a href=\"{% url 'timetable_periods' schema_name=tenant.schema_name %}?highlight={{ a.timetable.id }}\"\n"
    "                           class=\"btn-secondary btn-sm\" style=\"text-decoration:none;\">View Timetable</a>\n"
    "                        <form method=\"post\" action=\"{% url 'api_unassign_timetable' schema_name=tenant.schema_name %}\""
)

NEW_ACTIONS_CELL = (
    "                    <td style=\"text-align:right; white-space:nowrap;\">\n"
    "                        <a href=\"{% url 'class_detailed' schema_name=tenant.schema_name class_id=a.school_class.id %}\"\n"
    "                           class=\"btn-secondary btn-sm\" style=\"text-decoration:none; margin-right:0.3rem;\">View Class</a>\n"
    "                        <a href=\"{% url 'timetable_periods' schema_name=tenant.schema_name %}?highlight={{ a.timetable.id }}\"\n"
    "                           class=\"btn-secondary btn-sm\" style=\"text-decoration:none;\">View Timetable</a>\n"
    "                        <form method=\"post\" action=\"{% url 'api_unassign_timetable' schema_name=tenant.schema_name %}\""
)


def _patch_timetable_assignments_template(path, dry_run, verbose):
    _log(f"Patching assign template: {path}")
    content = _read(path)
    if content is None:
        return False

    if PATCH_MARKER_V2 in content:
        _log("  - already patched (v2), skipping")
        return True

    changed = False

    # 1) Clickable class name
    if "class_id=a.school_class.id %}\" " in content and "View Class" in content:
        _log("  - class link + View Class button already present")
    else:
        if OLD_CLASS_CELL in content:
            content = content.replace(OLD_CLASS_CELL, NEW_CLASS_CELL, 1)
            _log("  + made class name clickable -> class_detailed")
            changed = True
        else:
            _log("  WARN: could not find class-name cell anchor")

        if OLD_ACTIONS_CELL in content:
            content = content.replace(OLD_ACTIONS_CELL, NEW_ACTIONS_CELL, 1)
            _log("  + added 'View Class' button in Actions column")
            changed = True
        else:
            _log("  WARN: could not find Actions-cell anchor")

    if not changed:
        _log("  no changes needed")
        return True

    # Inject a marker comment at the top (after {% block body %}) so re-runs
    # are idempotent even if a class has no header row.
    if PATCH_MARKER_V2 not in content:
        marker = (
            "{# " + PATCH_MARKER_V2 + ": clickable class name + View Class button #}\n"
        )
        # Place the marker right before the first `{% block body %}` content.
        for anchor in ("{% block body %}\n", "{% block body %}"):
            if anchor in content:
                content = content.replace(anchor, anchor + marker, 1)
                break

    return _write(path, content, dry_run, verbose)


# =====================================================================
# main
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "v2 patch: (a) show an 'Assign Periods Timetable to this Class' "
            "button when no timetable is assigned to a class; "
            "(b) make the class name clickable + add a 'View Class' button "
            "on /timetable/assign/."
        )
    )
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--target-dir', default='.')
    args = parser.parse_args()

    target = Path(args.target_dir).resolve()
    _log(f"Target dir: {target}")
    _log(f"Dry-run:    {args.dry_run}")
    _log(f"Verbose:    {args.verbose}")
    print('-' * 60)

    if not (target / 'manage.py').exists():
        _log("WARNING: manage.py not found at target root. Continuing anyway.")

    ok = True

    # ---- Class-detail templates ----
    print('-' * 60)
    _log("STEP 1: Patch templates/tenant/wing_class_detailed.html "
         "(empty-state -> assign button)")
    ok &= _patch_class_detailed_template(
        target / 'templates' / 'tenant' / 'wing_class_detailed.html',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 2: Patch templates/tenant/single_class_detailed.html "
         "(empty-state -> assign button)")
    ok &= _patch_class_detailed_template(
        target / 'templates' / 'tenant' / 'single_class_detailed.html',
        args.dry_run, args.verbose,
    )

    # ---- Timetable-assignments template ----
    print('-' * 60)
    _log("STEP 3: Patch templates/tenant/timetable_assignments.html "
         "(clickable class name + View Class button)")
    ok &= _patch_timetable_assignments_template(
        target / 'templates' / 'tenant' / 'timetable_assignments.html',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    if ok:
        _log("DONE.")
        if not args.dry_run:
            _log("")
            _log("NEXT STEPS:")
            _log("  1. Restart Django dev server (no DB migration needed):")
            _log("       python3 manage.py runserver")
            _log("")
            _log("  2. Open /portal/<schema>/my-classes/<id>/ for a class")
            _log("     that has NO timetable assigned. Clicking")
            _log("     'Manage Periods Timetable' now shows a button:")
            _log("       [ Assign Periods Timetable to this Class ]")
            _log("     which deep-links to /portal/<schema>/timetable/assign/.")
            _log("")
            _log("  3. On /portal/<schema>/timetable/assign/:")
            _log("     - The class name is now a clickable link")
            _log("       -> /portal/<schema>/my-classes/<id>/")
            _log("     - A new 'View Class' button sits next to")
            _log("       'View Timetable' in the Actions column.")
        return 0
    _log("FAILED: one or more steps did not complete.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
