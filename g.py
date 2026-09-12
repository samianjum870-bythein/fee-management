#!/usr/bin/env python3
"""
axis_patcher.py
===============

REMOVE_EDIT_BUTTON_v1
---------------------

Problem:
  On the class-detailed page (`wing_class_detailed.html` /
  `single_class_detailed.html`), the periods-timetable card shows three
  buttons:

      [ See Timings ] [ 📚 Manage Subjects ] [ ✎ Edit ]

  The user wants:
    - REMOVE the "✎ Edit" button entirely.
    - RENAME "📚 Manage Subjects" to "📚 Manage and Edit Subjects".

Fix:
  1. In the card-header `innerHTML`, drop the `<button ... edit-btn>`
     line and change the manage-subjects-btn label.
  2. Remove the dead `_editBtn.addEventListener(...)` wiring line
     (harmless if left, but cleaner without it).

Nothing else is touched. The inline Manage Subjects grid still works
exactly as before.

Idempotent. Safe to run multiple times.

Usage:
    python3 axis_patcher.py [--dry-run] [--verbose] [--target-dir=.]
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


MARKER = "REMOVE_EDIT_BUTTON_v1"


def _log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


def _read(path: Path):
    try:
        return path.read_text(encoding='utf-8')
    except Exception as e:
        _log(f"ERROR reading {path}: {e}")
        return None


def _write(path: Path, content: str, dry_run: bool, verbose: bool) -> bool:
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


# ---------------------------------------------------------------------
# Anchors — exact strings as they appear in the current templates
# ---------------------------------------------------------------------

# 1) The two-button block we want to collapse into a single renamed button.
BUTTONS_OLD = (
    "                '<button type=\"button\" class=\"btn-sm btn-secondary manage-subjects-btn\">&#128218; Manage Subjects</button>' +\n"
    "                '<button type=\"button\" class=\"btn-sm btn-warning edit-btn\">&#9998; Edit</button>' +\n"
)

BUTTONS_NEW = (
    "                '<button type=\"button\" class=\"btn-sm btn-secondary manage-subjects-btn\">&#128218; Manage and Edit Subjects</button>' +\n"
)


# 2) The wiring block. Drop the `_editBtn` line, keep the `_msubBtn` line.
WIRING_OLD = (
    "        var _msubBtn = header.querySelector('.manage-subjects-btn');\n"
    "        var _editBtn = header.querySelector('.edit-btn');\n"
    "        if (_msubBtn) _msubBtn.addEventListener('click', _openInlineSubjectsGrid);\n"
    "        if (_editBtn) _editBtn.addEventListener('click', _openEditTimetableForm);\n"
)

WIRING_NEW = (
    "        var _msubBtn = header.querySelector('.manage-subjects-btn');\n"
    "        if (_msubBtn) _msubBtn.addEventListener('click', _openInlineSubjectsGrid);\n"
)


# 3) Fallback wiring — the variant where Edit is still wired to the inline grid.
WIRING_FALLBACK_OLD = (
    "        var _msubBtn = header.querySelector('.manage-subjects-btn');\n"
    "        var _editBtn = header.querySelector('.edit-btn');\n"
    "        if (_msubBtn) _msubBtn.addEventListener('click', _openInlineSubjectsGrid);\n"
    "        if (_editBtn) _editBtn.addEventListener('click', _openInlineSubjectsGrid);\n"
)


def patch_template(path: Path, dry_run: bool, verbose: bool) -> bool:
    _log(f"Patching template: {path}")
    content = _read(path)
    if content is None:
        return False

    if MARKER in content:
        _log("  - already patched, skipping")
        return True

    changed = False

    # --- 1) Buttons block ---
    if BUTTONS_OLD in content:
        content = content.replace(BUTTONS_OLD, BUTTONS_NEW, 1)
        _log("  + removed edit-btn, renamed manage-subjects-btn label")
        changed = True
    else:
        # Maybe edit-btn was already removed but the label still says
        # 'Manage Subjects'. Try a label-only fix as a safety net.
        label_old = "&#128218; Manage Subjects</button>"
        label_new = "&#128218; Manage and Edit Subjects</button>"
        if label_old in content:
            content = content.replace(label_old, label_new, 1)
            _log("  + (fallback) renamed manage-subjects-btn label only")
            changed = True
        else:
            _log("  WARN: could not find buttons block / label anchor")

    # --- 2) Wiring block (Edit -> _openEditTimetableForm variant) ---
    if WIRING_OLD in content:
        content = content.replace(WIRING_OLD, WIRING_NEW, 1)
        _log("  + removed dead _editBtn wiring (edit -> inline-grid variant)")
        changed = True
    elif WIRING_FALLBACK_OLD in content:
        content = content.replace(WIRING_FALLBACK_OLD, WIRING_NEW, 1)
        _log("  + removed dead _editBtn wiring (edit -> _openEditTimetableForm variant)")
        changed = True
    else:
        _log("  - no wiring block matched (may already be patched)")

    # --- 3) Marker ---
    if MARKER not in content:
        anchor = "{% block body %}\n"
        if anchor in content:
            content = content.replace(
                anchor,
                anchor + "{# " + MARKER + " #}\n",
                1,
            )

    if not changed:
        _log("  - no changes applied")
        return True

    return _write(path, content, dry_run, verbose)


# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "REMOVE_EDIT_BUTTON_v1 -- Remove the ✎ Edit button from the "
            "class-detailed periods-timetable card and rename "
            "'📚 Manage Subjects' to '📚 Manage and Edit Subjects'."
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

    print('-' * 60)
    _log("STEP 1: templates/tenant/wing_class_detailed.html")
    ok &= patch_template(
        target / 'templates' / 'tenant' / 'wing_class_detailed.html',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 2: templates/tenant/single_class_detailed.html")
    ok &= patch_template(
        target / 'templates' / 'tenant' / 'single_class_detailed.html',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    if ok:
        _log("DONE.")
        if not args.dry_run:
            _log("")
            _log("NEXT STEPS:")
            _log("  1. Hard-refresh the browser (Ctrl+F5 / Cmd+Shift+R).")
            _log("  2. Open /portal/<schema>/my-classes/<id>/")
            _log("     - Click '📅 Manage Periods Timetable'.")
            _log("     - Card header now shows only two buttons:")
            _log("         [ See Timings ]  [ 📚 Manage and Edit Subjects ]")
            _log("     - The '✎ Edit' button is gone.")
        return 0
    _log("FAILED: one or more steps did not complete.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
