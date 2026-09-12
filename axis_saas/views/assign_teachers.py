"""
AXIS views - assign periods to teachers (ASSIGN_TEACHERS_v1).

Lets the admin pick a class (that already has a periods timetable assigned)
and, for each period slot, choose a subject. The subject's teacher in this
class (from ClassSubject.teacher) is saved alongside.
"""
import json
import logging

from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    SchoolClass, ClassTimetableAssignment,
    ClassSubject, PeriodTeacherAssignment,
)
from .helpers import get_tenant, require_tenant_type, require_school_feature
from axis_saas.utils.class_display import get_class_display_name


logger = logging.getLogger(__name__)


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def timetable_assign_teachers(request, schema_name):
    """Main page: shows classes that have a periods timetable + a
    button to open the assignment modal."""
    tenant = get_tenant(request, schema_name)

    with schema_context(schema_name):
        class_assignments = list(
            ClassTimetableAssignment.objects
            .select_related('school_class__wing_category', 'timetable')
            .order_by('school_class__name', 'school_class__section')
        )

        class_rows = []
        for a in class_assignments:
            cls = a.school_class
            display = get_class_display_name(cls, tenant.tenant_type)
            total_periods = 0
            for d in (a.timetable.days or []):
                try:
                    total_periods += int(d.get('periods_count') or 0)
                except (TypeError, ValueError):
                    pass
            assigned_count = PeriodTeacherAssignment.objects.filter(
                school_class=cls, subject__isnull=False,
            ).count()
            class_rows.append({
                'class_id': cls.id,
                'class_display_name': display,
                'timetable_title': a.timetable.title,
                'timetable_label': a.timetable.label or '',
                'total_periods': total_periods,
                'assigned_count': assigned_count,
            })

        assigned_class_ids = {a.school_class_id for a in class_assignments}
        classes_without_timetable = []
        for c in SchoolClass.objects.filter(is_active=True).order_by('name', 'section'):
            if c.id not in assigned_class_ids:
                classes_without_timetable.append({
                    'id': c.id,
                    'display_name': get_class_display_name(c, tenant.tenant_type),
                })

    context = {
        'tenant': tenant,
        'class_rows': class_rows,
        'classes_without_timetable': classes_without_timetable,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
    response = render(request, 'tenant/timetable_assign_teachers.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_get_teacher_assignments(request, schema_name, class_id):
    """Return data needed to render the grid for a given class."""
    tenant = get_tenant(request, schema_name)

    with schema_context(schema_name):
        school_class = get_object_or_404(SchoolClass, id=class_id, is_active=True)
        assignment = (
            ClassTimetableAssignment.objects
            .select_related('timetable')
            .filter(school_class=school_class)
            .first()
        )

        class_display = get_class_display_name(school_class, tenant.tenant_type)

        if not assignment or not assignment.timetable:
            return JsonResponse({
                'has_timetable': False,
                'class_id': school_class.id,
                'class_display': class_display,
            })

        tt = assignment.timetable

        # Subjects of this class whose teacher is set (these are the only
        # valid choices for period assignment)
        subjects = []
        for cs in (
            ClassSubject.objects
            .filter(school_class=school_class, is_active=True, teacher__isnull=False)
            .select_related('subject', 'teacher')
            .order_by('subject__name')
        ):
            subjects.append({
                'subject_id': cs.subject_id,
                'subject_name': cs.subject.name,
                'teacher_id': cs.teacher_id,
                'teacher_name': cs.teacher.full_name if cs.teacher else '',
            })

        existing = {}
        for pta in PeriodTeacherAssignment.objects.filter(school_class=school_class):
            key = f"{pta.day_of_week}|{pta.period_order}"
            existing[key] = {
                'subject_id': pta.subject_id,
            }

        return JsonResponse({
            'has_timetable': True,
            'class_id': school_class.id,
            'class_display': class_display,
            'timetable_title': tt.title,
            'timetable_label': tt.label or '',
            'timetable_days': tt.days or [],
            'subjects': subjects,
            'existing': existing,
        })


@csrf_exempt
@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_save_teacher_assignments(request, schema_name, class_id):
    """Persist the full set of (day, period) -> subject assignments for a class."""
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    items = data.get('assignments', [])
    if not isinstance(items, list):
        return JsonResponse({'success': False, 'error': 'assignments must be a list'}, status=400)

    with schema_context(schema_name):
        school_class = get_object_or_404(SchoolClass, id=class_id, is_active=True)

        # Only subjects whose teacher is already set for this class are valid
        class_subject_teacher = {
            cs.subject_id: cs.teacher_id
            for cs in ClassSubject.objects.filter(
                school_class=school_class, is_active=True, teacher__isnull=False,
            )
        }

        existing_map = {
            (pta.day_of_week, pta.period_order): pta
            for pta in PeriodTeacherAssignment.objects.filter(school_class=school_class)
        }

        saved = 0
        removed = 0
        skipped = 0

        for item in items:
            try:
                day = int(item.get('day'))
                order = int(item.get('order'))
            except (TypeError, ValueError):
                skipped += 1
                continue

            subject_id_raw = item.get('subject_id')
            if subject_id_raw in (None, '', 'null', 0, '0'):
                subject_id = None
            else:
                try:
                    subject_id = int(subject_id_raw)
                except (TypeError, ValueError):
                    skipped += 1
                    continue

            key = (day, order)

            if subject_id is None:
                existing = existing_map.get(key)
                if existing is not None:
                    existing.delete()
                    removed += 1
                continue

            if subject_id not in class_subject_teacher:
                # Teacher not assigned for this subject in this class -> skip
                skipped += 1
                continue

            teacher_id = class_subject_teacher[subject_id]
            existing = existing_map.get(key)
            if existing is not None:
                existing.subject_id = subject_id
                existing.teacher_id = teacher_id
                existing.save(update_fields=['subject', 'teacher', 'updated_at'])
            else:
                PeriodTeacherAssignment.objects.create(
                    school_class=school_class,
                    day_of_week=day,
                    period_order=order,
                    subject_id=subject_id,
                    teacher_id=teacher_id,
                )
            saved += 1

        return JsonResponse({
            'success': True,
            'saved': saved,
            'removed': removed,
            'skipped': skipped,
        })
