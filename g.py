#!/usr/bin/env python3
"""
axis_patcher.py — ATTENDANCE_SYSTEM_BUGFIX_V1
================================================

Fixes ALL 8 reported bugs in the attendance system (admin + staff):

  🔴 CRITICAL #1  Authorization bypass in staff_attendance_students_api
                  and staff_attendance_mark_api.  `is_period_teacher`
                  defaulted to True, letting any staff member read any
                  class's roster / attempt a mark.

  🔴 CRITICAL #2  ClassTeacherEditQuota counter incremented even when
                  NOTHING was saved (empty records[], all-invalid ids,
                  etc.).  Teacher could burn through their entire
                  backdate quota without ever writing a row.

  🟠 HIGH    #3   Race condition: two concurrent saves both read the
                  same pre-increment quota value and both write count+1.
                  No select_for_update() lock.

  🟠 HIGH    #4   _is_holiday() treated ANY StudentAttendance row with
                  status='holiday' as evidence that the whole day is a
                  holiday, so a single stray row silently flipped the
                  entire day into a holiday.

  🟡 MEDIUM  #5   Mobile attendance template: after opening a subject
                  period, the date-picker input stays disabled when the
                  user navigates back to a class-teacher view.

  🟡 MEDIUM  #6   _is_holiday() compared a UTC date (created_at.date())
                  against a PKT local date (timezone.localdate()), so
                  holiday rules created around midnight could apply to
                  the wrong day.

  🟡 MEDIUM  #7   test_today_edit_does_not_consume_quota was self-
                  contradicting (name + comment said "does not
                  consume", assertion said "= 1").

  🟡 MEDIUM  #8   Staff / admin mark APIs accepted status='holiday'
                  from the client, giving a single POST the power to
                  flip the entire day into a holiday (see bug #4).

Idempotent. Safe to re-run. Never deletes or overwrites unrelated code.

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


MARKER = "ATTENDANCE_SYSTEM_BUGFIX_V1"


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


# =====================================================================
# 1. admin_attendence.py
# =====================================================================

def patch_admin_attendance(root, args):
    path = root / "axis_saas" / "views" / "admin_attendence.py"
    content = read_file(path)
    if content is None:
        return False

    changes = []

    # -------- #4 + #8: introduce MARKABLE_STATUSES --------------------
    old_const = (
        "ATTENDANCE_STATUSES = (\n"
        "    'present', 'absent', 'late', 'half_day', 'excused', 'holiday',\n"
        ")"
    )
    new_const = (
        "ATTENDANCE_STATUSES = (\n"
        "    'present', 'absent', 'late', 'half_day', 'excused', 'holiday',\n"
        ")\n"
        "# ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-4 + BUG-8):\n"
        "# 'holiday' is only ever set on a whole class by the calendar /\n"
        "# cron jobs.  A single student's mark must NEVER be able to flip\n"
        "# the entire day into a holiday.  The mark APIs therefore accept\n"
        "# only MARKABLE_STATUSES; the records/filter APIs keep the full\n"
        "# ATTENDANCE_STATUSES list so 'holiday' rows can still be\n"
        "# queried explicitly.\n"
        "MARKABLE_STATUSES = (\n"
        "    'present', 'absent', 'late', 'half_day', 'excused',\n"
        ")"
    )
    if "MARKABLE_STATUSES" not in content:
        if old_const in content:
            content = content.replace(old_const, new_const, 1)
            changes.append("#4/#8 add MARKABLE_STATUSES")
        else:
            log(f"  WARN: could not add MARKABLE_STATUSES in {path}")
    else:
        log(f"  SKIP (already added): MARKABLE_STATUSES in {path}")

    # -------- #6: timezone fix in _is_holiday -------------------------
    if "timezone.localtime(ca).date() <= on_date" not in content:
        old_weekly = (
            "    try:\n"
            "        for wh in WeeklyHoliday.objects.filter(day_of_week=dow):\n"
            "            ca = getattr(wh, 'created_at', None)\n"
            "            if ca is None or ca.date() <= on_date:\n"
            "                return True, f\"Weekly holiday ({wh.label or 'Weekend'})\"\n"
            "    except Exception:\n"
            "        pass"
        )
        new_weekly = (
            "    try:\n"
            "        for wh in WeeklyHoliday.objects.filter(day_of_week=dow):\n"
            "            ca = getattr(wh, 'created_at', None)\n"
            "            # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-6): created_at is\n"
            "            # stored in UTC.  Compare in the project's local\n"
            "            # timezone so a rule created at 23:00 PKT does not\n"
            "            # land on the wrong local date.\n"
            "            ca_local = timezone.localtime(ca).date() if ca else None\n"
            "            if ca_local is None or ca_local <= on_date:\n"
            "                return True, f\"Weekly holiday ({wh.label or 'Weekend'})\"\n"
            "    except Exception:\n"
            "        pass"
        )
        if old_weekly in content:
            content = content.replace(old_weekly, new_weekly, 1)
            changes.append("#6 weekly-holiday timezone fix")
        else:
            log(f"  WARN: weekly holiday block not matched in {path}")

        old_annual = (
            "    try:\n"
            "        for ah in AnnualHoliday.objects.filter(\n"
            "            month=on_date.month, day=on_date.day,\n"
            "        ):\n"
            "            ca = getattr(ah, 'created_at', None)\n"
            "            if ca is None or ca.date() <= on_date:\n"
            "                return True, f\"Annual holiday ({ah.label})\"\n"
            "    except Exception:\n"
            "        pass"
        )
        new_annual = (
            "    try:\n"
            "        for ah in AnnualHoliday.objects.filter(\n"
            "            month=on_date.month, day=on_date.day,\n"
            "        ):\n"
            "            ca = getattr(ah, 'created_at', None)\n"
            "            # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-6): UTC -> local.\n"
            "            ca_local = timezone.localtime(ca).date() if ca else None\n"
            "            if ca_local is None or ca_local <= on_date:\n"
            "                return True, f\"Annual holiday ({ah.label})\"\n"
            "    except Exception:\n"
            "        pass"
        )
        if old_annual in content:
            content = content.replace(old_annual, new_annual, 1)
            changes.append("#6 annual-holiday timezone fix")
        else:
            log(f"  WARN: annual holiday block not matched in {path}")
    else:
        log(f"  SKIP (already fixed): #6 timezone in {path}")

    # -------- #4: remove historical-evidence holiday check -----------
    historical_block = re.compile(
        r"\n    # Historical evidence: any attendance row marked as holiday\.\n"
        r"    try:\n"
        r"        if StudentAttendance\.objects\.filter\(\n"
        r"            date=on_date, status='holiday',\n"
        r"        \)\.exists\(\):\n"
        r"            return True, \"Marked as holiday in records\"\n"
        r"    except Exception:\n"
        r"        pass\n"
    )
    if historical_block.search(content):
        content = historical_block.sub("\n", content, count=1)
        changes.append("#4 remove historical-evidence holiday check")
    else:
        log(f"  SKIP (already removed or absent): historical-evidence "
            f"block in {path}")

    # -------- #4 + #8: mark API uses MARKABLE_STATUSES ---------------
    # admin_attendance_mark_api
    old_mark = (
        "                status = (rec.get('status') or 'present').strip().lower()\n"
        "                if status not in ATTENDANCE_STATUSES:\n"
        "                    status = 'present'"
    )
    new_mark = (
        "                status = (rec.get('status') or 'present').strip().lower()\n"
        "                # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-4 + BUG-8):\n"
        "                # never accept 'holiday' from a single-student\n"
        "                # mark — that would flip the whole day.\n"
        "                if status not in MARKABLE_STATUSES:\n"
        "                    status = 'present'"
    )
    n = content.count(old_mark)
    if n:
        content = content.replace(old_mark, new_mark)
        changes.append(f"#4/#8 mark-api status gate ({n} occurrence(s))")
    else:
        log(f"  SKIP (already done or absent): admin mark-api status gate")

    # -------- #4 + #8: bulk mark API uses MARKABLE_STATUSES ----------
    old_bulk = (
        "    if status not in ATTENDANCE_STATUSES:\n"
        "        return JsonResponse({'ok': False, 'error': 'Invalid status'},\n"
        "                            status=400)"
    )
    new_bulk = (
        "    # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-4 + BUG-8):\n"
        "    # 'holiday' is reserved for whole-class calendar marks.\n"
        "    if status not in MARKABLE_STATUSES:\n"
        "        return JsonResponse({'ok': False, 'error': 'Invalid status'},\n"
        "                            status=400)"
    )
    if old_bulk in content:
        content = content.replace(old_bulk, new_bulk, 1)
        changes.append("#4/#8 bulk-mark-api status gate")
    else:
        log(f"  SKIP (already done or absent): admin bulk status gate")

    if not changes:
        log(f"  NO CHANGES for {path}")
        return True

    return write_file(path, content, args.dry_run,
                      ", ".join(changes))


# =====================================================================
# 2. staff_attendence.py
# =====================================================================

def patch_staff_attendance(root, args):
    path = root / "axis_saas" / "views" / "staff_attendence.py"
    content = read_file(path)
    if content is None:
        return False

    changes = []

    # -------- #8: introduce MARKABLE_STATUSES -------------------------
    old_const = (
        "ATTENDANCE_STATUSES = (\n"
        "    'present', 'absent', 'late', 'half_day', 'excused', 'holiday',\n"
        ")"
    )
    new_const = (
        "ATTENDANCE_STATUSES = (\n"
        "    'present', 'absent', 'late', 'half_day', 'excused', 'holiday',\n"
        ")\n"
        "# ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-8): class teachers may only\n"
        "# set one of these statuses on an individual student.  'holiday'\n"
        "# is reserved for whole-class calendar marks.\n"
        "MARKABLE_STATUSES = (\n"
        "    'present', 'absent', 'late', 'half_day', 'excused',\n"
        ")"
    )
    if "MARKABLE_STATUSES" not in content:
        if old_const in content:
            content = content.replace(old_const, new_const, 1)
            changes.append("#8 add MARKABLE_STATUSES")
        else:
            log(f"  WARN: could not add MARKABLE_STATUSES in {path}")
    else:
        log(f"  SKIP (already added): MARKABLE_STATUSES in {path}")

    # -------- #1: is_period_teacher default True -> False ------------
    #      Appears in both students_api and mark_api.
    n_before = content.count("is_period_teacher = True")
    if n_before:
        content = content.replace(
            "is_period_teacher = True",
            "is_period_teacher = False",
        )
        changes.append(
            f"#1 is_period_teacher default True->False "
            f"({n_before} site(s))"
        )
    else:
        log(f"  SKIP (already fixed): #1 default in {path}")

    # -------- #6: timezone fix in _is_holiday -------------------------
    if "timezone.localtime(ca).date() <= on_date" not in content:
        old_weekly = (
            "    try:\n"
            "        for wh in WeeklyHoliday.objects.filter(day_of_week=dow):\n"
            "            ca = getattr(wh, 'created_at', None)\n"
            "            if ca is None or ca.date() <= on_date:\n"
            "                return True, f\"Weekly holiday ({wh.label or 'Weekend'})\"\n"
            "    except Exception:\n"
            "        pass"
        )
        new_weekly = (
            "    try:\n"
            "        for wh in WeeklyHoliday.objects.filter(day_of_week=dow):\n"
            "            ca = getattr(wh, 'created_at', None)\n"
            "            # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-6): created_at is\n"
            "            # stored in UTC; compare in local time.\n"
            "            ca_local = timezone.localtime(ca).date() if ca else None\n"
            "            if ca_local is None or ca_local <= on_date:\n"
            "                return True, f\"Weekly holiday ({wh.label or 'Weekend'})\"\n"
            "    except Exception:\n"
            "        pass"
        )
        if old_weekly in content:
            content = content.replace(old_weekly, new_weekly, 1)
            changes.append("#6 weekly-holiday timezone fix")
        else:
            log(f"  WARN: weekly holiday block not matched in {path}")

        old_annual = (
            "    try:\n"
            "        for ah in AnnualHoliday.objects.filter(\n"
            "            month=on_date.month, day=on_date.day,\n"
            "        ):\n"
            "            ca = getattr(ah, 'created_at', None)\n"
            "            if ca is None or ca.date() <= on_date:\n"
            "                return True, f\"Annual holiday ({ah.label})\"\n"
            "    except Exception:\n"
            "        pass"
        )
        new_annual = (
            "    try:\n"
            "        for ah in AnnualHoliday.objects.filter(\n"
            "            month=on_date.month, day=on_date.day,\n"
            "        ):\n"
            "            ca = getattr(ah, 'created_at', None)\n"
            "            # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-6): UTC -> local.\n"
            "            ca_local = timezone.localtime(ca).date() if ca else None\n"
            "            if ca_local is None or ca_local <= on_date:\n"
            "                return True, f\"Annual holiday ({ah.label})\"\n"
            "    except Exception:\n"
            "        pass"
        )
        if old_annual in content:
            content = content.replace(old_annual, new_annual, 1)
            changes.append("#6 annual-holiday timezone fix")
        else:
            log(f"  WARN: annual holiday block not matched in {path}")
    else:
        log(f"  SKIP (already fixed): #6 timezone in {path}")

    # -------- #4: remove historical-evidence holiday check -----------
    historical_block = re.compile(
        r"\n    try:\n"
        r"        if StudentAttendance\.objects\.filter\(\n"
        r"            date=on_date, status='holiday',\n"
        r"        \)\.exists\(\):\n"
        r"            return True, \"Marked as holiday in records\"\n"
        r"    except Exception:\n"
        r"        pass\n"
    )
    if historical_block.search(content):
        content = historical_block.sub("\n", content, count=1)
        changes.append("#4 remove historical-evidence holiday check")
    else:
        log(f"  SKIP (already removed or absent): historical-evidence "
            f"block in {path}")

    # -------- #2 + #3 + #7: quota increment logic ---------------------
    old_quota = (
        "        if is_ct:\n"
        "            quota = ClassTeacherEditQuota.for_class_date(\n"
        "                school_class, att_date,\n"
        "            )\n"
        "            quota.teacher_edit_count = (\n"
        "                quota.teacher_edit_count or 0\n"
        "            ) + 1\n"
        "            quota.last_teacher_edit_at = timezone.now()\n"
        "            quota.last_teacher_edit_by_id = staff.pk\n"
        "            quota.last_teacher_edit_by_name = staff.full_name\n"
        "            quota.save()\n"
        "\n"
        "            permission = _compute_permission_payload(\n"
        "                staff, school_class, att_date,\n"
        "            )"
    )
    new_quota = (
        "        # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-2 + BUG-3 + BUG-7):\n"
        "        # Consume a backdate edit only when the caller is the\n"
        "        # class teacher, at least one row was actually written,\n"
        "        # AND the target date is strictly in the past.  Today's\n"
        "        # attendance is always editable regardless of the backdate\n"
        "        # quota and must NOT create or increment a quota row.\n"
        "        #\n"
        "        # The row is locked with select_for_update() inside an\n"
        "        # atomic block so two concurrent saves cannot both read\n"
        "        # the same pre-increment value and both write count+1.\n"
        "        today = _today()\n"
        "        if is_ct and saved > 0 and att_date < today:\n"
        "            with transaction.atomic():\n"
        "                quota, _ = (\n"
        "                    ClassTeacherEditQuota.objects\n"
        "                    .select_for_update()\n"
        "                    .get_or_create(\n"
        "                        school_class=school_class, date=att_date,\n"
        "                    )\n"
        "                )\n"
        "                quota.teacher_edit_count = (\n"
        "                    quota.teacher_edit_count or 0\n"
        "                ) + 1\n"
        "                quota.last_teacher_edit_at = timezone.now()\n"
        "                quota.last_teacher_edit_by_id = staff.pk\n"
        "                quota.last_teacher_edit_by_name = staff.full_name\n"
        "                quota.save()\n"
        "            permission = _compute_permission_payload(\n"
        "                staff, school_class, att_date,\n"
        "            )"
    )
    if old_quota in content:
        content = content.replace(old_quota, new_quota, 1)
        changes.append("#2/#3/#7 quota logic rewrite")
    else:
        log(f"  WARN: quota block not matched in {path}")

    # -------- #8: staff mark API status gate --------------------------
    old_mark_status = (
        "                status = (rec.get('status') or 'present').strip().lower()\n"
        "                if status not in ATTENDANCE_STATUSES:\n"
        "                    status = 'present'"
    )
    new_mark_status = (
        "                status = (rec.get('status') or 'present').strip().lower()\n"
        "                # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-8): reject\n"
        "                # 'holiday' from a per-student mark.\n"
        "                if status not in MARKABLE_STATUSES:\n"
        "                    status = 'present'"
    )
    if old_mark_status in content:
        content = content.replace(old_mark_status, new_mark_status, 1)
        changes.append("#8 staff mark-api status gate")
    else:
        log(f"  SKIP (already done or absent): staff mark-api status gate")

    if not changes:
        log(f"  NO CHANGES for {path}")
        return True

    return write_file(path, content, args.dry_run,
                      ", ".join(changes))


# =====================================================================
# 3. templates/mobile/staff/attendence.html  (bug #5)
# =====================================================================

def patch_mobile_template(root, args):
    path = root / "templates" / "mobile" / "staff" / "attendence.html"
    content = read_file(path)
    if content is None:
        return False

    changes = []

    # ---- back(): re-enable the date picker ---------------------------
    old_back = (
        "    function back() {\n"
        "        q('#attHomeView').style.display = 'block';\n"
        "        q('#attDetailView').style.display = 'none';\n"
        "        currentClass = null;"
    )
    new_back = (
        "    function back() {\n"
        "        q('#attHomeView').style.display = 'block';\n"
        "        q('#attDetailView').style.display = 'none';\n"
        "        // ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-5): re-enable the date\n"
        "        // picker; it is disabled while viewing a single subject\n"
        "        // period, but the next view may be a class-teacher view.\n"
        "        q('#attDatePicker').disabled = false;\n"
        "        currentClass = null;"
    )
    if "q('#attDatePicker').disabled = false;" in content:
        log(f"  SKIP (already present): date-picker re-enable in {path}")
    else:
        if old_back in content:
            content = content.replace(old_back, new_back, 1)
            changes.append("#5 back() re-enables date picker")
        else:
            log(f"  WARN: back() block not matched in {path}")

        # ---- openClass(): re-enable the date picker ------------------
        old_open = (
            "        q('#attDetailTitle').textContent = c.name;\n"
            "        q('#attDetailSub').textContent = c.student_count + ' students · Class Teacher';\n"
            "        q('#attDatePicker').value = TODAY;\n"
            "        q('#attHomeView').style.display = 'none';\n"
            "        q('#attDetailView').style.display = 'block';\n"
            "        switchTab('today');\n"
            "        loadStudents(c.id, null, TODAY);"
        )
        new_open = (
            "        q('#attDetailTitle').textContent = c.name;\n"
            "        q('#attDetailSub').textContent = c.student_count + ' students · Class Teacher';\n"
            "        q('#attDatePicker').value = TODAY;\n"
            "        // ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-5): subject-period\n"
            "        // view disables this input; class-teacher view needs it\n"
            "        // enabled again.\n"
            "        q('#attDatePicker').disabled = false;\n"
            "        q('#attHomeView').style.display = 'none';\n"
            "        q('#attDetailView').style.display = 'block';\n"
            "        switchTab('today');\n"
            "        loadStudents(c.id, null, TODAY);"
        )
        if old_open in content:
            content = content.replace(old_open, new_open, 1)
            changes.append("#5 openClass() re-enables date picker")
        else:
            log(f"  WARN: openClass() block not matched in {path}")

    if not changes:
        log(f"  NO CHANGES for {path}")
        return True

    return write_file(path, content, args.dry_run,
                      ", ".join(changes))


# =====================================================================
# 4. tests/test_attendance_system.py  (bug #7)
# =====================================================================

def patch_test_suite(root, args):
    path = (root / "axis_saas" / "tests"
            / "test_attendance_system.py")
    content = read_file(path)
    if content is None:
        log(f"  SKIP: test file not found at {path}")
        return True  # not fatal

    old_test = (
        "    def test_today_edit_does_not_consume_quota(self):\n"
        "        today = timezone.localdate()\n"
        "        records = [{\"student_id\": s.id, \"status\": \"present\"}\n"
        "                   for s in self.students]\n"
        "        request = self._staff_request(\n"
        "            self.class_teacher, method=\"POST\",\n"
        "            path=\"/mark/\",\n"
        "            data={\n"
        "                \"class_id\": self.class_obj.id,\n"
        "                \"date\": today.isoformat(),\n"
        "                \"records\": records,\n"
        "            },\n"
        "            is_json=True,\n"
        "        )\n"
        "        response = staff_att.staff_attendance_mark_api(request)\n"
        "        body = json.loads(response.content)\n"
        "        self.assertTrue(body[\"ok\"])\n"
        "        self.assertEqual(body[\"saved\"], 5)\n"
        "        # Today's edit should not increment the quota counter.\n"
        "        with schema_context(self.schema):\n"
        "            q = ClassTeacherEditQuota.objects.filter(\n"
        "                school_class=self.class_obj, date=today,\n"
        "            ).first()\n"
        "        self.assertIsNotNone(q)\n"
        "        self.assertEqual(q.teacher_edit_count, 1)"
    )
    new_test = (
        "    def test_today_edit_does_not_consume_quota(self):\n"
        "        # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-7): the previous\n"
        "        # version of this test contradicted itself — the name\n"
        "        # and comment said \"does not consume quota\" but the\n"
        "        # assertion checked `teacher_edit_count == 1` (i.e.,\n"
        "        # that it DID).  The correct behaviour, per\n"
        "        # _compute_permission_payload(), is that today's date is\n"
        "        # always editable regardless of the backdate quota, so\n"
        "        # no quota row must be created for today.\n"
        "        today = timezone.localdate()\n"
        "        records = [{\"student_id\": s.id, \"status\": \"present\"}\n"
        "                   for s in self.students]\n"
        "        request = self._staff_request(\n"
        "            self.class_teacher, method=\"POST\",\n"
        "            path=\"/mark/\",\n"
        "            data={\n"
        "                \"class_id\": self.class_obj.id,\n"
        "                \"date\": today.isoformat(),\n"
        "                \"records\": records,\n"
        "            },\n"
        "            is_json=True,\n"
        "        )\n"
        "        response = staff_att.staff_attendance_mark_api(request)\n"
        "        body = json.loads(response.content)\n"
        "        self.assertTrue(body[\"ok\"])\n"
        "        self.assertEqual(body[\"saved\"], 5)\n"
        "        with schema_context(self.schema):\n"
        "            q = ClassTeacherEditQuota.objects.filter(\n"
        "                school_class=self.class_obj, date=today,\n"
        "            ).first()\n"
        "        self.assertIsNone(q)"
    )
    if "BUG-7" in content and "assertIsNone(q)" in content:
        log(f"  SKIP (already fixed): test_today_edit in {path}")
        return True
    if old_test in content:
        content = content.replace(old_test, new_test, 1)
        return write_file(path, content, args.dry_run,
                          "#7 fix self-contradicting quota test")
    log(f"  WARN: test_today_edit_does_not_consume_quota body not "
        f"matched in {path}; skipping")
    return True


# =====================================================================
# MAIN
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            f"{MARKER} — fixes all 8 reported attendance bugs "
            f"(authorization bypass, quota counter, race condition, "
            f"holiday logic, mobile date picker, timezone, test, "
            f"status gate)."
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
        ("Admin attendance views",  patch_admin_attendance),
        ("Staff attendance views",  patch_staff_attendance),
        ("Mobile attendance template", patch_mobile_template),
        ("Attendance test suite",   patch_test_suite),
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
            log("  1. python manage.py test axis_saas.tests.test_attendance_system")
            log("  2. Restart the Django server / WSGI workers.")
        return 0
    log("One or more steps failed. See messages above.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
