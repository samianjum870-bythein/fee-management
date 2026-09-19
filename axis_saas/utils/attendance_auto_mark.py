"""ATTENDANCE_AUTO_MARK_LAZY_V1
================================

Reusable lazy auto-mark for missed attendance.

This module extracts the same logic that ``management/commands/
attendance_auto_present.py`` uses, but wraps it in a small function
that any view can call. The rule is identical:

  * For every PAST day in the window (today is excluded), for every
    active class, find every active student who has NO full-day
    StudentAttendance row for that date, and create one.

  * Holidays are skipped entirely (weekly / annual / vacation).

  * Students on an APPROVED StudentLeave get ``status='excused'``
    instead of ``present``.

  * Existing rows are never touched. Idempotent.

Why it exists
-------------
The scheduled cron job ``attendance_auto_present`` is not always
configured on every deployment. Without it, a day where no human
marks attendance stays "missing" forever. The lazy trigger fires when
an admin or class teacher opens the attendance page, so the system
self-heals on the next visit.

Rate limit
----------
``trigger_lazy_auto_mark`` uses Redis ``cache.add`` to run at most
once per hour per tenant schema. If two workers race, exactly one
wins the lock; the other returns 0 immediately.
"""

import logging
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import schema_context

logger = logging.getLogger(__name__)

# How many past days we look back on every lazy trigger. Large enough
# to recover from a weekend + one day of downtime, small enough that
# the cost per trigger stays negligible (< 1 second per class).
LAZY_WINDOW_DAYS = 7

# Redis lock TTL. Keep it at 1 hour so the catch-up runs at most once
# per hour per tenant, regardless of how many pages the admin opens.
LAZY_LOCK_TTL = 3600


def _is_holiday(on_date):
    """Return (is_holiday, reason) for ``on_date`` in the current schema."""
    from axis_saas.models import WeeklyHoliday, AnnualHoliday, Vacation

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

    return False, ""


def auto_mark_missed_dates(schema_name, days_back=LAZY_WINDOW_DAYS, verbose=False):
    """Backfill full-day attendance for the last ``days_back`` days.

    Must be called while already inside ``schema_context(schema_name)``
    is NOT required — this function opens its own context.

    Returns the number of rows created.
    """
    from axis_saas.models import (
        SchoolClass, Student, StudentAttendance, StudentLeave,
    )

    if not schema_name or schema_name == 'public':
        return 0

    created = 0
    today = timezone.localdate()

    with schema_context(schema_name):
        classes = list(SchoolClass.objects.filter(is_active=True))
        if not classes:
            return 0

        # Walk from oldest to newest so logs read naturally.
        for i in range(days_back, 0, -1):
            d = today - timedelta(days=i)

            is_hol, reason = _is_holiday(d)
            if is_hol:
                if verbose:
                    logger.info(
                        'ATTENDANCE_AUTO_MARK_LAZY_V1: %s skipped (%s)',
                        d, reason,
                    )
                continue

            for cls in classes:
                student_ids = list(
                    Student.objects
                    .filter(school_class=cls, status='active')
                    .values_list('id', flat=True)
                )
                if not student_ids:
                    continue

                already = set(
                    StudentAttendance.objects
                    .filter(
                        school_class=cls,
                        date=d,
                        period_order__isnull=True,
                        student_id__in=student_ids,
                    )
                    .values_list('student_id', flat=True)
                )
                missing = [sid for sid in student_ids if sid not in already]
                if not missing:
                    continue

                on_leave = set(
                    StudentLeave.objects
                    .filter(
                        status='approved',
                        start_date__lte=d,
                        end_date__gte=d,
                        student_id__in=missing,
                    )
                    .values_list('student_id', flat=True)
                )

                with transaction.atomic():
                    for sid in missing:
                        try:
                            StudentAttendance.objects.create(
                                student_id=sid,
                                school_class=cls,
                                date=d,
                                period_order=None,
                                status=(
                                    'excused' if sid in on_leave else 'present'
                                ),
                                source=(
                                    'auto_leave' if sid in on_leave
                                    else 'auto_system'
                                ),
                                marked_at=timezone.now(),
                                remarks=(
                                    'Auto-marked (lazy catch-up)'
                                    if sid not in on_leave
                                    else 'Auto-marked (student on approved leave)'
                                ),
                            )
                            created += 1
                        except Exception as exc:
                            # Unique constraints + races land here silently.
                            if verbose:
                                logger.warning(
                                    'ATTENDANCE_AUTO_MARK_LAZY_V1: '
                                    'create failed (student=%s, date=%s): %s',
                                    sid, d, exc,
                                )

    return created


def trigger_lazy_auto_mark(schema_name, days_back=LAZY_WINDOW_DAYS):
    """Run the catch-up pass at most once per hour per tenant schema.

    Safe to call from any view. Never raises.
    """
    if not schema_name or schema_name == 'public':
        return 0

    key = f'attendance_auto_mark:last_run:{schema_name}'

    # ``cache.add`` returns True only if the key did NOT already exist,
    # so exactly one caller per hour wins the lock.
    try:
        got_lock = cache.add(
            key, timezone.now().isoformat(), timeout=LAZY_LOCK_TTL,
        )
    except Exception:
        # Cache backend down — degrade to "skip this run" rather than
        # hammering the DB on every page load.
        return 0

    if not got_lock:
        return 0

    try:
        created = auto_mark_missed_dates(
            schema_name, days_back=days_back, verbose=False,
        )
        if created:
            logger.info(
                'ATTENDANCE_AUTO_MARK_LAZY_V1: schema=%s created %s '
                'auto-marked row(s) for the last %s day(s)',
                schema_name, created, days_back,
            )
        return created
    except Exception:
        # Release the lock so the next page load can retry sooner.
        try:
            cache.delete(key)
        except Exception:
            pass
        logger.exception(
            'ATTENDANCE_AUTO_MARK_LAZY_V1: catch-up failed for schema=%s',
            schema_name,
        )
        return 0
