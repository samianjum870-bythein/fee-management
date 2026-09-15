#!/usr/bin/env python3
"""
axis_patcher.py
===============

SUBSTITUTE_FIXTURE_V1
---------------------

Adds a "Today's Leave & Fixtures" system to the
    /portal/<schema>/timetable/assign-teachers/
page.

What it does
------------
1. Adds a new model `SubstituteAssignment` that records: which absent
   teacher, on which date / day / period / class / subject, was covered
   by which substitute teacher.

2. Extends `axis_saas/views/assign_teachers.py` with three endpoints:
     * GET   /portal/<schema>/api/timetable/todays-leave/
              -> today's approved-leave teachers + their periods today +
                 the free teachers available at each of those periods,
                 plus any existing substitute assignment for that slot.
     * POST  /portal/<schema>/api/timetable/substitute/create/
              -> create / update a substitute assignment.
     * POST  /portal/<schema>/api/timetable/substitute/delete/
              -> remove a substitute assignment.
     * GET   /portal/<schema>/api/timetable/substitute/records/
              -> full history of substitute assignments.

3. Updates the template
     templates/tenant/timetable_assign_teachers.html
   with:
     * a "📜 See Records" button in the page header,
     * a new red-accented "🚨 Today's Leave & Fixtures" card that
       auto-loads on page open,
     * a full-screen overlay listing every substitute record with
       date, day, period, class, subject, absent teacher, substitute
       teacher, who created it, and when.

4. Adds the four new URL routes to `axis_saas/public_urls.py`.

5. Adds the four new views to the `assign_teachers` re-export block
   in `axis_saas/views/__init__.py`.

Everything is hooked into the existing Leave Management system: a
teacher counts as "on leave today" iff they have an APPROVED
LeaveRequest whose `start_date <= today <= end_date`.

Idempotent — safe to re-run.

Usage:
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py --target-dir /path/to/project
    python3 axis_patcher.py                 # apply in place
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------
def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def _replace_once(content, old, new, label, verbose):
    """Replace first literal occurrence. Returns (content, changed)."""
    if new.strip() and new.strip() in content:
        if verbose:
            log(f"  SKIP (already patched): {label}")
        return content, False
    if old not in content:
        log(f"  WARN: {label} — anchor not found; leaving it alone.")
        return content, False
    content = content.replace(old, new, 1)
    if verbose:
        log(f"  patched: {label}")
    return content, True


def _write(path, content, dry_run, verbose, label):
    if dry_run:
        log(f"  DRY-RUN: would write {path} ({label})")
        return True
    try:
        path.write_text(content, encoding='utf-8')
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as exc:
        log(f"  ERROR writing {path}: {exc}")
        return False


# =====================================================================
# 1) Models — add SubstituteAssignment
# =====================================================================
MODELS_REL = Path('axis_saas') / 'models.py'

MODELS_ANCHOR = '''    def __str__(self):
        subject_name = self.subject.name if self.subject else '—'
        return f"{self.school_class} | D{self.day_of_week} P{self.period_order}: {subject_name}"
'''

MODELS_NEW = '''    def __str__(self):
        subject_name = self.subject.name if self.subject else '—'
        return f"{self.school_class} | D{self.day_of_week} P{self.period_order}: {subject_name}"


# ========== SUBSTITUTE FIXTURES (SUBSTITUTE_FIXTURE_V1) ==========

class SubstituteAssignment(models.Model):
    """One-day substitute / fixture assignment.

    When a teacher has an approved leave that covers `date`, another
    teacher who is FREE at that exact (day_of_week, period_order) can be
    assigned as a substitute for the absent teacher's period in a
    specific class. This is scoped to a single calendar date — it does
    NOT modify PeriodTeacherAssignment.
    """
    DAY_CHOICES = [
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'),
        (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    ]

    absent_teacher = models.ForeignKey(
        'Staff',
        on_delete=models.CASCADE,
        related_name='substitute_absences',
    )
    substitute_teacher = models.ForeignKey(
        'Staff',
        on_delete=models.CASCADE,
        related_name='substitute_assignments',
    )
    school_class = models.ForeignKey(
        'SchoolClass',
        on_delete=models.CASCADE,
        related_name='substitute_assignments',
    )
    subject = models.ForeignKey(
        'Subject',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='substitute_assignments',
    )
    day_of_week = models.IntegerField(choices=DAY_CHOICES)
    period_order = models.PositiveIntegerField()
    date = models.DateField(default=date.today)
    reason = models.CharField(max_length=255, blank=True, default='')
    created_by = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', 'day_of_week', 'period_order']
        unique_together = [
            ('school_class', 'day_of_week', 'period_order', 'date'),
        ]

    def __str__(self):
        sub = self.substitute_teacher.full_name if self.substitute_teacher else '?'
        abs_ = self.absent_teacher.full_name if self.absent_teacher else '?'
        return (
            f"{self.date} P{self.period_order} "
            f"{self.school_class}: {abs_} → {sub}"
        )
'''


def patch_models(root, dry_run, verbose):
    path = root / MODELS_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = path.read_text(encoding='utf-8')

    if 'class SubstituteAssignment(models.Model)' in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, changed = _replace_once(
        content, MODELS_ANCHOR, MODELS_NEW,
        "models.py: SubstituteAssignment", verbose,
    )
    if not changed:
        return False
    return _write(path, content, dry_run, verbose, "SubstituteAssignment model")


# =====================================================================
# 2) Migration — 0027_substitute_assignment.py
# =====================================================================
MIGRATION_REL = (
    Path('axis_saas') / 'migrations' / '0027_substitute_assignment.py'
)

MIGRATION_CONTENT = '''# Generated by axis_patcher — SUBSTITUTE_FIXTURE_V1
#
# Adds the SubstituteAssignment model. One row per (class, day, period,
# date) covering a teacher who was absent on approved leave and the
# substitute teacher who took that specific period for that one day.

import datetime

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('axis_saas', '0026_leave_suspensions'),
    ]

    operations = [
        migrations.CreateModel(
            name='SubstituteAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day_of_week', models.IntegerField(choices=[(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday')])),
                ('period_order', models.PositiveIntegerField()),
                ('date', models.DateField(default=datetime.date.today)),
                ('reason', models.CharField(blank=True, default='', max_length=255)),
                ('created_by', models.CharField(blank=True, default='', max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('absent_teacher', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='substitute_absences',
                    to='axis_saas.staff',
                )),
                ('school_class', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='substitute_assignments',
                    to='axis_saas.schoolclass',
                )),
                ('subject', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='substitute_assignments',
                    to='axis_saas.subject',
                )),
                ('substitute_teacher', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='substitute_assignments',
                    to='axis_saas.staff',
                )),
            ],
            options={
                'ordering': ['-date', 'day_of_week', 'period_order'],
                'unique_together': {('school_class', 'day_of_week', 'period_order', 'date')},
            },
        ),
    ]
'''


def patch_migration(root, dry_run, verbose):
    path = root / MIGRATION_REL
    if path.is_file():
        log(f"SKIP (already exists): {path}")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    return _write(path, MIGRATION_CONTENT, dry_run, verbose, "new migration")


# =====================================================================
# 3) Views — extend assign_teachers.py
# =====================================================================
VIEWS_REL = Path('axis_saas') / 'views' / 'assign_teachers.py'

VIEWS_APPEND = '''

# =====================================================================
# SUBSTITUTE_FIXTURE_V1
# ---------------------------------------------------------------------
# Today's leave teachers + their periods + free substitute teachers +
# the fixture-assignment endpoints.
# =====================================================================

def _today_dow():
    from datetime import date as _d
    return _d.today().weekday()


def _today_date():
    from datetime import date as _d
    return _d.today()


def _day_label(dow):
    return ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
            'Friday', 'Saturday', 'Sunday'][dow]


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_get_todays_leave(request, schema_name):
    """GET: today's approved-leave teachers + their periods today +
    the free teachers available at each of those periods.

    Also returns any existing SubstituteAssignment for the same
    (class, day, period, date) so the UI can show "✓ already covered".
    """
    from datetime import date as _date

    from ..models import (
        LeaveRequest, Staff, PeriodTeacherAssignment, SubstituteAssignment,
    )

    tenant = get_tenant(request, schema_name)
    today = _date.today()
    today_dow = today.weekday()

    with schema_context(schema_name):
        leave_qs = (
            LeaveRequest.objects
            .filter(
                status='approved',
                start_date__lte=today,
                end_date__gte=today,
            )
            .select_related('staff')
        )
        on_leave_map = {lv.staff_id: lv for lv in leave_qs if lv.staff_id}
        on_leave_ids = set(on_leave_map.keys())

        if not on_leave_ids:
            return JsonResponse({
                'success': True,
                'date': today.isoformat(),
                'day_name': _day_label(today_dow),
                'day_of_week': today_dow,
                'on_leave_count': 0,
                'assignments': [],
            })

        # Every period the absent teachers are supposed to teach today.
        absent_periods = (
            PeriodTeacherAssignment.objects
            .filter(teacher_id__in=on_leave_ids, day_of_week=today_dow)
            .select_related(
                'teacher', 'school_class', 'school_class__wing_category',
                'subject',
            )
            .order_by('period_order', 'school_class__name',
                      'school_class__section')
        )

        # Busy map: which teacher has a PeriodTeacherAssignment today
        # at which period (across all classes).
        busy_rows = (
            PeriodTeacherAssignment.objects
            .filter(day_of_week=today_dow, teacher__isnull=False)
            .values_list('teacher_id', 'period_order')
        )
        busy_by_period = {}
        for _tid, _porder in busy_rows:
            busy_by_period.setdefault(_porder, set()).add(_tid)

        # Existing substitute assignments today.
        existing_subs = {}
        for sa in (
            SubstituteAssignment.objects
            .filter(date=today, day_of_week=today_dow)
            .select_related('substitute_teacher')
        ):
            existing_subs[(sa.school_class_id, sa.period_order)] = sa

        # Same map keyed by (substitute_teacher_id, period_order) so we
        # can exclude already-used substitutes from the free list.
        already_used_sub_by_period = {}
        for sa in existing_subs.values():
            already_used_sub_by_period.setdefault(
                sa.period_order, set()
            ).add(sa.substitute_teacher_id)

        all_active_teachers = list(
            Staff.objects.filter(status='active').order_by('full_name')
        )

        assignments = []
        for ap in absent_periods:
            absent_teacher = ap.teacher
            if absent_teacher is None:
                continue
            period = ap.period_order
            cls = ap.school_class
            subject = ap.subject

            busy_at_period = busy_by_period.get(period, set())
            used_subs = already_used_sub_by_period.get(period, set())

            free_teachers = []
            for t in all_active_teachers:
                if t.id == absent_teacher.id:
                    continue
                if t.id in on_leave_ids:
                    continue
                if t.id in busy_at_period:
                    continue
                if t.id in used_subs:
                    continue
                free_teachers.append({
                    'id': t.id,
                    'name': t.full_name,
                    'job_title': t.job_title or '',
                })

            leave = on_leave_map.get(absent_teacher.id)
            existing_sa = existing_subs.get((cls.id, period))

            try:
                cls_display = get_class_display_name(
                    cls, tenant.tenant_type,
                )
            except Exception:
                cls_display = str(cls)

            assignments.append({
                'absent_teacher_id': absent_teacher.id,
                'absent_teacher_name': absent_teacher.full_name,
                'absent_teacher_job_title': absent_teacher.job_title or '',
                'leave_reason': (leave.reason or '') if leave else '',
                'leave_start': leave.start_date.isoformat() if leave else '',
                'leave_end': leave.end_date.isoformat() if leave else '',
                'class_id': cls.id,
                'class_display': cls_display,
                'subject_id': subject.id if subject else None,
                'subject_name': subject.name if subject else '',
                'day_of_week': today_dow,
                'period_order': period,
                'free_teachers': free_teachers,
                'existing_substitute': ({
                    'id': existing_sa.id,
                    'teacher_id': existing_sa.substitute_teacher_id,
                    'teacher_name': (
                        existing_sa.substitute_teacher.full_name
                        if existing_sa.substitute_teacher else ''
                    ),
                } if existing_sa else None),
            })

        return JsonResponse({
            'success': True,
            'date': today.isoformat(),
            'day_name': _day_label(today_dow),
            'day_of_week': today_dow,
            'on_leave_count': len(on_leave_ids),
            'assignments': assignments,
        })


@csrf_exempt
@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_create_substitute(request, schema_name):
    """POST: create / update a substitute assignment for today."""
    from datetime import date as _date

    from ..models import (
        Staff, SchoolClass, Subject, PeriodTeacherAssignment,
        SubstituteAssignment, LeaveRequest,
    )

    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse(
            {'success': False, 'error': 'Invalid JSON'}, status=400,
        )

    def _to_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    absent_teacher_id = _to_int(data.get('absent_teacher_id'))
    substitute_teacher_id = _to_int(data.get('substitute_teacher_id'))
    class_id = _to_int(data.get('class_id'))
    subject_id = _to_int(data.get('subject_id'))
    period_order = _to_int(data.get('period_order'))

    if not (absent_teacher_id and substitute_teacher_id and class_id
            and period_order):
        return JsonResponse(
            {'success': False,
             'error': 'absent_teacher_id, substitute_teacher_id, class_id '
                      'and period_order are required.'},
            status=400,
        )
    if absent_teacher_id == substitute_teacher_id:
        return JsonResponse(
            {'success': False,
             'error': 'The substitute cannot be the same as the absent '
                      'teacher.'},
            status=400,
        )

    today = _date.today()
    today_dow = today.weekday()

    with schema_context(schema_name):
        absent = Staff.objects.filter(id=absent_teacher_id).first()
        substitute = Staff.objects.filter(
            id=substitute_teacher_id, status='active',
        ).first()
        school_class = SchoolClass.objects.filter(id=class_id).first()
        if not (absent and substitute and school_class):
            return JsonResponse(
                {'success': False, 'error': 'Invalid teacher or class.'},
                status=400,
            )

        subject = None
        if subject_id:
            subject = Subject.objects.filter(id=subject_id).first()

        # The absent teacher must actually be on approved leave today.
        on_leave = LeaveRequest.objects.filter(
            staff=absent,
            status='approved',
            start_date__lte=today,
            end_date__gte=today,
        ).exists()
        if not on_leave:
            return JsonResponse({
                'success': False,
                'error': (
                    f'{absent.full_name} is not on approved leave today. '
                    f'Cannot create a substitute.'
                ),
            }, status=400)

        # The substitute must be free at this period.
        busy = PeriodTeacherAssignment.objects.filter(
            teacher=substitute,
            day_of_week=today_dow,
            period_order=period_order,
        ).exclude(school_class=school_class).exists()
        if busy:
            return JsonResponse({
                'success': False,
                'error': (
                    f'{substitute.full_name} is already teaching another '
                    f'class at period {period_order}.'
                ),
            }, status=400)

        # The substitute must not already be covering another class at
        # the same period today.
        clash = SubstituteAssignment.objects.filter(
            substitute_teacher=substitute,
            date=today,
            day_of_week=today_dow,
            period_order=period_order,
        ).exclude(school_class=school_class).first()
        if clash:
            return JsonResponse({
                'success': False,
                'error': (
                    f'{substitute.full_name} is already covering '
                    f'{clash.school_class} at this period.'
                ),
            }, status=400)

        obj, created = SubstituteAssignment.objects.update_or_create(
            school_class=school_class,
            day_of_week=today_dow,
            period_order=period_order,
            date=today,
            defaults={
                'absent_teacher': absent,
                'substitute_teacher': substitute,
                'subject': subject,
                'reason': (data.get('reason') or '').strip()[:255] or (
                    f'{absent.full_name} on leave'
                ),
                'created_by': request.session.get(
                    'school_admin_username', 'admin',
                ) or 'admin',
            },
        )

        return JsonResponse({
            'success': True,
            'created': created,
            'id': obj.id,
        })


@csrf_exempt
@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_substitute(request, schema_name):
    """POST: delete a substitute assignment by id."""
    from ..models import SubstituteAssignment

    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse(
            {'success': False, 'error': 'Invalid JSON'}, status=400,
        )

    sub_id = data.get('id')
    try:
        sub_id = int(sub_id)
    except (TypeError, ValueError):
        return JsonResponse(
            {'success': False, 'error': 'id required'}, status=400,
        )

    with schema_context(schema_name):
        sa = SubstituteAssignment.objects.filter(id=sub_id).first()
        if sa is None:
            return JsonResponse(
                {'success': False, 'error': 'Record not found'}, status=404,
            )
        sa.delete()
        return JsonResponse({'success': True})


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_get_substitute_records(request, schema_name):
    """GET: full history of substitute assignments, newest first."""
    from ..models import SubstituteAssignment

    with schema_context(schema_name):
        qs = (
            SubstituteAssignment.objects
            .select_related(
                'absent_teacher', 'substitute_teacher',
                'school_class', 'school_class__wing_category',
                'subject',
            )
            .order_by('-date', '-created_at')[:500]
        )

        records = []
        for sa in qs:
            try:
                cls_label = str(sa.school_class) if sa.school_class else ''
            except Exception:
                cls_label = ''
            records.append({
                'id': sa.id,
                'date': sa.date.isoformat(),
                'day_of_week': sa.day_of_week,
                'day_name': sa.get_day_of_week_display(),
                'period_order': sa.period_order,
                'class_name': cls_label,
                'subject_name': sa.subject.name if sa.subject else '',
                'absent_teacher_name': (
                    sa.absent_teacher.full_name if sa.absent_teacher else ''
                ),
                'substitute_teacher_name': (
                    sa.substitute_teacher.full_name
                    if sa.substitute_teacher else ''
                ),
                'reason': sa.reason or '',
                'created_by': sa.created_by or '',
                'created_at': (
                    sa.created_at.isoformat() if sa.created_at else ''
                ),
            })

        return JsonResponse({'success': True, 'records': records})
'''


def patch_views(root, dry_run, verbose):
    path = root / VIEWS_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = path.read_text(encoding='utf-8')

    if 'def api_get_todays_leave(request, schema_name):' in content:
        log(f"SKIP (already patched): {path}")
        return True

    # Ensure csrf_exempt / require_http_methods / json / schema_context
    # imports exist. They already do in this file, but be defensive.
    needed_imports = []
    if 'import json' not in content:
        needed_imports.append('import json')
    if 'from django.views.decorators.csrf import csrf_exempt' not in content:
        needed_imports.append(
            'from django.views.decorators.csrf import csrf_exempt'
        )
    if 'from django.views.decorators.http import require_http_methods' not in content:
        needed_imports.append(
            'from django.views.decorators.http import require_http_methods'
        )
    if 'from django_tenants.utils import schema_context' not in content:
        needed_imports.append(
            'from django_tenants.utils import schema_context'
        )
    if needed_imports:
        content = '\n'.join(needed_imports) + '\n' + content

    content = content.rstrip() + '\n' + VIEWS_APPEND
    return _write(path, content, dry_run, verbose, "assign_teachers views")


# =====================================================================
# 4) views/__init__.py — re-export new views
# =====================================================================
VIEWS_INIT_REL = Path('axis_saas') / 'views' / '__init__.py'

VIEWS_INIT_OLD = '''from .assign_teachers import (  # noqa: F401
    timetable_assign_teachers,
    api_get_teacher_assignments,
    api_save_teacher_assignments,
)'''

VIEWS_INIT_NEW = '''from .assign_teachers import (  # noqa: F401
    timetable_assign_teachers,
    api_get_teacher_assignments,
    api_save_teacher_assignments,
    api_get_todays_leave,
    api_create_substitute,
    api_delete_substitute,
    api_get_substitute_records,
)'''


def patch_views_init(root, dry_run, verbose):
    path = root / VIEWS_INIT_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = path.read_text(encoding='utf-8')

    if 'api_get_todays_leave' in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, changed = _replace_once(
        content, VIEWS_INIT_OLD, VIEWS_INIT_NEW,
        "views/__init__.py exports", verbose,
    )
    if not changed:
        # Fallback: append a fresh import block at the end.
        content = content.rstrip() + '\n\n' + VIEWS_INIT_NEW + '\n'
    return _write(path, content, dry_run, verbose, "views __init__ exports")


# =====================================================================
# 5) public_urls.py — import + register new endpoints
# =====================================================================
URLS_REL = Path('axis_saas') / 'public_urls.py'

URLS_IMPORT_OLD = '''from .views.assign_teachers import (
    timetable_assign_teachers,
    api_get_teacher_assignments,
    api_save_teacher_assignments,
)'''

URLS_IMPORT_NEW = '''from .views.assign_teachers import (
    timetable_assign_teachers,
    api_get_teacher_assignments,
    api_save_teacher_assignments,
    api_get_todays_leave,
    api_create_substitute,
    api_delete_substitute,
    api_get_substitute_records,
)'''

URLS_ANCHOR = '''    path('portal/<slug:schema_name>/api/timetable/teacher-assignments/<int:class_id>/save/',
         portal_wrapper(login_required_for_schema(api_save_teacher_assignments)),
         name='api_save_teacher_assignments'),
'''

URLS_NEW = '''    path('portal/<slug:schema_name>/api/timetable/teacher-assignments/<int:class_id>/save/',
         portal_wrapper(login_required_for_schema(api_save_teacher_assignments)),
         name='api_save_teacher_assignments'),
    # ===== SUBSTITUTE_FIXTURE_V1 =====
    path('portal/<slug:schema_name>/api/timetable/todays-leave/',
         portal_wrapper(login_required_for_schema(api_get_todays_leave)),
         name='api_timetable_todays_leave'),
    path('portal/<slug:schema_name>/api/timetable/substitute/create/',
         portal_wrapper(login_required_for_schema(api_create_substitute)),
         name='api_timetable_substitute_create'),
    path('portal/<slug:schema_name>/api/timetable/substitute/delete/',
         portal_wrapper(login_required_for_schema(api_delete_substitute)),
         name='api_timetable_substitute_delete'),
    path('portal/<slug:schema_name>/api/timetable/substitute/records/',
         portal_wrapper(login_required_for_schema(api_get_substitute_records)),
         name='api_timetable_substitute_records'),
'''


def patch_urls(root, dry_run, verbose):
    path = root / URLS_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = path.read_text(encoding='utf-8')

    if 'api_timetable_todays_leave' in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, c1 = _replace_once(
        content, URLS_IMPORT_OLD, URLS_IMPORT_NEW,
        "public_urls.py imports", verbose,
    )
    content, c2 = _replace_once(
        content, URLS_ANCHOR, URLS_NEW,
        "public_urls.py routes", verbose,
    )

    if not (c1 and c2):
        log("ERROR: public_urls.py anchors not found — aborting this file.")
        return False
    return _write(path, content, dry_run, verbose, "public_urls routes")


# =====================================================================
# 6) Template — add See Records button + Today's Leave card + overlay
# =====================================================================
TEMPLATE_REL = (
    Path('templates') / 'tenant' / 'timetable_assign_teachers.html'
)

TEMPLATE_BTN_OLD = '''        <button type="button" id="openAssignTeachersBtn" class="btn-primary btn-lg">📅 Assign Periods to Teachers</button>'''

TEMPLATE_BTN_NEW = '''        <button type="button" id="seeRecordsBtn" class="btn-secondary btn-lg">📜 See Records</button>
        <button type="button" id="openAssignTeachersBtn" class="btn-primary btn-lg">📅 Assign Periods to Teachers</button>'''

TEMPLATE_LEAVE_CARD_ANCHOR = '''{% if classes_without_timetable %}
<div class="card" style="border-left:4px solid #f59e0b;">'''

TEMPLATE_LEAVE_CARD_NEW = '''<!-- ===== SUBSTITUTE_FIXTURE_V1: Today's Leave & Fixtures ===== -->
<div class="card" id="todayLeaveCard" style="border-left:4px solid #ef4444;">
    <div class="card-header">
        <h3>🚨 Today's Leave &amp; Fixtures</h3>
        <span class="text-muted" style="font-size:0.85rem;" id="todayLeaveMeta">Loading…</span>
    </div>
    <div id="todayLeaveBody">
        <p class="text-muted" style="text-align:center; padding:1.5rem 0;">Loading…</p>
    </div>
</div>
<!-- ===== END SUBSTITUTE_FIXTURE_V1 ===== -->

{% if classes_without_timetable %}
<div class="card" style="border-left:4px solid #f59e0b;">'''

TEMPLATE_END_ANCHOR = '''    // ===== END INLINE_TIMETABLE_ASSIGN_V1 =====
})();
</script>
{% endblock %}'''

TEMPLATE_END_NEW = '''    // ===== END INLINE_TIMETABLE_ASSIGN_V1 =====
})();
</script>

<!-- ===== SUBSTITUTE_FIXTURE_V1: Records overlay + client JS ===== -->
<div id="subRecordsOverlay" class="overlay">
    <div class="overlay-content">
        <h2>📜 Substitute / Fixture Records</h2>
        <div id="subRecordsBody">
            <p class="text-muted" style="text-align:center; padding:1.5rem 0;">Loading…</p>
        </div>
        <div class="form-actions">
            <button type="button" id="closeRecordsBtn" class="btn-secondary">Close</button>
        </div>
    </div>
</div>

<script>
(function () {
    'use strict';

    var SCHEMA = '{{ tenant.schema_name|escapejs }}';
    var API_LEAVE      = '/portal/' + SCHEMA + '/api/timetable/todays-leave/';
    var API_SUB_CREATE = '/portal/' + SCHEMA + '/api/timetable/substitute/create/';
    var API_SUB_DELETE = '/portal/' + SCHEMA + '/api/timetable/substitute/delete/';
    var API_SUB_RECORD = '/portal/' + SCHEMA + '/api/timetable/substitute/records/';

    var leaveCard     = document.getElementById('todayLeaveCard');
    var leaveMeta     = document.getElementById('todayLeaveMeta');
    var leaveBody     = document.getElementById('todayLeaveBody');
    var recordsOverlay = document.getElementById('subRecordsOverlay');
    var recordsBody   = document.getElementById('subRecordsBody');
    var seeRecordsBtn = document.getElementById('seeRecordsBtn');
    var closeRecordsBtn = document.getElementById('closeRecordsBtn');

    function esc(s) {
        if (s === null || s === undefined) return '';
        return String(s).replace(/[&<>"']/g, function (c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
        });
    }

    function getCookie(name) {
        var v = null;
        if (document.cookie) {
            document.cookie.split(';').forEach(function (c) {
                c = c.trim();
                if (c.substring(0, name.length + 1) === (name + '=')) {
                    v = decodeURIComponent(c.substring(name.length + 1));
                }
            });
        }
        return v;
    }

    // ---- Today's leave + fixtures -----------------------------------
    function loadTodaysLeave() {
        if (!leaveBody) return;
        leaveBody.innerHTML = '<p class="text-muted" style="text-align:center; padding:1.5rem 0;">Loading…</p>';
        fetch(API_LEAVE, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.success) {
                    leaveBody.innerHTML = '<p style="color:#dc2626; text-align:center;">Failed to load.</p>';
                    return;
                }
                renderTodaysLeave(data);
            })
            .catch(function (err) {
                leaveBody.innerHTML = '<p style="color:#dc2626; text-align:center;">Network error: ' + esc(err.message) + '</p>';
            });
    }

    function renderTodaysLeave(data) {
        if (leaveMeta) {
            leaveMeta.textContent = data.day_name + ' · ' + data.date +
                ' · ' + data.on_leave_count + ' teacher(s) on leave';
        }
        var list = data.assignments || [];
        if (list.length === 0) {
            leaveBody.innerHTML =
                '<p class="text-muted" style="text-align:center; padding:1.5rem 0;">' +
                '✅ No teachers on leave with scheduled periods today. Nothing to cover.</p>';
            return;
        }

        var html = '<div style="overflow-x:auto;">';
        html += '<table class="data-table">';
        html += '<thead><tr>';
        html += '<th>Period</th>';
        html += '<th>Absent Teacher</th>';
        html += '<th>Class</th>';
        html += '<th>Subject</th>';
        html += '<th>Leave</th>';
        html += '<th>Substitute</th>';
        html += '<th style="text-align:right;">Action</th>';
        html += '</tr></thead><tbody>';

        list.forEach(function (a, idx) {
            html += '<tr>';
            html += '<td><strong>P' + a.period_order + '</strong></td>';
            html += '<td><strong>' + esc(a.absent_teacher_name) + '</strong>' +
                    (a.absent_teacher_job_title ? '<br><span class="text-muted" style="font-size:0.78rem;">' + esc(a.absent_teacher_job_title) + '</span>' : '') +
                    '</td>';
            html += '<td>' + esc(a.class_display) + '</td>';
            html += '<td>' + esc(a.subject_name || '—') + '</td>';
            html += '<td><span class="tag" style="font-size:0.72rem;">' +
                    esc(a.leave_start) + ' → ' + esc(a.leave_end) + '</span></td>';

            if (a.existing_substitute) {
                html += '<td><span class="tag tag-green">✓ ' +
                        esc(a.existing_substitute.teacher_name) +
                        '</span></td>';
                html += '<td style="text-align:right; white-space:nowrap;">' +
                        '<button type="button" class="btn-danger btn-sm remove-sub-btn" data-id="' +
                        a.existing_substitute.id + '">Remove</button></td>';
            } else if (!a.free_teachers || a.free_teachers.length === 0) {
                html += '<td colspan="2" class="text-muted" style="text-align:center;">' +
                        'No free teacher available at this period</td>';
            } else {
                var opts = '<option value="">— Choose free teacher —</option>';
                a.free_teachers.forEach(function (t) {
                    opts += '<option value="' + t.id + '">' +
                            esc(t.name) +
                            (t.job_title ? ' (' + esc(t.job_title) + ')' : '') +
                            '</option>';
                });
                html += '<td><select class="form-control sub-select" data-idx="' +
                        idx + '" style="min-width:180px;">' + opts + '</select></td>';
                html += '<td style="text-align:right;">' +
                        '<button type="button" class="btn-success btn-sm assign-sub-btn" data-idx="' +
                        idx + '">Assign</button></td>';
            }
            html += '</tr>';
        });

        html += '</tbody></table></div>';
        leaveBody.innerHTML = html;

        leaveBody.querySelectorAll('.assign-sub-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var idx = parseInt(btn.getAttribute('data-idx'), 10);
                var sel = leaveBody.querySelector('.sub-select[data-idx="' + idx + '"]');
                var subId = sel ? sel.value : '';
                if (!subId) {
                    alert('Please choose a substitute teacher first.');
                    return;
                }
                createSubstitute(list[idx], parseInt(subId, 10), btn);
            });
        });
        leaveBody.querySelectorAll('.remove-sub-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var id = parseInt(btn.getAttribute('data-id'), 10);
                if (!confirm('Remove this substitute assignment?')) return;
                removeSubstitute(id, btn);
            });
        });
    }

    function createSubstitute(a, substituteId, btn) {
        if (btn) { btn.disabled = true; btn.textContent = 'Assigning…'; }
        fetch(API_SUB_CREATE, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken') || '',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({
                absent_teacher_id: a.absent_teacher_id,
                substitute_teacher_id: substituteId,
                class_id: a.class_id,
                subject_id: a.subject_id,
                period_order: a.period_order,
            })
        })
        .then(function (r) { return r.json(); })
        .then(function (res) {
            if (!res.success) {
                alert('Error: ' + (res.error || 'Unknown error'));
                if (btn) { btn.disabled = false; btn.textContent = 'Assign'; }
                return;
            }
            loadTodaysLeave();
        })
        .catch(function (err) {
            alert('Network error: ' + err.message);
            if (btn) { btn.disabled = false; btn.textContent = 'Assign'; }
        });
    }

    function removeSubstitute(id, btn) {
        if (btn) { btn.disabled = true; btn.textContent = 'Removing…'; }
        fetch(API_SUB_DELETE, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken') || '',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({ id: id })
        })
        .then(function (r) { return r.json(); })
        .then(function (res) {
            if (!res.success) {
                alert('Error: ' + (res.error || 'Unknown error'));
                if (btn) { btn.disabled = false; btn.textContent = 'Remove'; }
                return;
            }
            loadTodaysLeave();
        })
        .catch(function (err) {
            alert('Network error: ' + err.message);
            if (btn) { btn.disabled = false; btn.textContent = 'Remove'; }
        });
    }

    // ---- Records overlay --------------------------------------------
    function openRecords() {
        if (!recordsOverlay) return;
        recordsOverlay.classList.add('active');
        recordsBody.innerHTML = '<p class="text-muted" style="text-align:center; padding:1.5rem 0;">Loading…</p>';
        fetch(API_SUB_RECORD, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.success) {
                    recordsBody.innerHTML = '<p style="color:#dc2626; text-align:center;">Failed to load records.</p>';
                    return;
                }
                var recs = data.records || [];
                if (recs.length === 0) {
                    recordsBody.innerHTML = '<p class="text-muted" style="text-align:center; padding:1.5rem 0;">No substitute records yet.</p>';
                    return;
                }
                var html = '<div style="overflow-x:auto; max-height:60vh;">';
                html += '<table class="data-table">';
                html += '<thead><tr>';
                html += '<th>Date</th>';
                html += '<th>Day</th>';
                html += '<th>Period</th>';
                html += '<th>Class</th>';
                html += '<th>Subject</th>';
                html += '<th>Absent</th>';
                html += '<th>Substitute</th>';
                html += '<th>Created By</th>';
                html += '<th>Created At</th>';
                html += '</tr></thead><tbody>';
                recs.forEach(function (r) {
                    var createdShort = (r.created_at || '').replace('T', ' ').substring(0, 19);
                    html += '<tr>';
                    html += '<td>' + esc(r.date) + '</td>';
                    html += '<td>' + esc(r.day_name) + '</td>';
                    html += '<td><strong>P' + r.period_order + '</strong></td>';
                    html += '<td>' + esc(r.class_name) + '</td>';
                    html += '<td>' + esc(r.subject_name || '—') + '</td>';
                    html += '<td>' + esc(r.absent_teacher_name) + '</td>';
                    html += '<td><span class="tag tag-green">' +
                            esc(r.substitute_teacher_name) + '</span></td>';
                    html += '<td><span class="text-muted" style="font-size:0.78rem;">' +
                            esc(r.created_by || '—') + '</span></td>';
                    html += '<td><span class="text-muted" style="font-size:0.78rem;">' +
                            esc(createdShort) + '</span></td>';
                    html += '</tr>';
                });
                html += '</tbody></table></div>';
                recordsBody.innerHTML = html;
            })
            .catch(function (err) {
                recordsBody.innerHTML = '<p style="color:#dc2626; text-align:center;">Network error: ' + esc(err.message) + '</p>';
            });
    }

    if (seeRecordsBtn) seeRecordsBtn.addEventListener('click', openRecords);
    if (closeRecordsBtn) closeRecordsBtn.addEventListener('click', function () {
        recordsOverlay.classList.remove('active');
    });
    if (recordsOverlay) recordsOverlay.addEventListener('click', function (e) {
        if (e.target === recordsOverlay) recordsOverlay.classList.remove('active');
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && recordsOverlay && recordsOverlay.classList.contains('active')) {
            recordsOverlay.classList.remove('active');
        }
    });

    // Auto-load on page open.
    if (leaveCard) loadTodaysLeave();
})();
</script>
<!-- ===== END SUBSTITUTE_FIXTURE_V1 ===== -->
{% endblock %}'''


def patch_template(root, dry_run, verbose):
    path = root / TEMPLATE_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = path.read_text(encoding='utf-8')

    if 'SUBSTITUTE_FIXTURE_V1' in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, c1 = _replace_once(
        content, TEMPLATE_BTN_OLD, TEMPLATE_BTN_NEW,
        "template: See Records button", verbose,
    )
    content, c2 = _replace_once(
        content, TEMPLATE_LEAVE_CARD_ANCHOR, TEMPLATE_LEAVE_CARD_NEW,
        "template: Today's Leave card", verbose,
    )
    content, c3 = _replace_once(
        content, TEMPLATE_END_ANCHOR, TEMPLATE_END_NEW,
        "template: records overlay + JS", verbose,
    )

    if not (c1 and c2 and c3):
        log("ERROR: template anchors not all found — aborting this file.")
        return False
    return _write(path, content, dry_run, verbose, "timetable_assign_teachers.html")


# =====================================================================
# Main
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "SUBSTITUTE_FIXTURE_V1 — add a Today's-Leave & Fixtures system "
            "on /portal/<schema>/timetable/assign-teachers/."
        )
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without writing.')
    parser.add_argument('--verbose', action='store_true',
                        help='Verbose output.')
    parser.add_argument('--target-dir', default='.',
                        help='Project root (default: current dir).')
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not (root / 'manage.py').is_file():
        log(f"ERROR: manage.py not found in {root}. Wrong --target-dir?")
        return 1

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")

    ok = True

    log("--- 1/6: models.py (SubstituteAssignment) ---")
    ok &= patch_models(root, args.dry_run, args.verbose)

    log("--- 2/6: migrations/0027_substitute_assignment.py ---")
    ok &= patch_migration(root, args.dry_run, args.verbose)

    log("--- 3/6: views/assign_teachers.py ---")
    ok &= patch_views(root, args.dry_run, args.verbose)

    log("--- 4/6: views/__init__.py ---")
    ok &= patch_views_init(root, args.dry_run, args.verbose)

    log("--- 5/6: public_urls.py ---")
    ok &= patch_urls(root, args.dry_run, args.verbose)

    log("--- 6/6: templates/tenant/timetable_assign_teachers.html ---")
    ok &= patch_template(root, args.dry_run, args.verbose)

    if not ok:
        log("One or more steps failed. See messages above.")
        return 2

    log("Done.")
    if args.dry_run:
        log("Re-run without --dry-run to apply.")
    else:
        log("Next steps:")
        log("  1. python manage.py migrate_schemas --shared")
        log("  2. python manage.py migrate_schemas           # tenant schemas")
        log("  3. Restart the dev server.")
        log("  4. Visit /portal/<schema>/timetable/assign-teachers/")
        log("     — the red 'Today's Leave & Fixtures' card loads on open,")
        log("       and the '📜 See Records' button opens the history overlay.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
