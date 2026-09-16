"""ATTENDANCE_PRODUCTION_V2 — daily reminder.

Emails / notifies every class teacher who has NOT marked full-day
attendance by the end of the day. Schedule at 16:00 via cron:

    0 16 * * * cd /srv/app && python manage.py attendance_reminder
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django_tenants.utils import schema_context

from axis_saas.models import (
    SchoolClient, SchoolClass, StudentAttendance, Notification,
    WeeklyHoliday, AnnualHoliday, Vacation,
)


class Command(BaseCommand):
    help = "Notify class teachers whose class attendance is still unmarked."

    def handle(self, *args, **options):
        today = timezone.localdate()
        dow = today.weekday()
        grand = 0
        for tenant in SchoolClient.objects.exclude(schema_name='public'):
            with schema_context(tenant.schema_name):
                if WeeklyHoliday.objects.filter(day_of_week=dow).exists():
                    continue
                if AnnualHoliday.objects.filter(month=today.month,
                                                day=today.day).exists():
                    continue
                if Vacation.objects.filter(start_date__lte=today,
                                           end_date__gte=today).exists():
                    continue
                pending = []
                for cls in SchoolClass.objects.filter(is_active=True):
                    marked = StudentAttendance.objects.filter(
                        school_class=cls, date=today,
                        period_order__isnull=True,
                    ).exists()
                    if not marked:
                        pending.append(str(cls))
                if not pending:
                    continue
                Notification.objects.create(
                    message=(
                        f"Attendance not marked today for: "
                        f"{', '.join(pending[:5])}"
                        + (f" (+{len(pending)-5} more)"
                           if len(pending) > 5 else "")
                    ),
                    link=f'/portal/{tenant.schema_name}/attendance/',
                )
                grand += len(pending)
        self.stdout.write(self.style.SUCCESS(
            f"Reminders sent. Pending classes across tenants: {grand}"
        ))
