"""Backfill DaySchedule.updated_at for legacy rows (TIMETABLE_HARDENING_V1).

DaySchedule.updated_at is the optimistic-lock token used by the calendar
save endpoint. Rows created before TIMETABLE_OPTIMISTIC_LOCK_V1 have
updated_at=NULL. The save endpoint treats NULL as "no version info —
allow overwrite", so those rows can be clobbered by concurrent edits
until they are re-saved once. This command seeds them with a single
timestamp so the first edit after upgrade goes through the normal
compare-and-refuse path.

Run once after upgrade:

    python manage.py backfill_dayschedule_updated_at
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django_tenants.utils import schema_context

from axis_saas.models import SchoolClient, DaySchedule


class Command(BaseCommand):
    help = (
        "Seed DaySchedule.updated_at for rows created before the "
        "TIMETABLE_OPTIMISTIC_LOCK_V1 migration. Idempotent."
    )

    def handle(self, *args, **options):
        tenants = SchoolClient.objects.exclude(schema_name="public")
        total = 0
        for tenant in tenants:
            with schema_context(tenant.schema_name):
                qs = DaySchedule.objects.filter(updated_at__isnull=True)
                count = qs.count()
                if count == 0:
                    continue
                # auto_now does not fire on QuerySet.update(), so this
                # is the correct way to seed the column directly.
                qs.update(updated_at=timezone.now())
                self.stdout.write(
                    f"  {tenant.schema_name}: backfilled {count} DaySchedule row(s)"
                )
                total += count
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to backfill."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Done. Total rows updated: {total}")
            )
