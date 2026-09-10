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
    WeeklyHoliday, AnnualHoliday, Vacation
)
from .helpers import get_tenant, require_tenant_type, require_school_feature

logger = logging.getLogger(__name__)


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
        for ds in all_day_schedules:
            if ds.day_of_week not in weekly_holiday_days:
                day_schedules.setdefault(ds.day_of_week, []).append(ds)

    context = {
        'tenant': tenant,
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
        'months': months,
        'days': days,
    }
    return render(request, 'tenant/timetable_management.html', context)


# ========== API: SAVE DAY SCHEDULES ==========
@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_save_day_schedules(request, schema_name):
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

        deleted_all, _ = DaySchedule.objects.filter(academic_calendar=calendar).delete()
        logger.info(f"Deleted {deleted_all} existing schedules for calendar {calendar.pk}")

        total_created = 0
        for idx, item in enumerate(schedules_data):
            day = item.get('day')
            start = item.get('start')
            end = item.get('end')
            periods = item.get('periods')
            duration = item.get('duration')
            label = item.get('label', '')

            if day is None or start is None or end is None or periods is None or duration is None:
                logger.warning(f"Skipping incomplete schedule at index {idx}")
                continue

            try:
                start_time = datetime.strptime(start, '%H:%M').time()
                end_time = datetime.strptime(end, '%H:%M').time()
                periods_int = int(periods)
                duration_int = int(duration)
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid data at index {idx}: {e}")
                continue

            if not label:
                return JsonResponse({"error": "Label is required"}, status=400)

            try:
                DaySchedule.objects.create(
                    academic_calendar=calendar,
                    day_of_week=day,
                    order=idx,
                    label=label,
                    start_time=start_time,
                    end_time=end_time,
                    periods=periods_int,
                    duration=duration_int
                )
                total_created += 1
            except Exception as e:
                logger.exception(f"Error creating DaySchedule for day {day}, index {idx}: {e}")
                return JsonResponse({'error': f'Failed to save day {day} slot {idx}: {str(e)}'}, status=500)

        logger.info(f"Successfully created {total_created} day schedules for schema {schema_name}")

    return JsonResponse({'success': True, 'created': total_created, 'deleted': deleted_all})


# ========== HOLIDAY API ENDPOINTS ==========
@csrf_exempt
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


@csrf_exempt
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


@csrf_exempt
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
@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_update_calendar(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_add_period(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_period(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)


@csrf_exempt
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


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_save_timetable(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)
