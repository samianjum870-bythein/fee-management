"""ASSIGN_TEACHERS_HARDENING_V4 — additional test coverage.

The V3 suite (``test_assign_teachers.py``) covered CSRF and the
server-side save-conflict check. This file fills the remaining gaps
identified in the V4 review:

  * api_create_substitute functional paths (V3 only asserted CSRF).
  * Orphan PTA rows must not appear in the GET busy map (BUG-4).
  * Orphan PTA rows must be removed by reconcile when the class has no
    timetable at all (BUG-5).
  * api_get_todays_leave non-holiday branch (V3 only touched CSRF).
  * api_save_teacher_assignments refuses a missing version token when
    rows already exist (BUG-7).
  * Two sequential saves that would put the same teacher in the same
    slot of two classes: the second must be refused.

Run:
    python manage.py test axis_saas.tests.test_assign_teachers_hardening_v4
"""

from datetime import date

from django.test import TestCase  # noqa: F401
from django_tenants.utils import schema_context

from axis_saas.models import (
    AcademicCalendar,
    ClassSubject,
    ClassTimetableAssignment,
    DaySchedule,
    LeaveRequest,
    PeriodsTimetable,
    PeriodTeacherAssignment,
    ScheduleLabel,
    SubstituteAssignment,
)
from axis_saas.tests.test_assign_teachers import AssignTeachersTestBase


# --------------------------------------------------------------------- helpers

_DAY_LABELS = {
    0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday',
    4: 'Friday', 5: 'Saturday', 6: 'Sunday',
}


def _make_class_with_timetable(base, name, section, periods=8,
                                day_of_week=0):
    """Create a class + a periods timetable with `periods` periods on
    the given `day_of_week` (default Monday) and assign it. Returns
    the class."""
    cls = base._make_class(name, section)
    _day_label = _DAY_LABELS.get(day_of_week, str(day_of_week))
    with schema_context(base.tenant.schema_name):
        cal, _ = AcademicCalendar.objects.get_or_create(pk=1)
        lbl, _ = ScheduleLabel.objects.get_or_create(name='Senior')
        DaySchedule.objects.get_or_create(
            academic_calendar=cal, day_of_week=day_of_week, order=0,
            label=lbl,
            defaults={
                'start_time': '08:00', 'end_time': '14:00',
                'periods': periods,
            },
        )
        tt = PeriodsTimetable.objects.create(
            title=f'TT-{cls.id}', label=lbl, break_duration=0,
            days=[{
                'day_of_week': day_of_week, 'day_label': _day_label,
                'start': '08:00', 'end': '14:00',
                'periods_count': periods, 'break_after': None,
                'break_duration': 0,
                'periods': [
                    {'order': i, 'start': '08:00', 'end': '08:45',
                     'duration': 45, 'is_break': False}
                    for i in range(1, periods + 1)
                ],
            }],
        )
        ClassTimetableAssignment.objects.create(
            school_class=cls, timetable=tt,
        )
    return cls


def _seed_absent_teacher_with_leave(base):
    """Set up: class + teacher + subject + 8-period timetable on
    today's weekday + an approved leave for today + a PTA row at
    (today, P1).

    V4_1: the timetable MUST live on today's weekday. Otherwise the
    PTA row is an orphan and the orphan filter added in V4 removes
    it, so api_create_substitute returns
    "Period 1 on <Day> does not exist in this class's timetable"
    instead of exercising the substitute branch.
    """
    today = date.today()
    today_dow = today.weekday()
    cls = _make_class_with_timetable(
        base, 'Grade 5', 'A', periods=8, day_of_week=today_dow,
    )
    teacher = base._make_teacher('Absent', 'Teacher')
    subj = base._make_subject('Math')
    base._assign_subject(cls, subj, teacher)

    with schema_context(base.tenant.schema_name):
        LeaveRequest.objects.create(
            staff=teacher, leave_type='casual', title='t', reason='r',
            start_date=today, end_date=today,
            total_days=1, status='approved',
        )
        PeriodTeacherAssignment.objects.create(
            school_class=cls, day_of_week=today_dow, period_order=1,
            subject=subj, teacher=teacher,
        )
    return cls, teacher, subj, today_dow


# --------------------------------------------------------------- test classes

class SubstituteFixtureFunctionalTests(AssignTeachersTestBase):
    """api_create_substitute functional paths (V3 only tested CSRF)."""

    def test_create_substitute_happy_path(self):
        cls, absent, subj, dow = _seed_absent_teacher_with_leave(self)
        sub_teacher = self._make_teacher('Free', 'Teacher')
        response = self._post_json(
            'api/timetable/substitute/create/',
            {
                'absent_teacher_id': absent.id,
                'substitute_teacher_id': sub_teacher.id,
                'class_id': cls.id,
                'subject_id': subj.id,
                'period_order': 1,
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['success'])
        with schema_context(self.tenant.schema_name):
            self.assertTrue(
                SubstituteAssignment.objects.filter(
                    substitute_teacher=sub_teacher,
                    absent_teacher=absent,
                ).exists()
            )

    def test_substitute_cannot_equal_absent(self):
        cls, absent, subj, dow = _seed_absent_teacher_with_leave(self)
        response = self._post_json(
            'api/timetable/substitute/create/',
            {
                'absent_teacher_id': absent.id,
                'substitute_teacher_id': absent.id,
                'class_id': cls.id,
                'subject_id': subj.id,
                'period_order': 1,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_substitute_on_leave_is_refused(self):
        cls, absent, subj, dow = _seed_absent_teacher_with_leave(self)
        other = self._make_teacher('Also', 'OnLeave')
        today = date.today()
        with schema_context(self.tenant.schema_name):
            LeaveRequest.objects.create(
                staff=other, leave_type='casual', title='x', reason='y',
                start_date=today, end_date=today,
                total_days=1, status='approved',
            )
        response = self._post_json(
            'api/timetable/substitute/create/',
            {
                'absent_teacher_id': absent.id,
                'substitute_teacher_id': other.id,
                'class_id': cls.id,
                'subject_id': subj.id,
                'period_order': 1,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('leave', response.json()['error'].lower())

    def test_substitute_double_booking_is_refused(self):
        """Substitute already teaching another class at that period."""
        cls, absent, subj, dow = _seed_absent_teacher_with_leave(self)
        other_teacher = self._make_teacher('Busy', 'Teacher')
        other_cls = _make_class_with_timetable(
            self, 'Grade 6', 'B', periods=8,
        )
        with schema_context(self.tenant.schema_name):
            PeriodTeacherAssignment.objects.create(
                school_class=other_cls, day_of_week=dow, period_order=1,
                subject=subj, teacher=other_teacher,
            )
        response = self._post_json(
            'api/timetable/substitute/create/',
            {
                'absent_teacher_id': absent.id,
                'substitute_teacher_id': other_teacher.id,
                'class_id': cls.id,
                'subject_id': subj.id,
                'period_order': 1,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('already', response.json()['error'].lower())


class TodaysLeaveNonHolidayTests(AssignTeachersTestBase):
    """api_get_todays_leave non-holiday branch (V3 only tested CSRF)."""

    def test_no_leave_returns_empty_list(self):
        _make_class_with_timetable(self, 'Grade 7', 'A', periods=8)
        response = self.client.get(
            self.url('api/timetable/todays-leave/'),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body['success'])
        self.assertFalse(body.get('is_holiday', False))
        self.assertEqual(body['on_leave_count'], 0)
        self.assertEqual(body['assignments'], [])

    def test_orphan_pta_not_surfaced_as_absent_period(self):
        """A PTA row for a period that doesn't exist in the class's
        current timetable must be filtered out (BUG-4-adjacent).

        V4_1: the class's timetable is created on today's weekday
        so the orphan filter is what excludes the row — not a
        day-of-week mismatch between the timetable and the PTA.
        """
        today = date.today()
        today_dow = today.weekday()
        cls = _make_class_with_timetable(
            self, 'Grade 8', 'A', periods=4,
            day_of_week=today_dow,
        )
        teacher = self._make_teacher('OnLeave', 'Teacher')
        subj = self._make_subject('Bio')
        self._assign_subject(cls, subj, teacher)

        with schema_context(self.tenant.schema_name):
            LeaveRequest.objects.create(
                staff=teacher, leave_type='casual', title='t', reason='r',
                start_date=today, end_date=today,
                total_days=1, status='approved',
            )
            # Orphan: period 7 does not exist in a 4-period timetable.
            PeriodTeacherAssignment.objects.create(
                school_class=cls, day_of_week=today_dow, period_order=7,
                subject=subj, teacher=teacher,
            )
        response = self.client.get(
            self.url('api/timetable/todays-leave/'),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['assignments'], [])
        self.assertEqual(body['on_leave_count'], 0)


class OrphanBusyMapTests(AssignTeachersTestBase):
    """BUG-4: orphan PTA rows must not appear in the GET busy map."""

    def test_orphan_pta_filtered_from_get_busy_map(self):
        class_a = _make_class_with_timetable(self, 'Grade 1', 'A', periods=8)
        class_b = _make_class_with_timetable(self, 'Grade 2', 'B', periods=4)

        teacher = self._make_teacher('Shared', 'Teacher')
        subj = self._make_subject('Math')
        with schema_context(self.tenant.schema_name):
            ClassSubject.objects.create(
                school_class=class_a, subject=subj,
                teacher=teacher, is_active=True,
            )
            # Orphan: period 8 does not exist in Class B (4 periods).
            PeriodTeacherAssignment.objects.create(
                school_class=class_b, day_of_week=0, period_order=8,
                subject=subj, teacher=teacher,
            )

        response = self.client.get(
            self.url(f'api/timetable/teacher-assignments/{class_a.id}/'),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200, response.content)
        busy = response.json().get('teacher_busy', {})
        self.assertNotIn(f'{teacher.id}|0|8', busy)


class OptimisticLockEnforcementTests(AssignTeachersTestBase):
    """BUG-7: missing token must be refused when rows exist."""

    def test_missing_token_refused_when_rows_exist(self):
        cls = _make_class_with_timetable(self, 'Grade 9', 'A', periods=8)
        teacher = self._make_teacher('Lock', 'Test')
        subj = self._make_subject('Chem')
        self._assign_subject(cls, subj, teacher)

        with schema_context(self.tenant.schema_name):
            PeriodTeacherAssignment.objects.create(
                school_class=cls, day_of_week=0, period_order=1,
                subject=subj, teacher=teacher,
            )

        # No timetable_updated_at in the payload.
        r = self._post_json(
            f'api/timetable/teacher-assignments/{cls.id}/save/',
            {'assignments': [{'day': 0, 'order': 2, 'subject_id': subj.id}]},
        )
        self.assertEqual(r.status_code, 409, r.content)

    def test_empty_save_refused_when_rows_exist(self):
        cls = _make_class_with_timetable(self, 'Grade 10', 'A', periods=8)
        teacher = self._make_teacher('Empty', 'Test')
        subj = self._make_subject('Phys')
        self._assign_subject(cls, subj, teacher)

        with schema_context(self.tenant.schema_name):
            PeriodTeacherAssignment.objects.create(
                school_class=cls, day_of_week=0, period_order=1,
                subject=subj, teacher=teacher,
            )

        r = self._post_json(
            f'api/timetable/teacher-assignments/{cls.id}/save/',
            {'assignments': []},
        )
        self.assertEqual(r.status_code, 400, r.content)


class ReconcileUnassignedClassTests(AssignTeachersTestBase):
    """BUG-5: reconcile must clean rows for classes with no timetable."""

    def test_orphan_pta_removed_when_class_has_no_timetable(self):
        cls = self._make_class('Grade 11', 'A')
        teacher = self._make_teacher('Recon', 'V4')
        subj = self._make_subject('Hist')
        self._assign_subject(cls, subj, teacher)

        with schema_context(self.tenant.schema_name):
            row = PeriodTeacherAssignment.objects.create(
                school_class=cls, day_of_week=0, period_order=1,
                subject=subj, teacher=teacher,
            )
            row_id = row.id

        from axis_saas.views.assign_teachers import (
            _reconcile_period_teacher_assignments,
        )
        _reconcile_period_teacher_assignments(self.tenant.schema_name)

        with schema_context(self.tenant.schema_name):
            self.assertFalse(
                PeriodTeacherAssignment.objects.filter(id=row_id).exists()
            )


class SequentialConflictTests(AssignTeachersTestBase):
    """Two saves that would put the same teacher in the same slot of
    two classes: the second must observe the conflict."""

    def test_second_save_observes_conflict(self):
        class_a = _make_class_with_timetable(self, 'Grade 3', 'A', periods=8)
        class_b = _make_class_with_timetable(self, 'Grade 4', 'B', periods=8)
        teacher = self._make_teacher('Solo', 'Teacher')
        subj = self._make_subject('Sci')

        with schema_context(self.tenant.schema_name):
            ClassSubject.objects.create(
                school_class=class_a, subject=subj,
                teacher=teacher, is_active=True,
            )
            ClassSubject.objects.create(
                school_class=class_b, subject=subj,
                teacher=teacher, is_active=True,
            )

        r1 = self._post_json(
            f'api/timetable/teacher-assignments/{class_a.id}/save/',
            {'assignments': [{'day': 0, 'order': 1, 'subject_id': subj.id}]},
        )
        self.assertEqual(r1.status_code, 200, r1.content)

        r2 = self._post_json(
            f'api/timetable/teacher-assignments/{class_b.id}/save/',
            {'assignments': [{'day': 0, 'order': 1, 'subject_id': subj.id}]},
        )
        self.assertEqual(r2.status_code, 400, r2.content)
        self.assertIn('conflict', r2.json()['error'].lower())
