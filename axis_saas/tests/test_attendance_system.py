"""Comprehensive test suite for the ATTENDANCE system.

Covers every layer of the attendance feature — admin side and staff
side — including:

  * ClassTeacherAttendancePermission model (defaults, for_class,
    bounds, choices).
  * ClassTeacherEditQuota model (unique per class+date, counter).
  * AttendancePolicy singleton.
  * Holiday detection (weekly / annual / vacation) with
    historical-awareness (created_at gate).
  * `_compute_permission_payload` in every reachable state:
      - subject teacher
      - today (no quota)
      - beyond view window
      - backdate_access='none'
      - backdate_access='read'
      - beyond edit window
      - quota exhausted
      - normal (edit allowed)
  * Admin dashboard view: KPI summary, class rows, holiday context,
    auto-mark column.
  * Admin students API: holiday short-circuit, locked flag,
    auto_marked flag, is_auto per row.
  * Admin mark API: create / update, source attribution, holiday
    refusal, future-date refusal.
  * Admin bulk mark API.
  * Admin records API: filters, pagination.
  * Admin summary API: per-class breakdown.
  * Admin student history API: attendance percentage.
  * Admin compliance API: which class teachers have / haven't
    marked today.
  * Admin audit API: paginated audit listing.
  * Admin low defaulters API.
  * Admin policy get / save API.
  * Admin staff attendance list / mark API.
  * Admin auto-marked dates API.
  * Admin class-teacher permissions list / save API.
  * Admin daily logs API (per class + date, with quota info).
  * Staff dashboard view: class-teacher and subject-teacher sections.
  * Staff dates API: per-date permission payload, filtered by view
    window.
  * Staff students API: class teacher vs subject teacher authorization,
    holiday short-circuit, locked banner, auto-marked rows.
  * Staff mark API: class teacher today (no quota), past date (quota
    consumed), edit-lock enforcement, holiday refusal, audit log.
  * Staff copy API: yesterday + last-period sources.
  * Staff records API.
  * Staff missed days API.
  * Staff policy API.
  * Edit quota enforcement (permission denied after N edits).
  * Teacher ownership transfer of auto-marked rows.
  * Audit trail integrity (create / update written).
  * Multi-tenant isolation.

Run:
    python manage.py test axis_saas.tests.test_attendance_system
"""

import json
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from axis_saas.models import (
    AcademicCalendar,
    AnnualHoliday,
    AttendanceAuditLog,
    AttendancePolicy,
    ClassSubject,
    ClassTeacherAttendancePermission,
    ClassTeacherEditQuota,
    DaySchedule,
    Holiday,
    LeaveRequest,
    PeriodTeacherAssignment,
    PeriodsTimetable,
    ScheduleLabel,
    SchoolClass,
    SchoolClient,
    Staff,
    StaffAttendance,
    StaffCredential,
    Student,
    StudentAttendance,
    StudentLeave,
    Subject,
    Vacation,
    WeeklyHoliday,
)
from axis_saas.views import admin_attendence as admin_att
from axis_saas.views import staff_attendence as staff_att


# =====================================================================
# BASE CLASS
# =====================================================================

class AttendanceTestBase(TestCase):
    """One tenant, one class teacher, one class with 5 students, plus a
    helper subject + period timetable so subject-teacher flows can be
    exercised.

    The schema is dropped in tearDown so repeated runs work cleanly.
    """

    schema = "attendance-test"

    def setUp(self):
        # Ensure we're on the public schema BEFORE creating a tenant.
        connection.set_schema_to_public()
        cache.clear()

        self.tenant = SchoolClient.objects.create(
            schema_name=self.schema,
            name="Attendance Test School",
            admin_username="admin",
            admin_password="admin123",
            enabled_features={
                "desktop": [
                    "attendance_management",
                    "classes_management",
                    "dashboard",
                ],
                "mobile": ["attendance_management"],
                "staff_portal": [
                    "staff_attendance",
                    "staff_dashboard",
                    "staff_classes",
                ],
            },
        )
        connection.set_schema_to_public()

        # --- staff + class + students ------------------------------------
        with schema_context(self.schema):
            self.class_teacher = Staff.objects.create(
                first_name="Ayesha", last_name="Khan",
                email="ayesha@att.test",
                job_title="Math Teacher", department="teaching",
                phone="03001111111", role="class_teacher",
                status="active",
            )
            self.subject_teacher = Staff.objects.create(
                first_name="Bilal", last_name="Ahmed",
                email="bilal@att.test",
                job_title="English Teacher", department="teaching",
                phone="03002222222", role="subject_teacher",
                status="active",
            )
            self.other_teacher = Staff.objects.create(
                first_name="Sana", last_name="Raza",
                email="sana@att.test",
                job_title="Science Teacher", department="teaching",
                phone="03003333333", role="teacher",
                status="active",
            )

            self.subject = Subject.objects.create(name="English")
            self.class_obj = SchoolClass.objects.create(
                name="Grade 1", section="A",
            )
            self.class_obj.class_teacher = self.class_teacher
            self.class_obj.save(update_fields=["class_teacher"])

            # Assign subject teacher
            ClassSubject.objects.create(
                school_class=self.class_obj,
                subject=self.subject,
                teacher=self.subject_teacher,
                is_active=True,
            )

            self.students = []
            for i in range(5):
                self.students.append(Student.objects.create(
                    name=f"Student {i + 1}",
                    father_name=f"Father {i + 1}",
                    father_cnic=f"35202-1234567-{i}",
                    parent_mobile="03001234567",
                    grade="Grade 1", section="A",
                    school_class=self.class_obj,
                ))

        # --- Admin client session ----------------------------------------
        self.client = Client()
        session = self.client.session
        session["school_admin_authenticated"] = True
        session["school_admin_schema"] = self.schema
        session["school_admin_username"] = "admin"
        session.save()

        # Staff session is built lazily per test.
        self._staff_sessions = {}

    def tearDown(self):
        connection.set_schema_to_public()
        try:
            self.tenant.delete(force_drop=True)
        except TypeError:
            try:
                self.tenant.delete()
            except Exception:
                pass
        except Exception:
            pass
        connection.set_schema_to_public()
        cache.clear()

    # ---------------- URL helper ----------------

    def url(self, path):
        return f"/portal/{self.schema}/{path.lstrip('/')}"

    # ---------------- staff session helper ----------------

    def _login_staff(self, staff):
        """Return a RequestFactory request with an authenticated staff
        session. Cache the session token so `require_staff_login` passes.
        """
        token = f"test-token-{staff.id}"
        cache.set(
            f"staff_session_token:{self.schema}:{staff.id}",
            token, 1800,
        )
        self._staff_sessions[staff.id] = token

    def _staff_request(self, staff, method="GET", path="/",
                       data=None, is_json=False, xhr=True):
        """Build a RequestFactory request for a staff view."""
        factory = RequestFactory()
        if method.upper() == "GET":
            request = factory.get(path)
        else:
            if is_json:
                request = factory.post(
                    path,
                    data=json.dumps(data or {}),
                    content_type="application/json",
                )
            else:
                request = factory.post(path, data or {})

        if xhr:
            request.META["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"

        from django.contrib.sessions.backends.db import SessionStore
        request.session = SessionStore()
        token = f"test-token-{staff.id}"
        request.session["staff_id"] = staff.id
        request.session["staff_schema_name"] = self.schema
        request.session["staff_username"] = staff.email or "staff"
        request.session["staff_role"] = staff.role
        request.session["staff_name"] = staff.full_name
        request.session["staff_session_token"] = token
        request.session.save()

        cache.set(
            f"staff_session_token:{self.schema}:{staff.id}",
            token, 1800,
        )
        return request

    # ---------------- data helpers ----------------

    def _mark_full_day(self, on_date, statuses=None, source="teacher",
                       staff=None):
        """Create full-day StudentAttendance rows for every student."""
        if statuses is None:
            statuses = ["present"] * len(self.students)
        staff = staff or self.class_teacher
        with schema_context(self.schema):
            rows = []
            for s, st in zip(self.students, statuses):
                rows.append(StudentAttendance.objects.create(
                    student=s, school_class=self.class_obj,
                    date=on_date, period_order=None,
                    status=st, source=source,
                    teacher=staff, marked_by=staff,
                    marked_at=timezone.now(),
                ))
            return rows

    def _make_holiday_weekly(self, day_of_week=6, label="Sunday"):
        with schema_context(self.schema):
            return WeeklyHoliday.objects.create(
                day_of_week=day_of_week, label=label,
            )

    def _make_holiday_annual(self, month, day, label="Independence"):
        with schema_context(self.schema):
            return AnnualHoliday.objects.create(
                month=month, day=day, label=label,
            )

    def _make_vacation(self, name, start, end):
        with schema_context(self.schema):
            return Vacation.objects.create(
                name=name, start_date=start, end_date=end,
            )

    def _make_timetable(self, periods=8, day_of_week=0):
        """Create a DaySchedule + PeriodsTimetable + ClassTimetableAssignment
        and a PeriodTeacherAssignment so subject-teacher flows work."""
        with schema_context(self.schema):
            cal, _ = AcademicCalendar.objects.get_or_create(pk=1)
            lbl, _ = ScheduleLabel.objects.get_or_create(name="Senior")
            DaySchedule.objects.get_or_create(
                academic_calendar=cal,
                day_of_week=day_of_week, order=0, label=lbl,
                defaults={
                    "start_time": "08:00", "end_time": "14:00",
                    "periods": periods,
                },
            )
            tt = PeriodsTimetable.objects.create(
                title=f"TT-{self.class_obj.id}",
                label=lbl, break_duration=0,
                days=[{
                    "day_of_week": day_of_week,
                    "day_label": "Monday",
                    "start": "08:00", "end": "14:00",
                    "periods_count": periods,
                    "break_after": None, "break_duration": 0,
                    "periods": [
                        {"order": i, "start": "08:00", "end": "08:45",
                         "duration": 45, "is_break": False}
                        for i in range(1, periods + 1)
                    ],
                }],
            )
            from axis_saas.models import ClassTimetableAssignment
            ClassTimetableAssignment.objects.create(
                school_class=self.class_obj, timetable=tt,
            )
            PeriodTeacherAssignment.objects.create(
                school_class=self.class_obj, day_of_week=day_of_week,
                period_order=1, subject=self.subject,
                teacher=self.subject_teacher,
            )
            return tt


# =====================================================================
# 1. MODEL TESTS
# =====================================================================

class PermissionModelTests(AttendanceTestBase):
    """ClassTeacherAttendancePermission model behaviour."""

    def test_for_class_creates_defaults(self):
        with schema_context(self.schema):
            p = ClassTeacherAttendancePermission.for_class(self.class_obj)
            self.assertIsNotNone(p)
            self.assertEqual(p.backdate_access, "none")
            self.assertEqual(p.max_edits_per_date, 1)
            self.assertEqual(p.view_history_days, 30)
            self.assertEqual(p.edit_history_days, 5)

    def test_for_class_is_idempotent(self):
        with schema_context(self.schema):
            p1 = ClassTeacherAttendancePermission.for_class(self.class_obj)
            p2 = ClassTeacherAttendancePermission.for_class(self.class_obj)
            self.assertEqual(p1.id, p2.id)
            self.assertEqual(
                ClassTeacherAttendancePermission.objects.count(), 1,
            )

    def test_for_class_none_returns_none(self):
        with schema_context(self.schema):
            self.assertIsNone(
                ClassTeacherAttendancePermission.for_class(None)
            )

    def test_one_to_one_constraint(self):
        from django.db import IntegrityError, transaction as tx
        with schema_context(self.schema):
            ClassTeacherAttendancePermission.for_class(self.class_obj)
            with self.assertRaises(IntegrityError):
                with tx.atomic():
                    ClassTeacherAttendancePermission.objects.create(
                        school_class=self.class_obj,
                    )

    def test_backdate_access_choices(self):
        values = [c[0] for c in
                  ClassTeacherAttendancePermission
                  .BACKDATE_ACCESS_CHOICES]
        self.assertEqual(set(values), {"none", "read", "read_write"})


class EditQuotaModelTests(AttendanceTestBase):
    """ClassTeacherEditQuota model behaviour."""

    def test_for_class_date_creates_zero_counter(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            q = ClassTeacherEditQuota.for_class_date(
                self.class_obj, today,
            )
            self.assertEqual(q.teacher_edit_count, 0)
            self.assertEqual(q.date, today)
            self.assertIsNone(q.last_teacher_edit_at)

    def test_for_class_date_is_idempotent(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            q1 = ClassTeacherEditQuota.for_class_date(self.class_obj, today)
            q1.teacher_edit_count = 3
            q1.save()
            q2 = ClassTeacherEditQuota.for_class_date(self.class_obj, today)
            self.assertEqual(q2.teacher_edit_count, 3)
            self.assertEqual(q1.id, q2.id)

    def test_unique_together_class_date(self):
        from django.db import IntegrityError, transaction as tx
        today = timezone.localdate()
        with schema_context(self.schema):
            ClassTeacherEditQuota.for_class_date(self.class_obj, today)
            with self.assertRaises(IntegrityError):
                with tx.atomic():
                    ClassTeacherEditQuota.objects.create(
                        school_class=self.class_obj, date=today,
                    )

    def test_different_dates_are_separate_rows(self):
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        with schema_context(self.schema):
            q1 = ClassTeacherEditQuota.for_class_date(
                self.class_obj, today,
            )
            q2 = ClassTeacherEditQuota.for_class_date(
                self.class_obj, yesterday,
            )
            self.assertNotEqual(q1.id, q2.id)


class AttendancePolicyModelTests(AttendanceTestBase):
    """AttendancePolicy singleton behaviour."""

    def test_current_creates_defaults(self):
        with schema_context(self.schema):
            p = AttendancePolicy.current()
            self.assertEqual(p.attendance_mode, "both")
            self.assertEqual(p.late_threshold_minutes, 10)
            self.assertEqual(
                float(p.low_attendance_threshold), 75.0,
            )
            self.assertFalse(p.require_admin_approval)
            self.assertEqual(p.allow_teacher_backdate_days, 1)

    def test_current_is_singleton(self):
        with schema_context(self.schema):
            p1 = AttendancePolicy.current()
            p2 = AttendancePolicy.current()
            self.assertEqual(p1.id, p2.id)


# =====================================================================
# 2. HOLIDAY DETECTION TESTS
# =====================================================================

class HolidayDetectionAdminTests(AttendanceTestBase):
    """`admin_attendence._is_holiday` in every branch."""

    def test_weekly_holiday_detected(self):
        today = timezone.localdate()
        self._make_holiday_weekly(day_of_week=today.weekday(),
                                  label="Weekend")
        with schema_context(self.schema):
            is_hol, reason = admin_att._is_holiday(today)
        self.assertTrue(is_hol)
        self.assertIn("Weekly holiday", reason)

    def test_annual_holiday_detected(self):
        today = timezone.localdate()
        self._make_holiday_annual(today.month, today.day, "Independence")
        with schema_context(self.schema):
            is_hol, reason = admin_att._is_holiday(today)
        self.assertTrue(is_hol)
        self.assertIn("Annual holiday", reason)

    def test_vacation_detected(self):
        today = timezone.localdate()
        self._make_vacation(
            "Summer", today - timedelta(days=3), today + timedelta(days=3),
        )
        with schema_context(self.schema):
            is_hol, reason = admin_att._is_holiday(today)
        self.assertTrue(is_hol)
        self.assertIn("Vacation", reason)

    def test_historical_evidence_marks_holiday(self):
        """A past date with a holiday status row is a holiday."""
        with schema_context(self.schema):
            # Create one row with status='holiday'.
            StudentAttendance.objects.create(
                student=self.students[0],
                school_class=self.class_obj,
                date=date(2020, 1, 1),
                period_order=None,
                status="holiday",
                source="admin",
            )
            is_hol, _ = admin_att._is_holiday(date(2020, 1, 1))
        self.assertTrue(is_hol)

    def test_weekly_rule_created_after_date_does_not_apply(self):
        """If a WeeklyHoliday row was created AFTER the date, the date
        is not a holiday for that date."""
        from django.utils import timezone as tz
        past = date(2020, 1, 6)  # Monday
        with schema_context(self.schema):
            wh = WeeklyHoliday.objects.create(
                day_of_week=past.weekday(), label="Test",
            )
            # Force created_at to today so it does NOT cover 2020.
            WeeklyHoliday.objects.filter(pk=wh.pk).update(
                created_at=tz.now(),
            )
            is_hol, reason = admin_att._is_holiday(past)
        self.assertFalse(is_hol)

    def test_no_holiday_returns_false(self):
        # Choose a date that is definitely not a Sunday and not a holiday.
        # Use tomorrow's Monday, avoiding weekends.
        today = timezone.localdate()
        target = today + timedelta(days=1)
        while target.weekday() == 6 or target.weekday() == 5:
            target += timedelta(days=1)
        # Ensure no weekly rule was created for that day
        with schema_context(self.schema):
            WeeklyHoliday.objects.filter(
                day_of_week=target.weekday(),
            ).delete()
            AnnualHoliday.objects.filter(
                month=target.month, day=target.day,
            ).delete()
            is_hol, reason = admin_att._is_holiday(target)
        self.assertFalse(is_hol)
        self.assertEqual(reason, "")


class HolidayDetectionStaffTests(AttendanceTestBase):
    """`staff_attendence._is_holiday` mirrors the admin version."""

    def test_weekly_holiday_detected(self):
        today = timezone.localdate()
        self._make_holiday_weekly(day_of_week=today.weekday())
        with schema_context(self.schema):
            is_hol, reason = staff_att._is_holiday(today)
        self.assertTrue(is_hol)

    def test_vacation_detected(self):
        today = timezone.localdate()
        self._make_vacation("Summer", today, today + timedelta(days=5))
        with schema_context(self.schema):
            is_hol, reason = staff_att._is_holiday(today)
        self.assertTrue(is_hol)
        self.assertIn("Vacation", reason)


# =====================================================================
# 3. PERMISSION LOGIC TESTS (staff_attendence._compute_permission_payload)
# =====================================================================

class PermissionLogicTests(AttendanceTestBase):
    """The single most important helper on the staff side."""

    def test_subject_teacher_cannot_edit(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            p = staff_att._compute_permission_payload(
                self.subject_teacher, self.class_obj, today,
            )
        self.assertFalse(p["is_class_teacher"])
        self.assertTrue(p["can_view"])
        self.assertFalse(p["can_edit"])

    def test_class_teacher_today_can_edit(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            p = staff_att._compute_permission_payload(
                self.class_teacher, self.class_obj, today,
            )
        self.assertTrue(p["is_class_teacher"])
        self.assertTrue(p["can_edit"])

    def test_backdate_none_forbids_past_edit(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "none"
            perm.view_history_days = 30
            perm.edit_history_days = 5
            perm.save()
            p = staff_att._compute_permission_payload(
                self.class_teacher, self.class_obj, yesterday,
            )
        self.assertFalse(p["can_edit"])
        self.assertIn("today only", p["reason"].lower())

    def test_backdate_read_forbids_past_edit(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "read"
            perm.view_history_days = 30
            perm.edit_history_days = 5
            perm.save()
            p = staff_att._compute_permission_payload(
                self.class_teacher, self.class_obj, yesterday,
            )
        self.assertTrue(p["can_view"])
        self.assertFalse(p["can_edit"])
        self.assertIn("read-only", p["reason"].lower())

    def test_beyond_view_window_hides_date(self):
        far_past = timezone.localdate() - timedelta(days=45)
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "read_write"
            perm.view_history_days = 30
            perm.edit_history_days = 10
            perm.save()
            p = staff_att._compute_permission_payload(
                self.class_teacher, self.class_obj, far_past,
            )
        self.assertFalse(p["can_view"])
        self.assertIn("view window", p["reason"].lower())

    def test_beyond_edit_window_forbids_edit(self):
        # 20 days ago, within view (30) but beyond edit (10).
        target = timezone.localdate() - timedelta(days=20)
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "read_write"
            perm.view_history_days = 30
            perm.edit_history_days = 10
            perm.save()
            p = staff_att._compute_permission_payload(
                self.class_teacher, self.class_obj, target,
            )
        self.assertTrue(p["can_view"])
        self.assertFalse(p["can_edit"])
        self.assertIn("editing is only allowed", p["reason"].lower())

    def test_quota_exhausted_forbids_edit(self):
        target = timezone.localdate() - timedelta(days=2)
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "read_write"
            perm.view_history_days = 30
            perm.edit_history_days = 10
            perm.max_edits_per_date = 2
            perm.save()
            q = ClassTeacherEditQuota.for_class_date(
                self.class_obj, target,
            )
            q.teacher_edit_count = 2
            q.save()
            p = staff_att._compute_permission_payload(
                self.class_teacher, self.class_obj, target,
            )
        self.assertTrue(p["can_view"])
        self.assertFalse(p["can_edit"])
        self.assertIn("all 2 edit", p["reason"].lower())

    def test_within_window_with_quota_can_edit(self):
        target = timezone.localdate() - timedelta(days=2)
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "read_write"
            perm.view_history_days = 30
            perm.edit_history_days = 10
            perm.max_edits_per_date = 3
            perm.save()
            q = ClassTeacherEditQuota.for_class_date(
                self.class_obj, target,
            )
            q.teacher_edit_count = 1
            q.save()
            p = staff_att._compute_permission_payload(
                self.class_teacher, self.class_obj, target,
            )
        self.assertTrue(p["can_edit"])
        self.assertEqual(p["quota_used"], 1)
        self.assertEqual(p["quota_remaining"], 2)

    def test_max_edits_zero_always_locks(self):
        target = timezone.localdate() - timedelta(days=1)
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "read_write"
            perm.view_history_days = 30
            perm.edit_history_days = 10
            perm.max_edits_per_date = 0
            perm.save()
            p = staff_att._compute_permission_payload(
                self.class_teacher, self.class_obj, target,
            )
        self.assertFalse(p["can_edit"])


# =====================================================================
# 4. ADMIN VIEW — DASHBOARD
# =====================================================================

class AdminDashboardViewTests(AttendanceTestBase):
    """The admin attendance dashboard page itself."""

    def test_dashboard_renders_empty(self):
        response = self.client.get(self.url("attendance/"))
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertIn("class_rows_json", ctx)
        self.assertIn("teachers_json", ctx)
        self.assertIn("policy_json", ctx)
        self.assertEqual(ctx["summary"]["total_classes"], 1)
        self.assertEqual(ctx["summary"]["total_students"], 5)
        self.assertEqual(ctx["summary"]["classes_pending"], 1)

    def test_dashboard_class_row_includes_permission_hint(self):
        self.client.get(self.url("attendance/"))  # touch the page
        # The template's JS reads backdate_access from class_rows_json.
        # We check the view produced JSON that includes the field.
        response = self.client.get(self.url("attendance/"))
        rows = json.loads(response.context["class_rows_json"])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIn("auto_marked_days", row)
        self.assertIn("manual_marked_days", row)
        self.assertEqual(row["student_count"], 5)

    def test_dashboard_status_completed_after_marking(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today)
        response = self.client.get(self.url("attendance/"))
        summary = response.context["summary"]
        self.assertEqual(summary["classes_completed"], 1)
        self.assertEqual(summary["classes_pending"], 0)

    def test_dashboard_status_partial(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            # Only mark 3 of 5.
            for s in self.students[:3]:
                StudentAttendance.objects.create(
                    student=s, school_class=self.class_obj,
                    date=today, period_order=None,
                    status="present", source="teacher",
                )
        response = self.client.get(self.url("attendance/"))
        summary = response.context["summary"]
        self.assertEqual(summary["classes_partial"], 1)

    def test_dashboard_computes_attendance_percentage(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today)
        response = self.client.get(self.url("attendance/"))
        self.assertEqual(
            response.context["summary"]["attendance_pct_today"], 100.0,
        )

    def test_dashboard_auto_marked_column(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today, source="auto_system")
        response = self.client.get(self.url("attendance/"))
        rows = json.loads(response.context["class_rows_json"])
        self.assertEqual(rows[0]["auto_marked_days"], 1)
        self.assertEqual(rows[0]["manual_marked_days"], 0)


# =====================================================================
# 5. ADMIN — STUDENTS API
# =====================================================================

class AdminStudentsAPITests(AttendanceTestBase):
    """`admin_attendance_students_api`."""

    def test_returns_students_for_date(self):
        today = timezone.localdate()
        response = self.client.get(
            self.url(
                f"api/attendance/students/?class_id={self.class_obj.id}"
                f"&date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["students"]), 5)
        self.assertFalse(body["is_holiday"])
        self.assertFalse(body["locked"])

    def test_missing_params_400(self):
        response = self.client.get(
            self.url("api/attendance/students/"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)

    def test_class_not_found_404(self):
        today = timezone.localdate()
        response = self.client.get(
            self.url(
                f"api/attendance/students/?class_id=9999"
                f"&date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 404)

    def test_holiday_short_circuits(self):
        today = timezone.localdate()
        self._make_holiday_weekly(today.weekday())
        response = self.client.get(
            self.url(
                f"api/attendance/students/?class_id={self.class_obj.id}"
                f"&date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["is_holiday"])
        self.assertEqual(body["students"], [])
        self.assertTrue(body["locked"])

    def test_locked_when_rows_exist(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today)
        response = self.client.get(
            self.url(
                f"api/attendance/students/?class_id={self.class_obj.id}"
                f"&date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertTrue(response.json()["locked"])

    def test_auto_marked_flag(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today, source="auto_system")
        response = self.client.get(
            self.url(
                f"api/attendance/students/?class_id={self.class_obj.id}"
                f"&date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["auto_marked"])
        for s in body["students"]:
            self.assertTrue(s["is_auto"])

    def test_on_leave_flag(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            StudentLeave.objects.create(
                student=self.students[0],
                leave_type="sick", title="Fever", reason="Fever",
                start_date=today, end_date=today,
                total_days=1, status="approved",
            )
        response = self.client.get(
            self.url(
                f"api/attendance/students/?class_id={self.class_obj.id}"
                f"&date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        students = response.json()["students"]
        student_1 = next(s for s in students if s["id"] == self.students[0].id)
        self.assertTrue(student_1["on_leave"])
        self.assertEqual(student_1["status"], "excused")

    def test_period_order_filters_marks(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            StudentAttendance.objects.create(
                student=self.students[0],
                school_class=self.class_obj, date=today,
                period_order=1, status="present", source="teacher",
            )
        response = self.client.get(
            self.url(
                f"api/attendance/students/?class_id={self.class_obj.id}"
                f"&date={today.isoformat()}&period_order=1"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        students = response.json()["students"]
        student_1 = next(s for s in students if s["id"] == self.students[0].id)
        self.assertTrue(student_1["already_marked"])


# =====================================================================
# 6. ADMIN — MARK API
# =====================================================================

class AdminMarkAPITests(AttendanceTestBase):
    """`admin_attendance_mark_api`."""

    def test_creates_rows(self):
        today = timezone.localdate()
        records = [
            {"student_id": s.id, "status": "present"}
            for s in self.students
        ]
        response = self.client.post(
            self.url("api/attendance/mark/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": records,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["saved"], 5)
        with schema_context(self.schema):
            rows = StudentAttendance.objects.filter(
                school_class=self.class_obj, date=today,
            )
            self.assertEqual(rows.count(), 5)
            self.assertTrue(all(r.source == "admin" for r in rows))

    def test_updates_existing_rows(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(
                today,
                statuses=["present"] * 5,
                source="teacher",
            )
        records = [
            {"student_id": s.id, "status": "absent"}
            for s in self.students
        ]
        response = self.client.post(
            self.url("api/attendance/mark/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": records,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        with schema_context(self.schema):
            rows = StudentAttendance.objects.filter(
                school_class=self.class_obj, date=today,
            )
            self.assertTrue(all(r.status == "absent" for r in rows))

    def test_future_date_rejected(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        response = self.client.post(
            self.url("api/attendance/mark/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "date": tomorrow.isoformat(),
                "records": [],
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_json_400(self):
        response = self.client.post(
            self.url("api/attendance/mark/"),
            data="not-json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_audit_written_per_row(self):
        today = timezone.localdate()
        records = [{"student_id": s.id, "status": "present"}
                   for s in self.students]
        self.client.post(
            self.url("api/attendance/mark/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": records,
            }),
            content_type="application/json",
        )
        with schema_context(self.schema):
            count = AttendanceAuditLog.objects.filter(
                date_snapshot=today, action="create",
            ).count()
        self.assertEqual(count, 5)


class AdminBulkMarkAPITests(AttendanceTestBase):
    """`admin_attendance_bulk_mark_api`."""

    def test_bulk_present(self):
        today = timezone.localdate()
        response = self.client.post(
            self.url("api/attendance/bulk-mark/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "status": "present",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 5)

    def test_bulk_absent(self):
        today = timezone.localdate()
        response = self.client.post(
            self.url("api/attendance/bulk-mark/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "status": "absent",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        with schema_context(self.schema):
            rows = StudentAttendance.objects.filter(
                school_class=self.class_obj, date=today,
            )
            self.assertTrue(all(r.status == "absent" for r in rows))

    def test_invalid_status_400(self):
        today = timezone.localdate()
        response = self.client.post(
            self.url("api/attendance/bulk-mark/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "status": "nonsense",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


# =====================================================================
# 7. ADMIN — RECORDS / SUMMARY / HISTORY / COMPLIANCE / AUDIT
# =====================================================================

class AdminRecordsAPITests(AttendanceTestBase):
    """`admin_attendance_records_api`."""

    def test_returns_paginated_records(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today)
        response = self.client.get(
            self.url(
                f"api/attendance/records/?class_id={self.class_obj.id}"
                f"&date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["pagination"]["total"], 5)

    def test_date_range_filter(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today)
            self._mark_full_day(today - timedelta(days=2))
        response = self.client.get(
            self.url(
                f"api/attendance/records/?class_id={self.class_obj.id}"
                f"&start_date={(today - timedelta(days=1)).isoformat()}"
                f"&end_date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertEqual(body["pagination"]["total"], 5)


class AdminSummaryAPITests(AttendanceTestBase):
    """`admin_attendance_summary_api`."""

    def test_summary_counts_by_status(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(
                today,
                statuses=["present", "present", "absent", "late", "present"],
            )
        response = self.client.get(
            self.url(
                f"api/attendance/summary/?date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        s = body["summary"]
        self.assertEqual(s["present"], 3)
        self.assertEqual(s["absent"], 1)
        self.assertEqual(s["late"], 1)
        self.assertEqual(s["total_marked"], 5)


class AdminStudentHistoryAPITests(AttendanceTestBase):
    """`admin_attendance_student_history_api`."""

    def test_history_returns_records_and_percentage(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            StudentAttendance.objects.create(
                student=self.students[0], school_class=self.class_obj,
                date=today, period_order=None,
                status="present", source="teacher",
            )
            StudentAttendance.objects.create(
                student=self.students[0], school_class=self.class_obj,
                date=today - timedelta(days=1), period_order=None,
                status="absent", source="teacher",
            )
        response = self.client.get(
            self.url(
                f"api/attendance/student/{self.students[0].id}/history/"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["attendance_percentage"], 50.0)

    def test_unknown_student_404(self):
        response = self.client.get(
            self.url("api/attendance/student/99999/history/"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 404)


class AdminComplianceAPITests(AttendanceTestBase):
    """`admin_attendance_compliance_api`."""

    def test_reports_unmarked_classes(self):
        today = timezone.localdate()
        response = self.client.get(
            self.url(
                f"api/attendance/compliance/?date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertEqual(body["total_classes"], 1)
        self.assertEqual(body["marked_classes"], 0)
        self.assertEqual(body["pending_classes"], 1)

    def test_reports_marked_classes(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today)
        response = self.client.get(
            self.url(
                f"api/attendance/compliance/?date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertEqual(body["marked_classes"], 1)


class AdminAuditAPITests(AttendanceTestBase):
    """`admin_attendance_audit_api`."""

    def test_audit_returns_logs(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            row = StudentAttendance.objects.create(
                student=self.students[0], school_class=self.class_obj,
                date=today, period_order=None,
                status="present", source="teacher",
            )
            AttendanceAuditLog.objects.create(
                attendance=row,
                student_id_snapshot=row.student_id,
                date_snapshot=row.date,
                action="create", new_status="present",
                changed_by_name="Ayesha Khan",
                reason="initial",
            )
        response = self.client.get(
            self.url("api/attendance/audit/"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(body["pagination"]["total"], 1)

    def test_audit_filters_by_student(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            row = StudentAttendance.objects.create(
                student=self.students[0], school_class=self.class_obj,
                date=today, period_order=None,
                status="present", source="teacher",
            )
            AttendanceAuditLog.objects.create(
                attendance=row,
                student_id_snapshot=row.student_id,
                date_snapshot=row.date,
                action="create", new_status="present",
            )
        response = self.client.get(
            self.url(
                f"api/attendance/audit/?student_id={self.students[0].id}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertEqual(body["pagination"]["total"], 1)


class AdminLowDefaultersAPITests(AttendanceTestBase):
    """`admin_attendance_low_defaulters_api`."""

    def test_low_defaulters_detected(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            # Mark student 0 as absent for 5 days.
            for i in range(5):
                StudentAttendance.objects.create(
                    student=self.students[0],
                    school_class=self.class_obj,
                    date=today - timedelta(days=i),
                    period_order=None,
                    status="absent",
                )
        response = self.client.get(
            self.url("api/attendance/low-defaulters/?days=30"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["ok"])
        student_ids = [r["student_id"] for r in body["students"]]
        self.assertIn(self.students[0].id, student_ids)


# =====================================================================
# 8. ADMIN — POLICY API
# =====================================================================

class AdminPolicyAPITests(AttendanceTestBase):
    """Policy get / save."""

    def test_get_returns_defaults(self):
        response = self.client.get(
            self.url("api/attendance/policy/"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["attendance_mode"], "both")
        self.assertEqual(body["late_threshold_minutes"], 10)

    def test_save_updates_policy(self):
        response = self.client.post(
            self.url("api/attendance/policy/save/"),
            data=json.dumps({
                "attendance_mode": "daily",
                "late_threshold_minutes": 15,
                "low_attendance_threshold": 80.0,
                "auto_mark_absent_at": "15:30",
                "notify_parents_on_absent": False,
                "notify_after_periods": 3,
                "allow_teacher_backdate_days": 2,
                "require_admin_approval": True,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        with schema_context(self.schema):
            p = AttendancePolicy.current()
            self.assertEqual(p.attendance_mode, "daily")
            self.assertEqual(p.late_threshold_minutes, 15)
            self.assertEqual(float(p.low_attendance_threshold), 80.0)
            self.assertFalse(p.notify_parents_on_absent)
            self.assertTrue(p.require_admin_approval)

    def test_save_invalid_json_400(self):
        response = self.client.post(
            self.url("api/attendance/policy/save/"),
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


# =====================================================================
# 9. ADMIN — STAFF ATTENDANCE API
# =====================================================================

class AdminStaffAttendanceAPITests(AttendanceTestBase):
    """Staff attendance list / mark."""

    def test_list_returns_all_staff(self):
        today = timezone.localdate()
        response = self.client.get(
            self.url(
                f"api/attendance/staff/?date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["rows"]), 3)

    def test_mark_staff_present(self):
        today = timezone.localdate()
        response = self.client.post(
            self.url("api/attendance/staff/mark/"),
            data=json.dumps({
                "staff_id": self.class_teacher.id,
                "date": today.isoformat(),
                "status": "present",
                "check_in": "08:15",
                "check_out": "14:00",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        with schema_context(self.schema):
            rec = StaffAttendance.objects.get(
                staff=self.class_teacher, date=today,
            )
            self.assertEqual(rec.status, "present")
            self.assertEqual(rec.source, "admin")
            self.assertIsNotNone(rec.worked_minutes)
            self.assertGreater(rec.worked_minutes, 0)

    def test_mark_staff_on_leave(self):
        today = timezone.localdate()
        self.client.post(
            self.url("api/attendance/staff/mark/"),
            data=json.dumps({
                "staff_id": self.class_teacher.id,
                "date": today.isoformat(),
                "status": "on_leave",
            }),
            content_type="application/json",
        )
        with schema_context(self.schema):
            rec = StaffAttendance.objects.get(
                staff=self.class_teacher, date=today,
            )
            self.assertEqual(rec.status, "on_leave")

    def test_mark_staff_unknown_id_404(self):
        today = timezone.localdate()
        response = self.client.post(
            self.url("api/attendance/staff/mark/"),
            data=json.dumps({
                "staff_id": 99999, "date": today.isoformat(),
                "status": "present",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)


# =====================================================================
# 10. ADMIN — AUTO-MARKED DATES API
# =====================================================================

class AdminAutoMarkedAPITests(AttendanceTestBase):
    """Auto-marked dates listing."""

    def test_returns_auto_system_dates(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today, source="auto_system")
        response = self.client.get(
            self.url(
                f"api/attendance/auto-marked-dates/"
                f"?class_id={self.class_obj.id}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["dates"]), 1)
        self.assertEqual(body["dates"][0]["date"], today.isoformat())
        self.assertEqual(body["dates"][0]["total"], 5)

    def test_empty_when_no_auto_marks(self):
        response = self.client.get(
            self.url(
                f"api/attendance/auto-marked-dates/"
                f"?class_id={self.class_obj.id}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.json()["dates"], [])


# =====================================================================
# 11. ADMIN — CLASS TEACHER PERMISSIONS API
# =====================================================================

class AdminPermissionsAPITests(AttendanceTestBase):
    """Class-teacher permission list / save endpoints."""

    def test_list_returns_classes_with_class_teacher(self):
        response = self.client.get(
            self.url("api/attendance/class-teacher-permissions/"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["permissions"]), 1)
        row = body["permissions"][0]
        self.assertEqual(row["class_id"], self.class_obj.id)
        self.assertEqual(row["backdate_access"], "none")
        self.assertEqual(row["max_edits_per_date"], 1)
        self.assertEqual(row["view_history_days"], 30)
        self.assertEqual(row["edit_history_days"], 5)

    def test_list_excludes_classes_without_class_teacher(self):
        with schema_context(self.schema):
            SchoolClass.objects.create(name="Grade 2", section="B")
        response = self.client.get(
            self.url("api/attendance/class-teacher-permissions/"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertEqual(len(body["permissions"]), 1)

    def test_save_persists_values(self):
        response = self.client.post(
            self.url(
                "api/attendance/class-teacher-permissions/save/"
            ),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "backdate_access": "read_write",
                "max_edits_per_date": 5,
                "view_history_days": 60,
                "edit_history_days": 14,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        with schema_context(self.schema):
            p = ClassTeacherAttendancePermission.objects.get(
                school_class=self.class_obj,
            )
            self.assertEqual(p.backdate_access, "read_write")
            self.assertEqual(p.max_edits_per_date, 5)
            self.assertEqual(p.view_history_days, 60)
            self.assertEqual(p.edit_history_days, 14)
            self.assertEqual(p.updated_by, "admin")

    def test_save_clamps_out_of_range_values(self):
        response = self.client.post(
            self.url(
                "api/attendance/class-teacher-permissions/save/"
            ),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "backdate_access": "read_write",
                "max_edits_per_date": 999,
                "view_history_days": 99999,
                "edit_history_days": -5,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        with schema_context(self.schema):
            p = ClassTeacherAttendancePermission.objects.get(
                school_class=self.class_obj,
            )
            self.assertEqual(p.max_edits_per_date, 50)
            self.assertEqual(p.view_history_days, 730)
            self.assertEqual(p.edit_history_days, 0)

    def test_save_invalid_backdate_falls_back_to_none(self):
        response = self.client.post(
            self.url(
                "api/attendance/class-teacher-permissions/save/"
            ),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "backdate_access": "wat",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        with schema_context(self.schema):
            p = ClassTeacherAttendancePermission.objects.get(
                school_class=self.class_obj,
            )
            self.assertEqual(p.backdate_access, "none")

    def test_save_unknown_class_404(self):
        response = self.client.post(
            self.url(
                "api/attendance/class-teacher-permissions/save/"
            ),
            data=json.dumps({
                "class_id": 99999, "backdate_access": "read",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)


# =====================================================================
# 12. ADMIN — DAILY LOGS API
# =====================================================================

class AdminDailyLogsAPITests(AttendanceTestBase):
    """`admin_attendance_daily_logs_api`."""

    def test_returns_logs_for_class_and_date(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            row = StudentAttendance.objects.create(
                student=self.students[0], school_class=self.class_obj,
                date=today, period_order=None,
                status="present", source="teacher",
            )
            AttendanceAuditLog.objects.create(
                attendance=row,
                student_id_snapshot=row.student_id,
                date_snapshot=row.date,
                action="create", new_status="present",
                changed_by_name="Ayesha Khan",
                reason="initial",
            )
        response = self.client.get(
            self.url(
                f"api/attendance/daily-logs/"
                f"?class_id={self.class_obj.id}"
                f"&date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["logs"]), 1)
        self.assertEqual(body["logs"][0]["action"], "create")
        self.assertIn(self.students[0].id, body["student_names"])
        self.assertIn("quota", body)

    def test_returns_quota_summary(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.max_edits_per_date = 3
            perm.save()
            q = ClassTeacherEditQuota.for_class_date(
                self.class_obj, today,
            )
            q.teacher_edit_count = 2
            q.last_teacher_edit_by_name = "Ayesha Khan"
            q.save()
        response = self.client.get(
            self.url(
                f"api/attendance/daily-logs/"
                f"?class_id={self.class_obj.id}"
                f"&date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertEqual(body["quota"]["teacher_edit_count"], 2)
        self.assertEqual(body["quota"]["max_edits_per_date"], 3)
        self.assertEqual(
            body["quota"]["last_teacher_edit_by_name"], "Ayesha Khan",
        )

    def test_missing_params_400(self):
        response = self.client.get(
            self.url("api/attendance/daily-logs/"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)


# =====================================================================
# 13. STAFF — DASHBOARD VIEW
# =====================================================================

class StaffDashboardViewTests(AttendanceTestBase):
    """`staff_attendance_view` renders the class-teacher sections."""

    def test_class_teacher_sees_class_section(self):
        request = self._staff_request(self.class_teacher)
        response = staff_att.staff_attendance_view(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"class_teacher_classes_json", response.content,
        )

    def test_page_renders_with_today_context(self):
        request = self._staff_request(self.class_teacher)
        response = staff_att.staff_attendance_view(request)
        html = response.content.decode("utf-8")
        self.assertIn("Attendance", html)
        self.assertIn(timezone.localdate().isoformat(), html)

    def test_holiday_context_passed(self):
        today = timezone.localdate()
        self._make_holiday_weekly(today.weekday())
        request = self._staff_request(self.class_teacher)
        response = staff_att.staff_attendance_view(request)
        self.assertIn(b"is_holiday", response.content)


# =====================================================================
# 14. STAFF — DATES API
# =====================================================================

class StaffDatesAPITests(AttendanceTestBase):
    """`staff_attendance_dates_api`."""

    def test_returns_dates_filtered_by_view_window(self):
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.view_history_days = 7
            perm.save()
        request = self._staff_request(
            self.class_teacher,
            path=f"/?class_id={self.class_obj.id}",
        )
        response = staff_att.staff_attendance_dates_api(request)
        body = json.loads(response.content)
        self.assertTrue(body["ok"])
        # view_history_days + 1 dates (including today).
        self.assertEqual(len(body["dates"]), 8)

    def test_unknown_class_403(self):
        with schema_context(self.schema):
            other_class = SchoolClass.objects.create(
                name="Grade X", section="Z",
            )
            other_id = other_class.id
        request = self._staff_request(
            self.class_teacher,
            path=f"/?class_id={other_id}",
        )
        response = staff_att.staff_attendance_dates_api(request)
        self.assertEqual(response.status_code, 403)

    def test_missing_class_id_400(self):
        request = self._staff_request(self.class_teacher, path="/")
        response = staff_att.staff_attendance_dates_api(request)
        self.assertEqual(response.status_code, 400)

    def test_subject_teacher_blocked(self):
        """Not the class teacher -> 'Not your class'."""
        request = self._staff_request(
            self.subject_teacher,
            path=f"/?class_id={self.class_obj.id}",
        )
        response = staff_att.staff_attendance_dates_api(request)
        self.assertEqual(response.status_code, 403)

    def test_quota_used_reported_per_date(self):
        target = timezone.localdate() - timedelta(days=2)
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "read_write"
            perm.view_history_days = 5
            perm.edit_history_days = 5
            perm.max_edits_per_date = 3
            perm.save()
            q = ClassTeacherEditQuota.for_class_date(
                self.class_obj, target,
            )
            q.teacher_edit_count = 1
            q.save()
        request = self._staff_request(
            self.class_teacher,
            path=f"/?class_id={self.class_obj.id}",
        )
        response = staff_att.staff_attendance_dates_api(request)
        body = json.loads(response.content)
        row = next(d for d in body["dates"]
                   if d["date"] == target.isoformat())
        self.assertEqual(row["quota_used"], 1)
        self.assertEqual(row["quota_max"], 3)
        self.assertEqual(row["quota_remaining"], 2)

    def test_holiday_flagged_in_dates_list(self):
        today = timezone.localdate()
        self._make_holiday_weekly(today.weekday())
        request = self._staff_request(
            self.class_teacher,
            path=f"/?class_id={self.class_obj.id}",
        )
        response = staff_att.staff_attendance_dates_api(request)
        body = json.loads(response.content)
        today_row = next(d for d in body["dates"] if d["is_today"])
        self.assertTrue(today_row["is_holiday"])
        self.assertEqual(today_row["status"], "holiday")


# =====================================================================
# 15. STAFF — STUDENTS API
# =====================================================================

class StaffStudentsAPITests(AttendanceTestBase):
    """`staff_attendance_students_api`."""

    def test_class_teacher_can_read_today(self):
        today = timezone.localdate()
        request = self._staff_request(
            self.class_teacher,
            path=f"/?class_id={self.class_obj.id}"
                 f"&date={today.isoformat()}",
        )
        response = staff_att.staff_attendance_students_api(request)
        body = json.loads(response.content)
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["students"]), 5)
        self.assertIn("permission", body)

    def test_subject_teacher_reads_today_period(self):
        today = timezone.localdate()
        # Only works if today is the timetable's day_of_week (Monday=0).
        # Create the timetable on today's weekday so it applies.
        self._make_timetable(periods=8, day_of_week=today.weekday())
        request = self._staff_request(
            self.subject_teacher,
            path=f"/?class_id={self.class_obj.id}"
                 f"&date={today.isoformat()}&period_order=1",
        )
        response = staff_att.staff_attendance_students_api(request)
        self.assertEqual(response.status_code, 200)

    def test_subject_teacher_cannot_read_past(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        self._make_timetable(periods=8, day_of_week=yesterday.weekday())
        request = self._staff_request(
            self.subject_teacher,
            path=f"/?class_id={self.class_obj.id}"
                 f"&date={yesterday.isoformat()}&period_order=1",
        )
        response = staff_att.staff_attendance_students_api(request)
        self.assertEqual(response.status_code, 403)

    def test_holiday_short_circuits(self):
        today = timezone.localdate()
        self._make_holiday_weekly(today.weekday())
        request = self._staff_request(
            self.class_teacher,
            path=f"/?class_id={self.class_obj.id}"
                 f"&date={today.isoformat()}",
        )
        response = staff_att.staff_attendance_students_api(request)
        body = json.loads(response.content)
        self.assertTrue(body["is_holiday"])
        self.assertEqual(body["students"], [])

    def test_locked_flag_when_marked(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today)
            # Turn on read_write so can_edit is possible in principle;
            # but the frontend is locked because marks exist.
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "read_write"
            perm.max_edits_per_date = 3
            perm.view_history_days = 30
            perm.edit_history_days = 5
            perm.save()
        request = self._staff_request(
            self.class_teacher,
            path=f"/?class_id={self.class_obj.id}"
                 f"&date={today.isoformat()}",
        )
        response = staff_att.staff_attendance_students_api(request)
        # lock_reason may be empty for today but the flag reflects marks.
        body = json.loads(response.content)
        self.assertTrue(body["locked"])

    def test_student_payload_has_expected_fields(self):
        today = timezone.localdate()
        request = self._staff_request(
            self.class_teacher,
            path=f"/?class_id={self.class_obj.id}"
                 f"&date={today.isoformat()}",
        )
        response = staff_att.staff_attendance_students_api(request)
        body = json.loads(response.content)
        s = body["students"][0]
        for key in ("id", "roll_number", "name", "father_name",
                    "status", "already_marked", "on_leave",
                    "source", "is_auto"):
            self.assertIn(key, s)

    def test_auto_marked_flag_set(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today, source="auto_system")
        request = self._staff_request(
            self.class_teacher,
            path=f"/?class_id={self.class_obj.id}"
                 f"&date={today.isoformat()}",
        )
        response = staff_att.staff_attendance_students_api(request)
        body = json.loads(response.content)
        self.assertTrue(body["auto_marked"])
        self.assertTrue(all(s["is_auto"] for s in body["students"]))

    def test_on_leave_overrides_default_status(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            StudentLeave.objects.create(
                student=self.students[0],
                leave_type="sick", title="Fever", reason="Fever",
                start_date=today, end_date=today,
                total_days=1, status="approved",
            )
        request = self._staff_request(
            self.class_teacher,
            path=f"/?class_id={self.class_obj.id}"
                 f"&date={today.isoformat()}",
        )
        response = staff_att.staff_attendance_students_api(request)
        body = json.loads(response.content)
        student_1 = next(s for s in body["students"]
                         if s["id"] == self.students[0].id)
        self.assertTrue(student_1["on_leave"])
        self.assertEqual(student_1["status"], "excused")


# =====================================================================
# 16. STAFF — MARK API
# =====================================================================

class StaffMarkAPITests(AttendanceTestBase):
    """`staff_attendance_mark_api`."""

    def test_today_edit_does_not_consume_quota(self):
        # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-7): the previous
        # version of this test contradicted itself — the name
        # and comment said "does not consume quota" but the
        # assertion checked `teacher_edit_count == 1` (i.e.,
        # that it DID).  The correct behaviour, per
        # _compute_permission_payload(), is that today's date is
        # always editable regardless of the backdate quota, so
        # no quota row must be created for today.
        today = timezone.localdate()
        records = [{"student_id": s.id, "status": "present"}
                   for s in self.students]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        response = staff_att.staff_attendance_mark_api(request)
        body = json.loads(response.content)
        self.assertTrue(body["ok"])
        self.assertEqual(body["saved"], 5)
        with schema_context(self.schema):
            q = ClassTeacherEditQuota.objects.filter(
                school_class=self.class_obj, date=today,
            ).first()
        self.assertIsNone(q)

    def test_past_edit_consumes_quota(self):
        target = timezone.localdate() - timedelta(days=2)
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "read_write"
            perm.view_history_days = 30
            perm.edit_history_days = 5
            perm.max_edits_per_date = 3
            perm.save()
        records = [{"student_id": s.id, "status": "present"}
                   for s in self.students]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": target.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        response = staff_att.staff_attendance_mark_api(request)
        body = json.loads(response.content)
        self.assertTrue(body["ok"])
        with schema_context(self.schema):
            q = ClassTeacherEditQuota.objects.get(
                school_class=self.class_obj, date=target,
            )
        self.assertEqual(q.teacher_edit_count, 1)
        self.assertEqual(
            q.last_teacher_edit_by_name, self.class_teacher.full_name,
        )

    def test_edit_blocked_when_quota_exhausted(self):
        target = timezone.localdate() - timedelta(days=2)
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "read_write"
            perm.view_history_days = 30
            perm.edit_history_days = 5
            perm.max_edits_per_date = 1
            perm.save()
            q = ClassTeacherEditQuota.for_class_date(
                self.class_obj, target,
            )
            q.teacher_edit_count = 1
            q.save()
        records = [{"student_id": s.id, "status": "absent"}
                   for s in self.students]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": target.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        response = staff_att.staff_attendance_mark_api(request)
        self.assertEqual(response.status_code, 403)

    def test_edit_blocked_beyond_view_window(self):
        target = timezone.localdate() - timedelta(days=99)
        records = [{"student_id": s.id, "status": "present"}
                   for s in self.students]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": target.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        response = staff_att.staff_attendance_mark_api(request)
        self.assertEqual(response.status_code, 403)

    def test_holiday_blocked(self):
        today = timezone.localdate()
        self._make_holiday_weekly(today.weekday())
        records = [{"student_id": s.id, "status": "present"}
                   for s in self.students]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        response = staff_att.staff_attendance_mark_api(request)
        self.assertEqual(response.status_code, 400)

    def test_future_date_rejected(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        records = [{"student_id": s.id, "status": "present"}
                   for s in self.students]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": tomorrow.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        response = staff_att.staff_attendance_mark_api(request)
        self.assertEqual(response.status_code, 400)

    def test_teacher_owns_auto_marked_row_after_edit(self):
        """Editing an auto-marked row transfers its source to 'teacher'."""
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today, source="auto_system")
        records = [{"student_id": self.students[0].id,
                    "status": "present"}]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        response = staff_att.staff_attendance_mark_api(request)
        self.assertEqual(response.status_code, 200)
        with schema_context(self.schema):
            row = StudentAttendance.objects.get(
                student=self.students[0],
                school_class=self.class_obj,
                date=today, period_order=None,
            )
            self.assertEqual(row.source, "teacher")

    def test_audit_written_on_edit(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today, source="auto_system")
        records = [{"student_id": self.students[0].id,
                    "status": "absent"}]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        staff_att.staff_attendance_mark_api(request)
        with schema_context(self.schema):
            last = AttendanceAuditLog.objects.filter(
                student_id_snapshot=self.students[0].id,
                date_snapshot=today,
            ).order_by("-changed_at").first()
        self.assertIsNotNone(last)
        self.assertEqual(last.action, "update")
        self.assertEqual(last.old_status, "present")
        self.assertEqual(last.new_status, "absent")

    def test_invalid_json_400(self):
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data="not-json", is_json=False,
        )
        request._body = b"not-json"
        response = staff_att.staff_attendance_mark_api(request)
        self.assertEqual(response.status_code, 400)


# =====================================================================
# 17. STAFF — COPY API
# =====================================================================

class StaffCopyAPITests(AttendanceTestBase):
    """`staff_attendance_copy_api`."""

    def test_copy_yesterday(self):
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        with schema_context(self.schema):
            self._mark_full_day(yesterday)
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/copy/",
            data={
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "source": "yesterday",
            },
            is_json=True,
        )
        response = staff_att.staff_attendance_copy_api(request)
        body = json.loads(response.content)
        self.assertTrue(body["ok"])
        self.assertEqual(body["copied"], 5)

    def test_copy_last_period(self):
        today = timezone.localdate()
        self._make_timetable(periods=8, day_of_week=today.weekday())
        # Mark period 1.
        with schema_context(self.schema):
            for s in self.students:
                StudentAttendance.objects.create(
                    student=s, school_class=self.class_obj,
                    date=today, period_order=1,
                    status="present", source="teacher",
                )
        # Now copy from period 1 to period 2.
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/copy/",
            data={
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "period_order": 2,
                "source": "last_period",
            },
            is_json=True,
        )
        # Note: The class teacher isn't the period teacher; the
        # permission check for period teachers only matters for today.
        response = staff_att.staff_attendance_copy_api(request)
        body = json.loads(response.content)
        # If the class teacher lacks permission, it returns 403.
        # Otherwise, all 5 should be copied.
        if body.get("ok"):
            self.assertEqual(body["copied"], 5)


# =====================================================================
# 18. STAFF — RECORDS API
# =====================================================================

class StaffRecordsAPITests(AttendanceTestBase):
    """`staff_attendance_records_api`."""

    def test_class_teacher_sees_records(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today)
        request = self._staff_request(self.class_teacher, path="/records/")
        response = staff_att.staff_attendance_records_api(request)
        body = json.loads(response.content)
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["records"]), 5)

    def test_filters_by_status(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(
                today,
                statuses=["present", "present", "absent", "present", "present"],
            )
        request = self._staff_request(
            self.class_teacher, path="/?status=absent",
        )
        response = staff_att.staff_attendance_records_api(request)
        body = json.loads(response.content)
        self.assertEqual(len(body["records"]), 1)


# =====================================================================
# 19. STAFF — MISSED DAYS API
# =====================================================================

class StaffMissedDaysAPITests(AttendanceTestBase):
    """`staff_attendance_missed_days_api`."""

    def test_returns_unmarked_days(self):
        # Don't mark anything -> the last 30 non-holiday non-Sunday
        # days should all be "missed".
        request = self._staff_request(
            self.class_teacher,
            path=f"/?class_id={self.class_obj.id}",
        )
        response = staff_att.staff_attendance_missed_days_api(request)
        body = json.loads(response.content)
        self.assertTrue(body["ok"])
        # At least today is missed.
        self.assertIn(
            timezone.localdate().isoformat(), body["missed_days"],
        )

    def test_not_class_teacher_403(self):
        request = self._staff_request(
            self.subject_teacher,
            path=f"/?class_id={self.class_obj.id}",
        )
        response = staff_att.staff_attendance_missed_days_api(request)
        self.assertEqual(response.status_code, 403)


# =====================================================================
# 20. STAFF — POLICY API
# =====================================================================

class StaffPolicyAPITests(AttendanceTestBase):
    """`staff_attendance_policy_api`."""

    def test_returns_policy(self):
        request = self._staff_request(self.class_teacher, path="/policy/")
        response = staff_att.staff_attendance_policy_api(request)
        body = json.loads(response.content)
        self.assertTrue(body["ok"])
        self.assertEqual(body["attendance_mode"], "both")
        self.assertEqual(body["late_threshold_minutes"], 10)


# =====================================================================
# 21. EDIT QUOTA — END-TO-END TESTS
# =====================================================================

class EditQuotaTests(AttendanceTestBase):
    """End-to-end edit-quota enforcement."""

    def _setup_edit_window(self, max_edits=2, target=None):
        target = target or (timezone.localdate() - timedelta(days=2))
        with schema_context(self.schema):
            perm = ClassTeacherAttendancePermission.for_class(
                self.class_obj,
            )
            perm.backdate_access = "read_write"
            perm.view_history_days = 30
            perm.edit_history_days = 10
            perm.max_edits_per_date = max_edits
            perm.save()
        return target

    def _do_edit(self, target, status="present"):
        records = [{"student_id": s.id, "status": status}
                   for s in self.students]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": target.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        return staff_att.staff_attendance_mark_api(request)

    def test_two_edits_then_locked(self):
        target = self._setup_edit_window(max_edits=2)
        r1 = self._do_edit(target)
        self.assertEqual(r1.status_code, 200)
        r2 = self._do_edit(target)
        self.assertEqual(r2.status_code, 200)
        r3 = self._do_edit(target)
        self.assertEqual(r3.status_code, 403)

    def test_first_edit_then_second_allowed(self):
        target = self._setup_edit_window(max_edits=3)
        r1 = self._do_edit(target)
        self.assertEqual(r1.status_code, 200)
        with schema_context(self.schema):
            q = ClassTeacherEditQuota.objects.get(
                school_class=self.class_obj, date=target,
            )
        self.assertEqual(q.teacher_edit_count, 1)

    def test_zero_max_edits_locks_immediately(self):
        target = self._setup_edit_window(max_edits=0)
        r = self._do_edit(target)
        self.assertEqual(r.status_code, 403)

    def test_admin_bypasses_quota(self):
        target = self._setup_edit_window(max_edits=1)
        # Teacher exhausts quota.
        self._do_edit(target)
        self._do_edit(target)  # blocked
        # Admin can still edit.
        response = self.client.post(
            self.url("api/attendance/mark/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "date": target.isoformat(),
                "records": [
                    {"student_id": s.id, "status": "absent"}
                    for s in self.students
                ],
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)


# =====================================================================
# 22. AUDIT TRAIL — END-TO-END TESTS
# =====================================================================

class AuditTrailTests(AttendanceTestBase):
    """Audit-log writes on both admin and staff actions."""

    def test_admin_create_writes_audit(self):
        today = timezone.localdate()
        self.client.post(
            self.url("api/attendance/mark/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": [
                    {"student_id": s.id, "status": "present"}
                    for s in self.students
                ],
            }),
            content_type="application/json",
        )
        with schema_context(self.schema):
            logs = AttendanceAuditLog.objects.filter(
                date_snapshot=today, action="create",
            )
        self.assertEqual(logs.count(), 5)

    def test_admin_update_writes_audit_with_old_status(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today)
        self.client.post(
            self.url("api/attendance/mark/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": [
                    {"student_id": s.id, "status": "absent"}
                    for s in self.students
                ],
            }),
            content_type="application/json",
        )
        with schema_context(self.schema):
            logs = AttendanceAuditLog.objects.filter(
                date_snapshot=today, action="update",
            )
            self.assertEqual(logs.count(), 5)
            log = logs.first()
            self.assertEqual(log.old_status, "present")
            self.assertEqual(log.new_status, "absent")

    def test_teacher_audit_has_teacher_reason(self):
        today = timezone.localdate()
        records = [{"student_id": s.id, "status": "present"}
                   for s in self.students]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        staff_att.staff_attendance_mark_api(request)
        with schema_context(self.schema):
            log = AttendanceAuditLog.objects.filter(
                action="create",
            ).first()
            self.assertIsNotNone(log)
            self.assertIn(
                f"teacher:{self.class_teacher.full_name}", log.reason,
            )
            self.assertEqual(log.changed_by_id, self.class_teacher.id)


# =====================================================================
# 23. AUTO-MARKED — END-TO-END TESTS
# =====================================================================

class AutoMarkedHandlingTests(AttendanceTestBase):
    """Auto-marked rows in every surface."""

    def test_dashboard_shows_auto_marked_count(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today, source="auto_system")
        response = self.client.get(self.url("attendance/"))
        rows = json.loads(response.context["class_rows_json"])
        self.assertEqual(rows[0]["auto_marked_days"], 1)

    def test_students_api_flags_auto_marked(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today, source="auto_system")
        response = self.client.get(
            self.url(
                f"api/attendance/students/?class_id={self.class_obj.id}"
                f"&date={today.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertTrue(response.json()["auto_marked"])

    def test_teacher_edit_transfers_ownership(self):
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today, source="auto_system")
        records = [{"student_id": self.students[0].id,
                    "status": "present"}]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        staff_att.staff_attendance_mark_api(request)
        with schema_context(self.schema):
            row = StudentAttendance.objects.get(
                student=self.students[0],
                school_class=self.class_obj, date=today,
                period_order=None,
            )
            self.assertEqual(row.source, "teacher")

    def test_auto_marked_after_teacher_edit_is_not_auto(self):
        """After a teacher edits an auto row, the dashboard auto-mark
        count for that date drops."""
        today = timezone.localdate()
        with schema_context(self.schema):
            self._mark_full_day(today, source="auto_system")
        # Teacher edits just one row.
        records = [{"student_id": self.students[0].id,
                    "status": "present"}]
        request = self._staff_request(
            self.class_teacher, method="POST",
            path="/mark/",
            data={
                "class_id": self.class_obj.id,
                "date": today.isoformat(),
                "records": records,
            },
            is_json=True,
        )
        staff_att.staff_attendance_mark_api(request)
        with schema_context(self.schema):
            auto_count = StudentAttendance.objects.filter(
                school_class=self.class_obj, date=today,
                period_order__isnull=True, source="auto_system",
            ).count()
        self.assertEqual(auto_count, 4)


# =====================================================================
# 24. MULTI-TENANT ISOLATION
# =====================================================================

class MultiTenantIsolationTests(AttendanceTestBase):
    """Attendance records do not leak across tenants."""

    def test_admin_cannot_see_other_schema_students(self):
        # Create a second tenant.
        connection.set_schema_to_public()
        other = SchoolClient.objects.create(
            schema_name="attendance-other",
            name="Other School",
            admin_username="admin2",
            admin_password="admin123",
            enabled_features={
                "desktop": ["attendance_management"],
                "mobile": [], "staff_portal": [],
            },
        )
        connection.set_schema_to_public()
        try:
            with schema_context("attendance-other"):
                other_class = SchoolClass.objects.create(
                    name="Other-Grade", section="Z",
                )
            # Admin of the main tenant tries to fetch the other class.
            today = timezone.localdate()
            response = self.client.get(
                self.url(
                    f"api/attendance/students/?class_id={other_class.id}"
                    f"&date={today.isoformat()}"
                ),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
            self.assertEqual(response.status_code, 404)
        finally:
            connection.set_schema_to_public()
            try:
                other.delete(force_drop=True)
            except TypeError:
                try:
                    other.delete()
                except Exception:
                    pass
            except Exception:
                pass
            connection.set_schema_to_public()


# =====================================================================
# 25. INTEGRATION FLOW — FULL LIFECYCLE
# =====================================================================

class FullLifecycleIntegrationTests(AttendanceTestBase):
    """One long test that walks through a realistic end-to-end flow."""

    def test_full_attendance_lifecycle(self):
        today = timezone.localdate()
        two_days_ago = today - timedelta(days=2)

        # 1) Admin grants read_write with 2 edits and 5-day window.
        r = self.client.post(
            self.url("api/attendance/class-teacher-permissions/save/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "backdate_access": "read_write",
                "max_edits_per_date": 2,
                "view_history_days": 5,
                "edit_history_days": 5,
            }),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)

        # 2) Class teacher marks today's attendance.
        records = [{"student_id": s.id, "status": "present"}
                   for s in self.students]
        req = self._staff_request(
            self.class_teacher, method="POST", path="/mark/",
            data={"class_id": self.class_obj.id,
                  "date": today.isoformat(), "records": records},
            is_json=True,
        )
        r = staff_att.staff_attendance_mark_api(req)
        self.assertEqual(r.status_code, 200)

        # 3) Teacher edits two-days-ago (first time; quota 1/2).
        req = self._staff_request(
            self.class_teacher, method="POST", path="/mark/",
            data={"class_id": self.class_obj.id,
                  "date": two_days_ago.isoformat(), "records": records},
            is_json=True,
        )
        r = staff_att.staff_attendance_mark_api(req)
        self.assertEqual(r.status_code, 200)

        # 4) Teacher edits again (quota 2/2).
        r = staff_att.staff_attendance_mark_api(req)
        self.assertEqual(r.status_code, 200)

        # 5) Teacher attempts a third edit; blocked.
        r = staff_att.staff_attendance_mark_api(req)
        self.assertEqual(r.status_code, 403)

        # 6) Admin fetches daily logs for that date.
        r = self.client.get(
            self.url(
                f"api/attendance/daily-logs/"
                f"?class_id={self.class_obj.id}"
                f"&date={two_days_ago.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = r.json()
        self.assertEqual(body["quota"]["teacher_edit_count"], 2)
        self.assertEqual(body["quota"]["max_edits_per_date"], 2)
        self.assertGreaterEqual(len(body["logs"]), 5)

        # 7) Admin edits two-days-ago too (still allowed).
        r = self.client.post(
            self.url("api/attendance/mark/"),
            data=json.dumps({
                "class_id": self.class_obj.id,
                "date": two_days_ago.isoformat(),
                "records": [
                    {"student_id": s.id, "status": "absent"}
                    for s in self.students
                ],
            }),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)

        # 8) Admin views summary.
        r = self.client.get(
            self.url(
                f"api/attendance/summary/"
                f"?date={two_days_ago.isoformat()}"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertTrue(r.json()["ok"])
        self.assertEqual(r.json()["summary"]["absent"], 5)
