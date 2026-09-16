"""
Class display utilities for multi-tenant school management.
Provides a single source of truth for formatting class names.

STUDENT_PROFILE_WING_DOESNOTEXIST_FIX_V1 — see the patcher that generated this file.

The previous implementation dereferenced ``school_class.wing_category``
and ``school_class.wing_category.parent`` unconditionally. In production
this raised::

    axis_saas.models.WingCategory.DoesNotExist:
        WingCategory matching query does not exist.

on ``/portal/<schema>/students/<id>/`` whenever a ``SchoolClass`` row
pointed at a ``WingCategory`` id that no longer existed in the DB
(orphaned FK left behind by a manual SQL change, an aborted migration,
or a row that bypassed ``on_delete=SET_NULL``). The parent FK had the
same problem.

Every access now goes through ``_safe_wing_category()`` /
``_safe_parent()``, which return None on ``DoesNotExist``. When the
wing category (or its parent) cannot be resolved, the display
gracefully falls back to a flat ``"Class - Section"`` form instead of
raising.
"""

from django.apps import apps  # noqa: F401  (kept for backward compatibility)


def _safe_wing_category(school_class):
    """Return ``school_class.wing_category`` or None if the FK is orphaned.

    Never raises ``WingCategory.DoesNotExist``. Also returns None when
    ``school_class`` itself is None, or when the FK column is NULL.
    """
    if school_class is None:
        return None
    try:
        category_id = school_class.wing_category_id
    except AttributeError:
        return None
    if not category_id:
        return None
    try:
        return school_class.wing_category
    except Exception:
        # WingCategory.DoesNotExist, or any DB error — treat as missing.
        return None


def _safe_parent(wing_category):
    """Return ``wing_category.parent`` or None if the parent FK is orphaned.

    Never raises. A top-level wing category (``parent_id is NULL``) also
    returns None, which callers treat as "no parent name available".
    """
    if wing_category is None:
        return None
    try:
        parent_id = wing_category.parent_id
    except AttributeError:
        return None
    if not parent_id:
        return None
    try:
        return wing_category.parent
    except Exception:
        return None


def get_class_display_name(school_class, tenant_type, grade=None, section=None):
    """
    Return the formatted class name based on tenant type.

    * Wing School: ``"Main Category (Sub Category) - Class - Section"``
    * Single School: ``"Class - Section"``
    * Falls back to ``grade``-``section`` if ``school_class`` is None.

    When the wing category (or its parent) cannot be resolved, the
    helper degrades to the flat form instead of crashing.
    """
    if school_class is None:
        if section:
            return f"{grade} - {section}"
        return grade or ""

    wing_category = _safe_wing_category(school_class)
    if tenant_type == 'wing_school' and wing_category is not None:
        parent = _safe_parent(wing_category)
        if parent is not None:
            if school_class.section:
                return (
                    f"{parent.name} ({wing_category.name}) - "
                    f"{school_class.name} - {school_class.section}"
                )
            return f"{parent.name} ({wing_category.name}) - {school_class.name}"
        # Sub-category exists but parent is missing (orphaned FK) or
        # this is a top-level category. Use the sub name alone.
        if school_class.section:
            return (
                f"{wing_category.name} - {school_class.name} - "
                f"{school_class.section}"
            )
        return f"{wing_category.name} - {school_class.name}"

    # Flat form: single school, wing school with no category, or
    # orphaned wing_category FK.
    if school_class.section:
        return f"{school_class.name} - {school_class.section}"
    return school_class.name


def get_student_display_class(student, tenant_type=None):
    """
    Convenience function for students.

    If ``tenant_type`` is None, it is auto-detected from the student's
    school_class (wing category present -> wing_school).
    """
    if student is None:
        return ""
    school_class = getattr(student, 'school_class', None)
    if tenant_type is None:
        wing_category = _safe_wing_category(school_class)
        tenant_type = (
            'wing_school' if wing_category is not None
            else 'single_small_school'
        )
    return get_class_display_name(
        school_class,
        tenant_type,
        getattr(student, 'grade', None),
        getattr(student, 'section', None),
    )


def get_class_display_for_student(student, tenant):
    """
    Auto-detects tenant type from the tenant object and returns the
    formatted class name.
    """
    tenant_type = (
        getattr(tenant, 'tenant_type', 'single_small_school')
        if tenant is not None else 'single_small_school'
    )
    return get_student_display_class(student, tenant_type)
