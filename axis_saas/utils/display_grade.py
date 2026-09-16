"""
Display grade helper function to compute display grade for a student.
Uses django.apps to avoid circular imports.

STUDENT_PROFILE_WING_DOESNOTEXIST_FIX_V1 — see the patcher that generated this file.

Same defensive treatment as ``axis_saas/utils/class_display.py``. Any
orphaned ``wing_category`` / ``wing_category.parent`` FK resolves to
None and the helper falls back to the flat ``"Class - Section"`` form
instead of raising ``WingCategory.DoesNotExist`` on the student profile
page.
"""

from django.apps import apps  # noqa: F401  (kept for backward compatibility)


def _safe_wing_category(school_class):
    """Return ``school_class.wing_category`` or None if the FK is orphaned."""
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
        return None


def _safe_parent(wing_category):
    """Return ``wing_category.parent`` or None if the parent FK is orphaned."""
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


def get_student_display_grade(student):
    """
    Return the display grade string for a student based on its
    ``school_class`` and ``wing_category``.

    * Wing school: ``"Main (Sub) - Class - Section"``
    * Single school: ``"Class - Section"``
    * Falls back to ``student.grade`` / ``student.section`` when there
      is no ``school_class``, or when the wing category chain cannot
      be resolved.
    """
    if not student:
        return ""

    school_class = getattr(student, 'school_class', None)
    if not school_class:
        grade = getattr(student, 'grade', '') or ''
        section = getattr(student, 'section', '') or ''
        if section:
            return f"{grade} - {section}"
        return grade

    wing_category = _safe_wing_category(school_class)
    if wing_category is not None:
        parent = _safe_parent(wing_category)
        if parent is not None:
            if school_class.section:
                return (
                    f"{parent.name} ({wing_category.name}) - "
                    f"{school_class.name} - {school_class.section}"
                )
            return f"{parent.name} ({wing_category.name}) - {school_class.name}"
        # Parent missing (orphaned FK) or top-level category.
        if school_class.section:
            return (
                f"{wing_category.name} - {school_class.name} - "
                f"{school_class.section}"
            )
        return f"{wing_category.name} - {school_class.name}"

    # Flat form.
    if school_class.section:
        return f"{school_class.name} - {school_class.section}"
    return school_class.name
