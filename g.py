#!/usr/bin/env python3
"""
axis_patcher.py
===============
Fixes the reconciliation wiring in axis_saas/views/periods.py.

The previous patch left two bugs:

  (A) Inside `_reconcile_timetables()`, the first executable statement
      recursively calls `_reconcile_timetables(request, schema_name)`
      instead of `_load_timetables(request, schema_name)`.
      This would have caused infinite recursion if it were ever called.

  (B) `periods_management()` still calls `_load_timetables(...)` directly,
      so the reconciler is never invoked on page load.

  Result: editing the Academic Calendar (start/end time or periods count)
  never affected existing Periods Timetables, and slot removals never
  propagated.

This patcher:
  A) Fixes the self-recursion inside `_reconcile_timetables()`.
  B) Wires `periods_management()` to call `_reconcile_timetables()`.

Idempotent. Safe to run repeatedly.

Usage:
    python3 axis_patcher.py [--dry-run] [--verbose] [--target-dir=.]
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


# =====================================================================
# Helpers
# =====================================================================
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
# Patch periods.py
# =====================================================================
def patch_periods_py(path: Path, dry_run: bool, verbose: bool) -> bool:
    _log(f"Patching {path}")
    content = _read(path)
    if content is None:
        return False

    original = content
    changes = 0

    # -----------------------------------------------------------------
    # FIX A: self-recursion inside _reconcile_timetables()
    #   `timetables = _reconcile_timetables(...)`  (inside reconcile)
    # ->`timetables = _load_timetables(...)`         (correct)
    #
    # The two-line sequence below only exists inside the reconcile
    # function (no other place has `_reconcile_timetables(...)` followed
    # by `if not timetables:`).
    # -----------------------------------------------------------------
    fix_a_old = (
        '    timetables = _reconcile_timetables(request, schema_name)\n'
        '    if not timetables:\n'
        '        return timetables\n'
    )
    fix_a_new = (
        '    timetables = _load_timetables(request, schema_name)\n'
        '    if not timetables:\n'
        '        return timetables\n'
    )
    if fix_a_old in content:
        content = content.replace(fix_a_old, fix_a_new, 1)
        changes += 1
        _log("  + fixed self-recursion inside _reconcile_timetables()")
    else:
        _log("  - self-recursion already fixed (or anchor missing)")

    # -----------------------------------------------------------------
    # FIX B: periods_management() must call _reconcile_timetables()
    #        instead of _load_timetables().
    #
    # Anchor includes the `context = {` block, so it is unique — it
    # cannot accidentally match the `timetables = _load_timetables(...)`
    # that now lives inside _reconcile_timetables (that one is followed
    # by `if not timetables:`).
    # -----------------------------------------------------------------
    fix_b_old = (
        '    timetables = _load_timetables(request, schema_name)\n'
        '\n'
        '    context = {\n'
        "        'tenant': tenant,\n"
        "        'labels': labels,\n"
        "        'slots_by_label_json': json.dumps(slots_by_label),\n"
    )
    fix_b_new = (
        '    timetables = _reconcile_timetables(request, schema_name)\n'
        '\n'
        '    context = {\n'
        "        'tenant': tenant,\n"
        "        'labels': labels,\n"
        "        'slots_by_label_json': json.dumps(slots_by_label),\n"
    )
    if fix_b_old in content:
        content = content.replace(fix_b_old, fix_b_new, 1)
        changes += 1
        _log("  + wired periods_management() to _reconcile_timetables()")
    else:
        _log("  - periods_management already wired (or anchor missing)")

    # -----------------------------------------------------------------
    # Write + verify
    # -----------------------------------------------------------------
    if changes == 0:
        _log("  no changes needed")
        # Still run verification so the user can see the current state.
    else:
        if content != original:
            if not _write(path, content, dry_run, verbose):
                return False

    # Verification: print the two lines of interest.
    if not dry_run:
        try:
            text = path.read_text(encoding='utf-8')
            has_a = (
                '    timetables = _load_timetables(request, schema_name)\n'
                '    if not timetables:\n'
                '        return timetables\n'
            ) in text
            has_b = (
                '    timetables = _reconcile_timetables(request, schema_name)\n'
                '\n'
                '    context = {\n'
            ) in text
            _log(f"  verify: reconcile uses _load_timetables -> {has_a}")
            _log(f"  verify: periods_management uses _reconcile_timetables -> {has_b}")
        except Exception as e:
            _log(f"  verify: could not re-read file: {e}")

    return True


# =====================================================================
# main
# =====================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description='Fix periods reconciliation wiring (self-recursion + call site).'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only, do not write files.')
    parser.add_argument('--verbose', action='store_true',
                        help='Verbose output.')
    parser.add_argument('--target-dir', default='.',
                        help='Project root (default: current directory).')
    args = parser.parse_args()

    target = Path(args.target_dir).resolve()
    _log(f"Target dir: {target}")
    _log(f"Dry-run:    {args.dry_run}")
    _log(f"Verbose:    {args.verbose}")
    print('-' * 60)

    if not (target / 'manage.py').exists():
        _log("WARNING: manage.py not found at target root. Continuing anyway.")

    periods_py = target / 'axis_saas' / 'views' / 'periods.py'
    if not periods_py.exists():
        _log(f"ERROR: {periods_py} not found.")
        return 2

    ok = patch_periods_py(periods_py, args.dry_run, args.verbose)

    print('-' * 60)
    if ok:
        _log("DONE.")
        if not args.dry_run:
            _log("Restart the dev server (Ctrl+C then `python3 manage.py runserver`).")
            _log("Then hard-refresh the Periods page: Ctrl+Shift+R")
            _log("")
            _log("Test flow:")
            _log("  1. Open /portal/<schema>/timetable/periods/ and generate a timetable.")
            _log("  2. Open /portal/<schema>/timetable/ and change one of the slot's")
            _log("     periods count (keep start/end same) -> reload periods page.")
            _log("     The affected day should re-derive its per-period timings.")
            _log("  3. Change that slot's start OR end time -> reload periods page.")
            _log("     The affected day should be removed from the timetable.")
        return 0
    _log("FAILED: could not patch periods.py.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
