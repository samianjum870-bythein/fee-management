"""
AXIS views — class timetable assignments.

Lets the admin assign a PeriodsTimetable to a SchoolClass. Assignments
are stored in DB via the ClassTimetableAssignment model.

URL base: /portal/<schema>/timetable/assign/
"""
import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    SchoolClass, PeriodsTimetable, ClassTimetableAssignment,
)
from .helpers import get_tenant, require_tenant_type, require_school_feature
from axis_saas.utils.class_display import get_class_display_name


logger = logging.getLogger(__name__)
# ASSIGN_TEACHERS_HARDENING_V3: CSRF enforced on POST endpoints.


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def timetable_assignments(request, schema_name):
    tenant = get_tenant(request, schema_name)

    with schema_context(schema_name):
        assignments = list(
            ClassTimetableAssignment.objects
            .select_related('school_class__wing_category', 'timetable')
            .order_by('school_class__name', 'school_class__section')
        )
        for a in assignments:
            a.class_display_name = get_class_display_name(a.school_class, tenant.tenant_type)

        all_classes = list(
            SchoolClass.objects.filter(is_active=True)
            .select_related('wing_category')
            .order_by('name', 'section')
        )
        for c in all_classes:
            c.display_name = get_class_display_name(c, tenant.tenant_type)

        timetables = list(PeriodsTimetable.objects.order_by('title'))

    context = {
        'tenant': tenant,
        'assignments': assignments,
        'all_classes': all_classes,
        'timetables': timetables,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
    response = render(request, 'tenant/timetable_assignments.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_assign_timetable(request, schema_name):
    # ASSIGN_MULTI_TIMETABLE_V1
    # Rules:
    #   1. A class may have multiple timetables assigned.
    #   2. Every assigned timetable must share the SAME ScheduleLabel.
    #      i.e. once a class has a 'Senior' timetable, only other
    #      'Senior' timetables can be added; a 'Junior' timetable is
    #      refused.
    #   3. The exact same timetable cannot be assigned twice to the
    #      same class (also enforced by a DB UniqueConstraint).
    class_id = request.POST.get('class_id')
    timetable_id = request.POST.get('timetable_id')

    if not class_id or not timetable_id:
        messages.error(request, 'Class and timetable are required.')
        return redirect('timetable_assignments', schema_name=schema_name)

    with schema_context(schema_name):
        school_class = get_object_or_404(SchoolClass, id=class_id, is_active=True)
        timetable = get_object_or_404(PeriodsTimetable, id=timetable_id)

        existing_qs = ClassTimetableAssignment.objects.filter(
            school_class=school_class,
        ).select_related('timetable__label')

        # Duplicate guard
        if existing_qs.filter(timetable=timetable).exists():
            messages.error(
                request,
                f'Timetable "{timetable.title}" is already assigned '
                f'to {school_class}.',
            )
            return redirect('timetable_assignments', schema_name=schema_name)

        # Same-label guard
        existing_label_ids = set(
            existing_qs.values_list('timetable__label_id', flat=True)
        )
        if existing_label_ids:
            new_label_id = timetable.label_id
            if new_label_id not in existing_label_ids:
                existing_label_names = sorted({
                    a.timetable.label.name
                    for a in existing_qs
                    if a.timetable and a.timetable.label_id
                })
                messages.error(
                    request,
                    f'{school_class} already has timetables with '
                    f'label(s) {existing_label_names}. You can only '
                    f'assign another timetable with the same label.',
                )
                return redirect('timetable_assignments', schema_name=schema_name)

        ClassTimetableAssignment.objects.create(
            school_class=school_class,
            timetable=timetable,
        )
        messages.success(
            request,
            f'Timetable "{timetable.title}" assigned to {school_class}.',
        )

    # BUG-9 fix: after a new timetable is assigned, existing
    # PeriodTeacherAssignment rows may reference slots that don't
    # exist in the newly-assigned timetable. Reconcile against the
    # assigned timetable so orphan rows are dropped immediately.
    # NOTE: with multi-timetable assignments this reconcile only
    # touches rows valid for the NEW timetable; a full reconcile
    # is available via api_unassign_timetable.
    try:
        from .assign_teachers import _reconcile_period_teacher_assignments
        _reconcile_period_teacher_assignments(schema_name, timetable.id)
    except Exception as _exc:
        logger.warning(
            'ASSIGN_TEACHERS_HARDENING_V4_2: reconcile after assign '
            'failed: %s', _exc,
        )

    return redirect('timetable_assignments', schema_name=schema_name)


# =====================================================================
# ASSIGN_MULTI_TIMETABLE_V1
# ---------------------------------------------------------------------
# Returns the list of timetables the admin may assign to a given class.
#
#   * If the class already has one or more timetables assigned, the
#     response is filtered to timetables that share the SAME label
#     as the existing assignments, EXCLUDING the timetables that
#     are already assigned to this class.
#   * If the class has no assignments yet, every timetable is
#     returned.
# =====================================================================
@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_class_available_timetables(request, schema_name, class_id):
    with schema_context(schema_name):
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if school_class is None:
            return JsonResponse(
                {'ok': False, 'error': 'Class not found'}, status=404,
            )

        existing_qs = (
            ClassTimetableAssignment.objects
            .filter(school_class=school_class)
            .select_related('timetable__label')
        )
        assigned_timetable_ids = set(
            existing_qs.values_list('timetable_id', flat=True)
        )

        existing_label_ids = set(
            existing_qs.values_list('timetable__label_id', flat=True)
        )

        if existing_label_ids:
            label_name = None
            for a in existing_qs:
                if a.timetable and a.timetable.label_id:
                    label_name = a.timetable.label.name
                    break
            qs = (
                PeriodsTimetable.objects
                .select_related('label')
                .filter(label_id__in=existing_label_ids)
                .exclude(id__in=assigned_timetable_ids)
                .order_by('title', 'id')
            )
            has_assignments = True
        else:
            label_name = None
            qs = (
                PeriodsTimetable.objects
                .select_related('label')
                .order_by('label__name', 'title', 'id')
            )
            has_assignments = False

        timetables = [
            {
                'id': tt.id,
                'title': tt.title,
                'label': tt.label.name if tt.label_id else '',
            }
            for tt in qs
        ]

        return JsonResponse({
            'ok': True,
            'class_id': school_class.id,
            'class_display': str(school_class),
            'has_assignments': has_assignments,
            'assigned_count': existing_qs.count(),
            'label_name': label_name,
            'timetables': timetables,
        })


@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_unassign_timetable(request, schema_name):
    assignment_id = request.POST.get('assignment_id')
    if not assignment_id:
        messages.error(request, 'Assignment ID required.')
        return redirect('timetable_assignments', schema_name=schema_name)

    with schema_context(schema_name):
        assignment = get_object_or_404(ClassTimetableAssignment, id=assignment_id)
        class_label = str(assignment.school_class)
        assignment.delete()
        messages.success(request, f'Timetable unassigned from {class_label}.')

    # BUG-10 fix: after unassignment the class has no timetable, so
    # every PeriodTeacherAssignment row for it is now an orphan.
    # Call the full-schema reconcile (no timetable_id) — the V4_2
    # reconcile treats "class has no current timetable" as "delete
    # all its PTA rows".
    try:
        from .assign_teachers import _reconcile_period_teacher_assignments
        _reconcile_period_teacher_assignments(schema_name)
    except Exception as _exc:
        logger.warning(
            'ASSIGN_TEACHERS_HARDENING_V4_2: reconcile after unassign '
            'failed: %s', _exc,
        )

    return redirect('timetable_assignments', schema_name=schema_name)
