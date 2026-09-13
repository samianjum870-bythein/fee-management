#!/usr/bin/env python3
"""
axis_patcher.py
===============

LEAVE_MANAGEMENT_V2_TESTFIX_01 patcher.

Fixes 2 failing tests in `axis_saas/tests/test_leave_management.py`:

    FAIL: test_cancelled_leave_does_not_count
    FAIL: test_rejected_leave_does_not_count

Root cause
----------
Both tests used a 3-day request window (start = today+10, end = today+12)
to assert that a cancelled/rejected leave does NOT count toward the
weekly cap. But the tenant default policy has `max_leaves_per_week = 1`,
so a 3-day request is legitimately rejected by the weekly quota rule
regardless of whether the cancelled/rejected leave is being counted.

The tests were asserting the wrong thing: they were meant to confirm the
cancelled/rejected leave doesn't contribute to `used_week` — not to
bypass the weekly cap entirely.

Fix
---
Shrink both test cases to a SINGLE-DAY request (start == end == today+10).
A 1-day request fits under the default weekly cap of 1, so the assertion
`errors == []` now depends solely on the cancelled/rejected leave being
ignored.

Idempotent: re-running this patcher after the fix is a no-op.

Usage:
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py --target-dir /path/to/project
    python3 axis_patcher.py                          # apply in-place
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


TARGET_REL_PATH = Path('axis_saas') / 'tests' / 'test_leave_management.py'


# ------------------------------------------------------------------
# Old / new bodies for the two affected test methods.
# We use literal string replacement (not regex) so the pattern is
# unambiguous and won't accidentally match other tests.
# ------------------------------------------------------------------

OLD_CANCELLED_BLOCK = """    def test_cancelled_leave_does_not_count(self):
        self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=12),
            status='cancelled',
        )
        errors = self._validate(10, 12)
        self.assertEqual(errors, [])
"""

NEW_CANCELLED_BLOCK = """    def test_cancelled_leave_does_not_count(self):
        # Single-day request so the default weekly cap (1 day/week)
        # doesn't fire and mask the real assertion.
        self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            status='cancelled',
        )
        errors = self._validate(10, 10)
        self.assertEqual(errors, [])
"""

OLD_REJECTED_BLOCK = """    def test_rejected_leave_does_not_count(self):
        self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=12),
            status='rejected',
        )
        errors = self._validate(10, 12)
        self.assertEqual(errors, [])
"""

NEW_REJECTED_BLOCK = """    def test_rejected_leave_does_not_count(self):
        # Single-day request so the default weekly cap (1 day/week)
        # doesn't fire and mask the real assertion.
        self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            status='rejected',
        )
        errors = self._validate(10, 10)
        self.assertEqual(errors, [])
"""


# Marker prefix used for idempotency: if the file already contains this
# exact comment, we assume the fix has been applied.
FIX_MARKER = "Single-day request so the default weekly cap"


def patch_file(path, dry_run, verbose):
    try:
        content = path.read_text(encoding='utf-8')
    except Exception as exc:
        log(f"ERROR reading {path}: {exc}")
        return False

    if FIX_MARKER in content:
        log(f"SKIP (already fixed): {path}")
        return True

    original = content

    # ------- Fix 1: cancelled leave test -------
    if OLD_CANCELLED_BLOCK in content:
        content = content.replace(OLD_CANCELLED_BLOCK, NEW_CANCELLED_BLOCK, 1)
        if verbose:
            log("Patched test_cancelled_leave_does_not_count")
    else:
        # Try a tolerant regex in case whitespace drifted.
        pattern = re.compile(
            r"(def test_cancelled_leave_does_not_count\(self\):\n"
            r"(?:.*\n)*?"
            r"        errors = self\._validate\(10, 12\)\n"
            r"        self\.assertEqual\(errors, \[\]\)\n)",
        )
        m = pattern.search(content)
        if not m:
            log("WARN: could not locate test_cancelled_leave_does_not_count body")
        else:
            content = content[:m.start()] + NEW_CANCELLED_BLOCK + content[m.end():]
            if verbose:
                log("Patched test_cancelled_leave_does_not_count (regex fallback)")

    # ------- Fix 2: rejected leave test -------
    if OLD_REJECTED_BLOCK in content:
        content = content.replace(OLD_REJECTED_BLOCK, NEW_REJECTED_BLOCK, 1)
        if verbose:
            log("Patched test_rejected_leave_does_not_count")
    else:
        pattern = re.compile(
            r"(def test_rejected_leave_does_not_count\(self\):\n"
            r"(?:.*\n)*?"
            r"        errors = self\._validate\(10, 12\)\n"
            r"        self\.assertEqual\(errors, \[\]\)\n)",
        )
        m = pattern.search(content)
        if not m:
            log("WARN: could not locate test_rejected_leave_does_not_count body")
        else:
            content = content[:m.start()] + NEW_REJECTED_BLOCK + content[m.end():]
            if verbose:
                log("Patched test_rejected_leave_does_not_count (regex fallback)")

    if content == original:
        log(f"NO CHANGE: {path}")
        return True

    if dry_run:
        log(f"DRY-RUN: would patch {path} "
            f"({len(original)} -> {len(content)} bytes)")
        return True

    try:
        path.write_text(content, encoding='utf-8')
    except Exception as exc:
        log(f"ERROR writing {path}: {exc}")
        return False

    log(f"PATCHED: {path}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Fix the two failing leave-management tests that assumed a '
            '3-day request would pass the default weekly cap of 1 day.'
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
        log("Next step: python manage.py test axis_saas.tests.test_leave_management")
    return 0


if __name__ == '__main__':
    sys.exit(main())
