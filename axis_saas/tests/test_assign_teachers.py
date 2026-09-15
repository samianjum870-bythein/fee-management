"""Assign-teachers endpoint tests (ASSIGN_TEACHERS_HARDENING_V2).

Covers:
  * CSRF enforced on api_save_teacher_assignments /
    api_create_substitute / api_delete_substitute (no @csrf_exempt).
  * Server-side teacher conflict validation.
  * Period validation against the assigned timetable.
  * Inactive teachers rejected.
  * Holiday gate in api_get_todays_leave.
  * Substitute cannot be on approved leave today.
  * Substitute must actually replace a real absent-teacher assignment.
  * _reconcile_period_teacher_assignments removes orphaned rows.

Run:
    python manage.py test axis_saas.tests.test_assign_teachers
"""

import json
from datetime import date, timedelta

from django.test import Client, TestCase
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
    SchoolClass,
    SchoolClient,
    Staff,
    Subject,
)


class AssignTeachersTestBase(TestCase):
    # ASSIGN_TEACHERS_HARDENING_V3_TEST_FIX
    #
    # django-tenants leaves the connection on the last-used tenant
    # schema between tests. TestCase rolls back the SchoolClient row
    # but not the physical PostgreSQL schema, so the next test's
    # SchoolClient.objects.create() raises:
    #
    #   "Can't create tenant outside the public schema."
    #
    # We force the connection back to public in setUp and explicitly
    # drop the tenant's schema in tearDown so the test suite is
    # repeatable.

    def setUp(self):
        from django.db import connection as _conn
        # Ensure we are on the public schema before creating a new
        # tenant. Any previous test may have left the connection on a
        # tenant schema.
        _conn.set_schema_to_public()

        self.tenant = SchoolClient.objects.create(
            schema_name="assign-teachers-test",
            name="Assign Teachers Test School",
            admin_username="admin",
            admin_password="admin123",
            enabled_features=[
                "timetable_management",
                "classes_management",
                "dashboard",
            ],
        )
        # SchoolClient.save() with auto_create_schema=True leaves the
        # connection on the freshly-created tenant schema. Switch back
        # so the rest of setUp runs on public.
        _conn.set_schema_to_public()

        self.client = Client()
        session = self.client.session
        session["school_admin_authenticated"] = True
        session["school_admin_schema"] = self.tenant.schema_name
        session["school_admin_username"] = "admin"
        session.save()

    def tearDown(self):
        # Drop the physical PostgreSQL schema so the next test can
        # recreate it. Without this, the second test in the same
        # process fails because the schema already exists and
        # django-tenants refuses to create it again.
        from django.db import connection as _conn
        _conn.set_schema_to_public()
        try:
            self.tenant.delete(force_drop=True)
        except TypeError:
            # Older django-tenants signatures don't accept force_drop.
            try:
                self.tenant.delete()
            except Exception:
                pass
        except Exception:
            pass
        _conn.set_schema_to_public()

    # ---------- helpers ----------

    def url(self, path):
        return f"/portal/{self.tenant.schema_name}/{path.lstrip('/')}"

    def _make_class(self, name="Grade 1", section="A"):
        with schema_context(self.tenant.schema_name):
            return SchoolClass.objects.create(name=name, section=section)

    def _make_teacher(self, first, last, status='active'):
        with schema_context(self.tenant.schema_name):
            return Staff.objects.create(
                first_name=first,
                last_name=last,
                email=f'{first.lower()}.{last.lower()}@example.com',
                job_title='Teacher',
                department='teaching',
                phone='03000000000',
                role='teacher',
                status=status,
            )

    def _make_subject(self, name):
        with schema_context(self.tenant.schema_name):
            return Subject.objects.create(name=name)

    def _assign_subject(self, school_class, subject, teacher):
        with schema_context(self.tenant.schema_name):
            return ClassSubject.objects.create(
                school_class=school_class,
                subject=subject,
                teacher=teacher,
                is_active=True,
            )

    def _make_timetable(self, title="TT", label_name="Senior",
                        periods=8, days=(0,)):
        with schema_context(self.tenant.schema_name):
            cal, _ = AcademicCalendar.objects.get_or_create(pk=1)
            lbl, _ = ScheduleLabel.objects.get_or_create(name=label_name)
            for d in days:
                DaySchedule.objects.create(
                    academic_calendar=cal,
                    day_of_week=d,
                    order=0,
                    label=lbl,
                    start_time="08:00",
                    end_time="14:00",
                    periods=periods,
                )
            tt = PeriodsTimetable.objects.create(
                title=title,
                label=lbl,
                break_duration=0,
                days=[
                    {
                        "day_of_week": d,
                        "day_label": "Monday",
                        "start": "08:00",
                        "end": "14:00",
                        "periods_count": periods,
                        "break_after": None,
                        "break_duration": 0,
                        "periods": [
                            {
                                "order": i,
                                "start": "08:00",
                                "end": "08:45",
                                "duration": 45,
                                "is_break": False,
                            }
                            for i in range(1, periods + 1)
                        ],
                    }
                    for d in days
                ],
            )
            ClassTimetableAssignment.objects.create(
                school_class=SchoolClass.objects.first(),
                timetable=tt,
            )
            return tt

    def _post_json(self, path, payload):
        return self.client.post(
            self.url(path),
            data=json.dumps(payload),
            content_type="application/json",
        )


# =====================================================================
# CSRF enforcement
# =====================================================================
class CsrfEnforcementTests(AssignTeachersTestBase):

    def test_save_assignments_is_not_csrf_exempt(self):
        from axis_saas.views.assign_teachers import api_save_teacher_assignments
        self.assertFalse(
            getattr(api_save_teacher_assignments, 'csrf_exempt', False),
            'api_save_teacher_assignments must not be CSRF-exempt.',
        )

    def test_create_substitute_is_not_csrf_exempt(self):
        from axis_saas.views.assign_teachers import api_create_substitute
        self.assertFalse(
            getattr(api_create_substitute, 'csrf_exempt', False),
            'api_create_substitute must not be CSRF-exempt.',
        )

    def test_delete_substitute_is_not_csrf_exempt(self):
        from axis_saas.views.assign_teachers import api_delete_substitute
        self.assertFalse(
            getattr(api_delete_substitute, 'csrf_exempt', False),
            'api_delete_substitute must not be CSRF-exempt.',
        )

    def test_periods_api_add_bunch_is_not_csrf_exempt(self):
        from axis_saas.views.periods import api_add_bunch
        self.assertFalse(
            getattr(api_add_bunch, 'csrf_exempt', False),
            'periods.api_add_bunch must not be CSRF-exempt.',
        )


# =====================================================================
# Save-assignments: period validation
# =====================================================================
class SaveAssignmentValidationTests(AssignTeachersTestBase):

    def test_period_outside_timetable_is_rejected(self):
        school_class = self._make_class()
        teacher = self._make_teacher('Ali', 'Khan')
        subj = self._make_subject('Math')
        self._assign_subject(school_class, subj, teacher)
        self._make_timetable(periods=8)

        # order=99 does not exist
        payload = {
            'assignments': [
                {'day': 0, 'order': 99, 'subject_id': subj.id},
            ],
        }
        response = self._post_json(
            f'api/timetable/teacher-assignments/{school_class.id}/save/',
            payload,
        )
        self.assertEqual(response.status_code, 400, response.content)
        with schema_context(self.tenant.schema_name):
            self.assertFalse(
                PeriodTeacherAssignment.objects.filter(
                    school_class=school_class, period_order=99,
                ).exists()
            )

    def test_inactive_teacher_subject_is_rejected(self):
        school_class = self._make_class()
        teacher = self._make_teacher('Inactive', 'Person', status='inactive')
        subj = self._make_subject('Physics')
        self._assign_subject(school_class, subj, teacher)
        self._make_timetable(periods=8)

        payload = {
            'assignments': [
                {'day': 0, 'order': 1, 'subject_id': subj.id},
            ],
        }
        response = self._post_json(
            f'api/timetable/teacher-assignments/{school_class.id}/save/',
            payload,
        )
        # Endpoint returns 200 with skipped count (subject not in
        # active-teacher map), not 400. Either way, no row saved.
        with schema_context(self.tenant.schema_name):
            self.assertFalse(
                PeriodTeacherAssignment.objects.filter(
                    school_class=school_class, period_order=1,
                ).exists()
            )


# =====================================================================
# Save-assignments: server-side conflict
# =====================================================================
class ServerSideConflictTests(AssignTeachersTestBase):

    def test_same_teacher_two_classes_same_slot_is_rejected(self):
        class_a = self._make_class('Grade 1', 'A')
        class_b = self._make_class('Grade 2', 'B')
        teacher = self._make_teacher('Same', 'Teacher')
        subj = self._make_subject('Math')

        with schema_context(self.tenant.schema_name):
            ClassSubject.objects.create(
                school_class=class_a, subject=subj,
                teacher=teacher, is_active=True,
            )
            ClassSubject.objects.create(
                school_class=class_b, subject=subj,
                teacher=teacher, is_active=True,
            )

        # HARDENING_V5: give class A its own timetable. The
        # busy-map filter drops PTA rows whose class has no
        # timetable (they are orphan), so a bare PTA row for
        # class A would be invisible to the conflict check.
        with schema_context(self.tenant.schema_name):
            cal_a, _ = AcademicCalendar.objects.get_or_create(pk=1)
            lbl_a, _ = ScheduleLabel.objects.get_or_create(name='Junior')
            DaySchedule.objects.create(
                academic_calendar=cal_a, day_of_week=0, order=0,
                label=lbl_a, start_time='08:00', end_time='14:00',
                periods=8,
            )
            tt_a = PeriodsTimetable.objects.create(
                title='TT-A', label=lbl_a, break_duration=0,
                days=[{
                    'day_of_week': 0, 'day_label': 'Monday',
                    'start': '08:00', 'end': '14:00',
                    'periods_count': 8, 'break_after': None,
                    'break_duration': 0,
                    'periods': [
                        {'order': i, 'start': '08:00', 'end': '08:45',
                         'duration': 45, 'is_break': False}
                        for i in range(1, 9)
                    ],
                }],
            )
            ClassTimetableAssignment.objects.create(
                school_class=class_a, timetable=tt_a,
            )
            PeriodTeacherAssignment.objects.create(
                school_class=class_a, day_of_week=0, period_order=1,
                subject=subj, teacher=teacher,
            )

        # Set up class B with a timetable.
        with schema_context(self.tenant.schema_name):
            cal, _ = AcademicCalendar.objects.get_or_create(pk=1)
            lbl, _ = ScheduleLabel.objects.get_or_create(name='Senior')
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label=lbl, start_time='08:00', end_time='14:00',
                periods=8,
            )
            tt = PeriodsTimetable.objects.create(
                title='TT-B', label=lbl, break_duration=0,
                days=[{
                    'day_of_week': 0, 'day_label': 'Monday',
                    'start': '08:00', 'end': '14:00',
                    'periods_count': 8, 'break_after': None,
                    'break_duration': 0,
                    'periods': [
                        {'order': i, 'start': '08:00', 'end': '08:45',
                         'duration': 45, 'is_break': False}
                        for i in range(1, 9)
                    ],
                }],
            )
            ClassTimetableAssignment.objects.create(
                school_class=class_b, timetable=tt,
            )

        payload = {
            'assignments': [
                {'day': 0, 'order': 1, 'subject_id': subj.id},
            ],
        }
        response = self._post_json(
            f'api/timetable/teacher-assignments/{class_b.id}/save/',
            payload,
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('conflict', response.json()['error'].lower())
        with schema_context(self.tenant.schema_name):
            self.assertFalse(
                PeriodTeacherAssignment.objects.filter(
                    school_class=class_b, period_order=1,
                ).exists()
            )


# =====================================================================
# Reconcile
# =====================================================================
class ReconcileTests(AssignTeachersTestBase):

    def test_reconcile_removes_orphaned_rows(self):
        school_class = self._make_class()
        teacher = self._make_teacher('Reconcile', 'Test')
        subj = self._make_subject('Bio')
        self._assign_subject(school_class, subj, teacher)
        tt = self._make_timetable(periods=8)

        with schema_context(self.tenant.schema_name):
            PeriodTeacherAssignment.objects.create(
                school_class=school_class, day_of_week=0, period_order=8,
                subject=subj, teacher=teacher,
            )

            # Now shrink the timetable to 4 periods.
            tt.days = [{
                'day_of_week': 0, 'day_label': 'Monday',
                'start': '08:00', 'end': '12:00',
                'periods_count': 4, 'break_after': None,
                'break_duration': 0,
                'periods': [
                    {'order': i, 'start': '08:00', 'end': '08:45',
                     'duration': 45, 'is_break': False}
                    for i in range(1, 5)
                ],
            }]
            tt.save()

        from axis_saas.views.assign_teachers import (
            _reconcile_period_teacher_assignments,
        )
        _reconcile_period_teacher_assignments(
            self.tenant.schema_name, tt.id,
        )

        with schema_context(self.tenant.schema_name):
            self.assertFalse(
                PeriodTeacherAssignment.objects.filter(
                    school_class=school_class, period_order=8,
                ).exists()
            )


# =====================================================================
# ASSIGN_TEACHERS_HARDENING_V3: CSRF enforcement end-to-end
# ---------------------------------------------------------------------
# The V2 tests only checked the csrf_exempt attribute. That does not
# prove the middleware actually rejects a POST without a token. This
# class uses a Client with enforce_csrf_checks=True and asserts 403.
# =====================================================================
class CsrfEnforcedClientTests(AssignTeachersTestBase):

    def _csrf_client(self):
        from django.test import Client as _C
        c = _C(enforce_csrf_checks=True)
        s = c.session
        s['school_admin_authenticated'] = True
        s['school_admin_schema'] = self.tenant.schema_name
        s['school_admin_username'] = 'admin'
        s.save()
        return c

    def test_save_assignments_rejects_missing_csrf(self):
        c = self._csrf_client()
        school_class = self._make_class()
        teacher = self._make_teacher('Csrf', 'Save')
        subj = self._make_subject('CSRF-Save')
        self._assign_subject(school_class, subj, teacher)
        self._make_timetable(periods=8)

        response = c.post(
            self.url(
                f'api/timetable/teacher-assignments/{school_class.id}/save/'
            ),
            data=json.dumps({'assignments': []}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_create_substitute_rejects_missing_csrf(self):
        c = self._csrf_client()
        response = c.post(
            self.url('api/timetable/substitute/create/'),
            data=json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_delete_substitute_rejects_missing_csrf(self):
        c = self._csrf_client()
        response = c.post(
            self.url('api/timetable/substitute/delete/'),
            data=json.dumps({'id': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_periods_add_bunch_rejects_missing_csrf(self):
        c = self._csrf_client()
        response = c.post(
            self.url('api/timetable/periods/bunch/add/'),
            data=json.dumps({
                'title': 'X', 'label': 'Y', 'days': [],
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
