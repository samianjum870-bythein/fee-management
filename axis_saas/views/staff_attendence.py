"""
AXIS views - Staff (class teacher) Attendance.

Only CLASS TEACHERS can access these endpoints. A staff member who is
a subject teacher but not a class teacher receives 403 (and the tab is
not shown to them at all).

Reuses StudentAttendance model. Provides:
  * today's / any date's marking for the class-teacher's own classes
  * list of missed school days in the last 30 days so past attendance
    can be filled in
  * history view of their class's records
"""
import json
import logging
from datetime import datetime, timedelta

from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    SchoolClass, Student, Staff, StudentAttendance, WeeklyHoliday,
)
from .staff_portal import require_staff_login, require_staff_feature


logger = logging.getLogger(__name__)
ATTENDANCE_STATUSES = ('present', 'absent', 'late', 'holiday')


def _parse_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _parse_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _class_teacher_classes(staff):
    """Only classes where `staff` is the assigned class teacher."""
    return SchoolClass.objects.filter(
        class_teacher=staff, is_active=True
    ).order_by('name', 'section')


def _is_weekly_holiday(d):
    try:
        return WeeklyHoliday.objects.filter(day_of_week=d.weekday()).exists()
    except Exception:
        return False


# ------------------------------------------------------------------ page

@require_staff_login
@require_staff_feature('staff_attendance')
def staff_attendance_view(request):
    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        classes = list(_class_teacher_classes(staff))
        if not classes:
            return render(request, 'mobile/staff/403.html', status=403)
        class_data = [{
            'id': c.id,
            'name': str(c),
            'student_count': Student.objects.filter(
                school_class=c, status='active'
            ).count(),
        } for c in classes]
        today = timezone.localdate()
        today_marked = StudentAttendance.objects.filter(
            school_class__in=classes, date=today
        ).count()
    context = {
        'staff': staff,
        'classes_json': json.dumps(class_data),
        'today': today.isoformat(),
        'today_marked': today_marked,
    }
    response = render(request, 'mobile/staff/attendence.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


# ------------------------------------------------- students for a date

@require_staff_login
@require_staff_feature('staff_attendance')
def staff_attendance_students_api(request):
    class_id = _parse_int(request.GET.get('class_id'))
    att_date = _parse_date(request.GET.get('date'))
    if not class_id or not att_date:
        return JsonResponse(
            {'ok': False, 'error': 'class_id and date required'},
            status=400,
        )

    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if not staff:
            return JsonResponse(
                {'ok': False, 'error': 'Staff not found'}, status=404,
            )
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True, class_teacher=staff,
        ).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'You are not the class teacher of this class.'},
                status=403,
            )
        students = list(
            Student.objects
            .filter(school_class=school_class, status='active')
            .order_by('roll_number', 'name')
        )
        marks = {
            m['student_id']: m
            for m in StudentAttendance.objects.filter(
                school_class=school_class, date=att_date
            ).values('student_id', 'status', 'remarks')
        }
        payload = []
        for s in students:
            m = marks.get(s.id)
            payload.append({
                'id': s.id,
                'roll_number': s.roll_number or '',
                'name': s.name,
                'status': m['status'] if m else 'present',
                'remarks': m['remarks'] if m else '',
                'already_marked': bool(m),
            })
    return JsonResponse({
        'ok': True,
        'date': att_date.isoformat(),
        'class_id': school_class.id,
        'class_name': str(school_class),
        'students': payload,
    })


# ----------------------------------------------------------- mark / update

@require_staff_login
@require_http_methods(['POST'])
@require_staff_feature('staff_attendance')
def staff_attendance_mark_api(request):
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'error': 'Invalid JSON'}, status=400,
        )
    class_id = _parse_int(payload.get('class_id'))
    att_date = _parse_date(payload.get('date'))
    records = payload.get('records') or []
    if not class_id or not att_date or not isinstance(records, list):
        return JsonResponse(
            {'ok': False,
             'error': 'class_id, date and records[] required'},
            status=400,
        )
    if att_date > timezone.localdate():
        return JsonResponse(
            {'ok': False, 'error': 'Cannot mark attendance for future dates.'},
            status=400,
        )

    schema_name = request.session['staff_schema_name']
    saved = 0
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if not staff:
            return JsonResponse(
                {'ok': False, 'error': 'Staff not found'}, status=404,
            )
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True, class_teacher=staff,
        ).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'You are not the class teacher of this class.'},
                status=403,
            )
        student_ids = set(
            Student.objects
            .filter(school_class=school_class, status='active')
            .values_list('id', flat=True)
        )
        for rec in records:
            sid = _parse_int(rec.get('student_id'))
            if sid not in student_ids:
                continue
            status = (rec.get('status') or 'present').strip().lower()
            if status not in ATTENDANCE_STATUSES:
                status = 'present'
            remarks = (rec.get('remarks') or '')[:500]
            StudentAttendance.objects.update_or_create(
                student_id=sid,
                date=att_date,
                defaults={
                    'school_class': school_class,
                    'status': status,
                    'teacher': staff,
                    'remarks': remarks,
                },
            )
            saved += 1
    return JsonResponse({'ok': True, 'saved': saved})


# ----------------------------------------------------------- history list

@require_staff_login
@require_staff_feature('staff_attendance')
def staff_attendance_records_api(request):
    class_id = _parse_int(request.GET.get('class_id'))
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    status = (request.GET.get('status') or '').strip()

    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if not staff:
            return JsonResponse(
                {'ok': False, 'error': 'Staff not found'}, status=404,
            )
        my_class_ids = list(
            _class_teacher_classes(staff).values_list('id', flat=True)
        )
        if not my_class_ids:
            return JsonResponse({'ok': True, 'records': []})
        qs = (
            StudentAttendance.objects
            .filter(school_class_id__in=my_class_ids)
            .select_related('student', 'school_class')
            .order_by('-date', 'student__roll_number')
        )
        if class_id and class_id in my_class_ids:
            qs = qs.filter(school_class_id=class_id)
        if status in ATTENDANCE_STATUSES:
            qs = qs.filter(status=status)
        if start_date:
            qs = qs.filter(date__gte=start_date)
        if end_date:
            qs = qs.filter(date__lte=end_date)
        rows = list(qs[:500])
        data = [{
            'id': r.id,
            'date': r.date.isoformat(),
            'student_name': r.student.name if r.student else '',
            'roll_number': r.student.roll_number if r.student else '',
            'class_name': str(r.school_class) if r.school_class else '',
            'status': r.status,
            'remarks': r.remarks or '',
        } for r in rows]
    return JsonResponse({'ok': True, 'records': data})


# ---------------------------------------------------------- missed days

@require_staff_login
@require_staff_feature('staff_attendance')
def staff_attendance_missed_days_api(request):
    class_id = _parse_int(request.GET.get('class_id'))
    if not class_id:
        return JsonResponse(
            {'ok': False, 'error': 'class_id required'}, status=400,
        )

    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if not staff:
            return JsonResponse(
                {'ok': False, 'error': 'Staff not found'}, status=404,
            )
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True, class_teacher=staff,
        ).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'Not your class'}, status=403,
            )
        today = timezone.localdate()
        marked_dates = set(
            StudentAttendance.objects
            .filter(school_class=school_class)
            .values_list('date', flat=True)
        )
        missed = []
        for i in range(30):
            d = today - timedelta(days=i)
            if _is_weekly_holiday(d):
                continue
            if d not in marked_dates:
                missed.append(d.isoformat())
    return JsonResponse({
        'ok': True,
        'missed_days': missed,
        'class_id': class_id,
    })
