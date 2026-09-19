"""Staff list helpers - inline modal APIs (STAFF_LIST_MEGA_V1)."""

import json
import logging

from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    Staff, SchoolClass, Subject, ClassSubject, StaffCredential,
    LeaveRequest, LeaveSuspension,
)
from .helpers import (
    get_tenant, require_school_feature, require_tenant_type,
)

logger = logging.getLogger(__name__)


@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('staff_management')
def staff_subject_assignments_api(request, schema_name):
    """JSON: current ClassSubject rows + dropdown options."""
    with schema_context(schema_name):
        assignments = (
            ClassSubject.objects
            .select_related('school_class', 'subject', 'teacher')
            .order_by('school_class__name', 'school_class__section',
                      'subject__name')
        )
        rows = [{
            'id': a.id,
            'class_id': a.school_class_id,
            'class_name': str(a.school_class),
            'subject_id': a.subject_id,
            'subject_name': a.subject.name,
            'teacher_id': a.teacher_id,
            'teacher_name': a.teacher.full_name if a.teacher else '',
            'is_active': a.is_active,
        } for a in assignments]

        classes = list(
            SchoolClass.objects.filter(is_active=True)
            .order_by('name', 'section')
        )
        subjects = list(
            Subject.objects.filter(is_active=True).order_by('name')
        )
        teachers = list(
            Staff.objects.filter(status='active').order_by('full_name')
        )

    return JsonResponse({
        'ok': True,
        'rows': rows,
        'classes': [{'id': c.id, 'name': str(c)} for c in classes],
        'subjects': [{'id': s.id, 'name': s.name} for s in subjects],
        'teachers': [
            {'id': t.id, 'name': t.full_name, 'job_title': t.job_title or ''}
            for t in teachers
        ],
    })


@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('staff_management')
def staff_assign_subject_teacher_api(request, schema_name):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    class_id = body.get('class_id')
    subject_id = body.get('subject_id')
    teacher_id = body.get('teacher_id')

    if not class_id or not subject_id:
        return JsonResponse(
            {'ok': False, 'error': 'class_id and subject_id required'},
            status=400,
        )

    with schema_context(schema_name):
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if school_class is None:
            return JsonResponse(
                {'ok': False, 'error': 'Class not found'}, status=404,
            )
        subject = Subject.objects.filter(id=subject_id, is_active=True).first()
        if subject is None:
            return JsonResponse(
                {'ok': False, 'error': 'Subject not found'}, status=404,
            )

        cs, _ = ClassSubject.objects.get_or_create(
            school_class=school_class, subject=subject,
            defaults={'is_active': True},
        )

        if teacher_id in (None, '', 'null', 0, '0'):
            cs.teacher = None
        else:
            teacher = Staff.objects.filter(
                id=teacher_id, status='active',
            ).first()
            if teacher is None:
                return JsonResponse(
                    {'ok': False,
                     'error': 'Teacher not found or inactive'},
                    status=400,
                )
            cs.teacher = teacher

        cs.is_active = True
        cs.save()

        return JsonResponse({
            'ok': True,
            'row': {
                'id': cs.id,
                'class_id': school_class.id,
                'class_name': str(school_class),
                'subject_id': subject.id,
                'subject_name': subject.name,
                'teacher_id': cs.teacher_id,
                'teacher_name': cs.teacher.full_name if cs.teacher else '',
                'is_active': True,
            },
        })


@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('staff_management')
def staff_unassign_subject_teacher_api(request, schema_name):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    assignment_id = body.get('assignment_id')
    if not assignment_id:
        return JsonResponse(
            {'ok': False, 'error': 'assignment_id required'}, status=400,
        )

    with schema_context(schema_name):
        cs = ClassSubject.objects.filter(id=assignment_id).first()
        if cs is None:
            return JsonResponse(
                {'ok': False, 'error': 'Assignment not found'}, status=404,
            )
        cs.teacher = None
        cs.is_active = False
        cs.save(update_fields=['teacher', 'is_active'])
    return JsonResponse({'ok': True})


@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('staff_management')
def staff_class_teacher_management_api(request, schema_name):
    """JSON: every active class + its class teacher + candidates."""
    with schema_context(schema_name):
        classes = (
            SchoolClass.objects
            .filter(is_active=True)
            .select_related('class_teacher', 'wing_category',
                            'wing_category__parent')
            .order_by('name', 'section')
        )
        rows = [{
            'id': c.id,
            'name': str(c),
            'section': c.section or '',
            'wing': c.wing_category.name if c.wing_category_id else '',
            'wing_parent': (
                c.wing_category.parent.name
                if c.wing_category_id and c.wing_category.parent_id else ''
            ),
            'class_teacher_id': c.class_teacher_id,
            'class_teacher_name': (
                c.class_teacher.full_name if c.class_teacher_id else ''
            ),
            'student_count': c.students.count(),
        } for c in classes]

        teachers = list(
            Staff.objects.filter(status='active').order_by('full_name')
        )
        candidates = [
            {'id': t.id, 'name': t.full_name, 'job_title': t.job_title or ''}
            for t in teachers
        ]

    return JsonResponse({'ok': True, 'rows': rows, 'candidates': candidates})


@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('staff_management')
def staff_assign_class_teacher_api(request, schema_name):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    class_id = body.get('class_id')
    teacher_id = body.get('teacher_id')

    if not class_id:
        return JsonResponse(
            {'ok': False, 'error': 'class_id required'}, status=400,
        )

    with schema_context(schema_name):
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if school_class is None:
            return JsonResponse(
                {'ok': False, 'error': 'Class not found'}, status=404,
            )

        if teacher_id in (None, '', 'null', 0, '0'):
            school_class.class_teacher = None
        else:
            teacher = Staff.objects.filter(
                id=teacher_id, status='active',
            ).first()
            if teacher is None:
                return JsonResponse(
                    {'ok': False,
                     'error': 'Teacher not found or inactive'},
                    status=400,
                )
            # One-class-per-teacher rule (mirrors class_staff.py).
            SchoolClass.objects.filter(class_teacher=teacher).exclude(
                id=school_class.id,
            ).update(class_teacher=None)
            school_class.class_teacher = teacher

        school_class.save(update_fields=['class_teacher'])

        return JsonResponse({
            'ok': True,
            'class_id': school_class.id,
            'class_teacher_id': school_class.class_teacher_id,
            'class_teacher_name': (
                school_class.class_teacher.full_name
                if school_class.class_teacher_id else ''
            ),
        })


@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('staff_management')
def staff_quick_stats_api(request, schema_name):
    """JSON: lightweight analytics for the staff page header."""
    from datetime import timedelta
    today = timezone.localdate()

    with schema_context(schema_name):
        total = Staff.objects.count()
        active = Staff.objects.filter(status='active').count()
        on_leave_today = LeaveRequest.objects.filter(
            status='approved',
            start_date__lte=today, end_date__gte=today,
        ).values('staff_id').distinct().count()
        suspended = LeaveSuspension.objects.filter(
            is_active=True, start_date__lte=today,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=today),
        ).values('staff_id').distinct().count()
        class_teachers = SchoolClass.objects.filter(
            is_active=True, class_teacher__isnull=False,
        ).count()
        subject_assignments = ClassSubject.objects.filter(
            is_active=True, teacher__isnull=False,
        ).count()

    with schema_context('public'):
        with_credentials = StaffCredential.objects.filter(
            schema_name=schema_name, is_active=True,
        ).count()

    return JsonResponse({
        'ok': True,
        'total': total,
        'active': active,
        'inactive': total - active,
        'on_leave_today': on_leave_today,
        'suspended': suspended,
        'class_teachers': class_teachers,
        'subject_assignments': subject_assignments,
        'with_credentials': with_credentials,
    })
