#!/usr/bin/env python3
"""
axis_patcher_fix.py
===================
Fixes the reconciliation bug that made Academic Calendar edits NOT
propagate to existing Periods Timetables.

Root causes (present in the current `axis_saas/views/periods.py`):
  A. `_reconcile_timetables()` is defined, but its first line is:
         timetables = _reconcile_timetables(request, schema_name)
     which is an INFINITE SELF-RECURSION instead of calling
     `_load_timetables(...)`.
  B. Because that buggy string contains the substring
     `timetables = _reconcile_timetables`, the previous patcher's
     idempotency check skipped replacing the real call site inside
     `periods_management()` — so the page kept calling
     `_load_timetables()` and never reconciled.

This patcher:
  1) Fixes the recursion bug inside `_reconcile_timetables()`.
  2) Rewires `periods_management()` to call `_reconcile_timetables()`.
  3) Leaves everything else untouched (templates, other views).

Idempotent. Safe to run multiple times.

Usage:
    python3 axis_patcher_fix.py [--dry-run] [--verbose] [--target-dir=.]
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


def _replace_once(text, old, new, label):
    if old not in text:
        _log(f"  NOT FOUND anchor: {label}")
        return text, False
    return text.replace(old, new, 1), True


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
    # FIX A: recursion bug inside _reconcile_timetables()
    #        `timetables = _reconcile_timetables(request, schema_name)`
    #  ->    `timetables = _load_timetables(request, schema_name)`
    # -----------------------------------------------------------------
    bug_anchor = (
        '    """\n'
        '    Reconcile session-stored timetables against current DaySchedule rows.\n'
    )
    bug_old = (
        '    timetables = _reconcile_timetables(request, schema_name)\n'
        '    if not timetables:\n'
        '        return timetables\n'
    )
    bug_new = (
        '    timetables = _load_timetables(request, schema_name)\n'
        '    if not timetables:\n'
        '        return timetables\n'
    )

    if bug_anchor in content and bug_old in content:
        # Only replace if it actually appears inside the reconcile function.
        # We locate the function body and do a scoped replace.
        idx = content.find(bug_anchor)
        if idx != -1:
            # Find the next occurrence of bug_old after the docstring start
            sub = content[idx:]
            if bug_old in sub:
                sub_fixed = sub.replace(bug_old, bug_new, 1)
                content = content[:idx] + sub_fixed
                changes += 1
                _log("  + fixed recursion bug inside _reconcile_timetables()")
            else:
                _log("  - recursion bug line already fixed")
        else:
            _log("  - could not locate _reconcile_timetables body")
    else:
        _log("  - recursion bug line already fixed (or anchor missing)")

    # -----------------------------------------------------------------
    # FIX B: rewire periods_management() to use _reconcile_timetables()
    #        Only replace the call site in periods_management, NOT the
    #        one inside _reconcile_timetables (we already handled that).
    # -----------------------------------------------------------------
    # Use a context-unique anchor: the closing brace of the `with` block
    # followed by the timetables assignment, followed by the context dict.
    ctx_old = (
        "        }\n"
        "\n"
        "    timetables = _load_timetables(request, schema_name)\n"
        "\n"
        "    context = {\n"
        "        'tenant': tenant,\n"
        "        'labels': labels,\n"
    )
    ctx_new = (
        "        }\n"
        "\n"
        "    timetables = _reconcile_timetables(request, schema_name)\n"
        "\n"
        "    context = {\n"
        "        'tenant': tenant,\n"
        "        'labels': labels,\n"
    )

    if ctx_old in content:
        content = content.replace(ctx_old, ctx_new, 1)
        changes += 1
        _log("  + rewired periods_management() to call _reconcile_timetables()")
    else:
        # Maybe it was already rewired?
        if (
            "    timetables = _reconcile_timetables(request, schema_name)\n"
            "    context = {\n"
            in content.replace("        }\n\n", "")
        ):
            _log("  - periods_management already reconciled")
        else:
            _log("  WARN: could not find periods_management call-site anchor")

    if changes == 0:
        _log("  no changes needed")
        return True

    if content != original:
        return _write(path, content, dry_run, verbose)
    return True


# =====================================================================
# main
# =====================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description='Fix periods reconciliation bug (recursion + wiring).'
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

    ok = patch_periods_py(periods_py, args.dry_run, args.verbose)

    print('-' * 60)
    if ok:
        _log("DONE.")
        if not args.dry_run:
            _log("Restart the dev server (Ctrl+C then `python3 manage.py runserver`).")
            _log("Then hard-refresh the Periods page: Ctrl+Shift+R")
        return 0
    _log("FAILED: could not patch periods.py.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
