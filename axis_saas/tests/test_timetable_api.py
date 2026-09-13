"""Timetable API behaviour tests (TIMETABLE_HARDENING_V1_PHASE2).

The first timetable test file (``test_timetable.py``) covers DB-level
constraints only: case-insensitive label uniqueness, ScheduleLabel.name
uniqueness, PeriodsTimetable defaults. Five tests, roughly 5% of the
actual behaviour.

This file covers the actual view and API behaviour that was untested:

  * ``api_save_day_schedules``
      - create + update + delete in a single POST
      - empty-payload guard (default refuses; ``allow_empty=True`` allows)
      - optimistic-lock conflict -> HTTP 409
      - duplicate (label, day) inside one payload -> HTTP 400
  * ``api_add_bunch``
      - break that swallows the whole class window -> HTTP 400
      - stale slot mismatch vs the current calendar -> HTTP 400
  * ``api_update_label``
      - rename cascades to DaySchedule and PeriodsTimetable
      - rename collision on the same (calendar, day) -> HTTP 400
  * ``_reconcile_timetables``
      - orphaned timetable auto-deleted
      - delete notification created
  * ``api_batch_update_label_times``
      - multi-day timing update + duration recompute
      - unknown label -> HTTP 404
  * ``ClassTimetableAssignment``
      - OneToOne constraint on ``school_class``
      - cascade delete when ``PeriodsTimetable`` is deleted

Run:

    python manage.py test axis_saas.tests.test_timetable_api

If the test database cannot be created, grant ``CREATEDB`` to the role
in ``DATABASE_URL`` (a different PostgreSQL role from your OS user):

    sudo -u postgres psql -c "ALTER ROLE fee_user CREATEDB;"
"""

import json

from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django_tenants.utils import schema_context

from axis_saas.models import (
    AcademicCalendar,
    ClassTimetableAssignment,
    DaySchedule,
    Notification,
    PeriodsTimetable,
    ScheduleLabel,
    SchoolClass,
    SchoolClient,
)


# =====================================================================
# Base class — one tenant, one authenticated admin session
# =====================================================================
class TimetableAPITestBase(TestCase):

    def setUp(self):
        self.tenant = SchoolClient.objects.create(
            schema_name="tt-api-test",
            name="TT API Test School",
            admin_username="admin",
            admin_password="admin123",
            enabled_features=[
                "timetable_management",
                "classes_management",
                "dashboard",
            ],
        )
        self.client = Client()
        session = self.client.session
        session["school_admin_authenticated"] = True
        session["school_admin_schema"] = self.tenant.schema_name
        session["school_admin_username"] = "admin"
        session.save()

    # --- helpers --------------------------------------------------

    def url(self, path):
        return f"/portal/{self.tenant.schema_name}/{path.lstrip('/')}"

    def _ensure_calendar(self):
        with schema_context(self.tenant.schema_name):
            cal, _ = AcademicCalendar.objects.get_or_create(pk=1)
            return cal

    def _post_json(self, path, payload):
        return self.client.post(
            self.url(path),
            data=json.dumps(payload),
            content_type="application/json",
        )


# =====================================================================
# api_save_day_schedules
# =====================================================================
class SaveDaySchedulesTests(TimetableAPITestBase):

    def test_create_update_delete_in_one_call(self):
        """One POST creates Wed, updates Tue, deletes Mon."""
        cal = self._ensure_calendar()
        with schema_context(self.tenant.schema_name):
            _senior = ScheduleLabel.objects.create(name="Senior")
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label=_senior, start_time="08:00", end_time="14:00",
                periods=8,
            )
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=1, order=1,
                label=_senior, start_time="08:00", end_time="14:00",
                periods=8,
            )

        payload = {
            "schedules": [
                {"day": 1, "label": "Senior",
                 "start": "08:30", "end": "14:00", "periods": 8},
                {"day": 2, "label": "Senior",
                 "start": "08:00", "end": "14:00", "periods": 8},
            ],
        }
        response = self._post_json("api/timetable/day-schedules/", payload)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["created"], 1)
        self.assertEqual(body["updated"], 1)
        self.assertEqual(body["deleted"], 1)

        with schema_context(self.tenant.schema_name):
            self.assertEqual(DaySchedule.objects.count(), 2)
            tue = DaySchedule.objects.get(day_of_week=1)
            self.assertEqual(tue.start_time.strftime("%H:%M"), "08:30")
            self.assertTrue(DaySchedule.objects.filter(day_of_week=2).exists())
            self.assertFalse(DaySchedule.objects.filter(day_of_week=0).exists())

    def test_empty_payload_refused_by_default(self):
        """Empty schedules payload with existing rows -> HTTP 400."""
        cal = self._ensure_calendar()
        with schema_context(self.tenant.schema_name):
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label="Senior", start_time="08:00", end_time="14:00",
                periods=8,
            )

        response = self._post_json(
            "api/timetable/day-schedules/", {"schedules": []}
        )
        self.assertEqual(response.status_code, 400, response.content)
        with schema_context(self.tenant.schema_name):
            self.assertEqual(DaySchedule.objects.count(), 1)

    def test_empty_payload_allowed_with_explicit_flag(self):
        """allow_empty=True lets the client deliberately wipe."""
        cal = self._ensure_calendar()
        with schema_context(self.tenant.schema_name):
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label="Senior", start_time="08:00", end_time="14:00",
                periods=8,
            )

        response = self._post_json(
            "api/timetable/day-schedules/",
            {"schedules": [], "allow_empty": True},
        )
        self.assertEqual(response.status_code, 200, response.content)
        with schema_context(self.tenant.schema_name):
            self.assertEqual(DaySchedule.objects.count(), 0)

    def test_optimistic_lock_conflict_returns_409(self):
        """A stale client_updated_at aborts the entire save."""
        cal = self._ensure_calendar()
        with schema_context(self.tenant.schema_name):
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label="Senior", start_time="08:00", end_time="14:00",
                periods=8,
            )

        payload = {
            "schedules": [
                {
                    "day": 0, "label": "Senior",
                    "start": "08:00", "end": "14:00", "periods": 8,
                    "client_updated_at": "2000-01-01T00:00:00+00:00",
                },
            ],
        }
        response = self._post_json("api/timetable/day-schedules/", payload)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("conflicts", response.json())

    def test_duplicate_label_same_day_in_one_payload_rejected(self):
        """Two rows with the same label on the same day -> HTTP 400."""
        self._ensure_calendar()
        payload = {
            "schedules": [
                {"day": 0, "label": "Senior",
                 "start": "08:00", "end": "14:00", "periods": 8},
                {"day": 0, "label": "Senior",
                 "start": "09:00", "end": "15:00", "periods": 8},
            ],
        }
        response = self._post_json("api/timetable/day-schedules/", payload)
        self.assertEqual(response.status_code, 400, response.content)


# =====================================================================
# api_add_bunch  (periods timetable generator)
# =====================================================================
class AddBunchTests(TimetableAPITestBase):

    def test_break_swallows_entire_day_returns_400(self):
        """A break that eats the whole class window -> HTTP 400."""
        payload = {
            "title": "Bad TT",
            "label": "Senior",
            "break_duration": 60,
            "days": [
                {"day": 0, "start": "08:00", "end": "08:30",
                 "periods": 8, "break_after": 4},
            ],
        }
        response = self._post_json(
            "api/timetable/periods/bunch/add/", payload
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("too", response.json()["error"].lower())

    def test_slot_mismatch_returns_400(self):
        """Day whose timing does not match the calendar -> HTTP 400."""
        cal = self._ensure_calendar()
        with schema_context(self.tenant.schema_name):
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label="Senior", start_time="08:00", end_time="14:00",
                periods=8,
            )
        payload = {
            "title": "Mismatch TT",
            "label": "Senior",
            "break_duration": 0,
            "days": [
                {"day": 0, "start": "08:00", "end": "13:00",
                 "periods": 8, "break_after": None},
            ],
        }
        response = self._post_json(
            "api/timetable/periods/bunch/add/", payload
        )
        self.assertEqual(response.status_code, 400, response.content)


# =====================================================================
# api_update_label  (rename cascade)
# =====================================================================
class UpdateLabelTests(TimetableAPITestBase):

    def test_rename_cascades_to_dayschedule_and_timetable(self):
        cal = self._ensure_calendar()
        with schema_context(self.tenant.schema_name):
            label = ScheduleLabel.objects.create(name="Senior")
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label="Senior", start_time="08:00", end_time="14:00",
                periods=8,
            )
            PeriodsTimetable.objects.create(
                title="Senior TT", label="Senior",
                break_duration=0, days=[],
            )
            label_id = label.id

        response = self._post_json(
            "api/timetable/labels/update/",
            {"id": label_id, "name": "Seniors", "description": ""},
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["cascaded"]["day_schedules_updated"], 1)
        self.assertEqual(body["cascaded"]["timetables_updated"], 1)

        with schema_context(self.tenant.schema_name):
            self.assertEqual(DaySchedule.objects.get().label, "Seniors")
            self.assertEqual(PeriodsTimetable.objects.get().label, "Seniors")
            self.assertEqual(ScheduleLabel.objects.get().name, "Seniors")

    def test_rename_collision_blocked(self):
        """Senior -> Junior when Junior is on the same day -> HTTP 400."""
        cal = self._ensure_calendar()
        with schema_context(self.tenant.schema_name):
            label = ScheduleLabel.objects.create(name="Senior")
            ScheduleLabel.objects.create(name="Junior")
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label="Senior", start_time="08:00", end_time="14:00",
                periods=8,
            )
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=1,
                label="Junior", start_time="08:00", end_time="14:00",
                periods=8,
            )
            label_id = label.id

        response = self._post_json(
            "api/timetable/labels/update/",
            {"id": label_id, "name": "Junior", "description": ""},
        )
        self.assertEqual(response.status_code, 400, response.content)


# =====================================================================
# _reconcile_timetables
# =====================================================================
class ReconcileTests(TimetableAPITestBase):

    def test_orphaned_timetable_deleted_and_notification_created(self):
        cal = self._ensure_calendar()
        with schema_context(self.tenant.schema_name):
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label="Senior", start_time="08:00", end_time="14:00",
                periods=8,
            )
            tt = PeriodsTimetable.objects.create(
                title="Senior TT", label="Senior",
                break_duration=0,
                days=[{
                    "day_of_week": 0,
                    "day_label": "Monday",
                    "start": "08:00",
                    "end": "14:00",
                    "periods_count": 8,
                    "break_after": None,
                    "break_duration": 0,
                    "periods": [
                        {"order": i, "start": "08:00", "end": "08:45",
                         "duration": 45, "is_break": False}
                        for i in range(1, 9)
                    ],
                }],
            )
            tt_id = tt.id

            # Remove the calendar slot the timetable depends on.
            DaySchedule.objects.all().delete()

        # Trigger reconcile directly: signals use on_commit, which does
        # not fire inside a plain TestCase transaction.
        from axis_saas.views.periods import _reconcile_timetables
        _reconcile_timetables(self.tenant.schema_name)

        with schema_context(self.tenant.schema_name):
            self.assertFalse(
                PeriodsTimetable.objects.filter(id=tt_id).exists(),
                "Orphaned timetable should have been deleted",
            )
            self.assertTrue(
                Notification.objects.exists(),
                "Reconcile should have created a Notification",
            )


# =====================================================================
# api_batch_update_label_times
# =====================================================================
class BatchUpdateLabelTimesTests(TimetableAPITestBase):

    def test_batch_update_recomputes_duration(self):
        cal = self._ensure_calendar()
        with schema_context(self.tenant.schema_name):
            ScheduleLabel.objects.create(name="Senior")
            for day in (0, 1, 2):
                DaySchedule.objects.create(
                    academic_calendar=cal, day_of_week=day, order=day,
                    label="Senior", start_time="08:00", end_time="14:00",
                    periods=8, duration=45,
                )

        payload = {
            "label": "Senior",
            "updates": [
                {"day_of_week": 0, "start": "09:00", "end": "15:00"},
                {"day_of_week": 1, "start": "09:00", "end": "15:00"},
            ],
        }
        response = self._post_json(
            "api/timetable/day-schedules/batch-update/", payload
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["updated"], 2)

        with schema_context(self.tenant.schema_name):
            mon = DaySchedule.objects.get(day_of_week=0)
            tue = DaySchedule.objects.get(day_of_week=1)
            wed = DaySchedule.objects.get(day_of_week=2)
            self.assertEqual(mon.start_time.strftime("%H:%M"), "09:00")
            self.assertEqual(mon.end_time.strftime("%H:%M"), "15:00")
            # 6 hours = 360 minutes / 8 periods = 45 min per period.
            self.assertEqual(mon.duration, 45)
            self.assertEqual(tue.start_time.strftime("%H:%M"), "09:00")
            # Wednesday was not in the updates list -> unchanged.
            self.assertEqual(wed.start_time.strftime("%H:%M"), "08:00")

    def test_unknown_label_returns_404(self):
        self._ensure_calendar()
        payload = {
            "label": "DoesNotExist",
            "updates": [
                {"day_of_week": 0, "start": "09:00", "end": "15:00"},
            ],
        }
        response = self._post_json(
            "api/timetable/day-schedules/batch-update/", payload
        )
        self.assertEqual(response.status_code, 404, response.content)


# =====================================================================
# ClassTimetableAssignment  (OneToOne + cascade)
# =====================================================================
class TimetableAssignmentModelTests(TimetableAPITestBase):

    def _mk_class(self, name="Grade 1", section="A"):
        with schema_context(self.tenant.schema_name):
            return SchoolClass.objects.create(name=name, section=section)

    def _mk_tt(self, title):
        with schema_context(self.tenant.schema_name):
            return PeriodsTimetable.objects.create(
                title=title, label="Senior", break_duration=0, days=[],
            )

    def test_one_to_one_constraint_on_school_class(self):
        cls = self._mk_class()
        tt_a = self._mk_tt("A")
        tt_b = self._mk_tt("B")

        with schema_context(self.tenant.schema_name):
            ClassTimetableAssignment.objects.create(
                school_class=cls, timetable=tt_a,
            )
            # Wrap the failing insert in its own atomic block so the
            # outer TestCase transaction stays usable after the error.
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    ClassTimetableAssignment.objects.create(
                        school_class=cls, timetable=tt_b,
                    )

    def test_timetable_delete_cascades_to_assignment(self):
        cls = self._mk_class()
        tt = self._mk_tt("A")
        with schema_context(self.tenant.schema_name):
            assignment = ClassTimetableAssignment.objects.create(
                school_class=cls, timetable=tt,
            )
            assignment_id = assignment.id
            tt.delete()
            self.assertFalse(
                ClassTimetableAssignment.objects.filter(
                    id=assignment_id
                ).exists()
            )
