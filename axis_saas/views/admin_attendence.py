"""
AXIS views - Admin Attendance Management.

The admin has full universal access to student attendance:
  * mark any class, any date (past or today)
  * review history with filters
  * view per-student history
  * correct historical records

Reuses the existing StudentAttendance model (student, school_class,
date, status, teacher, remarks). No schema change is required.
"""
import json
import logging
from datetime import datetime

from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import SchoolClass, Student, Staff, StudentAttendance
from .helpers import (
    get_tenant, require_tenant_type, require_school_feature,
)
from axis_saas.utils.class_display import get_class_display_name


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


# ------------------------------------------------------------------ page

@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_view(request, schema_name):
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        classes = list(
            SchoolClass.objects.filter(is_active=True)
            .select_related('wing_category', 'class_teacher')
            .order_by('name', 'section')
        )
        class_data = []
        for c in classes:
            class_data.append({
                'id': c.id,
                'name': get_class_display_name(c, tenant.tenant_type),
                'teacher': c.class_teacher.full_name if c.class_teacher else '',
                'student_count': Student.objects.filter(
                    school_class=c, status='active'
                ).count(),
            })
        today = timezone.localdate()
        today_records = StudentAttendance.objects.filter(date=today).count()
        total_students = Student.objects.filter(status='active').count()

    context = {
        'tenant': tenant,
        'classes_json': json.dumps(class_data),
        'today': today.isoformat(),
        'today_records': today_records,
        'total_students': total_students,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
    response = render(request, 'tenant/attendence.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


# ------------------------------------------------------- students for date

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_students_api(request, schema_name):
    class_id = _parse_int(request.GET.get('class_id'))
    att_date = _parse_date(request.GET.get('date'))
    if not class_id or not att_date:
        return JsonResponse(
            {'ok': False, 'error': 'class_id and date (YYYY-MM-DD) required'},
            status=400,
        )

    with schema_context(schema_name):
        school_class = SchoolClass.objects.filter(id=class_id, is_active=True).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'Class not found'}, status=404,
            )
        students = list(
            Student.objects.filter(school_class=school_class, status='active')
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
                'father_name': s.father_name or '',
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

@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_mark_api(request, schema_name):
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

    saved = 0
    with schema_context(schema_name):
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True
        ).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'Class not found'}, status=404,
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
                    'remarks': remarks,
                },
            )
            saved += 1
    return JsonResponse({'ok': True, 'saved': saved})


# ----------------------------------------------------------- history list

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_records_api(request, schema_name):
    class_id = _parse_int(request.GET.get('class_id'))
    student_id = _parse_int(request.GET.get('student_id'))
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    status = (request.GET.get('status') or '').strip()
    try:
        page = max(1, int(request.GET.get('page', '1') or 1))
    except ValueError:
        page = 1
    try:
        page_size = min(200, max(1, int(request.GET.get('page_size', '50') or 50)))
    except ValueError:
        page_size = 50

    with schema_context(schema_name):
        qs = (
            StudentAttendance.objects
            .select_related('student', 'school_class', 'teacher')
            .order_by('-date', 'student__roll_number')
        )
        if class_id:
            qs = qs.filter(school_class_id=class_id)
        if student_id:
            qs = qs.filter(student_id=student_id)
        if status in ATTENDANCE_STATUSES:
            qs = qs.filter(status=status)
        if start_date:
            qs = qs.filter(date__gte=start_date)
        if end_date:
            qs = qs.filter(date__lte=end_date)

        total = qs.count()
        offset = (page - 1) * page_size
        rows = list(qs[offset:offset + page_size])
        data = [{
            'id': r.id,
            'date': r.date.isoformat(),
            'student_id': r.student_id,
            'student_name': r.student.name if r.student else '',
            'roll_number': r.student.roll_number if r.student else '',
            'class_name': str(r.school_class) if r.school_class else '',
            'status': r.status,
            'remarks': r.remarks or '',
            'teacher_name': r.teacher.full_name if r.teacher else '',
        } for r in rows]
        num_pages = (total + page_size - 1) // page_size if page_size else 1
    return JsonResponse({
        'ok': True,
        'records': data,
        'pagination': {
            'page': page, 'page_size': page_size,
            'total': total, 'num_pages': num_pages,
        },
    })


# ----------------------------------------------------------- daily summary

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_summary_api(request, schema_name):
    target_date = _parse_date(request.GET.get('date')) or timezone.localdate()
    with schema_context(schema_name):
        qs = StudentAttendance.objects.filter(date=target_date)
        present = qs.filter(status='present').count()
        absent = qs.filter(status='absent').count()
        late = qs.filter(status='late').count()
        holiday = qs.filter(status='holiday').count()
        total_marked = present + absent + late + holiday
        total_students = Student.objects.filter(status='active').count()
        unmarked = max(0, total_students - total_marked)
        per_class = []
        for cls in SchoolClass.objects.filter(is_active=True).order_by('name', 'section'):
            ids = list(Student.objects.filter(
                school_class=cls, status='active'
            ).values_list('id', flat=True))
            cqs = StudentAttendance.objects.filter(
                date=target_date, student_id__in=ids
            )
            per_class.append({
                'class_id': cls.id,
                'class_name': str(cls),
                'total': len(ids),
                'marked': cqs.count(),
                'present': cqs.filter(status='present').count(),
                'absent': cqs.filter(status='absent').count(),
                'late': cqs.filter(status='late').count(),
            })
    return JsonResponse({
        'ok': True,
        'date': target_date.isoformat(),
        'summary': {
            'present': present, 'absent': absent,
            'late': late, 'holiday': holiday,
            'total_marked': total_marked,
            'total_students': total_students,
            'unmarked': unmarked,
        },
        'per_class': per_class,
    })


# --------------------------------------------------- per-student history

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_student_history_api(request, schema_name, student_id):
    with schema_context(schema_name):
        student = (
            Student.objects
            .filter(id=student_id)
            .select_related('school_class')
            .first()
        )
        if not student:
            return JsonResponse(
                {'ok': False, 'error': 'Student not found'}, status=404,
            )
        qs = (
            StudentAttendance.objects
            .filter(student=student)
            .select_related('teacher')
            .order_by('-date')[:365]
        )
        records = [{
            'date': r.date.isoformat(),
            'status': r.status,
            'remarks': r.remarks or '',
            'teacher': r.teacher.full_name if r.teacher else '',
        } for r in qs]
        agg = StudentAttendance.objects.filter(student=student).aggregate(
            present=Count('id', filter=Q(status='present')),
            absent=Count('id', filter=Q(status='absent')),
            late=Count('id', filter=Q(status='late')),
            holiday=Count('id', filter=Q(status='holiday')),
        )
    return JsonResponse({
        'ok': True,
        'student': {
            'id': student.id,
            'name': student.name,
            'roll_number': student.roll_number,
            'class_name': str(student.school_class) if student.school_class else '',
        },
        'records': records,
        'summary': agg,
    })
