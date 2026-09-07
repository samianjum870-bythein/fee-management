"""
Template tags for class display.
"""
from django import template
from axis_saas.utils.class_display import get_student_display_class, get_class_display_name

register = template.Library()

@register.filter
def display_class(student, tenant):
    """
    Filter for a student object and a tenant.
    Usage: {{ student|display_class:tenant }}
    """
    return get_student_display_class(student, tenant.tenant_type)

@register.filter
def display_class_from_school_class(school_class, tenant):
    """
    Filter for a SchoolClass object and a tenant.
    Usage: {{ school_class|display_class_from_school_class:tenant }}
    """
    return get_class_display_name(school_class, tenant.tenant_type)
