"""Timetable API behaviour tests (TIMETABLE_HARDENING_V1_PHASE2).

The first timetable test file (``test_timetable.py``) covers DB-level
constraints only: case-insensitive label uniqueness, ScheduleLabel.name
uniqueness, PeriodsTimetable defaults. Five tests, roughly 5% of the
actual behaviour.

This file covers the actual view and API behaviour:

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
  * ``ClassTimetableAssignment`` (ASSIGN_MULTI_TIMETABLE_V1)
      - a class may hold MULTIPLE timetables
      - same timetable twice -> IntegrityError
      - cascade delete
      - GET /available-timetables/ filter by existing label
      - POST /assign/submit/ same-label enforcement + duplicate guard
      - assignment page renders with 2+ assignments

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
    """One tenant, one authenticated admin session.

    TIMETABLE_API_TESTS_FIX_V1
    --------------------------
    django-tenants TestCase rolls back the SchoolClient ROW but not
    the physical PostgreSQL schema, AND it does not reset
    ``connection.schema_name`` between test methods. The original
    setUp therefore left the connection on ``tt-api-test`` after the
    first test and every subsequent setUp failed with::

        Exception: Can't create tenant outside the public schema.
        Current schema is tt-api-test.

    We force public before create, force public after create, and
    explicitly drop the tenant schema in tearDown so the suite is
    repeatable.
    """

    def setUp(self):
        from django.db import connection as _conn
        _conn.set_schema_to_public()

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
        from django.db import connection as _conn
        _conn.set_schema_to_public()
        try:
            self.tenant.delete(force_drop=True)
        except TypeError:
            try:
                self.tenant.delete()
            except Exception:
                pass
        except Exception:
            pass
        _conn.set_schema_to_public()

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
            _lbl = ScheduleLabel.objects.create(name="Senior")
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label=_lbl, start_time="08:00", end_time="14:00",
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
            _lbl = ScheduleLabel.objects.create(name="Senior")
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label=_lbl, start_time="08:00", end_time="14:00",
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
            _lbl = ScheduleLabel.objects.create(name="Senior")
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label=_lbl, start_time="08:00", end_time="14:00",
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
        # api_save_day_schedules resolves every row's `label` via
        # ScheduleLabel.objects.get(name__iexact=...) BEFORE it runs
        # the duplicate check, so the label must exist.
        self._ensure_calendar()
        with schema_context(self.tenant.schema_name):
            ScheduleLabel.objects.create(name="Senior")

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
        # api_add_bunch resolves `label` via
        # ScheduleLabel.objects.filter(name__iexact=label_text).first()
        # and returns 400 "Label not found" if it doesn't exist. We
        # create it first so the request reaches the break-size check.
        with schema_context(self.tenant.schema_name):
            ScheduleLabel.objects.create(name="Senior")

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
            _lbl = ScheduleLabel.objects.create(name="Senior")
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label=_lbl, start_time="08:00", end_time="14:00",
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
                label=label, start_time="08:00", end_time="14:00",
                periods=8,
            )
            PeriodsTimetable.objects.create(
                title="Senior TT", label=label,
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
        # TIMETABLE_RECONCILE_SCHEMA_CONTEXT_FIX_V1:
        # TIMETABLE_FK_REFACTOR_V1 changed the shape of the
        # `cascaded` payload — `label` is now a ForeignKey, so
        # renaming a ScheduleLabel.name propagates to every
        # referencing DaySchedule / PeriodsTimetable automatically.
        # The endpoint therefore reports the literal string
        # `'auto (FK)'` instead of an integer count.
        self.assertEqual(
            body["cascaded"]["day_schedules_updated"], "auto (FK)",
        )
        self.assertEqual(
            body["cascaded"]["timetables_updated"], "auto (FK)",
        )

        with schema_context(self.tenant.schema_name):
            # label is now a FK, so compare .label.name not .label.
            self.assertEqual(DaySchedule.objects.get().label.name, "Seniors")
            self.assertEqual(PeriodsTimetable.objects.get().label.name, "Seniors")
            self.assertEqual(ScheduleLabel.objects.get().name, "Seniors")

    def test_rename_collision_blocked(self):
        """Senior -> Junior when Junior is on the same day -> HTTP 400."""
        cal = self._ensure_calendar()
        with schema_context(self.tenant.schema_name):
            label = ScheduleLabel.objects.create(name="Senior")
            junior_lbl = ScheduleLabel.objects.create(name="Junior")
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label=label, start_time="08:00", end_time="14:00",
                periods=8,
            )
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=1,
                label=junior_lbl, start_time="08:00", end_time="14:00",
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
            _lbl = ScheduleLabel.objects.create(name="Senior")
            DaySchedule.objects.create(
                academic_calendar=cal, day_of_week=0, order=0,
                label=_lbl, start_time="08:00", end_time="14:00",
                periods=8,
            )
            tt = PeriodsTimetable.objects.create(
                title="Senior TT", label=_lbl,
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
            _lbl = ScheduleLabel.objects.create(name="Senior")
            for day in (0, 1, 2):
                DaySchedule.objects.create(
                    academic_calendar=cal, day_of_week=day, order=day,
                    label=_lbl, start_time="08:00", end_time="14:00",
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
# ASSIGN_MULTI_TIMETABLE_V1_TESTS
# ---------------------------------------------------------------------
# A class may hold MANY timetables, all sharing the SAME ScheduleLabel.
# The exact same timetable cannot be assigned twice to the same class
# (enforced by a DB UniqueConstraint on (school_class, timetable)).
# =====================================================================


class TimetableAssignmentMultiTests(TimetableAPITestBase):
    """DB-level rules for the multi-assignment model."""

    def _mk_class(self, name="Grade 1", section="A"):
        with schema_context(self.tenant.schema_name):
            return SchoolClass.objects.create(name=name, section=section)

    def _mk_label(self, name):
        with schema_context(self.tenant.schema_name):
            return ScheduleLabel.objects.get_or_create(name=name)[0]

    def _mk_tt(self, title, label):
        with schema_context(self.tenant.schema_name):
            return PeriodsTimetable.objects.create(
                title=title, label=label, break_duration=0, days=[],
            )

    def test_one_to_one_constraint_is_gone(self):
        """A class can hold MULTIPLE distinct timetables now."""
        cls = self._mk_class()
        lbl = self._mk_label("Senior")
        tt_a = self._mk_tt("TT-A", lbl)
        tt_b = self._mk_tt("TT-B", lbl)

        with schema_context(self.tenant.schema_name):
            ClassTimetableAssignment.objects.create(
                school_class=cls, timetable=tt_a,
            )
            # No IntegrityError — the OneToOneField is now a ForeignKey.
            ClassTimetableAssignment.objects.create(
                school_class=cls, timetable=tt_b,
            )
            self.assertEqual(
                ClassTimetableAssignment.objects.filter(
                    school_class=cls,
                ).count(),
                2,
            )

    def test_same_timetable_twice_rejected(self):
        """Same (class, timetable) pair is refused by the DB."""
        cls = self._mk_class()
        lbl = self._mk_label("Senior")
        tt_a = self._mk_tt("TT-A", lbl)

        with schema_context(self.tenant.schema_name):
            ClassTimetableAssignment.objects.create(
                school_class=cls, timetable=tt_a,
            )
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    ClassTimetableAssignment.objects.create(
                        school_class=cls, timetable=tt_a,
                    )

    def test_timetable_delete_cascades_to_all_assignments(self):
        """Deleting a timetable removes every assignment that pointed
        at it, even when the same class held several assignments."""
        cls = self._mk_class()
        lbl = self._mk_label("Senior")
        tt_a = self._mk_tt("TT-A", lbl)
        tt_b = self._mk_tt("TT-B", lbl)

        with schema_context(self.tenant.schema_name):
            a1 = ClassTimetableAssignment.objects.create(
                school_class=cls, timetable=tt_a,
            )
            ClassTimetableAssignment.objects.create(
                school_class=cls, timetable=tt_b,
            )
            a1_id = a1.id
            tt_a.delete()
            self.assertFalse(
                ClassTimetableAssignment.objects.filter(id=a1_id).exists()
            )
            # The other assignment survives.
            self.assertTrue(
                ClassTimetableAssignment.objects.filter(
                    school_class=cls, timetable=tt_b,
                ).exists()
            )


class AvailableTimetablesAPITests(TimetableAPITestBase):
    """GET /api/timetable/class/<id>/available-timetables/."""

    def _mk_class(self, name="Grade 1", section="A"):
        with schema_context(self.tenant.schema_name):
            return SchoolClass.objects.create(name=name, section=section)

    def _mk_label(self, name):
        with schema_context(self.tenant.schema_name):
            return ScheduleLabel.objects.get_or_create(name=name)[0]

    def _mk_tt(self, title, label):
        with schema_context(self.tenant.schema_name):
            return PeriodsTimetable.objects.create(
                title=title, label=label, break_duration=0, days=[],
            )

    def _url(self, class_id):
        return self.url(
            f"api/timetable/class/{class_id}/available-timetables/"
        )

    def test_all_timetables_returned_when_no_assignment(self):
        cls = self._mk_class()
        s_lbl = self._mk_label("Senior")
        j_lbl = self._mk_label("Junior")
        self._mk_tt("Senior-A", s_lbl)
        self._mk_tt("Junior-A", j_lbl)

        response = self.client.get(
            self._url(cls.id),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["has_assignments"])
        self.assertEqual(body["assigned_count"], 0)
        titles = sorted(tt["title"] for tt in body["timetables"])
        self.assertEqual(titles, ["Junior-A", "Senior-A"])

    def test_only_same_label_returned_after_first_assignment(self):
        cls = self._mk_class()
        s_lbl = self._mk_label("Senior")
        j_lbl = self._mk_label("Junior")
        tt_s1 = self._mk_tt("Senior-A", s_lbl)
        self._mk_tt("Senior-B", s_lbl)
        self._mk_tt("Junior-A", j_lbl)

        with schema_context(self.tenant.schema_name):
            ClassTimetableAssignment.objects.create(
                school_class=cls, timetable=tt_s1,
            )

        response = self.client.get(
            self._url(cls.id),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertTrue(body["has_assignments"])
        self.assertEqual(body["assigned_count"], 1)
        self.assertEqual(body["label_name"], "Senior")
        titles = sorted(tt["title"] for tt in body["timetables"])
        # Senior-A already assigned -> excluded.
        # Junior-A different label -> excluded.
        self.assertEqual(titles, ["Senior-B"])

    def test_already_assigned_timetable_excluded(self):
        cls = self._mk_class()
        s_lbl = self._mk_label("Senior")
        tt_s1 = self._mk_tt("Senior-A", s_lbl)

        with schema_context(self.tenant.schema_name):
            ClassTimetableAssignment.objects.create(
                school_class=cls, timetable=tt_s1,
            )

        response = self.client.get(
            self._url(cls.id),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        body = response.json()
        self.assertEqual(body["timetables"], [])

    def test_unknown_class_404(self):
        response = self.client.get(
            self._url(99999),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 404)


class AssignTimetablePOSTTests(TimetableAPITestBase):
    """POST /timetable/assign/submit/ — same-label rule enforcement."""

    def _mk_class(self, name="Grade 1", section="A"):
        with schema_context(self.tenant.schema_name):
            return SchoolClass.objects.create(name=name, section=section)

    def _mk_label(self, name):
        with schema_context(self.tenant.schema_name):
            return ScheduleLabel.objects.get_or_create(name=name)[0]

    def _mk_tt(self, title, label):
        with schema_context(self.tenant.schema_name):
            return PeriodsTimetable.objects.create(
                title=title, label=label, break_duration=0, days=[],
            )

    def _post(self, class_id, timetable_id):
        return self.client.post(
            self.url("timetable/assign/submit/"),
            data={
                "class_id": class_id,
                "timetable_id": timetable_id,
            },
        )

    def _count(self, cls):
        with schema_context(self.tenant.schema_name):
            return ClassTimetableAssignment.objects.filter(
                school_class_id=cls.id,
            ).count()

    def test_first_assignment_succeeds(self):
        cls = self._mk_class()
        s_lbl = self._mk_label("Senior")
        tt = self._mk_tt("Senior-A", s_lbl)

        response = self._post(cls.id, tt.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._count(cls), 1)

    def test_second_same_label_assignment_allowed(self):
        cls = self._mk_class()
        s_lbl = self._mk_label("Senior")
        tt_a = self._mk_tt("Senior-A", s_lbl)
        tt_b = self._mk_tt("Senior-B", s_lbl)

        self._post(cls.id, tt_a.id)
        self._post(cls.id, tt_b.id)
        self.assertEqual(self._count(cls), 2)

    def test_third_same_label_assignment_allowed(self):
        cls = self._mk_class()
        s_lbl = self._mk_label("Senior")
        titles = ["Senior-A", "Senior-B", "Senior-C"]
        tts = [self._mk_tt(t, s_lbl) for t in titles]
        for tt in tts:
            self._post(cls.id, tt.id)
        self.assertEqual(self._count(cls), 3)

    def test_different_label_assignment_refused(self):
        cls = self._mk_class()
        s_lbl = self._mk_label("Senior")
        j_lbl = self._mk_label("Junior")
        tt_s = self._mk_tt("Senior-A", s_lbl)
        tt_j = self._mk_tt("Junior-A", j_lbl)

        self._post(cls.id, tt_s.id)
        self._post(cls.id, tt_j.id)
        # Junior is refused -> only the Senior assignment exists.
        self.assertEqual(self._count(cls), 1)
        with schema_context(self.tenant.schema_name):
            first = ClassTimetableAssignment.objects.get(
                school_class=cls,
            )
            self.assertEqual(first.timetable.label.name, "Senior")

    def test_duplicate_assignment_refused(self):
        cls = self._mk_class()
        s_lbl = self._mk_label("Senior")
        tt = self._mk_tt("Senior-A", s_lbl)

        self._post(cls.id, tt.id)
        self._post(cls.id, tt.id)  # exact same timetable again
        self.assertEqual(self._count(cls), 1)

    def test_missing_class_or_timetable_is_redirected(self):
        # A POST with no class_id / timetable_id must not 500 — it
        # redirects back to the page with an error message.
        response = self.client.post(
            self.url("timetable/assign/submit/"),
            data={},
        )
        self.assertEqual(response.status_code, 302)


class MultiTimetableRenderTests(TimetableAPITestBase):
    """The assignment page must render cleanly with multiple
    timetables attached to one class."""

    def _mk_class(self, name="Grade 1", section="A"):
        with schema_context(self.tenant.schema_name):
            return SchoolClass.objects.create(name=name, section=section)

    def _mk_label(self, name):
        with schema_context(self.tenant.schema_name):
            return ScheduleLabel.objects.get_or_create(name=name)[0]

    def _mk_tt(self, title, label):
        with schema_context(self.tenant.schema_name):
            return PeriodsTimetable.objects.create(
                title=title, label=label, break_duration=0, days=[],
            )

    def test_page_renders_with_multiple_assignments(self):
        cls = self._mk_class()
        s_lbl = self._mk_label("Senior")
        tt_a = self._mk_tt("Senior-A", s_lbl)
        tt_b = self._mk_tt("Senior-B", s_lbl)
        with schema_context(self.tenant.schema_name):
            ClassTimetableAssignment.objects.create(
                school_class=cls, timetable=tt_a,
            )
            ClassTimetableAssignment.objects.create(
                school_class=cls, timetable=tt_b,
            )

        response = self.client.get(self.url("timetable/assign/"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.context["assignments"]), 2)
        titles = sorted(
            a.timetable.title for a in response.context["assignments"]
        )
        self.assertEqual(titles, ["Senior-A", "Senior-B"])

    def test_page_renders_with_no_assignments(self):
        response = self.client.get(self.url("timetable/assign/"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(list(response.context["assignments"]), [])
