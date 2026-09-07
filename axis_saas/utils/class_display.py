"""
Class display utilities for multi-tenant school management.
Provides a single source of truth for formatting class names.
"""

from django.apps import apps


def get_class_display_name(school_class, tenant_type, grade=None, section=None):
    """
    Return the formatted class name based on tenant type.
    - Wing School: "Main Category (Sub Category) - Class - Section"
    - Single School: "Class - Section"
    Falls back to grade-section if school_class is None.
    """
    if school_class is None:
        # Use grade and section if provided
        if section:
            return f"{grade} - {section}"
        return grade or ""

    # Tenant type 'wing_school' or 'single_small_school'
    if tenant_type == 'wing_school' and school_class.wing_category:
        main = school_class.wing_category.parent
        sub = school_class.wing_category
        if school_class.section:
            return f"{main.name} ({sub.name}) - {school_class.name} - {school_class.section}"
        else:
            return f"{main.name} ({sub.name}) - {school_class.name}"
    else:
        if school_class.section:
            return f"{school_class.name} - {school_class.section}"
        else:
            return school_class.name


def get_student_display_class(student, tenant_type=None):
    """
    Convenience function for students.
    If tenant_type is None, it will be auto-detected from the student's tenant.
    """
    if tenant_type is None:
        # Try to get tenant_type from the student's wing_category or school_class
        # In practice, we'll pass tenant_type from the view.
        # Fallback to assuming wing_school if wing_category exists, else single.
        if student.school_class and student.school_class.wing_category:
            tenant_type = 'wing_school'
        else:
            tenant_type = 'single_small_school'
    return get_class_display_name(student.school_class, tenant_type, student.grade, student.section)


def get_class_display_for_student(student, tenant):
    """
    Auto-detects tenant type from the tenant object and returns formatted class.
    """
    tenant_type = getattr(tenant, 'tenant_type', 'single_small_school')
    return get_student_display_class(student, tenant_type)
