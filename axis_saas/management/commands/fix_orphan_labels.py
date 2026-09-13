# axis_saas/management/commands/fix_orphan_labels.py
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
            self.stdout.write(f"
=== {tenant.schema_name} ({tenant.name}) ===")
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
            f"
Done. Totals: ds_repaired={total_ds_fixed}, tt_repaired={total_tt_fixed}, "
            f"ds_orphans={total_ds_orphans}, tt_orphans={total_tt_orphans}"
        ))
