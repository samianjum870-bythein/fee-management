"""ATTENDANCE_AUTO_MARK_AND_LOCK_V1 — auto-mark missed attendance.

Runs once per day (typically a few minutes after midnight).  For every
active class in every tenant, it looks at the target date and, for any
active student who has NO full-day StudentAttendance row for that date,
creates one with:

    status     = 'present'
    source     = 'auto_system'
    marked_at  = now
    remarks    = 'Auto-marked by system (no teacher/admin action)'

Students who have an APPROVED StudentLeave covering the date get
``status='excused'`` / ``source='auto_leave'`` instead, so the leave
record stays authoritative even if the leave-approval signal did not
run.

Rules
-----
* Holidays are skipped entirely (weekly / annual / vacation).
* The target date must be in the PAST (strictly before today, in
  Asia/Karachi).  Today's attendance is never auto-marked — an admin
  or teacher still has the whole day to do it.
* Existing rows are never touched.  If a teacher marked 3 of 30
  students before being interrupted, only the remaining 27 are
  auto-marked.
* Idempotent: re-running for the same date is a no-op.

Schedule
--------
    5 0 * * * cd /srv/app && python manage.py attendance_auto_present

Backfill / diagnose
-------------------
    # See what the command WOULD do, without writing anything
    python manage.py attendance_auto_present --days 3650 --dry-run --verbose

    # Backfill a 10-year window
    python manage.py attendance_auto_present --days 3650

    # One specific date
    python manage.py attendance_auto_present --date 2026-09-01

    # One tenant only
    python manage.py attendance_auto_present --days 30 --schema ey
"""
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import schema_context

from axis_saas.models import (
    SchoolClient, SchoolClass, Student, StudentAttendance,
    StudentLeave, WeeklyHoliday, AnnualHoliday, Vacation,
)


class Command(BaseCommand):
    help = (
        "Auto-mark missed attendance as present for every active class "
        "in every tenant.  Holidays are skipped.  Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--date', help='Specific date YYYY-MM-DD (default: yesterday).',
        )
        parser.add_argument(
            '--days', type=int, default=1,
            help=(
                'How many days back to process ending yesterday '
                '(default: 1).  --date overrides this.'
            ),
        )
        parser.add_argument(
            '--schema', help='Only process this tenant schema.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help=(
                'Walk the whole decision tree and report how many rows '
                'WOULD be created, without writing anything.'
            ),
        )
        parser.add_argument(
            '--verbose', action='store_true',
            help=(
                'Print a per-tenant, per-date, per-class breakdown.  '
                'Use this first if the command seems to do nothing.'
            ),
        )

    def handle(self, *args, **options):
        verbose = bool(options.get('verbose'))
        dry_run = bool(options.get('dry_run'))
        today = timezone.localdate()

        # ---------------------------------------------------------- dates
        if options.get('date'):
            try:
                target = datetime.strptime(options['date'], '%Y-%m-%d').date()
            except ValueError:
                self.stderr.write(self.style.ERROR(
                    "Invalid --date; use YYYY-MM-DD."
                ))
                return
            dates = [target]
        else:
            days = max(1, int(options.get('days') or 1))
            dates = [today - timedelta(days=i) for i in range(days, 0, -1)]

        window_start = dates[0].isoformat()
        window_end = dates[-1].isoformat()
        self.stdout.write(
            f"[AXIS] Today (local): {today.isoformat()}"
        )
        self.stdout.write(
            f"[AXIS] Window: {window_start} .. {window_end}  "
            f"({len(dates)} day(s))"
        )
        if dry_run:
            self.stdout.write(self.style.WARNING(
                "[AXIS] DRY-RUN — nothing will be written."
            ))

        # -------------------------------------------------------- tenants
        qs = SchoolClient.objects.exclude(schema_name='public')
        if options.get('schema'):
            qs = qs.filter(schema_name=options['schema'])
        tenant_list = list(qs)
        self.stdout.write(f"[AXIS] Tenants: {len(tenant_list)}")

        if not tenant_list:
            self.stdout.write(self.style.WARNING(
                "No tenants found.  SchoolClient.objects (public schema) "
                "returned no rows other than 'public'.  If you expected "
                "tenants, check that SchoolClient.objects works from a "
                "shell:\n"
                "  python manage.py shell -c "
                "\"from axis_saas.models import SchoolClient; "
                "print(SchoolClient.objects.values_list('schema_name', "
                "flat=True))\""
            ))
            return

        grand_created = 0
        grand_candidate_pairs = 0
        grand_classes = 0
        grand_students = 0

        for tenant in tenant_list:
            try:
                with schema_context(tenant.schema_name):
                    created, candidate_pairs, n_classes, n_students = (
                        self._process_tenant(dates, today, dry_run, verbose)
                    )
            except Exception as exc:
                self.stderr.write(self.style.ERROR(
                    f"  {tenant.schema_name}: FAILED — "
                    f"{exc.__class__.__name__}: {exc}"
                ))
                continue

            grand_created += created
            grand_candidate_pairs += candidate_pairs
            grand_classes += n_classes
            grand_students += n_students

            self.stdout.write(
                f"  {tenant.schema_name}: "
                f"classes={n_classes}  active_students={n_students}  "
                f"candidate(class,date)_pairs={candidate_pairs}  "
                f"{'would-create' if dry_run else 'created'}={created}"
            )

        # ------------------------------------------------------- summary
        self.stdout.write("")
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"[DRY-RUN] Would create {grand_created} row(s) across "
                f"{grand_candidate_pairs} candidate (class, date) "
                f"pair(s) in {len(tenant_list)} tenant(s)."
            ))
            if grand_created == 0:
                self.stdout.write(self.style.WARNING(
                    "Interpretation: every past non-holiday date in the "
                    "window already has at least one full-day "
                    "StudentAttendance row per class — the cron job has "
                    "nothing left to do.  Re-run WITHOUT --dry-run to "
                    "confirm; it will print the same 'nothing to do' "
                    "message."
                ))
        elif grand_created == 0:
            self.stdout.write(self.style.SUCCESS(
                "Nothing to auto-mark.  Every past non-holiday date in "
                "the window already has at least one full-day "
                "StudentAttendance row per class."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Done. Total rows auto-marked: {grand_created}"
            ))

    # ------------------------------------------------------------------

    def _is_holiday(self, on_date):
        dow = on_date.weekday()
        try:
            wh = WeeklyHoliday.objects.filter(day_of_week=dow).first()
            if wh:
                return True, f"Weekly holiday ({wh.label or 'Weekend'})"
        except Exception:
            pass
        try:
            ah = AnnualHoliday.objects.filter(
                month=on_date.month, day=on_date.day,
            ).first()
            if ah:
                return True, f"Annual holiday ({ah.label})"
        except Exception:
            pass
        try:
            vac = Vacation.objects.filter(
                start_date__lte=on_date, end_date__gte=on_date,
            ).first()
            if vac:
                return True, f"Vacation ({vac.name})"
        except Exception:
            pass
        return False, ''

    def _process_tenant(self, dates, today, dry_run, verbose):
        """Return (created, candidate_pairs, n_classes, n_students).

        candidate_pairs counts (class, date) pairs where at least one
        active student was unmarked and would need a row.
        """
        created = 0
        candidate_pairs = 0

        classes = list(SchoolClass.objects.filter(is_active=True))
        n_classes = len(classes)

        # Pre-fetch active student ids per class — one query per class.
        class_students = {}
        for cls in classes:
            ids = list(
                Student.objects
                .filter(school_class=cls, status='active')
                .values_list('id', flat=True)
            )
            if ids:
                class_students[cls.id] = ids
        n_students = sum(len(v) for v in class_students.values())

        if verbose:
            self.stdout.write(
                f"    [verbose] classes={n_classes}  "
                f"active_students={n_students}"
            )

        if not classes:
            return 0, 0, 0, 0

        for d in dates:
            if d >= today:
                continue

            is_hol, reason = self._is_holiday(d)
            if is_hol:
                if verbose:
                    self.stdout.write(f"    [verbose] {d}: holiday — {reason}")
                continue

            for cls in classes:
                student_ids = class_students.get(cls.id) or []
                if not student_ids:
                    continue

                already_marked = set(
                    StudentAttendance.objects
                    .filter(
                        school_class=cls,
                        date=d,
                        period_order__isnull=True,
                        student_id__in=student_ids,
                    )
                    .values_list('student_id', flat=True)
                )

                # Students on approved leave that day → mark excused, not
                # present.
                on_leave_ids = set(
                    StudentLeave.objects
                    .filter(
                        status='approved',
                        start_date__lte=d,
                        end_date__gte=d,
                        student_id__in=student_ids,
                    )
                    .values_list('student_id', flat=True)
                )

                missing = [
                    sid for sid in student_ids
                    if sid not in already_marked
                ]
                if not missing:
                    continue

                candidate_pairs += 1

                if dry_run:
                    created += len(missing)
                    if verbose:
                        self.stdout.write(
                            f"    [verbose] {d} {cls}: would-create "
                            f"{len(missing)} "
                            f"(leave={len(on_leave_ids)})"
                        )
                    continue

                # Small batch — creates are cheap; wrap in a transaction
                # so partial failures roll back per (class, date).
                with transaction.atomic():
                    for sid in missing:
                        status = (
                            'excused' if sid in on_leave_ids else 'present'
                        )
                        source = (
                            'auto_leave' if status == 'excused'
                            else 'auto_system'
                        )
                        remarks = (
                            'Auto-marked (student on approved leave)'
                            if status == 'excused'
                            else 'Auto-marked by system '
                                 '(no teacher/admin action)'
                        )
                        try:
                            StudentAttendance.objects.create(
                                student_id=sid,
                                school_class=cls,
                                date=d,
                                period_order=None,
                                status=status,
                                source=source,
                                marked_at=timezone.now(),
                                remarks=remarks,
                            )
                            created += 1
                        except Exception as exc:
                            if verbose:
                                self.stdout.write(
                                    f"    [verbose] {d} {cls} student="
                                    f"{sid}: create failed — {exc}"
                                )
                if verbose:
                    self.stdout.write(
                        f"    [verbose] {d} {cls}: created {len(missing)} "
                        f"(leave={len(on_leave_ids)})"
                    )

        return created, candidate_pairs, n_classes, n_students
