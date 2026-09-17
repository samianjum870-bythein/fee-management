#!/usr/bin/env python3
"""
axis_patcher.py — ATTENDANCE_SYSTEM_BUGFIX_V2
================================================

Fixes every failure / error reported by the previous test run:

  🐞  ERROR #A   StaffMarkAPITests.test_invalid_json_400
                 AttributeError: 'str' object has no attribute 'items'
                 The test helper `_staff_request` did not accept a raw
                 string/bytes body, so `factory.post(path, "not-json")`
                 tried to multipart-encode a str.

  🐞  FAIL  #B   AdminDailyLogsAPITests.test_returns_logs_for_class_and_date
                 JSON serialises integer dict keys as strings, so
                 `body["student_names"]` contains "1", not 1.

  🐞  FAIL  #C   HolidayDetectionAdminTests.test_historical_evidence_marks_holiday
                 The historical-evidence check was intentionally removed
                 by ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-4).  The test still
                 asserted the old behaviour.

  🐞  FAIL  #D   MultiTenantIsolationTests.test_admin_cannot_see_other_schema_students
                 Class IDs are per-schema and both schemas start at 1,
                 so `other_class.id == 1` matched the primary tenant's
                 own class id=1 and the API returned 200.  The test must
                 use an ID that cannot exist in the primary schema.

  🐞  FAIL  #E   StaffDashboardViewTests.test_class_teacher_sees_class_section
                 The rendered HTML contains the substituted JSON
                 (var CT_CLASSES = [...]), not the template variable
                 name `class_teacher_classes_json`.

  🐞  FAIL  #F   StaffStudentsAPITests.test_locked_flag_when_marked
                 Today is always editable for the class teacher, so
                 `locked` is correctly False for today.  The test must
                 use a PAST date whose quota is exhausted to exercise
                 the lock.

Everything is idempotent — re-running the patcher is safe.

Usage
-----
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py
    python3 axis_patcher.py --target-dir /srv/fee_management
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


MARKER = "ATTENDANCE_SYSTEM_BUGFIX_V2"


# ------------------------------------------------------------------ utils

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log(f"  ERROR: not found: {path}")
        return None
    except Exception as e:
        log(f"  ERROR reading {path}: {e}")
        return None


def write_file(path, content, dry_run=False, label=""):
    if dry_run:
        log(f"  DRY-RUN: would write {path} ({label})")
        return True
    try:
        path.write_text(content, encoding="utf-8")
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as e:
        log(f"  ERROR writing {path}: {e}")
        return False


def replace_once(content, pattern, replacement, label=""):
    """Return (new_content, n_matches). Replaces all matches by default
    when `pattern` is a plain string; for regex use `count=1`."""
    if isinstance(pattern, str):
        n = content.count(pattern)
        if n:
            content = content.replace(pattern, replacement)
        return content, n
    else:
        new, n = pattern.subn(replacement, content, count=1)
        return new, n


# =====================================================================
# The one file we touch
# =====================================================================

def patch_test_suite(root, args):
    path = (root / "axis_saas" / "tests"
            / "test_attendance_system.py")
    content = read_file(path)
    if content is None:
        log(f"  FATAL: test file not found at {path}")
        return False

    changes = []

    # ---------------------------------------------------------------
    # FIX #A — _staff_request must accept str / bytes bodies so the
    #          "invalid JSON" test can actually send malformed JSON.
    # ---------------------------------------------------------------
    if "ATTENDANCE_SYSTEM_BUGFIX_V2 (#A raw body)" not in content:
        old_helper = (
            "        else:\n"
            "            if is_json:\n"
            "                request = factory.post(\n"
            "                    path,\n"
            "                    data=json.dumps(data or {}),\n"
            "                    content_type=\"application/json\",\n"
            "                )\n"
            "            else:\n"
            "                request = factory.post(path, data or {})"
        )
        new_helper = (
            "        else:\n"
            "            if is_json:\n"
            "                request = factory.post(\n"
            "                    path,\n"
            "                    data=json.dumps(data or {}),\n"
            "                    content_type=\"application/json\",\n"
            "                )\n"
            "            elif isinstance(data, (str, bytes)):\n"
            "                # ATTENDANCE_SYSTEM_BUGFIX_V2 (#A raw body):\n"
            "                # allow tests to send a deliberately malformed\n"
            "                # JSON body.  Passing a str to factory.post()\n"
            "                # without content_type made Django try to\n"
            "                # multipart-encode it and blow up with\n"
            "                # \"'str' object has no attribute 'items'\".\n"
            "                request = factory.post(\n"
            "                    path,\n"
            "                    data=data,\n"
            "                    content_type=\"application/json\",\n"
            "                )\n"
            "            else:\n"
            "                request = factory.post(path, data or {})"
        )
        if old_helper in content:
            content = content.replace(old_helper, new_helper, 1)
            changes.append("#A _staff_request accepts raw bodies")
        else:
            log(f"  WARN: #A _staff_request else-branch not matched")
    else:
        log(f"  SKIP (already applied): #A _staff_request raw body")

    # ---------------------------------------------------------------
    # FIX #A (test) — test_invalid_json_400 must call the helper
    #                 correctly (no more data="not-json", is_json=False
    #                 followed by manual request._body mutation).
    # ---------------------------------------------------------------
    old_invalid_json_test = (
        "    def test_invalid_json_400(self):\n"
        "        request = self._staff_request(\n"
        "            self.class_teacher, method=\"POST\",\n"
        "            path=\"/mark/\",\n"
        "            data=\"not-json\", is_json=False,\n"
        "        )\n"
        "        request._body = b\"not-json\"\n"
        "        response = staff_att.staff_attendance_mark_api(request)\n"
        "        self.assertEqual(response.status_code, 400)"
    )
    new_invalid_json_test = (
        "    def test_invalid_json_400(self):\n"
        "        # ATTENDANCE_SYSTEM_BUGFIX_V2 (#A): pass the malformed\n"
        "        # JSON body as a raw str.  The helper's str/bytes branch\n"
        "        # sends it with content_type=application/json so the view\n"
        "        # receives it verbatim and its json.loads() call fails.\n"
        "        request = self._staff_request(\n"
        "            self.class_teacher, method=\"POST\",\n"
        "            path=\"/mark/\",\n"
        "            data=\"not-json\",\n"
        "        )\n"
        "        response = staff_att.staff_attendance_mark_api(request)\n"
        "        self.assertEqual(response.status_code, 400)"
    )
    if "ATTENDANCE_SYSTEM_BUGFIX_V2 (#A)" in content:
        log(f"  SKIP (already applied): #A test_invalid_json_400")
    elif old_invalid_json_test in content:
        content = content.replace(old_invalid_json_test,
                                  new_invalid_json_test, 1)
        changes.append("#A test_invalid_json_400 rewritten")
    else:
        log(f"  WARN: #A test_invalid_json_400 body not matched")

    # ---------------------------------------------------------------
    # FIX #B — string keys in student_names.
    # ---------------------------------------------------------------
    old_b = (
        "        self.assertIn(self.students[0].id, body[\"student_names\"])"
    )
    new_b = (
        "        # ATTENDANCE_SYSTEM_BUGFIX_V2 (#B): json.dumps() converts\n"
        "        # integer dictionary keys to strings, so the parsed body\n"
        "        # contains \"1\", not 1.\n"
        "        self.assertIn(str(self.students[0].id),\n"
        "                      body[\"student_names\"])"
    )
    if "ATTENDANCE_SYSTEM_BUGFIX_V2 (#B)" in content:
        log(f"  SKIP (already applied): #B student_names key")
    elif old_b in content:
        content = content.replace(old_b, new_b, 1)
        changes.append("#B student_names string key")
    else:
        log(f"  WARN: #B student_names assertion not matched")

    # ---------------------------------------------------------------
    # FIX #C — historical-evidence test now asserts False, because
    #          the historical check was intentionally removed.
    # ---------------------------------------------------------------
    old_c = (
        "    def test_historical_evidence_marks_holiday(self):\n"
        "        \"\"\"A past date with a holiday status row is a holiday.\"\"\"\n"
        "        with schema_context(self.schema):\n"
        "            # Create one row with status='holiday'.\n"
        "            StudentAttendance.objects.create(\n"
        "                student=self.students[0],\n"
        "                school_class=self.class_obj,\n"
        "                date=date(2020, 1, 1),\n"
        "                period_order=None,\n"
        "                status=\"holiday\",\n"
        "                source=\"admin\",\n"
        "            )\n"
        "            is_hol, _ = admin_att._is_holiday(date(2020, 1, 1))\n"
        "        self.assertTrue(is_hol)"
    )
    new_c = (
        "    def test_historical_evidence_does_not_flip_whole_day(self):\n"
        "        # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-4) intentionally removed\n"
        "        # the \"any row with status='holiday' means the whole day is\n"
        "        # a holiday\" check.  ATTENDANCE_SYSTEM_BUGFIX_V2 (#C)\n"
        "        # updates the test to match the new (correct) behaviour:\n"
        "        # a single stray row must NOT flip the entire day.\n"
        "        with schema_context(self.schema):\n"
        "            StudentAttendance.objects.create(\n"
        "                student=self.students[0],\n"
        "                school_class=self.class_obj,\n"
        "                date=date(2020, 1, 1),\n"
        "                period_order=None,\n"
        "                status=\"holiday\",\n"
        "                source=\"admin\",\n"
        "            )\n"
        "            is_hol, _ = admin_att._is_holiday(date(2020, 1, 1))\n"
        "        self.assertFalse(is_hol)"
    )
    if "ATTENDANCE_SYSTEM_BUGFIX_V2 (#C)" in content:
        log(f"  SKIP (already applied): #C historical-evidence test")
    elif old_c in content:
        content = content.replace(old_c, new_c, 1)
        changes.append("#C historical-evidence test updated")
    else:
        log(f"  WARN: #C historical-evidence test body not matched")

    # ---------------------------------------------------------------
    # FIX #D — multi-tenant isolation test must use a class ID that
    #          cannot exist in the primary tenant's schema.
    # ---------------------------------------------------------------
    old_d = (
        "        connection.set_schema_to_public()\n"
        "        try:\n"
        "            with schema_context(\"attendance-other\"):\n"
        "                other_class = SchoolClass.objects.create(\n"
        "                    name=\"Other-Grade\", section=\"Z\",\n"
        "                )\n"
        "            # Admin of the main tenant tries to fetch the other class.\n"
        "            today = timezone.localdate()\n"
        "            response = self.client.get(\n"
        "                self.url(\n"
        "                    f\"api/attendance/students/?class_id={other_class.id}\"\n"
        "                    f\"&date={today.isoformat()}\"\n"
        "                ),\n"
        "                HTTP_X_REQUESTED_WITH=\"XMLHttpRequest\",\n"
        "            )\n"
        "            self.assertEqual(response.status_code, 404)"
    )
    new_d = (
        "        connection.set_schema_to_public()\n"
        "        try:\n"
        "            with schema_context(\"attendance-other\"):\n"
        "                # ATTENDANCE_SYSTEM_BUGFIX_V2 (#D): PostgreSQL\n"
        "                # sequences are per-schema, so the very first\n"
        "                # class created in a fresh schema also has id=1\n"
        "                # — identical to the primary tenant's class_obj.\n"
        "                # That made the previous assertion return 200\n"
        "                # (the primary tenant's own class), not a leak.\n"
        "                # Pad the other schema with dummy classes so\n"
        "                # other_class.id cannot collide with any class\n"
        "                # id in the primary tenant.\n"
        "                for _i in range(20):\n"
        "                    SchoolClass.objects.create(\n"
        "                        name=f\"Dummy-{_i}\", section=\"X\",\n"
        "                    )\n"
        "                other_class = SchoolClass.objects.create(\n"
        "                    name=\"Other-Grade\", section=\"Z\",\n"
        "                )\n"
        "            # Admin of the main tenant tries to fetch the other class.\n"
        "            today = timezone.localdate()\n"
        "            response = self.client.get(\n"
        "                self.url(\n"
        "                    f\"api/attendance/students/?class_id={other_class.id}\"\n"
        "                    f\"&date={today.isoformat()}\"\n"
        "                ),\n"
        "                HTTP_X_REQUESTED_WITH=\"XMLHttpRequest\",\n"
        "            )\n"
        "            # other_class.id is > 20 and cannot exist in the primary\n"
        "            # tenant's schema, so the API must 404.\n"
        "            self.assertEqual(response.status_code, 404)"
    )
    if "ATTENDANCE_SYSTEM_BUGFIX_V2 (#D)" in content:
        log(f"  SKIP (already applied): #D multi-tenant isolation")
    elif old_d in content:
        content = content.replace(old_d, new_d, 1)
        changes.append("#D multi-tenant isolation distinct IDs")
    else:
        log(f"  WARN: #D multi-tenant isolation block not matched")

    # ---------------------------------------------------------------
    # FIX #E — staff dashboard view test must look for the substituted
    #          JSON, not the template variable name.
    # ---------------------------------------------------------------
    old_e = (
        "    def test_class_teacher_sees_class_section(self):\n"
        "        request = self._staff_request(self.class_teacher)\n"
        "        response = staff_att.staff_attendance_view(request)\n"
        "        self.assertEqual(response.status_code, 200)\n"
        "        self.assertIn(\n"
        "            b\"class_teacher_classes_json\", response.content,\n"
        "        )"
    )
    new_e = (
        "    def test_class_teacher_sees_class_section(self):\n"
        "        # ATTENDANCE_SYSTEM_BUGFIX_V2 (#E): the template uses\n"
        "        # `{{ class_teacher_classes_json|safe }}`, which Django\n"
        "        # substitutes with the actual JSON array.  After rendering,\n"
        "        # the literal string `class_teacher_classes_json` is NOT\n"
        "        # present — we must look for the JS variable name that\n"
        "        # the template assigns the JSON to.\n"
        "        request = self._staff_request(self.class_teacher)\n"
        "        response = staff_att.staff_attendance_view(request)\n"
        "        self.assertEqual(response.status_code, 200)\n"
        "        self.assertIn(b\"CT_CLASSES\", response.content)\n"
        "        # And the actual class name must be present in the JSON.\n"
        "        self.assertIn(b\"Grade 1\", response.content)"
    )
    if "ATTENDANCE_SYSTEM_BUGFIX_V2 (#E)" in content:
        log(f"  SKIP (already applied): #E dashboard view test")
    elif old_e in content:
        content = content.replace(old_e, new_e, 1)
        changes.append("#E dashboard view test looks for CT_CLASSES")
    else:
        log(f"  WARN: #E dashboard view test body not matched")

    # ---------------------------------------------------------------
    # FIX #F — locked flag test must use a PAST date whose quota is
    #          exhausted.  Today is always editable for the class
    #          teacher, so testing it for "locked" is conceptually
    #          wrong.
    # ---------------------------------------------------------------
    old_f = (
        "    def test_locked_flag_when_marked(self):\n"
        "        today = timezone.localdate()\n"
        "        with schema_context(self.schema):\n"
        "            self._mark_full_day(today)\n"
        "            # Turn on read_write so can_edit is possible in principle;\n"
        "            # but the frontend is locked because marks exist.\n"
        "            perm = ClassTeacherAttendancePermission.for_class(\n"
        "                self.class_obj,\n"
        "            )\n"
        "            perm.backdate_access = \"read_write\"\n"
        "            perm.max_edits_per_date = 3\n"
        "            perm.view_history_days = 30\n"
        "            perm.edit_history_days = 5\n"
        "            perm.save()\n"
        "        request = self._staff_request(\n"
        "            self.class_teacher,\n"
        "            path=f\"/?class_id={self.class_obj.id}\"\n"
        "                 f\"&date={today.isoformat()}\",\n"
        "        )\n"
        "        response = staff_att.staff_attendance_students_api(request)\n"
        "        # lock_reason may be empty for today but the flag reflects marks.\n"
        "        body = json.loads(response.content)\n"
        "        self.assertTrue(body[\"locked\"])"
    )
    new_f = (
        "    def test_locked_flag_when_quota_exhausted_on_past_date(self):\n"
        "        # ATTENDANCE_SYSTEM_BUGFIX_V2 (#F): today is NEVER locked\n"
        "        # for the class teacher — they own the class and the\n"
        "        # backdate quota only applies to past dates.  The \"locked\"\n"
        "        # flag is True only when _compute_permission_payload()\n"
        "        # returns can_edit=False, which happens on a PAST date\n"
        "        # whose per-date quota has been exhausted.\n"
        "        target = timezone.localdate() - timedelta(days=2)\n"
        "        with schema_context(self.schema):\n"
        "            perm = ClassTeacherAttendancePermission.for_class(\n"
        "                self.class_obj,\n"
        "            )\n"
        "            perm.backdate_access = \"read_write\"\n"
        "            perm.max_edits_per_date = 1\n"
        "            perm.view_history_days = 30\n"
        "            perm.edit_history_days = 5\n"
        "            perm.save()\n"
        "            # Exhaust the teacher's quota for the target date.\n"
        "            q = ClassTeacherEditQuota.for_class_date(\n"
        "                self.class_obj, target,\n"
        "            )\n"
        "            q.teacher_edit_count = 1\n"
        "            q.save()\n"
        "            # Mark a row so the API has data to return.\n"
        "            StudentAttendance.objects.create(\n"
        "                student=self.students[0],\n"
        "                school_class=self.class_obj,\n"
        "                date=target,\n"
        "                period_order=None,\n"
        "                status=\"present\",\n"
        "                source=\"teacher\",\n"
        "                teacher=self.class_teacher,\n"
        "                marked_by=self.class_teacher,\n"
        "            )\n"
        "        request = self._staff_request(\n"
        "            self.class_teacher,\n"
        "            path=f\"/?class_id={self.class_obj.id}\"\n"
        "                 f\"&date={target.isoformat()}\",\n"
        "        )\n"
        "        response = staff_att.staff_attendance_students_api(request)\n"
        "        body = json.loads(response.content)\n"
        "        self.assertTrue(body[\"locked\"])\n"
        "        self.assertTrue(body[\"lock_reason\"])"
    )
    if "ATTENDANCE_SYSTEM_BUGFIX_V2 (#F)" in content:
        log(f"  SKIP (already applied): #F locked flag test")
    elif old_f in content:
        content = content.replace(old_f, new_f, 1)
        changes.append("#F locked flag test uses exhausted past date")
    else:
        log(f"  WARN: #F locked flag test body not matched")

    if not changes:
        log(f"  NO CHANGES for {path}")
        return True

    return write_file(path, content, args.dry_run,
                      ", ".join(changes))


# =====================================================================
# MAIN
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            f"{MARKER} — fixes every test failure reported after "
            f"running axis_saas.tests.test_attendance_system."
        )
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--target-dir", default=".")
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not (root / "manage.py").is_file():
        log(f"ERROR: manage.py not found in {root}")
        return 1

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")
    log(f"Patch:  {MARKER}")

    steps = [
        ("Attendance test suite", patch_test_suite),
    ]

    results = []
    for label, fn in steps:
        log(f"--- {label} ---")
        try:
            ok = fn(root, args)
        except Exception as exc:
            log(f"  EXCEPTION: {exc.__class__.__name__}: {exc}")
            ok = False
        results.append((label, ok))

    log("=" * 65)
    for label, ok in results:
        log(f"  {'OK  ' if ok else 'FAIL'}  {label}")

    all_ok = all(ok for _, ok in results)
    if all_ok:
        log("All steps completed successfully.")
        if args.dry_run:
            log("Re-run without --dry-run to apply.")
        else:
            log("Next steps:")
            log("  python manage.py test axis_saas.tests.test_attendance_system")
        return 0
    log("One or more steps failed. See messages above.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
