"""
AXIS views - single school classes management (card/grid view).
Reuses the same add/edit/delete logic as the primary class_management view,
but renders classes as cards for single-small-school tenants.

URL names used for add/edit/delete are the existing ones:
    add_class, edit_class, delete_class
"""
import logging

from django.shortcuts import render
from django.db.models import Count, Q
from django_tenants.utils import schema_context

from ..models import SchoolClass, Student
from .helpers import (
    get_tenant, require_tenant_type, require_school_feature, is_mobile_user_agent,
)
from axis_saas.utils.class_display import get_class_display_name


logger = logging.getLogger(__name__)


def _build_context(request, schema_name, tenant):
    search = (request.GET.get('q') or '').strip()
    section_filter = (request.GET.get('section') or '').strip()

    with schema_context(schema_name):
        qs = SchoolClass.objects.filter(is_active=True).select_related('class_teacher')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(section__icontains=search))
        if section_filter:
            qs = qs.filter(section__iexact=section_filter)

        qs = qs.annotate(student_count=Count('students')).order_by('name', 'section')
        classes = list(qs)
        for cls in classes:
            cls.display_name = get_class_display_name(cls, tenant.tenant_type)

        sections = list(
            SchoolClass.objects.filter(is_active=True)
            .exclude(section='')
            .values_list('section', flat=True)
            .distinct()
            .order_by('section')
        )

        total_classes = len(classes)
        total_students = Student.objects.filter(status='active').count()

    return {
        'tenant': tenant,
        'classes': classes,
        'sections': sections,
        'search_query': search,
        'selected_section': section_filter,
        'total_classes': total_classes,
        'total_students': total_students,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }


@require_tenant_type(['single_small_school', 'school'])
@require_school_feature('classes_management')
def single_classes_view(request, schema_name):
    tenant = get_tenant(request, schema_name)
    if is_mobile_user_agent(request):
        # Mobile is not supported yet - just render the same page.
        pass
    context = _build_context(request, schema_name, tenant)
    response = render(request, 'tenant/single_classes.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response
