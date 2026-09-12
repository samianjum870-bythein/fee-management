#!/usr/bin/env python3
"""
add_class_detailed_page.py
==========================
Adds a new "Class Detailed" page that opens from a View button on
each class card in the Classes Management page (/my-classes/).

Shows for a single class:
  * Horizontal analytics strip (Total / Active / Suspended / Male / Female)
  * Class Teacher section (prominent card + View Profile button)
  * Subject Teachers list (subject, teacher, assigned date, status, View Profile)
  * Students table — ONLY active + suspended students, with View Profile button

New files:
    axis_saas/views/wing_class_detailed.py
    axis_saas/views/single_class_detailed.py
    templates/tenant/wing_class_detailed.html
    templates/tenant/single_class_detailed.html

Modified files:
    axis_saas/views/__init__.py           (import new views)
    axis_saas/public_urls.py              (route + import)
    templates/tenant/wing_classes.html    (View button + JS)
    templates/tenant/single_classes.html  (View button + JS)

Idempotent. Safe to run multiple times.

Usage:
    python3 add_class_detailed_page.py [--dry-run] [--verbose] [--target-dir=.]
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


def _read(path):
    try:
        return path.read_text(encoding='utf-8')
    except Exception as e:
        _log(f"ERROR reading {path}: {e}")
        return None


def _write(path, content, dry_run, verbose):
    try:
        if dry_run:
            _log(f"DRY-RUN would write {path} ({len(content)} bytes)")
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        if verbose:
            _log(f"Wrote {path} ({len(content)} bytes)")
        else:
            _log(f"Wrote {path}")
        return True
    except Exception as e:
        _log(f"ERROR writing {path}: {e}")
        return False


def _ensure_file(path, content, dry_run, verbose):
    if path.exists():
        _log(f"SKIP (already exists): {path}")
        return True
    _log(f"CREATE: {path}")
    return _write(path, content, dry_run, verbose)


# =====================================================================
# Shared CSS for both detailed-page templates
# =====================================================================
DETAIL_CSS = r"""
    /* ---- Back link ---- */
    .back-link {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        color: var(--muted);
        text-decoration: none;
        font-size: 0.85rem;
        font-weight: 600;
        padding: 0.4rem 0.85rem;
        border-radius: 0.65rem;
        background: var(--surface);
        border: 1px solid var(--border);
        transition: all 0.2s ease;
        margin-bottom: 1.25rem;
    }
    .back-link:hover {
        color: var(--primary);
        border-color: var(--primary);
        transform: translateX(-2px);
    }

    /* ---- Page header ---- */
    .page-header {
        margin-bottom: 1.75rem;
        padding-bottom: 1.25rem;
        border-bottom: 1px solid var(--border);
        position: relative;
    }
    .page-header::after {
        content: '';
        position: absolute;
        bottom: -1px; left: 0;
        width: 64px; height: 3px;
        border-radius: 3px;
        background: linear-gradient(135deg, var(--primary), var(--primary-dark));
    }
    .page-title {
        font-size: 1.85rem;
        font-weight: 800;
        letter-spacing: -0.6px;
        margin: 0;
        line-height: 1.2;
        background: linear-gradient(135deg, var(--primary), var(--primary-dark));
        -webkit-background-clip: text;
        background-clip: text;
        color: transparent;
        overflow-wrap: anywhere;
    }
    .page-desc {
        color: var(--muted);
        margin: 0.4rem 0 0;
        font-size: 0.92rem;
        font-weight: 500;
    }

    /* ---- KPI strip ---- */
    .kpi-strip {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 0.9rem;
        margin-bottom: 1.75rem;
    }
    .kpi-card {
        display: flex;
        align-items: center;
        gap: 0.85rem;
        padding: 1rem 1.15rem;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 1rem;
        box-shadow: var(--shadow-sm);
        position: relative;
        overflow: hidden;
        transition: transform 0.22s ease, box-shadow 0.22s ease;
    }
    .kpi-card::before {
        content: '';
        position: absolute;
        top: 0; left: 0;
        width: 4px; height: 100%;
        background: linear-gradient(135deg, var(--primary), var(--primary-dark));
    }
    .kpi-card:hover {
        transform: translateY(-2px);
        box-shadow: var(--shadow);
    }
    .kpi-icon {
        width: 42px;
        height: 42px;
        border-radius: 11px;
        display: grid;
        place-items: center;
        background: linear-gradient(135deg, rgba(59,130,246,0.14), rgba(37,99,235,0.04));
        color: var(--primary);
        flex-shrink: 0;
    }
    .kpi-body {
        display: flex;
        flex-direction: column;
        min-width: 0;
    }
    .kpi-label {
        font-size: 0.66rem;
        text-transform: uppercase;
        letter-spacing: 0.65px;
        color: var(--muted);
        font-weight: 700;
    }
    .kpi-value {
        font-size: 1.5rem;
        font-weight: 800;
        color: var(--text);
        line-height: 1.15;
        letter-spacing: -0.4px;
    }

    /* ---- Section cards ---- */
    .section-card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 1.1rem;
        box-shadow: var(--shadow-sm);
        margin-bottom: 1.5rem;
        overflow: hidden;
    }
    .section-header {
        padding: 0.9rem 1.25rem;
        border-bottom: 1px solid var(--border);
        background: var(--surface-alt);
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .section-header h2 {
        margin: 0;
        font-size: 1rem;
        font-weight: 700;
        letter-spacing: -0.15px;
        color: var(--text);
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .section-body { padding: 1.25rem; }
    .empty-inline {
        margin: 0;
        color: var(--muted);
        font-size: 0.9rem;
        font-style: italic;
    }

    /* ---- Class teacher feature ---- */
    .teacher-feature {
        display: flex;
        align-items: center;
        gap: 1.15rem;
        padding: 1.25rem;
        background: linear-gradient(135deg, rgba(59,130,246,0.06), rgba(37,99,235,0.02));
        border-radius: 0.9rem;
        flex-wrap: wrap;
    }
    .teacher-avatar {
        width: 62px;
        height: 62px;
        border-radius: 1.1rem;
        background: linear-gradient(135deg, var(--primary), var(--primary-dark));
        color: #fff;
        font-size: 1.5rem;
        font-weight: 800;
        display: grid;
        place-items: center;
        flex-shrink: 0;
        box-shadow: 0 8px 20px -8px rgba(59,130,246,0.6);
    }
    .teacher-info { flex: 1; min-width: 180px; }
    .teacher-info h3 {
        margin: 0;
        font-size: 1.1rem;
        font-weight: 700;
        letter-spacing: -0.2px;
        color: var(--text);
    }
    .teacher-info p {
        margin: 0.25rem 0 0;
        font-size: 0.85rem;
        color: var(--muted);
    }

    /* ---- Data table ---- */
    .table-responsive { overflow-x: auto; }
    .data-table {
        width: 100%;
        border-collapse: collapse;
    }
    .data-table th, .data-table td {
        padding: 0.75rem 1rem;
        text-align: left;
        font-size: 0.86rem;
        border-bottom: 1px solid var(--border);
        vertical-align: middle;
    }
    .data-table th {
        background: var(--surface-alt);
        font-weight: 700;
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.55px;
        color: var(--muted);
    }
    .data-table tbody tr:last-child td { border-bottom: none; }
    .data-table tbody tr:hover { background: var(--surface-alt); }

    /* ---- Badges ---- */
    .badge {
        display: inline-flex;
        align-items: center;
        padding: 0.22rem 0.7rem;
        border-radius: 2rem;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.2px;
        line-height: 1.3;
        white-space: nowrap;
    }
    .badge-success {
        background: rgba(16, 185, 129, 0.12);
        color: #047857;
        border: 1px solid rgba(16, 185, 129, 0.25);
    }
    .badge-warn {
        background: rgba(245, 158, 11, 0.14);
        color: #b45309;
        border: 1px solid rgba(245, 158, 11, 0.3);
    }
    .badge-muted {
        background: var(--surface-alt);
        color: var(--muted);
        border: 1px solid var(--border);
    }

    /* ---- Action buttons / links ---- */
    .btn-view {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        padding: 0.5rem 0.95rem;
        background: linear-gradient(135deg, var(--primary), var(--primary-dark));
        color: #fff;
        border: none;
        border-radius: 0.7rem;
        font-weight: 700;
        font-size: 0.82rem;
        text-decoration: none;
        cursor: pointer;
        transition: transform 0.2s, box-shadow 0.2s;
        box-shadow: 0 6px 16px -6px rgba(59,130,246,0.55);
        font-family: inherit;
    }
    .btn-view:hover {
        transform: translateY(-1px);
        box-shadow: 0 10px 20px -6px rgba(59,130,246,0.65);
    }
    .action-link {
        display: inline-flex;
        align-items: center;
        gap: 0.3rem;
        padding: 0.35rem 0.75rem;
        background: var(--surface-alt);
        border: 1px solid var(--border);
        border-radius: 0.55rem;
        color: var(--text);
        text-decoration: none;
        font-size: 0.78rem;
        font-weight: 600;
        transition: all 0.2s ease;
    }
    .action-link:hover {
        color: var(--primary);
        border-color: var(--primary);
        transform: translateY(-1px);
    }

    /* ---- Responsive ---- */
    @media (max-width: 768px) {
        .page-title { font-size: 1.5rem; }
        .kpi-value { font-size: 1.3rem; }
        .teacher-feature { flex-direction: column; align-items: flex-start; }
        .data-table th, .data-table td { padding: 0.6rem 0.7rem; font-size: 0.8rem; }
    }
"""


# =====================================================================
# View file contents
# =====================================================================

WING_CLASS_DETAILED_VIEW = '''"""
AXIS views — wing school class detail page.

Opens from the "View" button on a class card in /my-classes/.
Displays analytics + class teacher + subject teachers + students
(active & suspended only) for a single class.
"""
import logging

from django.shortcuts import render, get_object_or_404
from django.db.models import Count
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
            SchoolClass.objects.select_related('class_teacher', 'wing_category'),
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

        # Only active + suspended students are shown in the list.
        students = list(
            students_qs
            .filter(status__in=['active', 'suspended'])
            .select_related('wing_category')
            .order_by('status', 'roll_number', 'name')
        )

        # Class teacher
        class_teacher = school_class.class_teacher

        # Subject teachers for this class (all currently active assignments)
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


@require_tenant_type(['wing_school'])
@require_school_feature('classes_management')
def wing_class_detailed_view(request, schema_name, class_id):
    tenant = get_tenant(request, schema_name)
    context = _build_context(schema_name, tenant, class_id)
    response = render(request, 'tenant/wing_class_detailed.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


def class_detailed_view(request, schema_name, class_id):
    """Dispatcher: route to correct template based on tenant type."""
    tenant = get_tenant(request, schema_name)
    if tenant.tenant_type == 'wing_school':
        return wing_class_detailed_view(request, schema_name, class_id)
    from .single_class_detailed import single_class_detailed_view
    return single_class_detailed_view(request, schema_name, class_id)
'''


SINGLE_CLASS_DETAILED_VIEW = '''"""
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
'''


# =====================================================================
# Template bodies
# =====================================================================

WING_DETAILED_TEMPLATE = (
    "{% extends 'tenant/base.html' %}\n"
    "{% load fee_extras %}\n"
    "{% load static %}\n"
    "{% block title %}{{ class_display_name }} | {{ tenant.name }}{% endblock %}\n"
    "\n"
    "{% block extra_head %}\n"
    "<style>\n"
    + DETAIL_CSS +
    "</style>\n"
    "{% endblock %}\n"
    "\n"
    "{% block body %}\n"
    "<a href=\"{% url 'classes_management' schema_name=tenant.schema_name %}\" class=\"back-link\">\n"
    "    <svg width=\"14\" height=\"14\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><line x1=\"19\" y1=\"12\" x2=\"5\" y2=\"12\"/><polyline points=\"12 19 5 12 12 5\"/></svg>\n"
    "    Back to Classes\n"
    "</a>\n"
    "\n"
    "<div class=\"page-header\">\n"
    "    <h1 class=\"page-title\">{{ class_display_name }}</h1>\n"
    "    <p class=\"page-desc\">Class details, teachers, and students</p>\n"
    "</div>\n"
    "\n"
    "<!-- Analytics strip -->\n"
    "<div class=\"kpi-strip\">\n"
    "    <div class=\"kpi-card\">\n"
    "        <div class=\"kpi-icon\"><svg width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><path d=\"M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2\"/><circle cx=\"9\" cy=\"7\" r=\"4\"/></svg></div>\n"
    "        <div class=\"kpi-body\"><span class=\"kpi-label\">Total Students</span><span class=\"kpi-value\">{{ analytics.visible }}</span></div>\n"
    "    </div>\n"
    "    <div class=\"kpi-card\">\n"
    "        <div class=\"kpi-icon\"><svg width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><polyline points=\"20 6 9 17 4 12\"/></svg></div>\n"
    "        <div class=\"kpi-body\"><span class=\"kpi-label\">Active</span><span class=\"kpi-value\">{{ analytics.total_active }}</span></div>\n"
    "    </div>\n"
    "    <div class=\"kpi-card\">\n"
    "        <div class=\"kpi-icon\"><svg width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><circle cx=\"12\" cy=\"12\" r=\"10\"/><line x1=\"12\" y1=\"8\" x2=\"12\" y2=\"12\"/><line x1=\"12\" y1=\"16\" x2=\"12.01\" y2=\"16\"/></svg></div>\n"
    "        <div class=\"kpi-body\"><span class=\"kpi-label\">Suspended</span><span class=\"kpi-value\">{{ analytics.total_suspended }}</span></div>\n"
    "    </div>\n"
    "    <div class=\"kpi-card\">\n"
    "        <div class=\"kpi-icon\"><svg width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><circle cx=\"12\" cy=\"8\" r=\"5\"/><path d=\"M20 21a8 8 0 0 0-16 0\"/></svg></div>\n"
    "        <div class=\"kpi-body\"><span class=\"kpi-label\">Male</span><span class=\"kpi-value\">{{ analytics.males }}</span></div>\n"
    "    </div>\n"
    "    <div class=\"kpi-card\">\n"
    "        <div class=\"kpi-icon\"><svg width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><circle cx=\"12\" cy=\"8\" r=\"5\"/><path d=\"M20 21a8 8 0 0 0-16 0\"/></svg></div>\n"
    "        <div class=\"kpi-body\"><span class=\"kpi-label\">Female</span><span class=\"kpi-value\">{{ analytics.females }}</span></div>\n"
    "    </div>\n"
    "</div>\n"
    "\n"
    "<!-- Class Teacher -->\n"
    "<div class=\"section-card\">\n"
    "    <div class=\"section-header\"><h2>Class Teacher</h2></div>\n"
    "    <div class=\"section-body\">\n"
    "        {% if class_teacher %}\n"
    "        <div class=\"teacher-feature\">\n"
    "            <div class=\"teacher-avatar\">{{ class_teacher.full_name|slice:\":1\"|upper }}</div>\n"
    "            <div class=\"teacher-info\">\n"
    "                <h3>{{ class_teacher.full_name }}</h3>\n"
    "                <p>{{ class_teacher.job_title }}{% if class_teacher.email %} · {{ class_teacher.email }}{% endif %}{% if class_teacher.phone %} · {{ class_teacher.phone }}{% endif %}</p>\n"
    "            </div>\n"
    "            <a href=\"{% url 'staff_profile' schema_name=tenant.schema_name staff_id=class_teacher.id %}\" class=\"btn-view\">\n"
    "                <svg width=\"14\" height=\"14\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.2\"><path d=\"M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/></svg>\n"
    "                View Profile\n"
    "            </a>\n"
    "        </div>\n"
    "        {% else %}\n"
    "        <p class=\"empty-inline\">No class teacher assigned to this class yet.</p>\n"
    "        {% endif %}\n"
    "    </div>\n"
    "</div>\n"
    "\n"
    "<!-- Subject Teachers -->\n"
    "<div class=\"section-card\">\n"
    "    <div class=\"section-header\"><h2>Subject Teachers ({{ subject_assignments|length }})</h2></div>\n"
    "    {% if subject_assignments %}\n"
    "    <div class=\"table-responsive\">\n"
    "        <table class=\"data-table\">\n"
    "            <thead>\n"
    "                <tr>\n"
    "                    <th>Subject</th>\n"
    "                    <th>Teacher</th>\n"
    "                    <th>Assigned Since</th>\n"
    "                    <th>Status</th>\n"
    "                    <th>Action</th>\n"
    "                </tr>\n"
    "            </thead>\n"
    "            <tbody>\n"
    "                {% for a in subject_assignments %}\n"
    "                <tr>\n"
    "                    <td><strong>{{ a.subject.name }}</strong></td>\n"
    "                    <td>{% if a.teacher %}{{ a.teacher.full_name }}{% else %}<em>Unassigned</em>{% endif %}</td>\n"
    "                    <td>{{ a.created_at|date:\"Y-m-d\" }}</td>\n"
    "                    <td>{% if a.teacher %}<span class=\"badge badge-success\">Active</span>{% else %}<span class=\"badge badge-muted\">No teacher</span>{% endif %}</td>\n"
    "                    <td>{% if a.teacher %}<a href=\"{% url 'staff_profile' schema_name=tenant.schema_name staff_id=a.teacher.id %}\" class=\"action-link\">View Profile</a>{% else %}&mdash;{% endif %}</td>\n"
    "                </tr>\n"
    "                {% endfor %}\n"
    "            </tbody>\n"
    "        </table>\n"
    "    </div>\n"
    "    {% else %}\n"
    "    <div class=\"section-body\"><p class=\"empty-inline\">No subjects assigned to this class yet.</p></div>\n"
    "    {% endif %}\n"
    "</div>\n"
    "\n"
    "<!-- Students -->\n"
    "<div class=\"section-card\">\n"
    "    <div class=\"section-header\"><h2>Students ({{ students|length }})</h2></div>\n"
    "    {% if students %}\n"
    "    <div class=\"table-responsive\">\n"
    "        <table class=\"data-table\">\n"
    "            <thead>\n"
    "                <tr>\n"
    "                    <th>Roll No</th>\n"
    "                    <th>Full Name</th>\n"
    "                    <th>Father Name</th>\n"
    "                    <th>Gender</th>\n"
    "                    <th>Status</th>\n"
    "                    <th>Action</th>\n"
    "                </tr>\n"
    "            </thead>\n"
    "            <tbody>\n"
    "                {% for s in students %}\n"
    "                <tr>\n"
    "                    <td><strong>{{ s.roll_number|default:\"—\" }}</strong></td>\n"
    "                    <td>{{ s.name }}</td>\n"
    "                    <td>{{ s.father_name }}</td>\n"
    "                    <td>{% if s.gender == 'male' %}Male{% elif s.gender == 'female' %}Female{% else %}&mdash;{% endif %}</td>\n"
    "                    <td>\n"
    "                        {% if s.status == 'active' %}<span class=\"badge badge-success\">Active</span>\n"
    "                        {% elif s.status == 'suspended' %}<span class=\"badge badge-warn\">Suspended</span>\n"
    "                        {% else %}<span class=\"badge badge-muted\">{{ s.get_status_display }}</span>{% endif %}\n"
    "                    </td>\n"
    "                    <td>\n"
    "                        <a href=\"{% url 'student_profile' schema_name=tenant.schema_name student_id=s.id %}\" class=\"action-link\">\n"
    "                            <svg width=\"12\" height=\"12\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.2\"><path d=\"M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/></svg>\n"
    "                            View Profile\n"
    "                        </a>\n"
    "                    </td>\n"
    "                </tr>\n"
    "                {% endfor %}\n"
    "            </tbody>\n"
    "        </table>\n"
    "    </div>\n"
    "    {% else %}\n"
    "    <div class=\"section-body\"><p class=\"empty-inline\">No active or suspended students in this class.</p></div>\n"
    "    {% endif %}\n"
    "</div>\n"
    "{% endblock %}\n"
)


SINGLE_DETAILED_TEMPLATE = WING_DETAILED_TEMPLATE  # same body, only filename differs


# =====================================================================
# Patches for existing files
# =====================================================================

def patch_views_init(target, dry_run, verbose):
    """Append new view imports at end of views/__init__.py."""
    path = target / 'axis_saas' / 'views' / '__init__.py'
    _log(f"Patching {path}")
    content = _read(path)
    if content is None:
        return False

    if 'wing_class_detailed_view' in content:
        _log("  - imports already present, skipping")
        return True

    block = (
        "\n"
        "# --- Class Detailed page views — added by add_class_detailed_page ---\n"
        "from .wing_class_detailed import *  # noqa: F401,F403\n"
        "from .wing_class_detailed import wing_class_detailed_view, class_detailed_view  # noqa: F401\n"
        "from .single_class_detailed import *  # noqa: F401,F403\n"
        "from .single_class_detailed import single_class_detailed_view  # noqa: F401\n"
    )
    if not content.endswith('\n'):
        content += '\n'
    content += block
    _log("  + appended detailed-view imports")
    return _write(path, content, dry_run, verbose)


def patch_public_urls(target, dry_run, verbose):
    """Register the /my-classes/<id>/ URL and import the view."""
    path = target / 'axis_saas' / 'public_urls.py'
    _log(f"Patching {path}")
    content = _read(path)
    if content is None:
        return False

    changed = False

    # (1) Extend the import line
    if 'class_detailed_view' in content:
        _log("  - class_detailed_view already imported, skipping import")
    else:
        old_import = ', classes_management_view'
        if old_import in content:
            content = content.replace(
                old_import,
                ', classes_management_view, class_detailed_view',
                1,
            )
            _log("  + extended .views import line")
            changed = True
        else:
            _log("  WARN: could not extend import line")

    # (2) Register URL pattern
    url_anchor = (
        "    path('portal/<slug:schema_name>/my-classes/', "
        "portal_wrapper(login_required_for_schema(classes_management_view)), "
        "name='classes_management'),"
    )
    if "name='class_detailed'" in content:
        _log("  - class_detailed URL already registered, skipping")
    elif url_anchor in content:
        new_url = (
            url_anchor + "\n"
            "    path('portal/<slug:schema_name>/my-classes/<int:class_id>/', "
            "portal_wrapper(login_required_for_schema(class_detailed_view)), "
            "name='class_detailed'),"
        )
        content = content.replace(url_anchor, new_url, 1)
        _log("  + registered /my-classes/<id>/ route")
        changed = True
    else:
        _log("  WARN: could not find my-classes URL anchor")

    if not changed:
        _log("  no changes needed")
        return True
    return _write(path, content, dry_run, verbose)


def patch_card_template(target, rel_path, dry_run, verbose):
    """Insert the View button HTML and JS wiring into a classes card template."""
    path = target / rel_path
    _log(f"Patching {path}")
    content = _read(path)
    if content is None:
        return False

    changed = False

    # --- (1) Insert the View button before the Delete button ---
    html_anchor = (
        "        <div class=\"class-card-actions\">\n"
        "            <button type=\"button\" class=\"action-icon delete-btn\" title=\"Delete class\">"
    )
    html_new = (
        "        <div class=\"class-card-actions\">\n"
        "            <button type=\"button\" class=\"action-icon view-btn\" title=\"View class details\">\n"
        "                <svg width=\"13\" height=\"13\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\">\n"
        "                    <path d=\"M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z\"/>\n"
        "                    <circle cx=\"12\" cy=\"12\" r=\"3\"/>\n"
        "                </svg>\n"
        "                View\n"
        "            </button>\n"
        "            <button type=\"button\" class=\"action-icon delete-btn\" title=\"Delete class\">"
    )
    if 'class="action-icon view-btn"' in content:
        _log("  - View button already present, skipping HTML")
    elif html_anchor in content:
        content = content.replace(html_anchor, html_new, 1)
        _log("  + injected View button HTML")
        changed = True
    else:
        _log("  WARN: could not find card-actions anchor")

    # --- (2) Wire the View button in JS ---
    js_anchor = (
        "    document.querySelectorAll('.class-card').forEach(card => {\n"
        "        const delBtn = card.querySelector('.delete-btn');"
    )
    js_new = (
        "    document.querySelectorAll('.class-card').forEach(card => {\n"
        "        const viewBtn = card.querySelector('.view-btn');\n"
        "        if (viewBtn) {\n"
        "            viewBtn.addEventListener('click', () => {\n"
        "                const id = card.dataset.classId;\n"
        "                window.location.href = `/portal/${SCHEMA}/my-classes/${id}/`;\n"
        "            });\n"
        "        }\n"
        "        const delBtn = card.querySelector('.delete-btn');"
    )
    if "card.querySelector('.view-btn')" in content:
        _log("  - View button JS already present, skipping")
    elif js_anchor in content:
        content = content.replace(js_anchor, js_new, 1)
        _log("  + wired View button JS")
        changed = True
    else:
        _log("  WARN: could not find card-foreach JS anchor")

    if not changed:
        _log("  no changes needed")
        return True
    return _write(path, content, dry_run, verbose)


# =====================================================================
# main
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "Add a per-class detailed page (analytics + class teacher + "
            "subject teachers + student list) reachable from a View button "
            "on each class card in the Classes Management page."
        )
    )
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--target-dir', default='.')
    args = parser.parse_args()

    target = Path(args.target_dir).resolve()
    _log(f"Target dir: {target}")
    _log(f"Dry-run:    {args.dry_run}")
    _log(f"Verbose:    {args.verbose}")
    print('-' * 60)

    if not (target / 'manage.py').exists():
        _log("WARNING: manage.py not found at target root. Continuing anyway.")

    ok = True

    print('-' * 60)
    _log("STEP 1: Create new view files")
    ok &= _ensure_file(
        target / 'axis_saas' / 'views' / 'wing_class_detailed.py',
        WING_CLASS_DETAILED_VIEW, args.dry_run, args.verbose,
    )
    ok &= _ensure_file(
        target / 'axis_saas' / 'views' / 'single_class_detailed.py',
        SINGLE_CLASS_DETAILED_VIEW, args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 2: Create new templates")
    ok &= _ensure_file(
        target / 'templates' / 'tenant' / 'wing_class_detailed.html',
        WING_DETAILED_TEMPLATE, args.dry_run, args.verbose,
    )
    ok &= _ensure_file(
        target / 'templates' / 'tenant' / 'single_class_detailed.html',
        SINGLE_DETAILED_TEMPLATE, args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 3: Import new views in axis_saas/views/__init__.py")
    ok &= patch_views_init(target, args.dry_run, args.verbose)

    print('-' * 60)
    _log("STEP 4: Register /my-classes/<id>/ route in public_urls.py")
    ok &= patch_public_urls(target, args.dry_run, args.verbose)

    print('-' * 60)
    _log("STEP 5: Patch templates/tenant/wing_classes.html (View button)")
    ok &= patch_card_template(
        target, 'templates/tenant/wing_classes.html',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 6: Patch templates/tenant/single_classes.html (View button)")
    ok &= patch_card_template(
        target, 'templates/tenant/single_classes.html',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    if ok:
        _log("DONE.")
        if not args.dry_run:
            _log("Next steps:")
            _log("  1. Restart Django dev server:  python3 manage.py runserver")
            _log("  2. Open /portal/<schema>/my-classes/")
            _log("  3. Click the new 'View' button on any class card.")
            _log("     -> You will land on the class's detailed page.")
        return 0
    _log("FAILED: one or more steps did not complete.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
