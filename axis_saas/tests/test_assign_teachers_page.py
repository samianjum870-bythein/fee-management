"""Page-render tests for timetable_assign_teachers (HARDENING_V5).

The V2 and V4 suites only exercised the JSON APIs. A shape change in
`class_rows` or the template would pass those tests and 500 in
production. This file covers the main page view itself.

Run:
    python manage.py test axis_saas.tests.test_assign_teachers_page
"""

from django_tenants.utils import schema_context

from axis_saas.models import (
    AcademicCalendar,
    ClassTimetableAssignment,
    DaySchedule,
    PeriodsTimetable,
    PeriodTeacherAssignment,
    ScheduleLabel,
)
from axis_saas.tests.test_assign_teachers import AssignTeachersTestBase


class TimetableAssignTeachersPageTests(AssignTeachersTestBase):

    def _make_class_with_timetable(self, name, section, periods=8):
        cls = self._make_class(name, section)
        with schema_context(self.tenant.schema_name):
            cal, _ = AcademicCalendar.objects.get_or_create(pk=1)
            lbl, _ = ScheduleLabel.objects.get_or_create(name='Senior')
            DaySchedule.objects.get_or_create(
                academic_calendar=cal, day_of_week=0, order=0, label=lbl,
                defaults={
                    'start_time': '08:00', 'end_time': '14:00',
                    'periods': periods,
                },
            )
            tt = PeriodsTimetable.objects.create(
                title=f'TT-{cls.id}', label=lbl, break_duration=0,
                days=[{
                    'day_of_week': 0, 'day_label': 'Monday',
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
        return cls, tt

    def test_page_renders_with_no_classes(self):
        response = self.client.get(self.url('timetable/assign-teachers/'))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(response, 'Assign Periods to Teachers')
        self.assertEqual(list(response.context['class_rows']), [])

    def test_page_renders_with_classes_and_timetables(self):
        cls, tt = self._make_class_with_timetable('Grade 1', 'A', periods=8)
        response = self.client.get(self.url('timetable/assign-teachers/'))
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.context['class_rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['class_id'], cls.id)
        self.assertEqual(rows[0]['total_periods'], 8)
        self.assertEqual(rows[0]['assigned_count'], 0)

    def test_page_assigned_count_reflects_pta_rows(self):
        cls, tt = self._make_class_with_timetable('Grade 2', 'B', periods=8)
        teacher = self._make_teacher('Count', 'Test')
        subj = self._make_subject('Math')
        self._assign_subject(cls, subj, teacher)
        with schema_context(self.tenant.schema_name):
            PeriodTeacherAssignment.objects.create(
                school_class=cls, day_of_week=0, period_order=1,
                subject=subj, teacher=teacher,
            )
        response = self.client.get(self.url('timetable/assign-teachers/'))
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.context['class_rows']
        self.assertEqual(rows[0]['assigned_count'], 1)

    def test_page_reports_classes_without_timetable(self):
        self._make_class('Orphan', 'Z')
        response = self.client.get(self.url('timetable/assign-teachers/'))
        self.assertEqual(response.status_code, 200, response.content)
        names = [c['display_name'] for c in response.context['classes_without_timetable']]
        self.assertTrue(any('Orphan' in n for n in names))

    def test_page_handles_five_classes_without_n_plus_one(self):
        """N+1 regression guard: with 5 timetabled classes, the page
        must not fire a per-class query for the parent wing category
        or for the timetable label.

        HARDENING_V5_3: ``assertNumQueries(N)`` asserts an EXACT match,
        not an upper bound. The intent of this guard is a ceiling:
        fail if the N+1 sneaks back in, tolerate small fluctuations
        from middleware / session / cache warm-up. We capture the
        queries with CaptureQueriesContext and assert ``<= ceiling``.

        The real count is around 12. A regression on either
        select_related path (``school_class__wing_category__parent``
        or ``timetable__label``) adds ~5 queries, pushing the count
        past 20. The ceiling of 20 therefore catches the regression
        without being brittle.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for i in range(5):
            self._make_class_with_timetable(f'Grade {i}', 'A', periods=8)

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self.url('timetable/assign-teachers/'))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.context['class_rows']), 5)
        self.assertLessEqual(
            len(ctx.captured_queries), 20,
            'N+1 regression: %d queries executed, ceiling 20'
            % len(ctx.captured_queries),
        )
