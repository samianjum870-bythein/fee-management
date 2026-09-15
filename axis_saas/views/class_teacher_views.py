"""
Class Teacher management views (future extension).
This file is created by the patcher for upcoming features like attendance, exams, etc.
"""

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.core.paginator import Paginator
from django_tenants.utils import schema_context

from ..models import SchoolClass, Staff, Student, WingCategory
from ..models import StudentAttendance
from datetime import date, timedelta
from .helpers import get_tenant, require_tenant_type, require_school_feature

# Placeholder for future class-teacher related views
# For now, the main class management view handles class-teacher assignments.


# =====================================================================
# ATTENDANCE_SYSTEM_REBUILD_V1 - class-teacher attendance helpers
# ---------------------------------------------------------------------
# Small utilities so any future class-teacher page can reuse the same
# access rules as the staff attendance views. A staff member is a class
# teacher ONLY for the classes where SchoolClass.class_teacher == staff.
# =====================================================================

def get_class_teacher_classes(staff):
    """Classes for which `staff` is the assigned class teacher."""
    from ..models import SchoolClass as _SC
    return _SC.objects.filter(
        class_teacher=staff, is_active=True,
    ).order_by('name', 'section')


def is_class_teacher(staff):
    """True if `staff` teaches at least one class as its class teacher."""
    return get_class_teacher_classes(staff).exists()


def get_class_attendance_for_date(school_class, attendance_date):
    """Return {student_id: StudentAttendance} for the given class+date."""
    from ..models import StudentAttendance as _SA
    return {
        r.student_id: r
        for r in _SA.objects.filter(
            school_class=school_class, date=attendance_date,
        )
    }


def get_class_attendance_summary(school_class, start_date=None, end_date=None):
    """Aggregate attendance stats for one class, optional date range."""
    from ..models import StudentAttendance as _SA
    qs = _SA.objects.filter(school_class=school_class)
    if start_date:
        qs = qs.filter(date__gte=start_date)
    if end_date:
        qs = qs.filter(date__lte=end_date)
    return {
        'present': qs.filter(status='present').count(),
        'absent': qs.filter(status='absent').count(),
        'late': qs.filter(status='late').count(),
        'holiday': qs.filter(status='holiday').count(),
        'total': qs.count(),
    }
