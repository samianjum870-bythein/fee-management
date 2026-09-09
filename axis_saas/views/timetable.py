"""
AXIS views – timetable module.
"""

import json
import logging
from datetime import datetime, time

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404
from django.contrib import messages
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    AcademicCalendar, Holiday, Period, TimetableEntry,
    SchoolClass, Subject, Staff
)
from .helpers import get_tenant, require_tenant_type, require_school_feature
from django.core.serializers.json import DjangoJSONEncoder

logger = logging.getLogger(__name__)

# ========== MAIN PAGE ==========
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def timetable_management(request, schema_name):
    """Main timetable management page."""
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        calendar, created = AcademicCalendar.objects.get_or_create(pk=1)
        periods = Period.objects.filter(academic_calendar=calendar).order_by('order')
        holidays = Holiday.objects.all().order_by('date')
        classes = SchoolClass.objects.filter(is_active=True).order_by('name', 'section')
        subjects = Subject.objects.filter(is_active=True).order_by('name')
        teachers = Staff.objects.filter(status='active').order_by('full_name')

        # Pre-fill working days as list
        working_days = calendar.working_days or []

    context = {
        'tenant': tenant,
        'calendar': calendar,
        'periods': periods,
        'holidays': holidays,
        'classes': classes,
        'subjects': subjects,
        'teachers': teachers,
        'working_days': working_days,
        'days_of_week': TimetableEntry.DAY_CHOICES,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
    return render(request, 'tenant/timetable_management.html', context)


# ========== API ENDPOINTS ==========

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_update_calendar(request, schema_name):
    """Update Academic Calendar settings."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    with schema_context(schema_name):
        calendar, _ = AcademicCalendar.objects.get_or_create(pk=1)
        if 'working_days' in data:
            calendar.working_days = data['working_days']
        if 'school_start_time' in data:
            calendar.school_start_time = data['school_start_time']
        if 'school_end_time' in data:
            calendar.school_end_time = data['school_end_time']
        if 'period_duration' in data:
            calendar.period_duration = int(data['period_duration'])
        calendar.save()
        return JsonResponse({'success': True, 'calendar': {'id': calendar.pk, 'working_days': calendar.working_days, 'school_start_time': calendar.school_start_time.isoformat(), 'school_end_time': calendar.school_end_time.isoformat(), 'period_duration': calendar.period_duration}})


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_add_holiday(request, schema_name):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    date_str = data.get('date')
    name = data.get('name', '').strip()
    is_recurring = data.get('is_recurring', False)

    if not date_str or not name:
        return JsonResponse({'error': 'Date and name are required'}, status=400)

    try:
        holiday_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Invalid date format'}, status=400)

    with schema_context(schema_name):
        holiday, created = Holiday.objects.get_or_create(date=holiday_date, defaults={'name': name, 'is_recurring': is_recurring})
        if not created:
            return JsonResponse({'error': 'Holiday already exists on this date'}, status=400)
        return JsonResponse({'success': True, 'holiday': {'id': holiday.pk, 'date': holiday.date.isoformat(), 'name': holiday.name, 'is_recurring': holiday.is_recurring}})


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_holiday(request, schema_name):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    holiday_id = data.get('id')
    if not holiday_id:
        return JsonResponse({'error': 'Holiday ID required'}, status=400)

    with schema_context(schema_name):
        holiday = get_object_or_404(Holiday, pk=holiday_id)
        holiday.delete()
        return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_add_period(request, schema_name):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    order = data.get('order')
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    name = data.get('name', '').strip()

    if not all([order, start_time, end_time]):
        return JsonResponse({'error': 'Order, start_time, and end_time are required'}, status=400)

    try:
        start = datetime.strptime(start_time, '%H:%M').time()
        end = datetime.strptime(end_time, '%H:%M').time()
    except ValueError:
        return JsonResponse({'error': 'Invalid time format (use HH:MM)'}, status=400)

    if start >= end:
        return JsonResponse({'error': 'Start time must be before end time'}, status=400)

    with schema_context(schema_name):
        calendar, _ = AcademicCalendar.objects.get_or_create(pk=1)
        try:
            period = Period.objects.create(
                academic_calendar=calendar,
                order=order,
                start_time=start,
                end_time=end,
                name=name
            )
            return JsonResponse({
                'success': True,
                'period': {
                    'id': period.pk,
                    'order': period.order,
                    'start_time': period.start_time.strftime('%H:%M'),
                    'end_time': period.end_time.strftime('%H:%M'),
                    'name': period.name
                }
            })
        except IntegrityError:
            return JsonResponse({'error': 'A period with this order already exists'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_period(request, schema_name):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    period_id = data.get('id')
    if not period_id:
        return JsonResponse({'error': 'Period ID required'}, status=400)

    with schema_context(schema_name):
        period = get_object_or_404(Period, pk=period_id)
        # Check if any timetable entries use this period
        if TimetableEntry.objects.filter(period=period).exists():
            return JsonResponse({'error': 'Cannot delete period that is used in timetable'}, status=400)
        period.delete()
        return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_update_period(request, schema_name):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    period_id = data.get('id')
    if not period_id:
        return JsonResponse({'error': 'Period ID required'}, status=400)

    order = data.get('order')
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    name = data.get('name', '').strip()

    if not all([order, start_time, end_time]):
        return JsonResponse({'error': 'Order, start_time, and end_time are required'}, status=400)

    try:
        start = datetime.strptime(start_time, '%H:%M').time()
        end = datetime.strptime(end_time, '%H:%M').time()
    except ValueError:
        return JsonResponse({'error': 'Invalid time format (use HH:MM)'}, status=400)

    if start >= end:
        return JsonResponse({'error': 'Start time must be before end time'}, status=400)

    with schema_context(schema_name):
        period = get_object_or_404(Period, pk=period_id)
        # Check if order change causes conflict
        if period.order != order:
            if Period.objects.filter(academic_calendar=period.academic_calendar, order=order).exists():
                return JsonResponse({'error': 'A period with this order already exists'}, status=400)
        period.order = order
        period.start_time = start
        period.end_time = end
        period.name = name
        period.save()
        return JsonResponse({
            'success': True,
            'period': {
                'id': period.pk,
                'order': period.order,
                'start_time': period.start_time.strftime('%H:%M'),
                'end_time': period.end_time.strftime('%H:%M'),
                'name': period.name
            }
        })


@require_http_methods(["GET"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_get_timetable(request, schema_name):
    """Fetch timetable entries for a given class and day."""
    class_id = request.GET.get('class_id')
    day = request.GET.get('day')
    if not class_id or day is None:
        return JsonResponse({'error': 'class_id and day are required'}, status=400)

    try:
        day = int(day)
    except ValueError:
        return JsonResponse({'error': 'Invalid day'}, status=400)

    with schema_context(schema_name):
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        entries = TimetableEntry.objects.filter(
            school_class=school_class,
            day_of_week=day
        ).select_related('period', 'subject', 'teacher').order_by('period__order')

        data = []
        for entry in entries:
            data.append({
                'id': entry.pk,
                'period_id': entry.period.pk,
                'period_order': entry.period.order,
                'subject_id': entry.subject.pk,
                'subject_name': entry.subject.name,
                'teacher_id': entry.teacher.pk,
                'teacher_name': entry.teacher.full_name,
                'academic_year': entry.academic_year,
            })
        return JsonResponse({'entries': data})


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_save_timetable(request, schema_name):
    """
    Save timetable entries for a class and day.
    Expects JSON: { class_id, day, entries: [ { period_id, subject_id, teacher_id, academic_year } ] }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    class_id = data.get('class_id')
    day = data.get('day')
    entries_data = data.get('entries', [])

    if not class_id or day is None:
        return JsonResponse({'error': 'class_id and day are required'}, status=400)

    try:
        day = int(day)
    except ValueError:
        return JsonResponse({'error': 'Invalid day'}, status=400)

    with schema_context(schema_name):
        school_class = get_object_or_404(SchoolClass, pk=class_id)

        # Validate that we have all required fields
        for entry in entries_data:
            if not all(k in entry for k in ('period_id', 'subject_id', 'teacher_id')):
                return JsonResponse({'error': 'Each entry must have period_id, subject_id, teacher_id'}, status=400)

        # Use transaction to ensure consistency
        try:
            with transaction.atomic():
                # Delete existing entries for this class and day
                TimetableEntry.objects.filter(school_class=school_class, day_of_week=day).delete()

                # Create new entries
                new_entries = []
                for entry_data in entries_data:
                    period = get_object_or_404(Period, pk=entry_data['period_id'])
                    subject = get_object_or_404(Subject, pk=entry_data['subject_id'])
                    teacher = get_object_or_404(Staff, pk=entry_data['teacher_id'])
                    academic_year = entry_data.get('academic_year', '')

                    # Clash detection: check if teacher is already assigned to another class at the same day and period
                    clash = TimetableEntry.objects.filter(
                        teacher=teacher,
                        day_of_week=day,
                        period=period
                    ).exclude(school_class=school_class)  # exclude current class
                    if clash.exists():
                        # Return error with details
                        clash_class = clash.first().school_class
                        return JsonResponse({
                            'error': f"Teacher '{teacher.full_name}' is already assigned to {clash_class} at this period."
                        }, status=400)

                    # Check if subject is already assigned to another class? Not necessary, but we can allow multiple classes same subject.

                    new_entries.append(
                        TimetableEntry(
                            school_class=school_class,
                            day_of_week=day,
                            period=period,
                            subject=subject,
                            teacher=teacher,
                            academic_year=academic_year
                        )
                    )

                TimetableEntry.objects.bulk_create(new_entries)

            return JsonResponse({'success': True})
        except ValidationError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception as e:
            logger.exception("Error saving timetable")
            return JsonResponse({'error': 'Internal server error'}, status=500)

