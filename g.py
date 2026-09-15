#!/usr/bin/env python3
"""
axis_patcher.py
===============

LEAVE_AUTOSUSPENSION_FIX_V4_2
-----------------------------

Two tests in test_leave_management_v2.py were failing:

  1. test_reject_response_carries_auto_suspension
  2. test_counts_rejections_after_latest_suspension

Both trace back to `_rejections_since_last_suspension`, which had two
behavioural bugs introduced by the V3 "hardening":

  a) When no prior suspension existed, it returned 0 unconditionally.
     Combined with the caller in `leave_reject` (`count >= threshold`
     to trigger the auto-suspension), that made it impossible for a
     fresh staff member to EVER be auto-suspended, no matter how many
     rejections they accumulated. The counter could only reach the
     threshold if a suspension already existed — circular.

  b) The cutoff was `latest.created_at`, which is `auto_now_add=True`
     on LeaveSuspension. So a back-dated manual suspension (e.g.
     start_date = today - 10 days, recorded today) would set the
     cutoff to "now", ignoring every rejection that happened during
     the suspension window.

Fix
---
`_rejections_since_last_suspension` now:

  * Uses `latest.start_date` as the cutoff when a prior suspension
    exists. A suspension is effective from its start_date, not from
    the day the admin typed it in.

  * Counts every rejected leave when no prior suspension exists. This
    is the only way the first auto-suspension can ever fire.

  * Keeps the "reviewed_at IS NULL falls back to created_at" branch so
    legacy rows still count.

The already-existing `test_zero_when_no_prior_suspension` test is
renamed and re-asserted, because its original assertion (0) encoded
the broken V3 behaviour.

Idempotent. Safe to re-run.

Usage:
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py                 # apply in place
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def _replace_once(content, old, new, label, verbose):
    if not old:
        return content, False
    if new and new in content and old not in content:
        if verbose:
            log(f"  SKIP (already patched): {label}")
        return content, False
    if old not in content:
        log(f"  WARN: {label} — anchor not found; leaving it alone.")
        return content, False
    content = content.replace(old, new, 1)
    if verbose:
        log(f"  patched: {label}")
    return content, True


def _write(path, content, dry_run, verbose, label):
    if dry_run:
        log(f"  DRY-RUN: would write {path} ({label})")
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as exc:
        log(f"  ERROR writing {path}: {exc}")
        return False


def _read(path, verbose):
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return None
    try:
        return path.read_text(encoding='utf-8')
    except Exception as exc:
        log(f"ERROR reading {path}: {exc}")
        return None


# =====================================================================
# 1) views/leave_management.py — fix _rejections_since_last_suspension
# =====================================================================
LEAVE_VIEWS_REL = Path('axis_saas') / 'views' / 'leave_management.py'

REJECTIONS_OLD = '''def _rejections_since_last_suspension(staff):
    """Count rejections *after* the most recent suspension.

    Two edge cases handled:
      * the "no prior suspension" branch returns 0 rather than all-time
        rejections, so a staff member with 3 historical rejections is
        not auto-suspended on the very next one.
      * reviewed_at is nullable; rows with reviewed_at=NULL that were
        created after the cut-off are still counted.
    """
    latest = (
        LeaveSuspension.objects
        .filter(staff=staff)
        .order_by('-created_at')
        .first()
    )
    qs = LeaveRequest.objects.filter(staff=staff, status='rejected')
    if latest is not None:
        cutoff = latest.created_at
        qs = qs.filter(
            Q(reviewed_at__gt=cutoff) |
            Q(reviewed_at__isnull=True, created_at__gt=cutoff)
        )
    else:
        # No prior suspension — rejections accumulated all-time don't
        # count towards the NEXT suspension.
        return 0
    return qs.count()
'''

REJECTIONS_NEW = '''def _rejections_since_last_suspension(staff):
    """Count rejections since the most recent suspension.

    LEAVE_AUTOSUSPENSION_FIX_V4_2
    -----------------------------
    Two changes from the V3 implementation:

    1. Cutoff is `latest.start_date`, not `latest.created_at`. The
       latter is `auto_now_add=True`, so a suspension back-dated by
       the admin (start_date = today - 10 days, recorded today) would
       set the cutoff to "now" and silently ignore every rejection
       from the suspension window. A suspension is effective from its
       start_date — that is the correct cut-off.

    2. When no prior suspension exists, count every rejected leave.
       The V3 branch `return 0` made it impossible for a fresh staff
       member to ever cross the auto-suspension threshold: the counter
       could only reach the threshold if a suspension already existed,
       which is circular. In practice, tenant leave volume is small,
       and stale rejections are better bounded by a rolling policy
       window (future work) than by a hard "0 forever" gate.

    Rejections whose `reviewed_at` is NULL still count as long as
    their `created_at` falls on or after the cutoff, so legacy rows
    from before `reviewed_at` was populated are not silently dropped.
    """
    latest = (
        LeaveSuspension.objects
        .filter(staff=staff)
        .order_by('-created_at')
        .first()
    )
    qs = LeaveRequest.objects.filter(staff=staff, status='rejected')
    if latest is not None:
        cutoff_date = latest.start_date
        qs = qs.filter(
            Q(reviewed_at__date__gte=cutoff_date) |
            Q(reviewed_at__isnull=True, created_at__date__gte=cutoff_date)
        )
    return qs.count()
'''


def patch_leave_views(root, dry_run, verbose):
    path = root / LEAVE_VIEWS_REL
    content = _read(path, verbose)
    if content is None:
        return False

    if 'LEAVE_AUTOSUSPENSION_FIX_V4_2' in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, changed = _replace_once(
        content, REJECTIONS_OLD, REJECTIONS_NEW,
        "_rejections_since_last_suspension (correct semantics)", verbose,
    )
    if not changed:
        return False
    return _write(path, content, dry_run, verbose,
                  "auto-suspension counter fix")


# =====================================================================
# 2) tests/test_leave_management_v2.py — re-assert the correct behaviour
# =====================================================================
TESTS_REL = Path('axis_saas') / 'tests' / 'test_leave_management_v2.py'

TEST_OLD = '''    def test_zero_when_no_prior_suspension(self):
        """A tenant with a staff member who has never been suspended
        sees 0, not all-time rejections. Prevents accidentally
        auto-suspending on the very first rejection."""
        self._make_leave(
            _today() - timedelta(days=10),
            _today() - timedelta(days=10),
            status='rejected',
        )
        with schema_context(self.tenant.schema_name):
            count = _rejections_since_last_suspension(self.staff)
        self.assertEqual(count, 0)
'''

TEST_NEW = '''    def test_counts_all_rejections_when_no_prior_suspension(self):
        """LEAVE_AUTOSUSPENSION_FIX_V4_2: with no prior suspension,
        count every rejected leave.

        The V3 test (previously named
        ``test_zero_when_no_prior_suspension``) asserted 0 here, which
        made the auto-suspension feature unreachable for a fresh staff
        member: the counter could never reach the threshold because it
        always started at 0 and had no way to increment. The correct
        behaviour — asserted by
        ``test_reject_response_carries_auto_suspension`` — is that the
        very first rejection on a fresh staff member counts towards
        the threshold.
        """
        self._make_leave(
            _today() - timedelta(days=10),
            _today() - timedelta(days=10),
            status='rejected',
        )
        with schema_context(self.tenant.schema_name):
            count = _rejections_since_last_suspension(self.staff)
        self.assertEqual(count, 1)
'''


def patch_tests(root, dry_run, verbose):
    path = root / TESTS_REL
    content = _read(path, verbose)
    if content is None:
        return False

    if 'LEAVE_AUTOSUSPENSION_FIX_V4_2' in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, changed = _replace_once(
        content, TEST_OLD, TEST_NEW,
        "test_zero_when_no_prior_suspension -> counts_all_rejections", verbose,
    )
    if not changed:
        return False
    return _write(path, content, dry_run, verbose,
                  "test updated to match correct counter semantics")


# =====================================================================
# Main
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "LEAVE_AUTOSUSPENSION_FIX_V4_2 — fix the two tests that "
            "exposed a design bug in _rejections_since_last_suspension. "
            "The counter now (a) uses the last suspension's start_date "
            "as the cutoff instead of its auto_now_add created_at, and "
            "(b) counts all rejections when no prior suspension exists, "
            "so a fresh staff member can actually cross the threshold."
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

    steps = [
        ("views/leave_management.py — _rejections_since_last_suspension",
         patch_leave_views),
        ("tests/test_leave_management_v2.py — re-assert correct behaviour",
         patch_tests),
    ]

    ok = True
    for i, (label, fn) in enumerate(steps, start=1):
        log(f"--- {i}/{len(steps)}: {label} ---")
        try:
            ok &= fn(root, args.dry_run, args.verbose)
        except Exception as exc:
            log(f"  ERROR in {label}: {exc}")
            ok = False

    if not ok:
        log("One or more steps failed. See messages above.")
        return 2

    log("Done.")
    if args.dry_run:
        log("Re-run without --dry-run to apply.")
    else:
        log("Next steps:")
        log("  1. python manage.py test axis_saas.tests.test_leave_management_v2")
        log("     All 20 tests should now pass.")
        log("  2. Sanity check by hand: on a fresh tenant, set")
        log("     'Auto-suspend after N rejected leaves' to 1 in the")
        log("     policy modal, reject a pending leave, and confirm the")
        log("     auto-suspension alert fires.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
