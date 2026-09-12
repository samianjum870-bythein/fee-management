"""
AXIS views — class timetable assignments.

Lets the admin assign a PeriodsTimetable to a SchoolClass. Assignments
are stored in DB via the ClassTimetableAssignment model.

URL base: /portal/<schema>/timetable/assign/
"""
import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    SchoolClass, PeriodsTimetable, ClassTimetableAssignment,
)
from .helpers import get_tenant, require_tenant_type, require_school_feature
from axis_saas.utils.class_display import get_class_display_name


logger = logging.getLogger(__name__)


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


@csrf_exempt
@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_assign_timetable(request, schema_name):
    class_id = request.POST.get('class_id')
    timetable_id = request.POST.get('timetable_id')

    if not class_id or not timetable_id:
        messages.error(request, 'Class and timetable are required.')
        return redirect('timetable_assignments', schema_name=schema_name)

    with schema_context(schema_name):
        school_class = get_object_or_404(SchoolClass, id=class_id, is_active=True)
        timetable = get_object_or_404(PeriodsTimetable, id=timetable_id)
        ClassTimetableAssignment.objects.update_or_create(
            school_class=school_class,
            defaults={'timetable': timetable},
        )
        messages.success(
            request,
            f'Timetable "{timetable.title}" assigned to {school_class}.',
        )
    return redirect('timetable_assignments', schema_name=schema_name)


@csrf_exempt
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
    return redirect('timetable_assignments', schema_name=schema_name)
