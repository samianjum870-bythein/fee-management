#!/usr/bin/env python3
"""
axis_patcher.py
===============

LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1
-------------------------------------

Verification first
------------------
I checked the current Leave Management code against the four things
you asked me to verify:

  1. Do weekly holidays (WeeklyHoliday) get excluded from leave quota
     counting?

       ✅ YES.  `_working_days_set()` in `views/leave_management.py`
       reads `WeeklyHoliday.objects.values_list('day_of_week')`, and
       both `_validate_leave_dates()` (admin) and `_month_used_days()` /
       `_week_used_days()` (staff) skip non-working days when the
       tenant policy has `count_working_days_only=True`.  The cache
       entry is invalidated by post_save / post_delete signals on
       WeeklyHoliday (see `axis_saas/signals.py`).  No change needed.

  2. Does the weekly-holiday edit propagate immediately to quota
     counting?

       ✅ YES.  The signal-driven cache invalidation covers it.

  3. Is `max_leaves_per_month` capped sensibly?

       ✅ YES.  It is clamped to [1, 31] on save.

  4. Is `max_leaves_per_week` capped at the *number of working days*
     for the tenant, not the hard-coded 7?

       ❌ NO.  This is the only real problem.

The problem in detail
---------------------
`leave_policy_save` currently does:

    policy.max_leaves_per_week = _to_int(
        'max_leaves_per_week', policy.max_leaves_per_week, 1, 7,
    )

The `hi=7` bound is a plain calendar-week cap.  If a tenant marks
Saturday + Sunday as weekly holidays (5 working days), the admin can
still set `max_leaves_per_week=7`, even though there are only 5
working days a week.  The UI input on the policy modal has `min="1"`
but no `max` attribute, so the browser lets the admin type 11, the
server silently clamps it to 7, and the field is not tied to the
tenant's real working-days count.  Confusing, and semantically wrong:
"max leave days per week" cannot exceed the number of days the school
is actually open.

On the other hand — and this is important — the *validation* logic
already behaves correctly in the presence of a too-high policy value:
`_validate_leave_dates` counts only unique working days, so an
impossible "7" against a 4-day working week will never accept more
than 4 leave days.  The bug is therefore only in the policy UI and
in the upper bound of `_to_int`, not in enforcement.

What this patch does
--------------------
  A. Add `_working_days_count()` helper to `views/leave_management.py`.
  B. In `leave_policy_save`, clamp `max_leaves_per_week` to
     `min(7, max(1, working_days_count))` instead of the constant 7.
     If a stale value is already above the cap in the DB, the next
     save will bring it down.
  C. Pass `working_days_count` to the tenant leave_management template
     so the policy modal can:
        - show a `max="N"` attribute on the weekly input, so the
          browser refuses anything above N,
        - render a hint like
          "Your school has 5 working days per week."
  D. Add `working_days_count` to the policy JSON so the client-side
     modal can also refresh the field when the modal opens.

Everything else in the leave system stays as-is (weekly-holiday
exclusion, monthly cap, suspension system, working-day counters,
auto-suspension, CSRF, row-locks, pagination).  This patch touches
ONLY the weekly-cap boundary and the modal's input constraints.

Idempotent. Safe to re-run.

Usage:
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py --target-dir /path/to/project
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
# 1) views/leave_management.py
#    - add _working_days_count() helper
#    - clamp max_leaves_per_week dynamically
#    - expose working_days_count in the page context and in policy_json
# =====================================================================
LEAVE_VIEWS_REL = Path('axis_saas') / 'views' / 'leave_management.py'

# --- 1a) Add the helper right after _working_days_set_cache_clear ---
WD_COUNT_ANCHOR = '''def _expire_stale_suspensions():
    """Bulk-deactivate suspensions whose end_date has passed.'''

WD_COUNT_NEW = '''def _working_days_count():
    """Number of working days per week for the current tenant.

    LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1
    ------------------------------------
    A week has 7 calendar days, but a school that marks Saturday and
    Sunday as weekly holidays only has 5 real working days.  The
    maximum value the admin can meaningfully put in
    `LeavePolicy.max_leaves_per_week` is that working-days count, not
    a hard 7.  This helper exposes it so both `leave_policy_save` and
    the policy modal template agree on the same bound.
    """
    try:
        return max(1, min(7, len(_working_days_set())))
    except Exception:
        return 7


def _expire_stale_suspensions():
    """Bulk-deactivate suspensions whose end_date has passed.'''


# --- 1b) Replace the hardcoded hi=7 clamp in leave_policy_save -------
POLICY_CLAMP_OLD = '''            # Upper bounds are the maximum meaningful value for each
            # field (7 days in a week, 31 days in a month, 90-day
            # consecutive cap to match the docs, 100 rejections, and
            # MAX_SUSPENSION_DAYS for suspension length).
            policy.max_leaves_per_month = _to_int(
                'max_leaves_per_month', policy.max_leaves_per_month, 1, 31,
            )
            policy.max_leaves_per_week = _to_int(
                'max_leaves_per_week', policy.max_leaves_per_week, 1, 7,
            )
            policy.max_consecutive_days = _to_int(
                'max_consecutive_days', policy.max_consecutive_days, 1, 90,
            )'''

POLICY_CLAMP_NEW = '''            # Upper bounds are the maximum meaningful value for each
            # field.
            #
            # LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1: `max_leaves_per_week`
            # is capped at the tenant's actual working-days count, not
            # the fixed calendar-week 7.  A school that takes Saturday
            # and Sunday off has 5 working days per week, so 5 is the
            # only meaningful upper bound.  `_working_days_count()`
            # computes this from the WeeklyHoliday table (already
            # cached and signal-invalidated elsewhere in this module).
            _week_hi = _working_days_count()
            policy.max_leaves_per_month = _to_int(
                'max_leaves_per_month', policy.max_leaves_per_month, 1, 31,
            )
            policy.max_leaves_per_week = _to_int(
                'max_leaves_per_week', policy.max_leaves_per_week,
                1, _week_hi,
            )
            policy.max_consecutive_days = _to_int(
                'max_consecutive_days', policy.max_consecutive_days, 1, 90,
            )'''


# --- 1c) Include working_days_count in the page context --------------
CONTEXT_ANCHOR = '''        page_context = {
            'page_number': page_obj.number,
            'num_pages': paginator.num_pages,
            'has_previous': page_obj.has_previous(),
            'has_next': page_obj.has_next(),
            'previous_page_number': (
                page_obj.previous_page_number() if page_obj.has_previous() else None
            ),
            'next_page_number': (
                page_obj.next_page_number() if page_obj.has_next() else None
            ),
            'total_count': paginator.count,
            'page_size': ADMIN_LEAVES_PAGE_SIZE,
        }'''

CONTEXT_NEW = '''        page_context = {
            'page_number': page_obj.number,
            'num_pages': paginator.num_pages,
            'has_previous': page_obj.has_previous(),
            'has_next': page_obj.has_next(),
            'previous_page_number': (
                page_obj.previous_page_number() if page_obj.has_previous() else None
            ),
            'next_page_number': (
                page_obj.next_page_number() if page_obj.has_next() else None
            ),
            'total_count': paginator.count,
            'page_size': ADMIN_LEAVES_PAGE_SIZE,
        }

        # LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1: expose the tenant's
        # working-days count to the template so the policy modal can
        # constrain the weekly input and show a hint.
        working_days_count = _working_days_count()
        working_days_names = [
            name for d, name in [
                (0, 'Mon'), (1, 'Tue'), (2, 'Wed'),
                (3, 'Thu'), (4, 'Fri'), (5, 'Sat'), (6, 'Sun'),
            ] if d in _working_days_set()
        ]'''

# Also add the count into the rendered context dict.
CTX_DICT_ANCHOR = '''        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
        'max_suspension_days': MAX_SUSPENSION_DAYS,
    }
    response = render(request, 'tenant/leave_management.html', context)'''

CTX_DICT_NEW = '''        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
        'max_suspension_days': MAX_SUSPENSION_DAYS,
        # LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1
        'working_days_count': working_days_count,
        'working_days_names': ', '.join(working_days_names) or 'none',
    }
    response = render(request, 'tenant/leave_management.html', context)'''

# Also add the count into policy_data so JS can read it.
POLICY_DATA_ANCHOR = '''        policy_data = {
            'max_leaves_per_month': policy.max_leaves_per_month,
            'max_leaves_per_week': policy.max_leaves_per_week,
            'max_consecutive_days': policy.max_consecutive_days,
            'allow_backdated': policy.allow_backdated,
            'count_approved_only': policy.count_approved_only,
            'count_working_days_only': policy.count_working_days_only,
            'max_rejections_before_suspension': policy.max_rejections_before_suspension,
            'suspension_days': policy.suspension_days,
        }'''

POLICY_DATA_NEW = '''        policy_data = {
            'max_leaves_per_month': policy.max_leaves_per_month,
            'max_leaves_per_week': policy.max_leaves_per_week,
            'max_consecutive_days': policy.max_consecutive_days,
            'allow_backdated': policy.allow_backdated,
            'count_approved_only': policy.count_approved_only,
            'count_working_days_only': policy.count_working_days_only,
            'max_rejections_before_suspension': policy.max_rejections_before_suspension,
            'suspension_days': policy.suspension_days,
            # LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1: consumed by the
            # policy modal's client-side code so it can set max="N"
            # on the weekly input.
            'working_days_count': _working_days_count(),
        }'''


def patch_leave_views(root, dry_run, verbose):
    path = root / LEAVE_VIEWS_REL
    content = _read(path, verbose)
    if content is None:
        return False

    if 'LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1' in content:
        log(f"SKIP (already patched): {path}")
        return True

    changed_any = False

    content, c = _replace_once(
        content, WD_COUNT_ANCHOR, WD_COUNT_NEW,
        "add _working_days_count helper", verbose,
    )
    changed_any = changed_any or c

    content, c = _replace_once(
        content, POLICY_CLAMP_OLD, POLICY_CLAMP_NEW,
        "clamp max_leaves_per_week by working days", verbose,
    )
    changed_any = changed_any or c

    content, c = _replace_once(
        content, CONTEXT_ANCHOR, CONTEXT_NEW,
        "compute working_days_count + names in view", verbose,
    )
    changed_any = changed_any or c

    content, c = _replace_once(
        content, CTX_DICT_ANCHOR, CTX_DICT_NEW,
        "inject working_days_count into page context", verbose,
    )
    changed_any = changed_any or c

    content, c = _replace_once(
        content, POLICY_DATA_ANCHOR, POLICY_DATA_NEW,
        "add working_days_count to policy_json", verbose,
    )
    changed_any = changed_any or c

    if not changed_any:
        log(f"  ERROR: no anchors matched in {path}")
        return False
    return _write(path, content, dry_run, verbose,
                  "working-days weekly cap")


# =====================================================================
# 2) templates/tenant/leave_management.html
#    - add `max="{{ working_days_count }}"` on the weekly input
#    - add a small hint under it showing the school's open days
#    - make openPolicyModal / savePolicy JS aware of the new cap
# =====================================================================
ADMIN_TPL_REL = Path('templates') / 'tenant' / 'leave_management.html'

# --- 2a) Add max + hint on the weekly input --------------------------
POLWEEK_OLD = '''        <div class="row">
            <label for="polWeek">Max leave days per week</label>
            <input type="number" id="polWeek" min="1" style="width:100%; padding:0.55rem 0.75rem; border-radius:0.65rem; border:1px solid var(--border); background:var(--surface-alt); color:var(--text);">
        </div>'''

POLWEEK_NEW = '''        <!-- LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1: the upper bound is
             the tenant's actual working-days count (7 minus their
             WeeklyHoliday entries), not a hard-coded 7. -->
        <div class="row">
            <label for="polWeek">Max leave days per week</label>
            <input type="number" id="polWeek" min="1"
                   max="{{ working_days_count }}"
                   style="width:100%; padding:0.55rem 0.75rem; border-radius:0.65rem; border:1px solid var(--border); background:var(--surface-alt); color:var(--text);">
            <p class="page-desc" style="font-size:0.75rem; margin-top:0.35rem;">
                Your school has <strong>{{ working_days_count }}</strong> working day{{ working_days_count|pluralize }}
                per week ({{ working_days_names }}).
                Weekly holidays are never counted towards leave quotas.
            </p>
        </div>'''

# --- 2b) Make openPolicyModal set max dynamically --------------------
OPEN_POLICY_OLD = '''    window.openPolicyModal = function() {
        document.getElementById('polMonth').value = POLICY.max_leaves_per_month;
        document.getElementById('polWeek').value = POLICY.max_leaves_per_week;
        document.getElementById('polConsec').value = POLICY.max_consecutive_days;'''

OPEN_POLICY_NEW = '''    window.openPolicyModal = function() {
        // LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1: enforce the tenant's
        // working-days cap on the weekly input.  The server also
        // clamps, but setting max + value here means the browser
        // refuses out-of-range numbers before they are ever submitted.
        var _weekMax = parseInt(POLICY.working_days_count, 10) || 7;
        var _polWeekEl = document.getElementById('polWeek');
        _polWeekEl.setAttribute('max', String(_weekMax));
        // If the stored value is above the cap (legacy data from
        // before this fix), show it clamped, so the next save will
        // persist the corrected value.
        var _storedWeek = parseInt(POLICY.max_leaves_per_week, 10) || 1;
        _polWeekEl.value = Math.min(_storedWeek, _weekMax);

        document.getElementById('polMonth').value = POLICY.max_leaves_per_month;
        document.getElementById('polConsec').value = POLICY.max_consecutive_days;'''

# --- 2c) Clamp on save before POST -----------------------------------
SAVE_POLICY_OLD = '''    window.savePolicy = function() {
        const body = {
            max_leaves_per_month: parseInt(document.getElementById('polMonth').value, 10) || 1,
            max_leaves_per_week: parseInt(document.getElementById('polWeek').value, 10) || 1,
            max_consecutive_days: parseInt(document.getElementById('polConsec').value, 10) || 1,'''

SAVE_POLICY_NEW = '''    window.savePolicy = function() {
        // LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1: clamp the weekly value
        // to the tenant's working-days count.  This makes the client
        // agree with the server, so a user who somehow bypasses the
        // input's max attribute sees the same number that gets saved.
        var _weekMax = parseInt(POLICY.working_days_count, 10) || 7;
        var _rawWeek = parseInt(document.getElementById('polWeek').value, 10) || 1;
        var _clampedWeek = Math.max(1, Math.min(_rawWeek, _weekMax));

        const body = {
            max_leaves_per_month: parseInt(document.getElementById('polMonth').value, 10) || 1,
            max_leaves_per_week: _clampedWeek,
            max_consecutive_days: parseInt(document.getElementById('polConsec').value, 10) || 1,'''


def patch_admin_template(root, dry_run, verbose):
    path = root / ADMIN_TPL_REL
    content = _read(path, verbose)
    if content is None:
        return False

    if 'LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1' in content:
        log(f"SKIP (already patched): {path}")
        return True

    changed_any = False

    content, c = _replace_once(
        content, POLWEEK_OLD, POLWEEK_NEW,
        "weekly input max + hint", verbose,
    )
    changed_any = changed_any or c

    content, c = _replace_once(
        content, OPEN_POLICY_OLD, OPEN_POLICY_NEW,
        "openPolicyModal caps polWeek", verbose,
    )
    changed_any = changed_any or c

    content, c = _replace_once(
        content, SAVE_POLICY_OLD, SAVE_POLICY_NEW,
        "savePolicy clamps polWeek", verbose,
    )
    changed_any = changed_any or c

    if not changed_any:
        log(f"  WARN: no template anchors matched in {path}")
        return False
    return _write(path, content, dry_run, verbose,
                  "weekly cap in policy modal")


# =====================================================================
# Main
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "LEAVE_WEEKLY_CAP_PER_WORKING_DAYS_V1 — the only fix needed "
            "in the leave module after verification: cap the "
            "max_leaves_per_week policy field at the tenant's actual "
            "working-days count (7 minus WeeklyHoliday entries), and "
            "reflect that bound in the policy modal's input."
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
        ("views/leave_management.py — dynamic weekly cap",
         patch_leave_views),
        ("templates/tenant/leave_management.html — policy modal",
         patch_admin_template),
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
        log("  1. Restart the server (views + template changed).")
        log("  2. Hard-refresh /portal/<schema>/leave/ (Ctrl+Shift+R).")
        log("  3. Open the Policy Settings modal:")
        log("     - 'Max leave days per week' input now has a max")
        log("       attribute matching your school's working days.")
        log("     - A hint line below it shows the working-days count")
        log("       and the day names.")
        log("     - If a stored value was above the new cap it is")
        log("       shown clamped; the next save persists the")
        log("       corrected value.")
        log("")
        log("  Nothing else in the leave module was changed — weekly-")
        log("  holiday exclusion, monthly cap, suspensions, working-")
        log("  day-aware counters, auto-suspension, CSRF, row-locks,")
        log("  and pagination are all left exactly as they were.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
