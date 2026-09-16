#!/usr/bin/env python3
"""
axis_patcher.py — STUDENT_PROFILE_WING_DOESNOTEXIST_FIX_V1
===========================================================

Fixes the intermittent 500 on /portal/<schema>/students/<id>/ :

    axis_saas.models.WingCategory.DoesNotExist:
        WingCategory matching query does not exist.

Root cause
----------
``axis_saas/utils/class_display.py`` (used by the ``display_class``
template filter that renders ``{{ student|display_class:tenant }}``
on ``student_profile.html``) dereferenced ``school_class.wing_category``
and ``school_class.wing_category.parent`` unconditionally:

    if tenant_type == 'wing_school' and school_class.wing_category:
        main = school_class.wing_category.parent
        sub = school_class.wing_category

If a ``SchoolClass`` row points at a ``WingCategory`` id that no longer
exists (orphaned FK — possible when a row is deleted by raw SQL, an
aborted migration, or any path that bypasses ``on_delete=SET_NULL``),
Django raises ``WingCategory.DoesNotExist`` the moment the attribute is
read. The same problem existed for the parent FK.

``axis_saas/utils/display_grade.py`` had the identical pattern and
crashed whenever ``get_student_display_grade(student)`` was invoked on
a student whose ``school_class`` had an orphaned ``wing_category``.

What this patch does
--------------------
Rewrites both helper modules so that:

  * All FK reads (``wing_category``, ``wing_category.parent``) go
    through ``_safe_wing_category()`` / ``_safe_parent()``, which
    return None on ``WingCategory.DoesNotExist`` instead of raising.
  * When the wing category is missing, the helper gracefully falls
    back to the flat ``"Class - Section"`` form.
  * When the parent is missing but the sub-category exists, the helper
    uses the sub-category name alone: ``"<Sub> - Class - Section"``.
  * ``student`` or ``school_class`` being None never raises.

The templates, views, models, URLs and migrations are NOT touched.
The fix is purely defensive at the utility layer, so every call site
(admin pages, staff pages, mobile pages) benefits automatically.

Idempotent. Running it twice does nothing after the first run.

Usage
-----
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py
    python3 axis_patcher.py --target-dir /srv/fee_management
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


MARKER = "STUDENT_PROFILE_WING_DOESNOTEXIST_FIX_V1"


# --------------------------------------------------------------------- utils

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log(f"  ERROR: not found: {path}")
        return None
    except Exception as e:
        log(f"  ERROR reading {path}: {e}")
        return None


def write_file(path, content, dry_run=False, label=""):
    if dry_run:
        log(f"  DRY-RUN: would write {path} ({label})")
        return True
    try:
        path.write_text(content, encoding="utf-8")
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as e:
        log(f"  ERROR writing {path}: {e}")
        return False


# ====================================================== class_display.py
#
# The __MARKER__ token is replaced at runtime with the MARKER constant
# so the generated file carries a discoverable idempotency stamp without
# forcing us to interleave f-string quotes with the source being emitted.

CLASS_DISPLAY_TEMPLATE = '''"""
Class display utilities for multi-tenant school management.
Provides a single source of truth for formatting class names.

__MARKER__ — see the patcher that generated this file.

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
'''


# ====================================================== display_grade.py

DISPLAY_GRADE_TEMPLATE = '''"""
Display grade helper function to compute display grade for a student.
Uses django.apps to avoid circular imports.

__MARKER__ — see the patcher that generated this file.

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
'''


def _render(template):
    return template.replace("__MARKER__", MARKER)


def patch_class_display(root, args):
    path = root / "axis_saas" / "utils" / "class_display.py"
    if not path.is_file():
        log(f"  ERROR: {path} does not exist")
        return False
    content = read_file(path)
    if content is None:
        return False
    if MARKER in content:
        log(f"  SKIP (already applied): {path}")
        return True
    return write_file(path, _render(CLASS_DISPLAY_TEMPLATE),
                      args.dry_run,
                      "class_display.py (defensive FK reads)")


def patch_display_grade(root, args):
    path = root / "axis_saas" / "utils" / "display_grade.py"
    if not path.is_file():
        log(f"  ERROR: {path} does not exist")
        return False
    content = read_file(path)
    if content is None:
        return False
    if MARKER in content:
        log(f"  SKIP (already applied): {path}")
        return True
    return write_file(path, _render(DISPLAY_GRADE_TEMPLATE),
                      args.dry_run,
                      "display_grade.py (defensive FK reads)")


# ====================================================== MAIN

def main():
    parser = argparse.ArgumentParser(
        description=(
            f"{MARKER} — make the student-profile class/grade display "
            f"helpers resilient to orphaned WingCategory FKs. Fixes the "
            f"intermittent 500 on /portal/<schema>/students/<id>/."
        )
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing.")
    parser.add_argument("--verbose", action="store_true",
                        help="Verbose output.")
    parser.add_argument("--target-dir", default=".",
                        help="Project root (default: current directory).")
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not (root / "manage.py").is_file():
        log(f"ERROR: manage.py not found in {root}")
        return 1

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")
    log(f"Patch:  {MARKER}")

    steps = [
        ("Utils: class_display.py", patch_class_display),
        ("Utils: display_grade.py", patch_display_grade),
    ]

    results = []
    for label, fn in steps:
        log(f"--- {label} ---")
        try:
            ok = fn(root, args)
        except Exception as exc:
            log(f"  EXCEPTION: {exc}")
            ok = False
        results.append((label, ok))

    log("=" * 65)
    for label, ok in results:
        log(f"  {'OK' if ok else 'FAIL'}  {label}")

    all_ok = all(ok for _, ok in results)
    if all_ok:
        log("All steps completed successfully.")
        if args.dry_run:
            log("Re-run without --dry-run to apply.")
        else:
            log("Restart the Django server (or reload the WSGI worker) "
                "to pick up the new module contents.")
        return 0
    log("One or more steps failed. See messages above.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
