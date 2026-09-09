# axis_saas/views/timetable.py
import json
import logging
from datetime import datetime

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context
from django.core.serializers import serialize

from ..models import (
    AcademicCalendar, Holiday, Period, TimetableEntry,
    SchoolClass, Subject, Staff, DaySchedule
)
from .helpers import get_tenant, require_tenant_type, require_school_feature

logger = logging.getLogger(__name__)


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

        subjects_json = [
            {'id': s.id, 'name': s.name}
            for s in subjects
        ]
        teachers_json = [
            {'id': t.id, 'full_name': t.full_name}
            for t in teachers
        ]

        day_schedules = {
            ds.day_of_week: ds for ds in DaySchedule.objects.filter(academic_calendar=calendar)
        }

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
        'days_of_week': TimetableEntry.DAY_CHOICES,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
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

        # Delete existing day schedules
        deleted_count = DaySchedule.objects.filter(academic_calendar=calendar).delete()[0]
        logger.info(f"Deleted {deleted_count} existing day schedules")

        created_count = 0
        for item in schedules_data:
            day = item.get('day')
            if day is None:
                continue
            start = item.get('start')
            end = item.get('end')
            periods = item.get('periods')
            duration = item.get('duration')
            if start is None or end is None or periods is None or duration is None:
                logger.warning(f"Skipping incomplete schedule for day {day}")
                continue
            try:
                start_time = datetime.strptime(start, '%H:%M').time()
                end_time = datetime.strptime(end, '%H:%M').time()
                periods_int = int(periods)
                duration_int = int(duration)
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid data for day {day}: {e}")
                continue

            try:
                DaySchedule.objects.create(
                    academic_calendar=calendar,
                    day_of_week=day,
                    start_time=start_time,
                    end_time=end_time,
                    periods=periods_int,
                    duration=duration_int
                )
                created_count += 1
            except Exception as e:
                logger.exception(f"Error creating DaySchedule for day {day}: {e}")
                return JsonResponse({'error': f'Failed to save day {day}: {str(e)}'}, status=500)

        logger.info(f"Successfully created {created_count} day schedules for schema {schema_name}")

    return JsonResponse({'success': True, 'created': created_count, 'deleted': deleted_count})


# ====== OTHER API ENDPOINTS (stubs for future use) ======
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
def api_add_holiday(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_holiday(request, schema_name):
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
