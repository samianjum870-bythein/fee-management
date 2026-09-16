"""ATTENDANCE_PRODUCTION_V2 — auto-mark unmarked students absent.

Runs at end of the day for every tenant whose AttendancePolicy has
`auto_mark_absent_at` set and whose current time has passed. Schedule:

    55 23 * * * cd /srv/app && python manage.py attendance_auto_absent
"""
from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import schema_context

from axis_saas.models import (
    SchoolClient, SchoolClass, Student, StudentAttendance,
    AttendancePolicy, WeeklyHoliday, AnnualHoliday, Vacation,
)


class Command(BaseCommand):
    help = "Auto-mark unmarked students as absent (per tenant policy)."

    def handle(self, *args, **options):
        today = timezone.localdate()
        now = timezone.localtime().time()
        dow = today.weekday()
        created_total = 0

        for tenant in SchoolClient.objects.exclude(schema_name='public'):
            with schema_context(tenant.schema_name):
                try:
                    policy = AttendancePolicy.current()
                except Exception:
                    policy = None
                if not policy or not policy.auto_mark_absent_at:
                    continue
                if now < policy.auto_mark_absent_at:
                    continue
                if WeeklyHoliday.objects.filter(day_of_week=dow).exists():
                    continue
                if AnnualHoliday.objects.filter(month=today.month,
                                                day=today.day).exists():
                    continue
                if Vacation.objects.filter(start_date__lte=today,
                                           end_date__gte=today).exists():
                    continue

                for cls in SchoolClass.objects.filter(is_active=True):
                    marked_ids = set(
                        StudentAttendance.objects
                        .filter(school_class=cls, date=today,
                                period_order__isnull=True)
                        .values_list('student_id', flat=True)
                    )
                    unmarked = Student.objects.filter(
                        school_class=cls, status='active',
                    ).exclude(id__in=marked_ids)
                    with transaction.atomic():
                        for s in unmarked:
                            StudentAttendance.objects.create(
                                student=s, school_class=cls, date=today,
                                period_order=None,
                                status='absent',
                                source='auto_absent',
                                marked_at=timezone.now(),
                                remarks='auto-marked by cron',
                            )
                            created_total += 1

        self.stdout.write(self.style.SUCCESS(
            f"Auto-absent rows created: {created_total}"
        ))
