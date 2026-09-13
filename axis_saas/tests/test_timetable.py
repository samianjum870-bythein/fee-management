"""Timetable hardening tests (TIMETABLE_HARDENING_V1).

Covers the case-insensitive label uniqueness and the shape of the
DaySchedule table after the CI unique constraint migration. Also
verifies that a label may appear on multiple days and that different
labels may share a day.

These are the minimum safety-net tests for the timetable feature. The
original review flagged that the whole timetable system had zero test
coverage — this is a first instalment, not a complete suite.
"""

from django.db import IntegrityError
from django.test import TestCase
from django_tenants.utils import schema_context

from axis_saas.models import (
    AcademicCalendar,
    DaySchedule,
    PeriodsTimetable,
    SchoolClient,
    ScheduleLabel,
)


class TimetableHardeningTests(TestCase):
    def setUp(self):
        self.tenant = SchoolClient.objects.create(
            schema_name="tt-hardening-test",
            name="TT Hardening Test School",
            admin_username="admin",
            admin_password="Admin@123",
        )

    # -------- case-insensitive uniqueness ----------------------------

    def test_ci_label_unique_per_day_rejects_case_variant(self):
        """'Senior' and 'senior' on the same day must collide."""
        with schema_context(self.tenant.schema_name):
            cal, _ = AcademicCalendar.objects.get_or_create(pk=1)
            DaySchedule.objects.create(
                academic_calendar=cal,
                day_of_week=0,
                label="Senior",
                start_time="08:00",
                end_time="14:00",
                periods=8,
            )
            with self.assertRaises(IntegrityError):
                DaySchedule.objects.create(
                    academic_calendar=cal,
                    day_of_week=0,
                    label="senior",
                    start_time="08:00",
                    end_time="14:00",
                    periods=8,
                )

    def test_same_label_across_days_allowed(self):
        with schema_context(self.tenant.schema_name):
            cal, _ = AcademicCalendar.objects.get_or_create(pk=1)
            DaySchedule.objects.create(
                academic_calendar=cal,
                day_of_week=0,
                label="Senior",
                start_time="08:00",
                end_time="14:00",
                periods=8,
            )
            DaySchedule.objects.create(
                academic_calendar=cal,
                day_of_week=1,
                label="Senior",
                start_time="08:00",
                end_time="14:00",
                periods=8,
            )
            self.assertEqual(
                DaySchedule.objects.filter(label__iexact="senior").count(), 2
            )

    def test_different_labels_same_day_allowed(self):
        with schema_context(self.tenant.schema_name):
            cal, _ = AcademicCalendar.objects.get_or_create(pk=1)
            DaySchedule.objects.create(
                academic_calendar=cal,
                day_of_week=0,
                label="Senior",
                start_time="08:00",
                end_time="14:00",
                periods=8,
            )
            DaySchedule.objects.create(
                academic_calendar=cal,
                day_of_week=0,
                label="Junior",
                start_time="08:00",
                end_time="14:00",
                periods=8,
            )
            self.assertEqual(DaySchedule.objects.filter(day_of_week=0).count(), 2)

    # -------- ScheduleLabel name uniqueness ---------------------------

    def test_schedule_label_name_is_unique(self):
        with schema_context(self.tenant.schema_name):
            ScheduleLabel.objects.create(name="Senior")
            with self.assertRaises(IntegrityError):
                ScheduleLabel.objects.create(name="Senior")

    # -------- PeriodsTimetable defaults -------------------------------

    def test_periods_timetable_days_default_is_empty_list(self):
        with schema_context(self.tenant.schema_name):
            tt = PeriodsTimetable.objects.create(title="Empty", label="Senior")
            self.assertEqual(tt.days, [])
            self.assertEqual(tt.break_duration, 0)
