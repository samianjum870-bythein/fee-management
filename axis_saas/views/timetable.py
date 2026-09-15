# axis_saas/views/timetable.py
import json
import logging
from datetime import datetime, date, timedelta

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    AcademicCalendar, Holiday, Period, TimetableEntry,
    SchoolClass, Subject, Staff, DaySchedule,
    WeeklyHoliday, AnnualHoliday, Vacation, ScheduleLabel,
    PeriodsTimetable,
)
from .helpers import get_tenant, require_tenant_type, require_school_feature

logger = logging.getLogger(__name__)
# ASSIGN_TEACHERS_HARDENING_V3: all POST endpoints in this file
# are no longer @csrf_exempt. The frontends already send
# X-CSRFToken + X-Requested-With.


# ---------- Helper for annual holiday next occurrence ----------
def get_next_occurrence(month: int, day: int) -> date:
    """Return the next date when this month/day occurs from today."""
    today = date.today()
    try:
        # Try this year
        candidate = date(today.year, month, day)
        if candidate >= today:
            return candidate
        # Try next year
        return date(today.year + 1, month, day)
    except ValueError:
        # Invalid date (e.g., Feb 30) – fallback to last day of month
        if month == 2 and day > 29:
            day = 28
        elif day > 30 and month in (4, 6, 9, 11):
            day = 30
        try:
            return date(today.year, month, day)
        except ValueError:
            return date(today.year, month, 1)


# ========== MAIN PAGE ==========
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def timetable_management(request, schema_name):
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        calendar, _ = AcademicCalendar.objects.get_or_create(pk=1)
        periods = Period.objects.filter(academic_calendar=calendar).order_by('order')
        holidays = Holiday.objects.all().order_by('date')
        classes = SchoolClass.objects.filter(is_active=True).order_by('name', 'section')
        subjects = Subject.objects.filter(is_active=True).order_by('name')
        teachers = Staff.objects.filter(status='active').order_by('full_name')

        # Weekly holidays - with fallback for missing table
        try:
            weekly_holidays = WeeklyHoliday.objects.all().order_by('day_of_week')
            weekly_holiday_days = [wh.day_of_week for wh in weekly_holidays]
        except Exception:
            weekly_holidays = []
            weekly_holiday_days = []

        # Annual holidays
        try:
            annual_holidays = AnnualHoliday.objects.all().order_by('month', 'day')
            for ah in annual_holidays:
                ah.next_occurrence = get_next_occurrence(ah.month, ah.day)
        except Exception:
            annual_holidays = []

        # Vacations
        next_vacation = None   # <- ALWAYS defined, prevents NameError on 500
        try:
            vacations = list(Vacation.objects.all().order_by('start_date'))
            # Compute total days for each vacation
            for vac in vacations:
                vac.total_days = (vac.end_date - vac.start_date).days + 1
            # Compute next upcoming vacation
            today = date.today()
            for vac in vacations:
                if vac.end_date >= today:
                    next_vacation = vac
                    break
        except Exception as e:
            logger.warning(f"Failed to fetch vacations: {e}")
            vacations = []

        # ---- Compute vacation statuses ----
        today = date.today()
        vacations = vacations or []
        for vac in vacations:
            if vac.end_date < today:
                vac.status = 'past'
            elif vac.start_date <= today <= vac.end_date:
                vac.status = 'current'
            elif (vac.start_date - today).days <= 10:
                vac.status = 'upcoming'
            else:
                vac.status = 'future'

        # ---- Schedule labels (reusable fixed labels) ----
        try:
            schedule_labels = list(ScheduleLabel.objects.all().order_by('name'))
        except Exception as e:
            logger.warning(f"ScheduleLabel fetch failed: {e}")
            schedule_labels = []

        # Available days for slot selection (exclude weekly holidays)
        all_days = TimetableEntry.DAY_CHOICES
        available_days = [(val, label) for val, label in all_days if val not in weekly_holiday_days]

        available_weekly_days = [(val, label) for val, label in TimetableEntry.DAY_CHOICES if val not in weekly_holiday_days]

        months = [f"{i:02d}" for i in range(1, 13)]
        days = [f"{i:02d}" for i in range(1, 32)]
        subjects_json = [{'id': s.id, 'name': s.name} for s in subjects]
        teachers_json = [{'id': t.id, 'full_name': t.full_name} for t in teachers]

        all_day_schedules = DaySchedule.objects.filter(academic_calendar=calendar).order_by('day_of_week', 'order')
        day_schedules = {}
        slots_by_label = {}
        for ds in all_day_schedules:
            if ds.day_of_week not in weekly_holiday_days:
                day_schedules.setdefault(ds.day_of_week, []).append(ds)
            # TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX: label is a FK now.
            _lbl = ds.label.name if ds.label_id else ''
            if _lbl:
                slots_by_label.setdefault(_lbl, []).append({
                    'id': ds.id,
                    'day': ds.day_of_week,
                    'day_label': ds.get_day_of_week_display(),
                    'start': ds.start_time.strftime('%H:%M'),
                    'end': ds.end_time.strftime('%H:%M'),
                    'periods': ds.periods,
                    'duration': ds.duration,
                })

    with schema_context(schema_name):
        try:
            has_period_timetables = PeriodsTimetable.objects.exists()
        except Exception:
            # Table may not exist yet if migrations haven't run
            has_period_timetables = False

    context = {
        'tenant': tenant,
        'has_period_timetables': has_period_timetables,
        'calendar': calendar,
        'periods': periods,
        'holidays': holidays,
        'classes': classes,
        'subjects': subjects,
        'teachers': teachers,
        'subjects_json': subjects_json,
        'teachers_json': teachers_json,
        'day_schedules': day_schedules,
        'weekly_holidays': weekly_holidays,
        'annual_holidays': annual_holidays,
        'vacations': vacations,
        'next_vacation': next_vacation,
        'days_of_week': TimetableEntry.DAY_CHOICES,
        'available_days': available_days,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
        'today': date.today().isoformat(),
        'available_weekly_days': available_weekly_days,
        'schedule_labels': schedule_labels,
        'months': months,
        'days': days,
        'slots_by_label_json': json.dumps(slots_by_label),
    }
    return render(request, 'tenant/timetable_management.html', context)


# ========== API: SAVE DAY SCHEDULES ==========
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
@transaction.atomic  # TIMETABLE_SAVE_V3: all-or-nothing save
def api_save_day_schedules(request, schema_name):
    """Save day schedules using a diff (create / update / delete) strategy.

    TIMETABLE_SAVE_V2
    -----------------
    The previous implementation wiped EVERY DaySchedule row for the tenant
    and re-created them from scratch on every autosave. That was fragile:

      * primary keys churned on every save
      * concurrent edits clobbered each other
      * a mid-save failure left the calendar empty
      * break_after / break_duration were silently reset to defaults

    We now diff by the natural key (label.lower(), day_of_week) which the
    model already guarantees unique via the CI constraint, and only touch
    rows that actually changed.

    Duration is ALWAYS computed server-side as max(1, total_min // periods).
    Any duration the client sends is ignored on purpose — this keeps the DB
    internally consistent regardless of which UI path (Add Slot, Edit Slot,
    Edit Timing, Quick Fill) triggered the save.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    schedules_data = data.get('schedules', [])
    if not isinstance(schedules_data, list):
        return JsonResponse({'error': 'schedules must be a list'}, status=400)

    logger.info(f"Received {len(schedules_data)} day schedules for schema {schema_name}")

    with schema_context(schema_name):
        calendar, created = AcademicCalendar.objects.get_or_create(pk=1)
        if created:
            logger.info(f"Created new AcademicCalendar for schema {schema_name}")

        # ---------- Validate the full payload BEFORE touching the DB ----------
        seen = {}
        # TIMETABLE_FK_REFACTOR_V1: resolve each incoming label TEXT to a
        # ScheduleLabel row up-front, so later code can key on FK ids
        # instead of case-folded strings.
        label_by_row = {}
        for i, item in enumerate(schedules_data):
            lbl = (item.get('label') or '').strip()
            dy = item.get('day')
            start = item.get('start')
            end = item.get('end')
            periods_raw = item.get('periods')

            if not lbl:
                return JsonResponse({'error': f"Row {i + 1}: Label is required."}, status=400)
            if len(lbl) > 50:
                return JsonResponse({'error': f"Row {i + 1}: Label is too long (max 50 chars)."}, status=400)

            try:
                _schedule_label = ScheduleLabel.objects.get(name__iexact=lbl)
            except ScheduleLabel.DoesNotExist:
                return JsonResponse(
                    {'error': f"Row {i + 1}: Label '{lbl}' not found."},
                    status=400,
                )
            except ScheduleLabel.MultipleObjectsReturned:
                return JsonResponse(
                    {'error': f"Row {i + 1}: Multiple labels match '{lbl}'."},
                    status=400,
                )
            label_by_row[i] = _schedule_label
            if dy is None or dy == '':
                return JsonResponse({'error': f"Row {i + 1}: Day is required."}, status=400)
            try:
                dy_int = int(dy)
            except (TypeError, ValueError):
                return JsonResponse({'error': f"Row {i + 1}: Invalid day value."}, status=400)
            if dy_int < 0 or dy_int > 6:
                return JsonResponse({'error': f"Row {i + 1}: Day must be 0-6."}, status=400)
            if not start or not end:
                return JsonResponse({'error': f"Row {i + 1}: Start and End are required."}, status=400)
            try:
                start_t = datetime.strptime(start, '%H:%M').time()
                end_t = datetime.strptime(end, '%H:%M').time()
            except (ValueError, TypeError):
                return JsonResponse({'error': f"Row {i + 1}: Invalid time format (use HH:MM)."}, status=400)
            if start_t >= end_t:
                return JsonResponse({'error': f"Row {i + 1}: Start time must be before End time."}, status=400)
            try:
                periods_int = int(periods_raw)
            except (TypeError, ValueError):
                return JsonResponse({'error': f"Row {i + 1}: Invalid periods value."}, status=400)
            if periods_int < 1:
                return JsonResponse({'error': f"Row {i + 1}: Periods must be at least 1."}, status=400)

            # TIMETABLE_FK_REFACTOR_V1: key on the FK id, not the text.
            key = (dy_int, _schedule_label.pk)
            if key in seen:
                day_name = dict(TimetableEntry.DAY_CHOICES).get(dy_int, str(dy_int))
                return JsonResponse({
                    'error': f"Ye label '{lbl}' {day_name} ke liye pehle hi set hai. "
                             f"Koi doosra label ya doosra din chunain."
                }, status=400)
            seen[key] = i

        # ---------- Diff-based apply ----------
        existing_rows = list(DaySchedule.objects.filter(academic_calendar=calendar))

        # TIMETABLE_SAVE_V3: refuse to silently wipe the whole calendar when
        # the client sends an empty payload unless it explicitly says it just
        # deleted every row. This guards against a stale / corrupted DOM.
        allow_empty = bool(data.get('allow_empty', False))
        if not schedules_data and existing_rows and not allow_empty:
            return JsonResponse({
                'error': (
                    f"Empty schedules payload received, but {len(existing_rows)} "
                    f"slot(s) already exist. Refusing to delete them. "
                    f"Reload the page and try again."
                )
            }, status=400)

        # TIMETABLE_FK_REFACTOR_V1: natural key is (day, label_id). No more
        # case-variant duplicates can exist because the DB enforces it.
        existing_map = {}
        for ds in existing_rows:
            existing_map[(ds.day_of_week, ds.label_id)] = ds

        # ---------- Optimistic lock pre-check (TIMETABLE_OPTIMISTIC_LOCK_V1) ----------
        # The client round-trips each row's `updated_at` as
        # `client_updated_at`. If that timestamp differs from the DB's
        # current value for the same natural key, another session
        # already touched that row — refuse the whole save rather
        # than silently overwrite their change. Rows with NULL
        # updated_at (legacy) and rows with an empty client_updated_at
        # are treated as "no version info — allow overwrite" so
        # pre-V5 data doesn't hard-fail on first edit.
        conflicts = []
        for _idx, _item in enumerate(schedules_data):
            try:
                _day = int(_item.get('day'))
            except (TypeError, ValueError):
                continue
            _sl = label_by_row.get(_idx)
            if _sl is None:
                continue
            _key = (_day, _sl.pk)
            _ds = existing_map.get(_key)
            _label = _sl.name
            if _ds is None or not _ds.updated_at:
                continue
            _client_ver = (_item.get('client_updated_at') or '').strip()
            if not _client_ver:
                continue
            if _client_ver != _ds.updated_at.isoformat():
                _day_name = dict(TimetableEntry.DAY_CHOICES).get(_day, str(_day))
                conflicts.append(f"{_day_name} / {_label}")
        if conflicts:
            return JsonResponse({
                'error': (
                    'Another session modified these row(s) since this page '
                    'was loaded: ' + ', '.join(conflicts) +
                    '. Reload the page and re-apply your changes.'
                ),
                'conflicts': conflicts,
            }, status=409)

        incoming_keys = set()
        created_count = 0
        updated_count = 0

        for idx, item in enumerate(schedules_data):
            day = int(item.get('day'))
            _schedule_label = label_by_row[idx]
            start_time = datetime.strptime(item.get('start'), '%H:%M').time()
            end_time = datetime.strptime(item.get('end'), '%H:%M').time()
            periods_int = int(item.get('periods'))

            total_min = (end_time.hour * 60 + end_time.minute) - (start_time.hour * 60 + start_time.minute)
            duration_int = max(1, total_min // periods_int)

            key = (day, _schedule_label.pk)
            incoming_keys.add(key)

            ds = existing_map.get(key)
            if ds is None:
                DaySchedule.objects.create(
                    academic_calendar=calendar,
                    day_of_week=day,
                    order=idx,
                    label=_schedule_label,
                    start_time=start_time,
                    end_time=end_time,
                    periods=periods_int,
                    duration=duration_int,
                )
                created_count += 1
            else:
                # label FK cannot "change" for the same natural key, so it
                # is not part of the dirty check.
                changed = (
                    ds.start_time != start_time
                    or ds.end_time != end_time
                    or ds.periods != periods_int
                    or ds.duration != duration_int
                    or ds.order != idx
                )
                if changed:
                    ds.start_time = start_time
                    ds.end_time = end_time
                    ds.periods = periods_int
                    ds.duration = duration_int
                    ds.order = idx
                    _update_fields = ['start_time', 'end_time',
                                      'periods', 'duration', 'order']
                    if ds.break_after is not None and ds.break_after >= periods_int:
                        ds.break_after = None
                        _update_fields.append('break_after')
                    ds.save(update_fields=_update_fields)
                    updated_count += 1

        deleted_count = 0
        for key, ds in existing_map.items():
            if key not in incoming_keys:
                ds.delete()
                deleted_count += 1

        logger.info(
            f"Day schedules saved for schema {schema_name}: "
            f"created={created_count}, updated={updated_count}, deleted={deleted_count}"
        )

        # TIMETABLE_OPTIMISTIC_LOCK_V1: return per-row updated_at so the
        # client can refresh its DOM attributes and avoid false conflicts
        # on the next autosave.
        updated_at_map = {}
        try:
            for _ds in DaySchedule.objects.filter(academic_calendar=calendar):
                _k = f"{_ds.day_of_week}|{_ds.label_id}"
                updated_at_map[_k] = _ds.updated_at.isoformat() if _ds.updated_at else ''
        except Exception:
            updated_at_map = {}

    return JsonResponse({
        'success': True,
        'created': created_count,
        'updated': updated_count,
        'deleted': deleted_count,
        'updated_at_map': updated_at_map,
    })




# ========== EDIT_TIMING_v1 : batch-update timing for a label ==========
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
@transaction.atomic  # TIMETABLE_SAVE_V4: all-or-nothing batch
def api_batch_update_label_times(request, schema_name):
    """Batch-update start/end times for all DaySchedule rows of a label.

    Body: { "label": "Senior",
            "updates": [ { "day_of_week": 0, "start": "08:00", "end": "14:00" }, ... ] }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    label = (data.get('label') or '').strip()
    updates = data.get('updates') or []
    if not label:
        return JsonResponse({'error': 'Label is required'}, status=400)
    if not isinstance(updates, list) or not updates:
        return JsonResponse({'error': 'updates must be a non-empty list'}, status=400)

    with schema_context(schema_name):
        # TIMETABLE_OPTIMISTIC_LOCK_V1: refuse to silently no-op when a
        # stale client sends a label that no longer exists.
        try:
            _sched_label = ScheduleLabel.objects.get(name__iexact=label)
        except ScheduleLabel.DoesNotExist:
            return JsonResponse({'error': f"Label '{label}' not found"}, status=404)
        calendar, _ = AcademicCalendar.objects.get_or_create(pk=1)
        updated_count = 0
        for item in updates:
            try:
                day = int(item.get('day_of_week'))
                start_str = item.get('start')
                end_str = item.get('end')
            except (TypeError, ValueError):
                continue
            if day is None or not start_str or not end_str:
                continue
            try:
                start_time = datetime.strptime(start_str, '%H:%M').time()
                end_time = datetime.strptime(end_str, '%H:%M').time()
            except ValueError:
                continue
            if start_time >= end_time:
                return JsonResponse({'error': 'End time must be after start time for day ' + str(day)}, status=400)

            ds = DaySchedule.objects.filter(
                academic_calendar=calendar, label=_sched_label, day_of_week=day
            ).first()
            if not ds:
                continue
            ds.start_time = start_time
            ds.end_time = end_time
            total_min = (end_time.hour * 60 + end_time.minute) - (start_time.hour * 60 + start_time.minute)
            if total_min > 0 and ds.periods > 0:
                ds.duration = max(1, total_min // ds.periods)
            ds.save(update_fields=['start_time', 'end_time', 'duration'])
            updated_count += 1

        return JsonResponse({'success': True, 'updated': updated_count})
# ========== END EDIT_TIMING_v1 ==========


# ========== HOLIDAY API ENDPOINTS ==========
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_add_holiday(request, schema_name):
    """Add a new holiday (weekly, annual, or vacation)."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    htype = data.get('type')
    if htype not in ('weekly', 'annual', 'vacation'):
        return JsonResponse({'error': 'Invalid holiday type'}, status=400)

    with schema_context(schema_name):
        try:
            if htype == 'weekly':
                day = data.get('day_of_week')
                label = data.get('label', '').strip()
                if day is None or not label:
                    return JsonResponse({'error': 'day_of_week and label required'}, status=400)
                if WeeklyHoliday.objects.filter(day_of_week=day).exists():
                    return JsonResponse({'error': f'Day {day} already has a weekly holiday'}, status=400)
                holiday = WeeklyHoliday.objects.create(day_of_week=day, label=label)
                return JsonResponse({'success': True, 'id': holiday.id})

            elif htype == 'annual':
                month = data.get('month')
                day = data.get('day')
                label = data.get('label', '').strip()
                if month is None or day is None or not label:
                    return JsonResponse({'error': 'month, day, and label required'}, status=400)
                if AnnualHoliday.objects.filter(month=month, day=day).exists():
                    return JsonResponse({'error': 'Annual holiday for this date already exists'}, status=400)
                holiday = AnnualHoliday.objects.create(month=month, day=day, label=label)
                return JsonResponse({'success': True, 'id': holiday.id})

            elif htype == 'vacation':
                name = data.get('name', '').strip()
                start_date = data.get('start_date')
                end_date = data.get('end_date')
                description = data.get('description', '').strip()
                if not name or not start_date or not end_date:
                    return JsonResponse({'error': 'name, start_date, end_date required'}, status=400)
                try:
                    start = date.fromisoformat(start_date)
                    end = date.fromisoformat(end_date)
                except ValueError:
                    return JsonResponse({'error': 'Invalid date format (use YYYY-MM-DD)'}, status=400)
                if start > end:
                    return JsonResponse({'error': 'Start date must be before end date'}, status=400)

                # ---- Check for overlapping vacations ----
                overlapping = Vacation.objects.filter(
                    start_date__lte=end,
                    end_date__gte=start
                ).exists()
                if overlapping:
                    return JsonResponse({'error': 'The selected date range overlaps with an existing vacation.'}, status=400)
                vacation = Vacation.objects.create(
                    name=name,
                    start_date=start,
                    end_date=end,
                    description=description
                )
                return JsonResponse({'success': True, 'id': vacation.id})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Unexpected error'}, status=500)


@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_holiday(request, schema_name):
    """Delete a holiday by type and id."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    htype = data.get('type')
    hid = data.get('id')
    if not htype or not hid:
        return JsonResponse({'error': 'type and id required'}, status=400)

    with schema_context(schema_name):
        try:
            if htype == 'weekly':
                holiday = WeeklyHoliday.objects.get(id=hid)
                holiday.delete()
            elif htype == 'annual':
                holiday = AnnualHoliday.objects.get(id=hid)
                holiday.delete()
            elif htype == 'vacation':
                holiday = Vacation.objects.get(id=hid)
                holiday.delete()
            else:
                return JsonResponse({'error': 'Invalid type'}, status=400)
            return JsonResponse({'success': True})
        except (WeeklyHoliday.DoesNotExist, AnnualHoliday.DoesNotExist, Vacation.DoesNotExist):
            return JsonResponse({'error': 'Holiday not found'}, status=404)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_update_holiday(request, schema_name):
    """Update an existing holiday (weekly, annual, or vacation)."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    htype = data.get('type')
    hid = data.get('id')
    if not htype or not hid:
        return JsonResponse({'error': 'type and id required'}, status=400)

    with schema_context(schema_name):
        try:
            if htype == 'weekly':
                holiday = WeeklyHoliday.objects.get(id=hid)
                day = data.get('day_of_week')
                label = data.get('label', '').strip()
                if day is None or not label:
                    return JsonResponse({'error': 'day_of_week and label required'}, status=400)
                # Check uniqueness (skip self)
                if WeeklyHoliday.objects.filter(day_of_week=day).exclude(id=hid).exists():
                    return JsonResponse({'error': f'Day {day} already has a weekly holiday'}, status=400)
                holiday.day_of_week = day
                holiday.label = label
                holiday.save()
                return JsonResponse({'success': True, 'id': holiday.id})

            elif htype == 'annual':
                holiday = AnnualHoliday.objects.get(id=hid)
                month = data.get('month')
                day = data.get('day')
                label = data.get('label', '').strip()
                if month is None or day is None or not label:
                    return JsonResponse({'error': 'month, day, and label required'}, status=400)
                if AnnualHoliday.objects.filter(month=month, day=day).exclude(id=hid).exists():
                    return JsonResponse({'error': 'Annual holiday for this date already exists'}, status=400)
                holiday.month = month
                holiday.day = day
                holiday.label = label
                holiday.save()
                return JsonResponse({'success': True, 'id': holiday.id})

            elif htype == 'vacation':
                holiday = Vacation.objects.get(id=hid)
                name = data.get('name', '').strip()
                start_date = data.get('start_date')
                end_date = data.get('end_date')
                description = data.get('description', '').strip()
                if not name or not start_date or not end_date:
                    return JsonResponse({'error': 'name, start_date, end_date required'}, status=400)
                try:
                    start = date.fromisoformat(start_date)
                    end = date.fromisoformat(end_date)
                except ValueError:
                    return JsonResponse({'error': 'Invalid date format (use YYYY-MM-DD)'}, status=400)
                if start > end:
                    return JsonResponse({'error': 'Start date must be before end date'}, status=400)
                holiday.name = name
                holiday.start_date = start
                holiday.end_date = end
                holiday.description = description
                holiday.save()
                return JsonResponse({'success': True, 'id': holiday.id})

            else:
                return JsonResponse({'error': 'Invalid type'}, status=400)

        except (WeeklyHoliday.DoesNotExist, AnnualHoliday.DoesNotExist, Vacation.DoesNotExist):
            return JsonResponse({'error': 'Holiday not found'}, status=404)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Unexpected error'}, status=500)


# ========== OTHER STUB ENDPOINTS ==========
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_update_calendar(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)


@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_add_period(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)


@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_period(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)


@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_update_period(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)


@require_http_methods(["GET"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_get_timetable(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)


@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_save_timetable(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)


# ========== SCHEDULE LABEL API ENDPOINTS ==========

@csrf_exempt
@require_http_methods(["GET"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_list_labels(request, schema_name):
    with schema_context(schema_name):
        labels = [{'id': l.id, 'name': l.name, 'description': l.description or ''}
                  for l in ScheduleLabel.objects.all().order_by('name')]
    return JsonResponse({'labels': labels})


@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_add_label(request, schema_name):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    if not name:
        return JsonResponse({'error': 'Label name is required'}, status=400)
    if len(name) > 50:
        return JsonResponse({'error': 'Label name too long (max 50 chars)'}, status=400)
    with schema_context(schema_name):
        if ScheduleLabel.objects.filter(name__iexact=name).exists():
            return JsonResponse({'error': f"Label '{name}' already exists"}, status=400)
        lbl = ScheduleLabel.objects.create(name=name, description=description[:150])
        return JsonResponse({'success': True,
                             'label': {'id': lbl.id, 'name': lbl.name, 'description': lbl.description or ''}})


@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
@transaction.atomic  # TIMETABLE_LABEL_AUDIT_V1: rename must be all-or-nothing
def api_update_label(request, schema_name):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    lbl_id = data.get('id')
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    if not lbl_id:
        return JsonResponse({'error': 'id required'}, status=400)
    if not name:
        return JsonResponse({'error': 'Label name is required'}, status=400)
    if len(name) > 50:
        return JsonResponse({'error': 'Label name too long (max 50 chars)'}, status=400)
    with schema_context(schema_name):
        try:
            lbl = ScheduleLabel.objects.get(id=lbl_id)
        except ScheduleLabel.DoesNotExist:
            return JsonResponse({'error': 'Label not found'}, status=404)

        # TIMETABLE_FK_REFACTOR_V1: DaySchedule.label and
        # PeriodsTimetable.label are ForeignKeys, so renaming
        # ScheduleLabel.name is a single-row UPDATE. No cascade, no
        # collision guard, no post-write verification.
        if ScheduleLabel.objects.filter(name__iexact=name).exclude(id=lbl_id).exists():
            return JsonResponse({'error': f"Another label '{name}' already exists"}, status=400)

        old_name = (lbl.name or '').strip()

        lbl.name = name
        lbl.description = description[:150]
        lbl.save(update_fields=['name', 'description'])

        # TIMETABLE_LABEL_AUDIT_V1 endpoints used to live here; they were
        # the inspection half of the same problem. Both removed.

        return JsonResponse({
            'success': True,
            'label': {'id': lbl.id, 'name': lbl.name, 'description': lbl.description or ''},
            'cascaded': {
                'day_schedules_updated': 'auto (FK)',
                'timetables_updated': 'auto (FK)',
                'old_name': old_name,
                'new_name': name,
            },
        })


# TIMETABLE_FK_REFACTOR_V1: api_audit_labels() and api_repair_labels()
# have been deleted. They existed to detect and fix drift between
# ScheduleLabel.name and the two CharField copies. Those copies are now
# ForeignKeys, so drift is structurally impossible.


@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_label(request, schema_name):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    lbl_id = data.get('id')
    if not lbl_id:
        return JsonResponse({'error': 'id required'}, status=400)
    from django.db.models.deletion import ProtectedError
    with schema_context(schema_name):
        try:
            _lbl = ScheduleLabel.objects.get(id=lbl_id)
        except ScheduleLabel.DoesNotExist:
            return JsonResponse({'error': 'Label not found'}, status=404)

        # TIMETABLE_FK_REFACTOR_V1: on_delete=PROTECT on DaySchedule.label
        # and PeriodsTimetable.label means Django will refuse to delete a
        # label that is still referenced. Surface that as a clean 400
        # instead of a 500.
        try:
            _lbl.delete()
        except ProtectedError:
            return JsonResponse({
                'error': (
                    f'Label "{_lbl.name}" is still used by one or more '
                    f'day schedules or periods timetables. Remove those '
                    f'first, then delete the label.'
                ),
            }, status=400)
    return JsonResponse({'success': True})
