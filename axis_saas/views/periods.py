import json
import logging
from datetime import datetime, date, timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context
from ..models import DaySchedule, AcademicCalendar
from .helpers import get_tenant, require_tenant_type, require_school_feature

logger = logging.getLogger(__name__)


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def periods_management(request, schema_name):
    """Main page for managing periods (lectures) and breaks."""
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        calendar, _ = AcademicCalendar.objects.get_or_create(pk=1)
        schedules = DaySchedule.objects.filter(academic_calendar=calendar).order_by('day_of_week', 'order')
        # Group by start_time, end_time to form bunches
        bunches = {}
        for ds in schedules:
            key = (ds.start_time, ds.end_time)
            bunches.setdefault(key, []).append(ds)
        # Sort each bunch by day_of_week
        bunch_data = []
        for (start, end), items in bunches.items():
            items.sort(key=lambda x: x.day_of_week)
            max_periods = max(ds.periods for ds in items) if items else 0
            bunch_data.append({
                'start_time': start,
                'end_time': end,
                'days': items,
                'max_periods': max_periods,
            })
        day_choices = DaySchedule.DAY_CHOICES
        used_days = set(schedules.values_list('day_of_week', flat=True))
    context = {
        'tenant': tenant,
        'bunches': bunch_data,
        'day_choices': day_choices,
        'used_days': used_days,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
    return render(request, 'tenant/timetable_periods.html', context)


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_update_break(request, schema_name):
    """Update break_after and break_duration for a DaySchedule."""
    try:
        data = json.loads(request.body)
    except:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    schedule_id = data.get('id')
    break_after = data.get('break_after')
    break_duration = data.get('break_duration')
    if schedule_id is None:
        return JsonResponse({'error': 'id required'}, status=400)
    with schema_context(schema_name):
        try:
            ds = DaySchedule.objects.get(id=schedule_id)
        except DaySchedule.DoesNotExist:
            return JsonResponse({'error': 'Schedule not found'}, status=404)
        if break_after is not None:
            ds.break_after = break_after
        if break_duration is not None:
            ds.break_duration = break_duration
        ds.save(update_fields=['break_after', 'break_duration'])
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Unexpected'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_add_bunch(request, schema_name):
    """Create a new bunch: add DaySchedule entries for selected days with same start/end."""
    try:
        data = json.loads(request.body)
    except:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    days = data.get('days', [])
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    periods = data.get('periods')
    duration = data.get('duration')
    label = data.get('label', '')
    if not days or not start_time or not end_time or periods is None or duration is None:
        return JsonResponse({'error': 'Missing required fields'}, status=400)
    with schema_context(schema_name):
        calendar, _ = AcademicCalendar.objects.get_or_create(pk=1)
        created_ids = []
        for day in days:
            ds, created = DaySchedule.objects.get_or_create(
                academic_calendar=calendar,
                day_of_week=day,
                start_time=start_time,
                end_time=end_time,
                defaults={
                    'order': 0,
                    'label': label,
                    'periods': periods,
                    'duration': duration,
                    'is_active': True,
                }
            )
            if not created:
                ds.periods = periods
                ds.duration = duration
                ds.label = label
                ds.save(update_fields=['periods', 'duration', 'label'])
            created_ids.append(ds.id)
        return JsonResponse({'success': True, 'created': created_ids})
    return JsonResponse({'error': 'Unexpected'}, status=500)
