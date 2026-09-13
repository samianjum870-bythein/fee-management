#!/usr/bin/env python3
"""
axis_patcher.py
===============

TIMETABLE_LABEL_AUDIT_V1
------------------------

Fixes the incomplete label-rename cascade. After TIMETABLE_LABEL_RENAME_V1
was applied, a rename only reached the calendar table on the timetable
management page — everywhere else (Periods Timetable page, Class Detail
pages, Assign Teachers, etc.) kept showing the stale text. Two reasons:

  1. `filter(label__iexact=old_name)` misses rows whose label has
     different whitespace ("seniors " vs "seniors"). So the cascade was
     silently partial.
  2. Data that was already broken BEFORE V1 existed was never repaired.

What this patcher does:

  A. Rewrites api_update_label so the cascade matches on
     `.strip().lower()` on BOTH sides — legacy rows with trailing
     whitespace or mixed case are caught. Response now includes a
     `verify` block exposing the current DB state so DevTools can
     confirm the cascade actually ran.

  B. Adds two new endpoints:
       GET  /api/timetable/labels/audit/   - report stale / orphan labels
       POST /api/timetable/labels/repair/  - auto-fix case/whitespace
                                             variants to canonical text
                                             (safe: only touches labels
                                              that DO have a canonical
                                              ScheduleLabel).

  C. Adds a management command `fix_orphan_labels` that runs the same
     audit + repair across every tenant schema, so pre-V1 corruption
     gets cleaned up.

  D. Wires up the two new URLs in public_urls.py.

Does NOT touch:
  * Orphan labels (no matching ScheduleLabel at all) — those need a
    human decision, so we only report them.
  * api_delete_label behaviour.

Idempotent. Safe to run multiple times.

Usage:
    python3 axis_patcher.py [--dry-run] [--verbose] [--target-dir=.]
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


MARKER = "TIMETABLE_LABEL_AUDIT_V1"


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


# =====================================================================
# STEP 1 — axis_saas/views/timetable.py
#
# Strategy: replace the entire api_update_label function using a regex
# that runs from its decorator (which has the V1 marker comment) to the
# start of api_delete_label. This is robust against small whitespace
# differences in the body.
# =====================================================================

# ---- 1a. New api_update_label + two new views, to be injected ----
NEW_API_UPDATE_AND_AUDIT = r'''@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
@transaction.atomic  # TIMETABLE_LABEL_AUDIT_V1: rename must be all-or-nothing
def api_update_label(request, schema_name):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    lbl_id = data.get('id')
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    if not lbl_id:
        return JsonResponse({'error': 'id required'}, status=400)
    if not name:
        return JsonResponse({'error': 'Label name is required'}, status=400)
    if len(name) > 50:
        return JsonResponse({'error': 'Label name too long (max 50 chars)'}, status=400)
    with schema_context(schema_name):
        try:
            lbl = ScheduleLabel.objects.get(id=lbl_id)
        except ScheduleLabel.DoesNotExist:
            return JsonResponse({'error': 'Label not found'}, status=404)
        if ScheduleLabel.objects.filter(name__iexact=name).exclude(id=lbl_id).exists():
            return JsonResponse({'error': f"Another label '{name}' already exists"}, status=400)

        old_name = (lbl.name or '').strip()
        old_key = old_name.lower()
        new_key = name.strip().lower()

        day_schedules_updated = 0
        timetables_updated = 0

        if old_key and old_key != new_key:
            # TIMETABLE_LABEL_AUDIT_V1: ScheduleLabel.name is canonical but
            # DaySchedule.label and PeriodsTimetable.label are denormalized
            # copies of that text. If the rename doesn't cascade everywhere,
            # the calendar table below, the Periods Timetable page, and every
            # class detail page keep showing the stale string forever.
            #
            # Matching uses `.strip().lower()` on BOTH sides so legacy rows
            # with trailing whitespace or mixed case are still caught. The
            # CI unique constraint (unique_label_per_day_ci) guarantees at
            # most one case variant per (calendar, day), so this cannot
            # accidentally steal a differently-cased sibling row.

            # ---- Find DaySchedule rows to rename -------------------------
            ds_to_update = []
            ds_days = set()
            for ds in DaySchedule.objects.all():
                if (ds.label or '').strip().lower() == old_key:
                    ds_to_update.append(ds.id)
                    ds_days.add(ds.day_of_week)

            # ---- Collision guard -----------------------------------------
            if ds_to_update:
                collisions = list(
                    DaySchedule.objects
                    .filter(day_of_week__in=ds_days)
                    .exclude(id__in=ds_to_update)
                    .values_list('label', 'day_of_week')
                )
                clashing = [
                    d for l, d in collisions
                    if (l or '').strip().lower() == new_key
                ]
                if clashing:
                    day_names = dict(TimetableEntry.DAY_CHOICES)
                    collision_labels = ', '.join(
                        day_names.get(d, str(d)) for d in sorted(set(clashing))
                    )
                    return JsonResponse({
                        'error': (
                            f"Cannot rename '{old_name}' to '{name}': the new "
                            f"name is already used on {collision_labels}. "
                            f"Rename or delete those slots first, then try again."
                        )
                    }, status=400)

            # ---- Apply DaySchedule cascade -------------------------------
            if ds_to_update:
                day_schedules_updated = (
                    DaySchedule.objects
                    .filter(id__in=ds_to_update)
                    .update(label=name)
                )

            # ---- Find & update PeriodsTimetable rows ---------------------
            tt_to_update = []
            for tt in PeriodsTimetable.objects.all():
                if (tt.label or '').strip().lower() == old_key:
                    tt_to_update.append(tt.id)
            if tt_to_update:
                timetables_updated = (
                    PeriodsTimetable.objects
                    .filter(id__in=tt_to_update)
                    .update(label=name)
                )

        lbl.name = name
        lbl.description = description[:150]
        lbl.save()

        # ---- Post-write verification block -------------------------------
        # Surfaces the current DB state so the frontend / DevTools can
        # confirm the cascade actually ran.
        try:
            verify = {
                'schedule_label_names': list(
                    ScheduleLabel.objects.order_by('name').values_list('name', flat=True)
                ),
                'day_schedule_labels': sorted(set(
                    (l or '') for l in DaySchedule.objects.values_list('label', flat=True)
                )),
                'timetable_labels': sorted(set(
                    (l or '') for l in PeriodsTimetable.objects.values_list('label', flat=True)
                )),
            }
        except Exception:
            verify = None

        return JsonResponse({
            'success': True,
            'label': {'id': lbl.id, 'name': lbl.name, 'description': lbl.description or ''},
            'cascaded': {
                'day_schedules_updated': day_schedules_updated,
                'timetables_updated': timetables_updated,
                'old_name': old_name,
                'new_name': name,
            },
            'verify': verify,
        })


@csrf_exempt
@require_http_methods(["GET"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_audit_labels(request, schema_name):
    """TIMETABLE_LABEL_AUDIT_V1: report label references that have drifted
    away from their canonical ScheduleLabel.

    Categories:
      * orphans         - label text exists on DaySchedule / PeriodsTimetable
                          but no ScheduleLabel matches it at all.
      * case_variants   - label text matches a ScheduleLabel only after
                          .strip().lower() (so the rendered text differs).
    """
    with schema_context(schema_name):
        canonical_by_key = {}
        for l in ScheduleLabel.objects.all():
            canonical_by_key[(l.name or '').strip().lower()] = l.name

        def _scan(values):
            orphans = {}
            variants = {}
            for v in values:
                key = (v or '').strip().lower()
                if not key:
                    continue
                if key not in canonical_by_key:
                    orphans[v] = orphans.get(v, 0) + 1
                elif v != canonical_by_key[key]:
                    variants[v] = variants.get(v, 0) + 1
            return orphans, variants

        ds_orphans, ds_variants = _scan(DaySchedule.objects.values_list('label', flat=True))
        tt_orphans, tt_variants = _scan(PeriodsTimetable.objects.values_list('label', flat=True))

        return JsonResponse({
            'schedule_labels': list(
                ScheduleLabel.objects.order_by('name').values_list('name', flat=True)
            ),
            'day_schedule_orphans': ds_orphans,
            'day_schedule_case_variants': ds_variants,
            'timetable_orphans': tt_orphans,
            'timetable_case_variants': tt_variants,
            'clean': not (ds_orphans or ds_variants or tt_orphans or tt_variants),
        })


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
@transaction.atomic  # TIMETABLE_LABEL_AUDIT_V1
def api_repair_labels(request, schema_name):
    """TIMETABLE_LABEL_AUDIT_V1: rewrite every DaySchedule / PeriodsTimetable
    label that matches a canonical ScheduleLabel only after
    .strip().lower() — so "Seniors " becomes "seniors".

    Orphan labels (no matching ScheduleLabel at all) are NOT touched; the
    caller decides what those should become.
    """
    with schema_context(schema_name):
        canonical_by_key = {}
        for l in ScheduleLabel.objects.all():
            canonical_by_key[(l.name or '').strip().lower()] = l.name

        # DaySchedule
        ds_by_target = {}
        for ds in DaySchedule.objects.all():
            key = (ds.label or '').strip().lower()
            if key in canonical_by_key and ds.label != canonical_by_key[key]:
                ds_by_target.setdefault(canonical_by_key[key], []).append(ds.id)
        ds_fixed = 0
        for target, ids in ds_by_target.items():
            ds_fixed += DaySchedule.objects.filter(id__in=ids).update(label=target)

        # PeriodsTimetable
        tt_by_target = {}
        for tt in PeriodsTimetable.objects.all():
            key = (tt.label or '').strip().lower()
            if key in canonical_by_key and tt.label != canonical_by_key[key]:
                tt_by_target.setdefault(canonical_by_key[key], []).append(tt.id)
        tt_fixed = 0
        for target, ids in tt_by_target.items():
            tt_fixed += PeriodsTimetable.objects.filter(id__in=ids).update(label=target)

        return JsonResponse({
            'success': True,
            'day_schedules_repaired': ds_fixed,
            'timetables_repaired': tt_fixed,
        })
'''


# Regex to grab the WHOLE existing api_update_label block, from its decorator
# line (which contains the V1 marker) up to (but NOT including) the next
# decorated function `api_delete_label`.
REPLACE_PATTERN = re.compile(
    r'@csrf_exempt\s*\n'
    r'@require_http_methods\(\["POST"\]\)\s*\n'
    r'@require_tenant_type\(\[' +
    r"'school', 'wing_school', 'single_small_school'\]\)\s*\n"
    r'@require_school_feature\(' +
    r"'timetable_management'\)\s*\n"
    r'@transaction\.atomic\s*#\s*TIMETABLE_LABEL_RENAME_V1.*?' +
    r'(?=@csrf_exempt\s*\n'
    r'@require_http_methods\(\["POST"\]\)\s*\n'
    r'@require_tenant_type\(\[' +
    r"'school', 'wing_school', 'single_small_school'\]\)\s*\n"
    r'@require_school_feature\(' +
    r"'timetable_management'\)\s*\n"
    r'def\s+api_delete_label)',
    re.DOTALL,
)


def patch_view_timetable(path: Path, dry_run: bool, verbose: bool) -> bool:
    _log(f"Patching view: {path}")
    content = _read(path)
    if content is None:
        return False

    if MARKER in content:
        _log("  - already patched, skipping")
        return True

    if "TIMETABLE_LABEL_RENAME_V1" not in content:
        _log("  ERROR: TIMETABLE_LABEL_RENAME_V1 marker not present — "
             "run the V1 patcher first")
        return False

    m = REPLACE_PATTERN.search(content)
    if not m:
        _log("  ERROR: could not locate api_update_label block via regex")
        return False

    new_content = content[:m.start()] + NEW_API_UPDATE_AND_AUDIT + "\n\n" + content[m.end():]
    _log("  + api_update_label rewritten (robust strip+lower cascade + verify block)")
    _log("  + api_audit_labels added")
    _log("  + api_repair_labels added")
    return _write(path, new_content, dry_run, verbose)


# =====================================================================
# STEP 2 — axis_saas/public_urls.py : wire up the two new endpoints
# =====================================================================
URLS_IMPORT_OLD = (
    "from .views.timetable import (\n"
    "    timetable_management, api_update_calendar, api_add_holiday, api_delete_holiday,\n"
    "    api_add_period, api_delete_period, api_update_period, api_get_timetable,\n"
    "    api_save_timetable, api_save_day_schedules, api_update_holiday,\n"
    "    api_list_labels, api_add_label, api_update_label, api_delete_label,\n"
    "    api_batch_update_label_times,\n"
    ")\n"
)

URLS_IMPORT_NEW = (
    "from .views.timetable import (\n"
    "    timetable_management, api_update_calendar, api_add_holiday, api_delete_holiday,\n"
    "    api_add_period, api_delete_period, api_update_period, api_get_timetable,\n"
    "    api_save_timetable, api_save_day_schedules, api_update_holiday,\n"
    "    api_list_labels, api_add_label, api_update_label, api_delete_label,\n"
    "    api_batch_update_label_times,\n"
    "    api_audit_labels, api_repair_labels,  # TIMETABLE_LABEL_AUDIT_V1\n"
    ")\n"
)


URLS_ROUTE_OLD = (
    "    path('portal/<slug:schema_name>/api/timetable/labels/delete/', "
    "portal_wrapper(login_required_for_schema(api_delete_label)), "
    "name='api_timetable_labels_delete'),\n"
)

URLS_ROUTE_NEW = (
    "    path('portal/<slug:schema_name>/api/timetable/labels/delete/', "
    "portal_wrapper(login_required_for_schema(api_delete_label)), "
    "name='api_timetable_labels_delete'),\n"
    "    # ===== TIMETABLE_LABEL_AUDIT_V1 =====\n"
    "    path('portal/<slug:schema_name>/api/timetable/labels/audit/', "
    "portal_wrapper(login_required_for_schema(api_audit_labels)), "
    "name='api_timetable_labels_audit'),\n"
    "    path('portal/<slug:schema_name>/api/timetable/labels/repair/', "
    "portal_wrapper(login_required_for_schema(api_repair_labels)), "
    "name='api_timetable_labels_repair'),\n"
)


def patch_public_urls(path: Path, dry_run: bool, verbose: bool) -> bool:
    _log(f"Patching URLs: {path}")
    content = _read(path)
    if content is None:
        return False

    if "api_timetable_labels_audit" in content:
        _log("  - already patched, skipping")
        return True

    changed = False

    if URLS_IMPORT_OLD in content:
        content = content.replace(URLS_IMPORT_OLD, URLS_IMPORT_NEW, 1)
        _log("  + added imports for api_audit_labels / api_repair_labels")
        changed = True
    else:
        _log("  WARN: timetable import block anchor not found")

    if URLS_ROUTE_OLD in content:
        content = content.replace(URLS_ROUTE_OLD, URLS_ROUTE_NEW, 1)
        _log("  + added audit + repair URL routes")
        changed = True
    else:
        _log("  WARN: labels/delete URL anchor not found")

    if not changed:
        return True
    return _write(path, content, dry_run, verbose)


# =====================================================================
# STEP 3 — axis_saas/management/commands/fix_orphan_labels.py
# =====================================================================
MGMT_CMD_FILENAME = "fix_orphan_labels.py"

MGMT_CMD_CONTENT = '''# axis_saas/management/commands/fix_orphan_labels.py
#
# TIMETABLE_LABEL_AUDIT_V1
# Audit (and optionally repair) DaySchedule / PeriodsTimetable labels that
# have drifted away from their canonical ScheduleLabel.
#
#   python manage.py fix_orphan_labels                       # audit all tenants
#   python manage.py fix_orphan_labels --apply               # fix case/ws variants
#   python manage.py fix_orphan_labels --schema school1      # one tenant only
#   python manage.py fix_orphan_labels --schema school1 --apply
#
from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

from axis_saas.models import (
    SchoolClient, ScheduleLabel, DaySchedule, PeriodsTimetable,
)


class Command(BaseCommand):
    help = ("Audit / repair DaySchedule.label and PeriodsTimetable.label "
            "references that no longer match a canonical ScheduleLabel.name.")

    def add_arguments(self, parser):
        parser.add_argument('--schema', help='Only process this tenant schema')
        parser.add_argument('--apply', action='store_true',
                            help='Actually repair case/whitespace variants '
                                 '(default: audit only)')

    def handle(self, *args, **options):
        tenants = SchoolClient.objects.exclude(schema_name='public')
        if options.get('schema'):
            tenants = tenants.filter(schema_name=options['schema'])

        if not tenants.exists():
            self.stdout.write(self.style.WARNING('No tenants to process.'))
            return

        total_ds_fixed = 0
        total_tt_fixed = 0
        total_ds_orphans = 0
        total_tt_orphans = 0

        for tenant in tenants:
            self.stdout.write(f"\n=== {tenant.schema_name} ({tenant.name}) ===")
            with schema_context(tenant.schema_name):
                canonical_by_key = {}
                for l in ScheduleLabel.objects.all():
                    canonical_by_key[(l.name or '').strip().lower()] = l.name

                ds_orphans, ds_variants = {}, {}
                for v in DaySchedule.objects.values_list('label', flat=True):
                    key = (v or '').strip().lower()
                    if not key:
                        continue
                    if key not in canonical_by_key:
                        ds_orphans[v] = ds_orphans.get(v, 0) + 1
                    elif v != canonical_by_key[key]:
                        ds_variants[v] = ds_variants.get(v, 0) + 1

                tt_orphans, tt_variants = {}, {}
                for v in PeriodsTimetable.objects.values_list('label', flat=True):
                    key = (v or '').strip().lower()
                    if not key:
                        continue
                    if key not in canonical_by_key:
                        tt_orphans[v] = tt_orphans.get(v, 0) + 1
                    elif v != canonical_by_key[key]:
                        tt_variants[v] = tt_variants.get(v, 0) + 1

                self.stdout.write(
                    f"  Canonical labels: {sorted(canonical_by_key.values())}"
                )
                if ds_variants:
                    self.stdout.write(self.style.WARNING(
                        f"  DaySchedule case/ws variants: {ds_variants}"
                    ))
                if tt_variants:
                    self.stdout.write(self.style.WARNING(
                        f"  PeriodsTimetable case/ws variants: {tt_variants}"
                    ))
                if ds_orphans:
                    self.stdout.write(self.style.ERROR(
                        f"  DaySchedule ORPHANS (no ScheduleLabel): {ds_orphans}"
                    ))
                if tt_orphans:
                    self.stdout.write(self.style.ERROR(
                        f"  PeriodsTimetable ORPHANS: {tt_orphans}"
                    ))

                total_ds_orphans += sum(ds_orphans.values())
                total_tt_orphans += sum(tt_orphans.values())

                if not ds_variants and not tt_variants and not ds_orphans and not tt_orphans:
                    self.stdout.write(self.style.SUCCESS("  Clean ✔"))
                    continue

                if not options['apply']:
                    self.stdout.write("  (audit only; pass --apply to repair variants)")
                    continue

                # Repair case/ws variants only
                ds_by_target = {}
                for ds in DaySchedule.objects.all():
                    key = (ds.label or '').strip().lower()
                    if key in canonical_by_key and ds.label != canonical_by_key[key]:
                        ds_by_target.setdefault(canonical_by_key[key], []).append(ds.id)
                ds_fixed = 0
                for target, ids in ds_by_target.items():
                    ds_fixed += DaySchedule.objects.filter(id__in=ids).update(label=target)

                tt_by_target = {}
                for tt in PeriodsTimetable.objects.all():
                    key = (tt.label or '').strip().lower()
                    if key in canonical_by_key and tt.label != canonical_by_key[key]:
                        tt_by_target.setdefault(canonical_by_key[key], []).append(tt.id)
                tt_fixed = 0
                for target, ids in tt_by_target.items():
                    tt_fixed += PeriodsTimetable.objects.filter(id__in=ids).update(label=target)

                self.stdout.write(self.style.SUCCESS(
                    f"  Repaired: DaySchedule={ds_fixed}, PeriodsTimetable={tt_fixed}"
                ))
                total_ds_fixed += ds_fixed
                total_tt_fixed += tt_fixed

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Totals: ds_repaired={total_ds_fixed}, tt_repaired={total_tt_fixed}, "
            f"ds_orphans={total_ds_orphans}, tt_orphans={total_tt_orphans}"
        ))
'''


def patch_management_command(target: Path, dry_run: bool, verbose: bool) -> bool:
    cmd_path = (target / 'axis_saas' / 'management' / 'commands'
                / MGMT_CMD_FILENAME)
    _log(f"Writing management command: {cmd_path}")
    if cmd_path.exists():
        existing = _read(cmd_path)
        if existing and MARKER in existing:
            _log("  - already present, skipping")
            return True
    return _write(cmd_path, MGMT_CMD_CONTENT, dry_run, verbose)


# =====================================================================
# main
# =====================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "TIMETABLE_LABEL_AUDIT_V1 — Cascade label renames robustly "
            "(strip+lower matching), add audit / repair endpoints, and "
            "add a management command to retroactively fix labels that "
            "drifted before this patch existed."
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
    _log("STEP 1: axis_saas/views/timetable.py")
    ok &= patch_view_timetable(
        target / 'axis_saas' / 'views' / 'timetable.py',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 2: axis_saas/public_urls.py")
    ok &= patch_public_urls(
        target / 'axis_saas' / 'public_urls.py',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 3: axis_saas/management/commands/fix_orphan_labels.py")
    ok &= patch_management_command(target, args.dry_run, args.verbose)

    print('-' * 60)
    if ok:
        _log("DONE.")
        if not args.dry_run:
            _log("")
            _log("NEXT STEPS (very important, do these in order):")
            _log("")
            _log("  1. RESTART Django. The view changes only take effect")
            _log("     after the dev server / gunicorn process restarts.")
            _log("     If you skip this step, the OLD api_update_label will")
            _log("     keep running and the bug will look unfixed.")
            _log("")
            _log("  2. Retroactively repair any data that was already broken")
            _log("     (renamed while the old code was running):")
            _log("       python manage.py fix_orphan_labels            # audit")
            _log("       python manage.py fix_orphan_labels --apply    # fix")
            _log("     Read the output. Anything listed under ORPHANS has no")
            _log("     matching ScheduleLabel and needs a manual decision.")
            _log("")
            _log("  3. Hard-refresh the browser (Ctrl+F5 / Cmd+Shift+R).")
            _log("")
            _log("  4. Test the rename again:")
            _log("       - Manage Labels → ✎ Edit → change name → Save.")
            _log("       - Open DevTools → Network → click the update request.")
            _log("       - Look at the response JSON. It now contains a")
            _log("         `cascaded` and a `verify` block, e.g.:")
            _log("           \"cascaded\": {\"day_schedules_updated\": 3, ...}")
            _log("           \"verify\": {\"day_schedule_labels\": [\"seniors middle\"], ...}")
            _log("       - If `day_schedules_updated` is > 0 and")
            _log("         `verify.day_schedule_labels` shows the new name,")
            _log("         the DB cascade worked.")
            _log("")
            _log("  5. Now open any OTHER page that shows labels:")
            _log("       - /portal/<schema>/timetable/periods/")
            _log("       - /portal/<schema>/my-classes/<id>/  (assigned timetable)")
            _log("     They should all show the NEW label after reload.")
            _log("")
            _log("  6. To run an on-demand audit any time:")
            _log("       GET /portal/<schema>/api/timetable/labels/audit/")
            _log("       POST /portal/<schema>/api/timetable/labels/repair/")
            _log("     Both are CSRF-protected via the session, so use the")
            _log("     browser (logged in) or curl with the CSRF cookie.")
        return 0
    _log("FAILED: one or more steps did not complete.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
