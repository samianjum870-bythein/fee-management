"""ATTENDANCE_PRODUCTION_V2 — monthly attendance report.

Writes one CSV per tenant for the previous calendar month under
``<BASE_DIR>/reports/attendance/``. Schedule on the 1st of each month:

    0 6 1 * * cd /srv/app && python manage.py attendance_monthly_report
"""
import csv
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django_tenants.utils import schema_context

from axis_saas.models import (
    SchoolClient, Student, StudentAttendance,
)


class Command(BaseCommand):
    help = "Export previous-month attendance as CSV per tenant."

    def handle(self, *args, **options):
        today = date.today()
        first_of_this = today.replace(day=1)
        last_month_end = first_of_this
        last_month_start = (first_of_this.replace(day=1)
                            - (first_of_this - first_of_this.replace(day=1))
                            if False else
                            (first_of_this.replace(day=1)
                             - __import__('datetime').timedelta(days=1)))
        last_month_start = last_month_start.replace(day=1)

        out_dir = Path(getattr(settings, 'BASE_DIR', '.')) / 'reports' / 'attendance'
        out_dir.mkdir(parents=True, exist_ok=True)

        for tenant in SchoolClient.objects.exclude(schema_name='public'):
            with schema_context(tenant.schema_name):
                rows = []
                qs = (
                    StudentAttendance.objects
                    .filter(date__gte=last_month_start, date__lt=last_month_end)
                    .values('student_id', 'student__name', 'student__roll_number',
                            'student__school_class__name',
                            'student__school_class__section')
                    .annotate(
                        present=Count('id', filter=Q(status='present')),
                        absent=Count('id', filter=Q(status='absent')),
                        late=Count('id', filter=Q(status='late')),
                        half_day=Count('id', filter=Q(status='half_day')),
                        excused=Count('id', filter=Q(status='excused')),
                    )
                )
                for r in qs:
                    total = sum((r[k] or 0) for k in
                                ('present', 'absent', 'late', 'half_day', 'excused'))
                    pct = round(((r['present'] or 0) + (r['late'] or 0)) / total * 100, 2) if total else 0
                    rows.append({
                        'student': r['student__name'],
                        'roll': r['student__roll_number'],
                        'class': f"{r['student__school_class__name'] or ''}-{r['student__school_class__section'] or ''}",
                        'present': r['present'], 'absent': r['absent'],
                        'late': r['late'], 'half_day': r['half_day'],
                        'excused': r['excused'], 'total': total,
                        'percentage': pct,
                    })
                if not rows:
                    continue
                out_file = out_dir / f"{tenant.schema_name}_{last_month_start.isoformat()}.csv"
                with out_file.open('w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
                self.stdout.write(self.style.SUCCESS(f"Wrote {out_file}"))
