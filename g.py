#!/usr/bin/env python3
"""
axis_patcher.py — ASSIGN_TEACHERS_HARDENING_V5_3
=================================================

Fixes the N+1 regression guard that V5_2 wrote incorrectly.

Root cause
----------
The V5_2 patch changed the ceiling from 30 to 20 in the test:

    with self.assertNumQueries(20):
        response = self.client.get(...)

But Django's ``assertNumQueries(N)`` requires an EXACT match, not an
upper bound. It asserts ``executed == N``. So on every run the test
fails the moment the real query count isn't precisely 20:

    AssertionError: 12 != 20 : 12 queries executed, 20 expected

The intent of the guard is a ceiling: "fail if the N+1 sneaks back in",
not "fail unless the count is exactly N". Anything that changes the
count by a single query — middleware, session backend, cache warm-up,
Django version — breaks the test for no reason.

Fix
---
Replace ``assertNumQueries`` with ``CaptureQueriesContext`` and an
explicit ``assertLessEqual`` on the captured query count. Same
regression protection, no false positives.

Files touched:
    axis_saas/tests/test_assign_teachers_page.py

Idempotent. Safe to re-run.

Usage:
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


# --------------------------------------------------------------------- helpers

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path, verbose=False):
    if not path.is_file():
        log(f"  ERROR: not found: {path}")
        return None
    try:
        return path.read_text(encoding='utf-8')
    except Exception as e:
        log(f"  ERROR reading {path}: {e}")
        return None


def write_file(path, content, dry_run=False, verbose=False, label=""):
    if dry_run:
        log(f"  DRY-RUN: would write {path} ({label})")
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as e:
        log(f"  ERROR writing {path}: {e}")
        return False


def replace_once(path, old, new, dry_run, verbose, label):
    content = read_file(path, verbose)
    if content is None:
        return False
    if old not in content:
        if new in content:
            log(f"  SKIP (already applied): {label}")
            return True
        log(f"  WARN: pattern not found: {label}")
        if verbose:
            log("    expected prefix:\n" + old[:500])
        return False
    new_content = content.replace(old, new, 1)
    return write_file(path, new_content, dry_run, verbose, label)


# --------------------------------------------------------------------- paths

PAGE_TEST_REL = Path('axis_saas') / 'tests' / 'test_assign_teachers_page.py'


# --------------------------------------------------------------------- patches

def patch_ceiling_to_max_queries(root, args):
    """Replace assertNumQueries(N) with a CaptureQueriesContext <= N check.

    Handles both the current 20 ceiling and the older 30 ceiling so
    the patcher is safe to run whether or not V5_2 was applied first.
    """
    path = root / PAGE_TEST_REL
    content = read_file(path, args.verbose)
    if content is None:
        return False

    # Marker check for idempotency.
    if 'HARDENING_V5_3' in content:
        log("  SKIP (already applied): HARDENING_V5_3 marker present")
        return True

    # The test currently has one of:
    #     with self.assertNumQueries(20):
    # or  with self.assertNumQueries(30):
    # We handle both by scanning for the exact block from the method
    # header down to the final assertion of the method. This is more
    # robust than an exact-string match against the surrounding
    # docstring (which differs between V5 and V5_2).
    method_start = (
        "    def test_page_handles_five_classes_without_n_plus_one(self):\n"
    )
    if method_start not in content:
        log("  WARN: target test method not found")
        return False

    idx = content.index(method_start)
    # Find the end of this method: the next `\n    def ` at column 4.
    next_def = content.find('\n    def ', idx + len(method_start))
    if next_def == -1:
        # Method is the last in the class; take everything to EOF.
        end_idx = len(content)
    else:
        end_idx = next_def

    method_body = content[idx:end_idx]

    # Build the replacement method body. We keep the docstring heading
    # but add a clear note about why it is a range check, not exact.
    new_method = (
        "    def test_page_handles_five_classes_without_n_plus_one(self):\n"
        "        \"\"\"N+1 regression guard: with 5 timetabled classes, the page\n"
        "        must not fire a per-class query for the parent wing category\n"
        "        or for the timetable label.\n"
        "\n"
        "        HARDENING_V5_3: ``assertNumQueries(N)`` asserts an EXACT match,\n"
        "        not an upper bound. The intent of this guard is a ceiling:\n"
        "        fail if the N+1 sneaks back in, tolerate small fluctuations\n"
        "        from middleware / session / cache warm-up. We capture the\n"
        "        queries with CaptureQueriesContext and assert ``<= ceiling``.\n"
        "\n"
        "        The real count is around 12. A regression on either\n"
        "        select_related path (``school_class__wing_category__parent``\n"
        "        or ``timetable__label``) adds ~5 queries, pushing the count\n"
        "        past 20. The ceiling of 20 therefore catches the regression\n"
        "        without being brittle.\n"
        "        \"\"\"\n"
        "        from django.db import connection\n"
        "        from django.test.utils import CaptureQueriesContext\n"
        "\n"
        "        for i in range(5):\n"
        "            self._make_class_with_timetable(f'Grade {i}', 'A', periods=8)\n"
        "\n"
        "        with CaptureQueriesContext(connection) as ctx:\n"
        "            response = self.client.get(self.url('timetable/assign-teachers/'))\n"
        "        self.assertEqual(response.status_code, 200, response.content)\n"
        "        self.assertEqual(len(response.context['class_rows']), 5)\n"
        "        self.assertLessEqual(\n"
        "            len(ctx.captured_queries), 20,\n"
        "            'N+1 regression: %d queries executed, ceiling 20'\n"
        "            % len(ctx.captured_queries),\n"
        "        )\n"
    )

    new_content = content[:idx] + new_method + content[end_idx:]
    return write_file(
        path, new_content, args.dry_run, args.verbose,
        "replace assertNumQueries with CaptureQueriesContext <= 20",
    )


# --------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(
        description=(
            'ASSIGN_TEACHERS_HARDENING_V5_3 — replace the brittle '
            'assertNumQueries(20) exact-match check with a '
            'CaptureQueriesContext <= 20 ceiling.'
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

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")

    results = []

    log("--- V5_3: swap assertNumQueries for CaptureQueriesContext ---")
    results.append(patch_ceiling_to_max_queries(root, args))

    ok = sum(1 for r in results if r)
    total = len(results)

    log("=" * 60)
    if ok == total:
        log(f"All {total} step(s) completed successfully.")
        if args.dry_run:
            log("Re-run without --dry-run to apply.")
        else:
            log("Next steps:")
            log("  1. Run: python manage.py test axis_saas.tests.test_assign_teachers_page")
            log("     Expected: 5 tests, all passing.")
        return 0

    log(f"{total - ok} of {total} step(s) failed. See messages above.")
    return 2


if __name__ == '__main__':
    sys.exit(main())
