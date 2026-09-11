"""
AXIS views - wing school classes management (card/grid view).
Reuses the same add/edit/delete logic as the primary class_management view,
but renders classes as cards for wing-based tenants.

URL names used for add/edit/delete are the existing ones:
    add_class, edit_class, delete_class
"""
import logging

from django.shortcuts import render, redirect
from django.db.models import Count, Q
from django_tenants.utils import schema_context

from ..models import SchoolClass, Student, WingCategory
from ..forms import available_wing_categories
from .helpers import (
    get_tenant, require_tenant_type, require_school_feature, is_mobile_user_agent,
)
from axis_saas.utils.class_display import get_class_display_name


logger = logging.getLogger(__name__)


def _build_context(request, schema_name, tenant):
    search = (request.GET.get('q') or '').strip()
    section_filter = (request.GET.get('section') or '').strip()
    wing_filter = (request.GET.get('wing') or '').strip()

    with schema_context(schema_name):
        qs = SchoolClass.objects.filter(is_active=True).select_related(
            'wing_category', 'class_teacher'
        )
        if wing_filter:
            qs = qs.filter(wing_category_id=wing_filter)
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

        wing_categories = list(available_wing_categories())

        total_classes = len(classes)
        total_students = Student.objects.filter(status='active').count()

        # For the "Add Class" modal dropdown
        wing_choices = [
            {
                'id': wc.id,
                'label': f"{wc.parent.name} ({wc.name})" if wc.parent_id else wc.name,
            }
            for wc in wing_categories
        ]

    return {
        'tenant': tenant,
        'classes': classes,
        'sections': sections,
        'wing_categories': wing_choices,
        'search_query': search,
        'selected_section': section_filter,
        'selected_wing': wing_filter,
        'total_classes': total_classes,
        'total_students': total_students,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }


@require_tenant_type(['wing_school'])
@require_school_feature('classes_management')
def wing_classes_view(request, schema_name):
    tenant = get_tenant(request, schema_name)
    if is_mobile_user_agent(request):
        # Mobile is not supported yet - just render the same page.
        pass
    context = _build_context(request, schema_name, tenant)
    response = render(request, 'tenant/wing_classes.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


def classes_management_view(request, schema_name):
    """Dispatcher: route to the correct view based on tenant type."""
    tenant = get_tenant(request, schema_name)
    if tenant.tenant_type == 'wing_school':
        return wing_classes_view(request, schema_name)
    # Everything else (single_small_school, legacy 'school') -> single view
    from .single_classes import single_classes_view
    return single_classes_view(request, schema_name)
