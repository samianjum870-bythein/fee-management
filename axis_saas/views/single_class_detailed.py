"""
AXIS views — single school class detail page.

Opens from the "View" button on a class card in /my-classes/.
Displays analytics + class teacher + subject teachers + students
(active & suspended only) for a single class.
"""
import logging

from django.shortcuts import render, get_object_or_404
from django_tenants.utils import schema_context

from ..models import SchoolClass, Student, ClassSubject
from .helpers import (
    get_tenant, require_tenant_type, require_school_feature, is_mobile_user_agent,
)
from axis_saas.utils.class_display import get_class_display_name


logger = logging.getLogger(__name__)


def _build_context(schema_name, tenant, class_id):
    with schema_context(schema_name):
        school_class = get_object_or_404(
            SchoolClass.objects.select_related('class_teacher'),
            id=class_id,
            is_active=True,
        )

        students_qs = Student.objects.filter(school_class=school_class)

        analytics = {
            'total_active':    students_qs.filter(status='active').count(),
            'total_suspended': students_qs.filter(status='suspended').count(),
            'males':           students_qs.filter(gender='male').count(),
            'females':         students_qs.filter(gender='female').count(),
        }
        analytics['visible'] = analytics['total_active'] + analytics['total_suspended']

        students = list(
            students_qs
            .filter(status__in=['active', 'suspended'])
            .order_by('status', 'roll_number', 'name')
        )

        class_teacher = school_class.class_teacher

        subject_assignments = list(
            ClassSubject.objects
            .filter(school_class=school_class, is_active=True)
            .select_related('subject', 'teacher')
            .order_by('subject__name')
        )

        display_name = get_class_display_name(school_class, tenant.tenant_type)

    return {
        'tenant': tenant,
        'class_obj': school_class,
        'class_display_name': display_name,
        'students': students,
        'analytics': analytics,
        'class_teacher': class_teacher,
        'subject_assignments': subject_assignments,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }


@require_tenant_type(['single_small_school', 'school'])
@require_school_feature('classes_management')
def single_class_detailed_view(request, schema_name, class_id):
    tenant = get_tenant(request, schema_name)
    context = _build_context(schema_name, tenant, class_id)
    response = render(request, 'tenant/single_class_detailed.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response
