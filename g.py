#!/usr/bin/env python3
"""
axis_patcher.py — ASSIGN_MULTI_TIMETABLE_V1_FIX
================================================

Two fixes on top of the previous ASSIGN_MULTI_TIMETABLE_V1 patcher:

1. views/timetable_assignments.py
   - `api_class_available_timetables` used `JsonResponse` but the
     module never imported it. Result:
         NameError: name 'JsonResponse' is not defined
     We add `from django.http import JsonResponse` to the imports.

2. tests/test_timetable_api.py
   - The old `TimetableAssignmentModelTests` class asserts the OLD
     OneToOne rule (a second assignment raises IntegrityError) and
     uses `label="Senior"` (a string) instead of a ScheduleLabel FK,
     which is now stale.
   - We replace that class with four new test classes:
       * TimetableAssignmentMultiTests — DB-level multi-assignment,
         same-(class, timetable) unique constraint, cascade delete.
       * AvailableTimetablesAPITests     — GET endpoint filter.
       * AssignTimetablePOSTTests        — POST same-label rule,
         duplicate guard, happy path.
       * MultiTimetableRenderTests       — the page still renders
         with one class holding 2 assignments.

Idempotent — safe to run multiple times.

Usage
-----
    python g.py --dry-run --verbose
    python g.py
    python g.py --target-dir /srv/app
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Log:
    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose
        self.errors = 0

    def info(self, msg: str) -> None:
        print(f"[{_ts()}] {msg}")

    def detail(self, msg: str) -> None:
        if self.verbose:
            print(f"[{_ts()}]   -> {msg}")

    def warn(self, msg: str) -> None:
        print(f"[{_ts()}] WARN: {msg}", file=sys.stderr)

    def error(self, msg: str) -> None:
        self.errors += 1
        print(f"[{_ts()}] ERROR: {msg}", file=sys.stderr)


def read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def write_text(path: Path, content: str, dry_run: bool, log: Log) -> bool:
    if dry_run:
        log.detail(f"would write: {path}")
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        log.detail(f"wrote: {path}")
        return True
    except OSError as exc:
        log.error(f"failed to write {path}: {exc}")
        return False


def replace_literal(
    path: Path,
    old: str,
    new: str,
    log: Log,
    dry_run: bool,
    marker: str | None = None,
    optional: bool = False,
) -> bool:
    src = read_text(path)
    if src is None:
        log.error(f"missing file: {path}")
        return False

    if marker and marker in src and old not in src:
        log.detail(f"already patched ({marker}): {path.name}")
        return True

    if old not in src:
        if optional:
            log.detail(f"anchor absent in {path.name} (already clean)")
            return True
        log.error(f"anchor NOT found in {path.name}:\n    {old[:220]!r}")
        return False

    patched = src.replace(old, new, 1)
    if patched == src:
        log.detail(f"no-op for {path.name}")
        return True
    return write_text(path, patched, dry_run, log)


# =====================================================================
# FIX 1 — add missing JsonResponse import
# =====================================================================

def fix_jsonresponse_import(root: Path, log: Log, dry_run: bool) -> None:
    log.info("FIX 1 — views/timetable_assignments.py: import JsonResponse")
    path = root / "axis_saas" / "views" / "timetable_assignments.py"

    old = (
        "from django.shortcuts import render, redirect, get_object_or_404\n"
        "from django.contrib import messages\n"
        "from django.views.decorators.csrf import csrf_exempt\n"
    )
    new = (
        "from django.shortcuts import render, redirect, get_object_or_404\n"
        "from django.contrib import messages\n"
        "from django.http import JsonResponse\n"
        "from django.views.decorators.csrf import csrf_exempt\n"
    )

    ok = replace_literal(path, old, new, log, dry_run,
                         marker="from django.http import JsonResponse")
    if ok:
        # Sanity check: confirm the import exists now.
        src = read_text(path) or ""
        if "from django.http import JsonResponse" in src:
            log.detail("JsonResponse import present")
        else:
            log.error("JsonResponse import missing after patch")


# =====================================================================
# FIX 2 — replace the stale TimetableAssignmentModelTests in
#         test_timetable_api.py with new multi-timetable test classes
# =====================================================================

NEW_TESTS = '''# =====================================================================
# ASSIGN_MULTI_TIMETABLE_V1_TESTS
# ---------------------------------------------------------------------
# These replace the old `TimetableAssignmentModelTests` which asserted
# the previous OneToOne behaviour (a second assignment for the same
# class raised IntegrityError). The rule is now:
#
#   * A class can hold MANY timetables.
#   * Every assigned timetable for a class must share the SAME
#     ScheduleLabel as the first one.
#   * The exact same timetable cannot be assigned twice to the same
#     class (enforced by a DB UniqueConstraint on
#     (school_class, timetable)).
#
# The four classes below cover: DB-level multi-assignment rules,
# the GET /available-timetables/ endpoint, the POST assign endpoint,
# and a page-render sanity check.
# =====================================================================


class TimetableAssignmentMultiTests(TimetableAPITestBase):
    """DB-level rules for the new multi-assignment model."""

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
        tt_s2 = self._mk_tt("Senior-B", s_lbl)
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
'''


def patch_tests(root: Path, log: Log, dry_run: bool) -> None:
    log.info("FIX 2 — tests/test_timetable_api.py: replace stale tests")
    path = root / "axis_saas" / "tests" / "test_timetable_api.py"

    src = read_text(path)
    if src is None:
        log.error(f"missing file: {path}")
        return

    if "ASSIGN_MULTI_TIMETABLE_V1_TESTS" in src:
        log.detail("tests already patched")
        return

    marker = "class TimetableAssignmentModelTests(TimetableAPITestBase):"
    idx = src.find(marker)
    if idx == -1:
        log.error(
            f"anchor class not found in {path.name}: {marker!r}"
        )
        return

    head = src[:idx].rstrip() + "\n\n\n"
    patched = head + NEW_TESTS
    write_text(path, patched, dry_run, log)
    log.detail("replaced TimetableAssignmentModelTests with 4 new test classes")


# =====================================================================
# Main
# =====================================================================

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ASSIGN_MULTI_TIMETABLE_V1_FIX — add missing JsonResponse "
            "import + replace stale OneToOne tests with multi-timetable "
            "tests."
        ),
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing.")
    parser.add_argument("--verbose", action="store_true",
                        help="Show every file touched.")
    parser.add_argument("--target-dir", default=".",
                        help="Project root (default: current directory).")
    args = parser.parse_args(argv)

    log = Log(args.verbose)
    root = Path(args.target_dir).expanduser().resolve()

    log.info(f"target root : {root}")
    if args.dry_run:
        log.info("mode        : DRY RUN (no files will be written)")
    log.info("patcher     : ASSIGN_MULTI_TIMETABLE_V1_FIX")
    log.info("")

    if not root.exists() or not root.is_dir():
        log.error(f"invalid target directory: {root}")
        return 1

    steps = [
        ("fix_jsonresponse_import", fix_jsonresponse_import),
        ("patch_tests",             patch_tests),
    ]

    for name, fn in steps:
        try:
            fn(root, log, args.dry_run)
        except Exception as exc:  # noqa: BLE001
            log.error(f"{name} raised: {exc!r}")
        log.info("")

    if args.dry_run:
        log.info("dry run complete — no changes were written")
    elif log.errors == 0:
        log.info("done.")
    else:
        log.info(f"done with {log.errors} error(s) — see above.")
    return 0 if log.errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
