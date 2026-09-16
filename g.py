#!/usr/bin/env python3
"""
axis_patcher.py — STAFF_ATTENDANCE_OVERHAUL_V1_FIX
====================================================

Repairs the admin attendance template (`templates/tenant/attendence.html`)
which the previous patcher failed to modify — no "Class Teacher
Permissions" button, no permissions modal, no logs modal, no
AXIS_ADMIN_ATT_PERMS JS module, no Logs button on auto-marked rows.

Also verifies every other file the previous patcher was supposed to
touch and repairs whatever is missing.

Idempotent and safe to re-run.

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


MARKER = "STAFF_ATTENDANCE_OVERHAUL_V1_FIX"


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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as e:
        log(f"  ERROR writing {path}: {e}")
        return False


# =====================================================================
# 1. MODELS — verify ClassTeacher* models exist
# =====================================================================

MODELS_APPEND = '''

# =====================================================================
# STAFF_ATTENDANCE_OVERHAUL_V1
# ---------------------------------------------------------------------
# Per-class attendance authority for the class teacher, controlled by
# the school admin from the attendance dashboard, plus a per-(class,
# date) edit counter that enforces the max-edits-per-date limit.
# =====================================================================


class ClassTeacherAttendancePermission(models.Model):
    """Per-class attendance authority for the class teacher.

    Only one row per SchoolClass. Created lazily on first access via
    :meth:`for_class`. Admin edits this row from the admin attendance
    dashboard.
    """
    BACKDATE_ACCESS_CHOICES = [
        ('none', "None - Only today's attendance"),
        ('read', 'Read only - View past, cannot edit'),
        ('read_write', 'Read & Write - View and edit past'),
    ]

    school_class = models.OneToOneField(
        'SchoolClass',
        on_delete=models.CASCADE,
        related_name='attendance_permission',
    )
    backdate_access = models.CharField(
        max_length=20,
        choices=BACKDATE_ACCESS_CHOICES,
        default='none',
        help_text=(
            "What the class teacher can do with past attendance. "
            "'none' = today only; 'read' = view-only history; "
            "'read_write' = view and edit history."
        ),
    )
    max_edits_per_date = models.PositiveIntegerField(
        default=1,
        help_text=(
            "Maximum number of times the class teacher can edit a "
            "single date's attendance before it permanently locks "
            "for the class teacher."
        ),
    )
    view_history_days = models.PositiveIntegerField(
        default=30,
        help_text=(
            "How many days back the class teacher can view the "
            "attendance records for this class."
        ),
    )
    edit_history_days = models.PositiveIntegerField(
        default=5,
        help_text=(
            "How many days back the class teacher can edit the "
            "attendance records for this class."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=150, blank=True, default='')

    class Meta:
        verbose_name = 'Class Teacher Attendance Permission'
        verbose_name_plural = 'Class Teacher Attendance Permissions'

    def __str__(self):
        return f"Attendance permission for {self.school_class}"

    @classmethod
    def for_class(cls, school_class):
        if school_class is None:
            return None
        obj, _ = cls.objects.get_or_create(school_class=school_class)
        return obj


class ClassTeacherEditQuota(models.Model):
    """Per-(class, date) counter of how many times the class teacher has
    edited the attendance."""
    school_class = models.ForeignKey(
        'SchoolClass',
        on_delete=models.CASCADE,
        related_name='attendance_edit_quotas',
    )
    date = models.DateField()
    teacher_edit_count = models.PositiveIntegerField(default=0)
    last_teacher_edit_at = models.DateTimeField(null=True, blank=True)
    last_teacher_edit_by_id = models.PositiveIntegerField(
        null=True, blank=True,
    )
    last_teacher_edit_by_name = models.CharField(
        max_length=150, blank=True, default='',
    )

    class Meta:
        unique_together = [('school_class', 'date')]
        ordering = ['-date']
        indexes = [
            models.Index(fields=['school_class', 'date']),
        ]

    def __str__(self):
        return (
            f"{self.school_class} {self.date} "
            f"(teacher edits: {self.teacher_edit_count})"
        )

    @classmethod
    def for_class_date(cls, school_class, on_date):
        obj, _ = cls.objects.get_or_create(
            school_class=school_class, date=on_date,
        )
        return obj
'''


def patch_models(root, args):
    path = root / "axis_saas" / "models.py"
    content = read_file(path)
    if content is None:
        return False
    if "class ClassTeacherAttendancePermission" in content:
        log(f"  OK (already present): {path}")
        return True
    log(f"  MISSING — appending new models to {path}")
    content = content.rstrip() + "\n" + MODELS_APPEND
    return write_file(path, content, args.dry_run, "add new models")


# =====================================================================
# 2. MIGRATION
# =====================================================================

MIGRATION_CONTENT = '''# Generated by axis_patcher — STAFF_ATTENDANCE_OVERHAUL_V1_FIX
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('axis_saas', '0033_attendance_auto_system_source'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClassTeacherAttendancePermission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('backdate_access', models.CharField(
                    choices=[
                        ('none', "None - Only today's attendance"),
                        ('read', 'Read only - View past, cannot edit'),
                        ('read_write', 'Read & Write - View and edit past'),
                    ],
                    default='none', max_length=20,
                )),
                ('max_edits_per_date', models.PositiveIntegerField(default=1)),
                ('view_history_days', models.PositiveIntegerField(default=30)),
                ('edit_history_days', models.PositiveIntegerField(default=5)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.CharField(blank=True, default='', max_length=150)),
                ('school_class', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='attendance_permission',
                    to='axis_saas.schoolclass',
                )),
            ],
            options={
                'verbose_name': 'Class Teacher Attendance Permission',
                'verbose_name_plural': 'Class Teacher Attendance Permissions',
            },
        ),
        migrations.CreateModel(
            name='ClassTeacherEditQuota',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('teacher_edit_count', models.PositiveIntegerField(default=0)),
                ('last_teacher_edit_at', models.DateTimeField(blank=True, null=True)),
                ('last_teacher_edit_by_id', models.PositiveIntegerField(blank=True, null=True)),
                ('last_teacher_edit_by_name', models.CharField(blank=True, default='', max_length=150)),
                ('school_class', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='attendance_edit_quotas',
                    to='axis_saas.schoolclass',
                )),
            ],
            options={
                'ordering': ['-date'],
                'unique_together': {('school_class', 'date')},
            },
        ),
        migrations.AddIndex(
            model_name='classteachereditquota',
            index=models.Index(
                fields=['school_class', 'date'],
                name='axis_saas_ct_quota_sd_idx',
            ),
        ),
    ]
'''


def create_migration(root, args):
    path = (root / "axis_saas" / "migrations"
            / "0034_class_teacher_attendance_permissions.py")
    if path.exists():
        log(f"  OK (already exists): {path}")
        return True
    log(f"  MISSING — creating {path}")
    return write_file(path, MIGRATION_CONTENT, args.dry_run, "new migration")


# =====================================================================
# 3. ADMIN VIEWS — verify new endpoints exist
# =====================================================================

ADMIN_APPEND = '''

# =====================================================================
# STAFF_ATTENDANCE_OVERHAUL_V1_FIX — class-teacher permission management
# =====================================================================


@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_class_teacher_permissions_list_api(request, schema_name):
    from ..models import ClassTeacherAttendancePermission
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        classes = list(
            SchoolClass.objects
            .filter(is_active=True, class_teacher__isnull=False)
            .select_related('class_teacher', 'wing_category',
                            'wing_category__parent')
            .order_by('name', 'section')
        )
        existing = {
            p.school_class_id: p
            for p in ClassTeacherAttendancePermission.objects.filter(
                school_class_id__in=[c.id for c in classes],
            )
        }
        rows = []
        for c in classes:
            p = existing.get(c.id)
            if p is None:
                p = ClassTeacherAttendancePermission.for_class(c)
            try:
                display_name = get_class_display_name(
                    c, tenant.tenant_type,
                )
            except Exception:
                display_name = str(c)
            rows.append({
                'class_id': c.id,
                'class_display': display_name,
                'class_teacher_id': c.class_teacher_id,
                'class_teacher_name': (
                    c.class_teacher.full_name if c.class_teacher else ''
                ),
                'backdate_access': p.backdate_access,
                'max_edits_per_date': p.max_edits_per_date,
                'view_history_days': p.view_history_days,
                'edit_history_days': p.edit_history_days,
                'updated_at': (
                    p.updated_at.isoformat() if p.updated_at else ''
                ),
                'updated_by': p.updated_by or '',
            })
    return JsonResponse({
        'ok': True,
        'permissions': rows,
    })


@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_class_teacher_permissions_save_api(request, schema_name):
    from ..models import ClassTeacherAttendancePermission
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    class_id = _parse_int(body.get('class_id'))
    if not class_id:
        return JsonResponse(
            {'ok': False, 'error': 'class_id required'}, status=400,
        )

    def _int(key, default, lo=0, hi=None):
        try:
            v = int(body.get(key, default))
        except (TypeError, ValueError):
            v = default
        v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    backdate_access = (body.get('backdate_access') or 'none').strip()
    if backdate_access not in ('none', 'read', 'read_write'):
        backdate_access = 'none'

    admin_name = request.session.get('school_admin_username', 'admin')

    with schema_context(schema_name):
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'Class not found'}, status=404,
            )
        p = ClassTeacherAttendancePermission.for_class(school_class)
        p.backdate_access = backdate_access
        p.max_edits_per_date = _int(
            'max_edits_per_date', p.max_edits_per_date, 0, 50,
        )
        p.view_history_days = _int(
            'view_history_days', p.view_history_days, 0, 730,
        )
        p.edit_history_days = _int(
            'edit_history_days', p.edit_history_days, 0, 730,
        )
        p.updated_by = admin_name
        p.save()
        return JsonResponse({
            'ok': True,
            'permission': {
                'class_id': school_class.id,
                'backdate_access': p.backdate_access,
                'max_edits_per_date': p.max_edits_per_date,
                'view_history_days': p.view_history_days,
                'edit_history_days': p.edit_history_days,
            },
        })


@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_daily_logs_api(request, schema_name):
    class_id = _parse_int(request.GET.get('class_id'))
    on_date = _parse_date(request.GET.get('date'))
    period_order = _parse_int(request.GET.get('period_order'))

    if not class_id or not on_date:
        return JsonResponse(
            {'ok': False, 'error': 'class_id and date required'}, status=400,
        )

    with schema_context(schema_name):
        att_qs = StudentAttendance.objects.filter(
            school_class_id=class_id, date=on_date,
        )
        if period_order is None:
            att_qs = att_qs.filter(period_order__isnull=True)
        else:
            att_qs = att_qs.filter(period_order=period_order)
        att_ids = list(att_qs.values_list('id', flat=True))

        logs_qs = (
            AttendanceAuditLog.objects
            .filter(attendance_id__in=att_ids)
            .select_related('changed_by')
            .order_by('-changed_at')
        )
        logs = [{
            'id': r.id,
            'action': r.action,
            'student_id': r.student_id_snapshot,
            'date': r.date_snapshot.isoformat() if r.date_snapshot else '',
            'period_order': r.period_snapshot,
            'old_status': r.old_status or '',
            'new_status': r.new_status or '',
            'changed_by': r.changed_by_name or (
                r.changed_by.full_name if r.changed_by else 'system'
            ),
            'changed_at': r.changed_at.isoformat() if r.changed_at else '',
            'reason': r.reason or '',
        } for r in logs_qs]

        from ..models import (
            ClassTeacherEditQuota, ClassTeacherAttendancePermission,
        )
        quota = ClassTeacherEditQuota.objects.filter(
            school_class_id=class_id, date=on_date,
        ).first()
        perm = ClassTeacherAttendancePermission.objects.filter(
            school_class_id=class_id,
        ).first()

        student_ids = {r['student_id'] for r in logs if r['student_id']}
        student_names = dict(
            Student.objects
            .filter(id__in=student_ids)
            .values_list('id', 'name')
        )

        return JsonResponse({
            'ok': True,
            'logs': logs,
            'student_names': student_names,
            'quota': {
                'teacher_edit_count': (
                    quota.teacher_edit_count if quota else 0
                ),
                'last_teacher_edit_at': (
                    quota.last_teacher_edit_at.isoformat()
                    if quota and quota.last_teacher_edit_at else ''
                ),
                'last_teacher_edit_by_name': (
                    quota.last_teacher_edit_by_name if quota else ''
                ),
                'max_edits_per_date': (
                    perm.max_edits_per_date if perm else 0
                ),
            },
        })
'''


def patch_admin_views(root, args):
    path = root / "axis_saas" / "views" / "admin_attendence.py"
    content = read_file(path)
    if content is None:
        return False
    if "admin_attendance_class_teacher_permissions_list_api" in content:
        log(f"  OK (already present): {path}")
        return True
    log(f"  MISSING — appending endpoints to {path}")
    content = content.rstrip() + "\n" + ADMIN_APPEND
    return write_file(path, content, args.dry_run,
                      "add class-teacher permission endpoints")


# =====================================================================
# 4. URLs — public_urls.py
# =====================================================================

def patch_public_urls(root, args):
    path = root / "axis_saas" / "public_urls.py"
    content = read_file(path)
    if content is None:
        return False

    changed = False

    if "admin_attendance_class_teacher_permissions_list_api" not in content:
        anchor_imp = (
            "    admin_attendance_auto_marked_dates_api,\n"
        )
        if anchor_imp in content:
            content = content.replace(
                anchor_imp,
                anchor_imp
                + "    # STAFF_ATTENDANCE_OVERHAUL_V1\n"
                + "    admin_attendance_class_teacher_permissions_list_api,\n"
                + "    admin_attendance_class_teacher_permissions_save_api,\n"
                + "    admin_attendance_daily_logs_api,\n",
                1,
            )
            changed = True
            log("  Inserted new imports")
        else:
            log("  WARN: could not find import anchor")

    if "class-teacher-permissions" not in content:
        anchor_route = (
            "    path('portal/<slug:schema_name>/api/attendance/auto-marked-dates/', "
            "portal_wrapper(login_required_for_schema(admin_attendance_auto_marked_dates_api)), "
            "name='admin_attendance_auto_marked_dates_api'),\n"
        )
        if anchor_route in content:
            new_routes = (
                "    # ===== STAFF_ATTENDANCE_OVERHAUL_V1 =====\n"
                "    path('portal/<slug:schema_name>/api/attendance/class-teacher-permissions/', "
                "portal_wrapper(login_required_for_schema(admin_attendance_class_teacher_permissions_list_api)), "
                "name='admin_attendance_class_teacher_permissions_list_api'),\n"
                "    path('portal/<slug:schema_name>/api/attendance/class-teacher-permissions/save/', "
                "portal_wrapper(login_required_for_schema(admin_attendance_class_teacher_permissions_save_api)), "
                "name='admin_attendance_class_teacher_permissions_save_api'),\n"
                "    path('portal/<slug:schema_name>/api/attendance/daily-logs/', "
                "portal_wrapper(login_required_for_schema(admin_attendance_daily_logs_api)), "
                "name='admin_attendance_daily_logs_api'),\n"
            )
            content = content.replace(anchor_route, anchor_route + new_routes, 1)
            changed = True
            log("  Inserted new routes")
        else:
            log("  WARN: could not find route anchor")

    if not changed:
        log(f"  OK (already applied): {path}")
        return True
    return write_file(path, content, args.dry_run, "add class-teacher URLs")


# =====================================================================
# 5. URLs — staff_urls.py
# =====================================================================

def patch_staff_urls(root, args):
    path = root / "axis_saas" / "staff_urls.py"
    content = read_file(path)
    if content is None:
        return False

    changed = False

    if "staff_attendance_dates_api" not in content:
        anchor_imp = "    staff_attendance_policy_api,\n"
        if anchor_imp in content:
            content = content.replace(
                anchor_imp,
                anchor_imp
                + "    # STAFF_ATTENDANCE_OVERHAUL_V1_FIX\n"
                + "    staff_attendance_dates_api,\n",
                1,
            )
            changed = True
            log("  Inserted dates_api import")

    if "api/attendance/dates/" not in content:
        anchor_route = (
            "    path('api/attendance/policy/', "
            "staff_attendance_policy_api, "
            "name='staff_attendance_policy_api'),\n"
        )
        if anchor_route in content:
            new_route = (
                "    # STAFF_ATTENDANCE_OVERHAUL_V1_FIX\n"
                "    path('api/attendance/dates/', "
                "staff_attendance_dates_api, "
                "name='staff_attendance_dates_api'),\n"
            )
            content = content.replace(
                anchor_route, anchor_route + new_route, 1,
            )
            changed = True
            log("  Inserted dates route")

    if not changed:
        log(f"  OK (already applied): {path}")
        return True
    return write_file(path, content, args.dry_run, "add dates API")


# =====================================================================
# 6. TENANT TEMPLATE FIX — THE MAIN FIX
# =====================================================================

PERMS_BUTTON_BLOCK = '''
<div style="display:flex; gap:.6rem; margin-bottom:1rem; flex-wrap:wrap;">
    <button type="button" class="att-action-btn"
            onclick="AXIS_ADMIN_ATT_PERMS.openPermissions()">
        ⚙️ Class Teacher Permissions
    </button>
</div>
'''

ADMIN_MODALS_AND_JS = r'''
<!-- ============ STAFF_ATTENDANCE_OVERHAUL_V1_FIX — admin extras ============ -->
<div id="attPermModal" class="att-modal">
    <div class="att-modal-panel" style="width:min(1100px, 100%);">
        <div class="att-modal-header">
            <div>
                <h3>Class Teacher Attendance Permissions</h3>
                <div class="sub">Control what each class teacher can view and edit.</div>
            </div>
            <button type="button" onclick="AXIS_ADMIN_ATT_PERMS.closePermissions()">×</button>
        </div>
        <div class="att-modal-body" id="attPermList" style="padding:1rem 1.2rem;">
            <div class="att-modal-empty">Loading…</div>
        </div>
        <div class="att-modal-footer">
            <span class="save-msg" id="attPermMsg"></span>
            <button type="button" onclick="AXIS_ADMIN_ATT_PERMS.closePermissions()">Close</button>
        </div>
    </div>
</div>

<div id="attLogsModal" class="att-modal">
    <div class="att-modal-panel" style="width:min(960px, 100%);">
        <div class="att-modal-header">
            <div>
                <h3 id="attLogsTitle">Daily Logs</h3>
                <div class="sub" id="attLogsSub"></div>
            </div>
            <button type="button" onclick="AXIS_ADMIN_ATT_PERMS.closeLogs()">×</button>
        </div>
        <div class="att-modal-body" id="attLogsBody" style="padding:0;">
            <div class="att-modal-empty">Loading…</div>
        </div>
    </div>
</div>

<script>
window.AXIS_ADMIN_ATT_PERMS = (function() {
    var SCHEMA = '{{ tenant.schema_name|escapejs }}';
    function q(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function(c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
        });
    }
    function csrf() {
        var m = document.querySelector('meta[name="csrf-token"]');
        if (m && m.getAttribute('content')) return m.getAttribute('content');
        var name = 'csrftoken=';
        var parts = (document.cookie || '').split(';');
        for (var i = 0; i < parts.length; i++) {
            var c = parts[i].trim();
            if (c.indexOf(name) === 0) return c.substring(name.length);
        }
        return '';
    }
    var permCache = [];

    function openPermissions() {
        q('#attPermModal').classList.add('open');
        loadPermissions();
    }
    function closePermissions() {
        q('#attPermModal').classList.remove('open');
    }
    function loadPermissions() {
        q('#attPermList').innerHTML = '<div class="att-modal-empty">Loading…</div>';
        fetch('/portal/' + SCHEMA + '/api/attendance/class-teacher-permissions/', {
            headers: {'X-Requested-With': 'XMLHttpRequest'}
        })
            .then(function(r) { return r.json(); })
            .then(function(j) {
                if (!j.ok) {
                    q('#attPermList').innerHTML =
                        '<div class="att-modal-empty">' + esc(j.error || 'Failed') + '</div>';
                    return;
                }
                permCache = j.permissions || [];
                renderPermissions();
            })
            .catch(function() {
                q('#attPermList').innerHTML =
                    '<div class="att-modal-empty">Network error.</div>';
            });
    }
    function renderPermissions() {
        if (!permCache.length) {
            q('#attPermList').innerHTML =
                '<div class="att-modal-empty">No classes with an assigned class teacher yet.</div>';
            return;
        }
        var selStyle = 'padding:.35rem .5rem; border-radius:.5rem; border:1px solid var(--border); background:var(--surface-alt); color:var(--text);';
        var inpStyle = 'width:72px; padding:.35rem .5rem; border-radius:.5rem; border:1px solid var(--border); background:var(--surface-alt); color:var(--text);';
        var h = '<div style="overflow:auto;">';
        h += '<table style="width:100%; border-collapse:collapse; font-size:.85rem;">';
        h += '<thead><tr style="text-align:left; border-bottom:1px solid var(--border);">';
        h += '<th style="padding:.55rem;">Class</th>';
        h += '<th style="padding:.55rem;">Class Teacher</th>';
        h += '<th style="padding:.55rem;">Backdate Access</th>';
        h += '<th style="padding:.55rem;">Max Edits / Date</th>';
        h += '<th style="padding:.55rem;">View Days</th>';
        h += '<th style="padding:.55rem;">Edit Days</th>';
        h += '<th></th></tr></thead><tbody>';
        permCache.forEach(function(p) {
            h += '<tr data-class-id="' + p.class_id + '" style="border-bottom:1px solid var(--border);">';
            h += '<td style="padding:.55rem; font-weight:700;">' + esc(p.class_display) + '</td>';
            h += '<td style="padding:.55rem;">' + esc(p.class_teacher_name) + '</td>';
            h += '<td style="padding:.55rem;"><select data-field="backdate_access" style="' + selStyle + '">';
            h += '<option value="none"' + (p.backdate_access === 'none' ? ' selected' : '') + '>Today only</option>';
            h += '<option value="read"' + (p.backdate_access === 'read' ? ' selected' : '') + '>Read only</option>';
            h += '<option value="read_write"' + (p.backdate_access === 'read_write' ? ' selected' : '') + '>Read &amp; Write</option>';
            h += '</select></td>';
            h += '<td style="padding:.55rem;"><input type="number" min="0" max="50" value="' + p.max_edits_per_date + '" data-field="max_edits_per_date" style="' + inpStyle + '"></td>';
            h += '<td style="padding:.55rem;"><input type="number" min="0" max="730" value="' + p.view_history_days + '" data-field="view_history_days" style="' + inpStyle + '"></td>';
            h += '<td style="padding:.55rem;"><input type="number" min="0" max="730" value="' + p.edit_history_days + '" data-field="edit_history_days" style="' + inpStyle + '"></td>';
            h += '<td style="padding:.55rem;"><button type="button" class="att-action-btn" onclick="AXIS_ADMIN_ATT_PERMS.savePermission(' + p.class_id + ')">Save</button></td>';
            h += '</tr>';
        });
        h += '</tbody></table></div>';
        q('#attPermList').innerHTML = h;
    }
    function savePermission(classId) {
        var row = q('#attPermList tr[data-class-id="' + classId + '"]');
        if (!row) return;
        function num(field, fallback) {
            var el = row.querySelector('[data-field="' + field + '"]');
            var v = parseInt(el.value, 10);
            return isNaN(v) ? fallback : v;
        }
        var payload = {
            class_id: classId,
            backdate_access: row.querySelector('[data-field="backdate_access"]').value,
            max_edits_per_date: num('max_edits_per_date', 0),
            view_history_days: num('view_history_days', 0),
            edit_history_days: num('edit_history_days', 0),
        };
        var msg = q('#attPermMsg');
        msg.className = 'save-msg';
        msg.textContent = 'Saving…';
        fetch('/portal/' + SCHEMA + '/api/attendance/class-teacher-permissions/save/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrf(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify(payload),
        })
            .then(function(r) { return r.json(); })
            .then(function(j) {
                if (!j.ok) {
                    msg.className = 'save-msg err';
                    msg.textContent = j.error || 'Failed';
                    return;
                }
                msg.className = 'save-msg ok';
                msg.textContent = 'Saved.';
                setTimeout(function() {
                    if (msg) { msg.textContent = ''; msg.className = 'save-msg'; }
                }, 1500);
            })
            .catch(function() {
                msg.className = 'save-msg err';
                msg.textContent = 'Network error.';
            });
    }
    function openLogs(classId, dateStr) {
        if (!classId) { classId = 0; }
        q('#attLogsModal').classList.add('open');
        q('#attLogsTitle').textContent = 'Daily Logs — ' + dateStr;
        q('#attLogsSub').textContent = 'Class ID ' + classId;
        q('#attLogsBody').innerHTML = '<div class="att-modal-empty">Loading…</div>';
        var url = '/portal/' + SCHEMA + '/api/attendance/daily-logs/?class_id=' + classId + '&date=' + encodeURIComponent(dateStr);
        fetch(url, { headers: {'X-Requested-With': 'XMLHttpRequest'} })
            .then(function(r) { return r.json(); })
            .then(function(j) {
                if (!j.ok) {
                    q('#attLogsBody').innerHTML = '<div class="att-modal-empty">' + esc(j.error || 'Failed') + '</div>';
                    return;
                }
                var logs = j.logs || [];
                var names = j.student_names || {};
                var qq = j.quota || {};
                var head = '<div style="padding:1rem 1.2rem; border-bottom:1px solid var(--border); background:var(--surface-alt);">';
                head += '<div style="font-size:.82rem; color:var(--muted);">';
                head += 'Teacher edits used: <strong>' + (qq.teacher_edit_count || 0) + '</strong> / <strong>' + (qq.max_edits_per_date || 0) + '</strong>';
                if (qq.last_teacher_edit_by_name) {
                    head += ' &nbsp;·&nbsp; Last edit by <strong>' + esc(qq.last_teacher_edit_by_name) + '</strong>';
                }
                if (qq.last_teacher_edit_at) {
                    head += ' on <strong>' + esc(qq.last_teacher_edit_at) + '</strong>';
                }
                head += '</div></div>';
                if (!logs.length) {
                    q('#attLogsBody').innerHTML = head + '<div class="att-modal-empty">No logs recorded for this date yet.</div>';
                    return;
                }
                var h = head + '<div>';
                logs.forEach(function(l) {
                    var sname = names[l.student_id] || ('Student #' + (l.student_id || '?'));
                    var badge = l.action === 'create' ? 'att-status-chip completed'
                              : l.action === 'update' ? 'att-status-chip partial'
                              : 'att-status-chip pending';
                    h += '<div style="display:grid; grid-template-columns:1fr auto; gap:.5rem; padding:.6rem 1.2rem; border-bottom:1px solid var(--border);">';
                    h += '<div>';
                    h += '<div style="font-weight:700; font-size:.85rem;">' + esc(sname) + '</div>';
                    h += '<div style="font-size:.72rem; color:var(--muted); margin-top:.15rem;">';
                    h += 'Status: ' + esc(l.old_status || '—') + ' → ' + esc(l.new_status || '—');
                    h += ' · By ' + esc(l.changed_by || 'system');
                    h += ' · ' + esc(l.changed_at || '');
                    h += '</div>';
                    if (l.reason) {
                        h += '<div style="font-size:.7rem; color:var(--muted); margin-top:.15rem;">Reason: ' + esc(l.reason) + '</div>';
                    }
                    h += '</div>';
                    h += '<div><span class="' + badge + '">' + esc(l.action) + '</span></div>';
                    h += '</div>';
                });
                h += '</div>';
                q('#attLogsBody').innerHTML = h;
            })
            .catch(function() {
                q('#attLogsBody').innerHTML = '<div class="att-modal-empty">Network error.</div>';
            });
    }
    function closeLogs() {
        q('#attLogsModal').classList.remove('open');
    }
    document.addEventListener('DOMContentLoaded', function() {
        var lm = q('#attLogsModal');
        if (lm) lm.addEventListener('click', function(e) {
            if (e.target === lm) closeLogs();
        });
        var pm = q('#attPermModal');
        if (pm) pm.addEventListener('click', function(e) {
            if (e.target === pm) closePermissions();
        });
    });
    return {
        openPermissions: openPermissions,
        closePermissions: closePermissions,
        savePermission: savePermission,
        openLogs: openLogs,
        closeLogs: closeLogs,
    };
})();
</script>
'''


def fix_tenant_template(root, args):
    path = root / "templates" / "tenant" / "attendence.html"
    content = read_file(path)
    if content is None:
        return False

    # If markers are already present, nothing to do.
    if ('AXIS_ADMIN_ATT_PERMS' in content
            and 'attPermModal' in content
            and 'Class Teacher Permissions' in content):
        log(f"  OK (already applied): {path}")
        return True

    original = content
    changes = []

    # ---- 1. Insert the permissions button after the page-desc line ----
    if 'Class Teacher Permissions' not in content:
        page_desc = (
            '<p class="page-desc">Class-wise attendance overview for '
            '{{ today }}. Every active class, its completion status, and '
            'its cumulative missing-day count.</p>'
        )
        if page_desc in content:
            content = content.replace(
                page_desc, page_desc + "\n" + PERMS_BUTTON_BLOCK, 1,
            )
            changes.append("button")
        else:
            log("  WARN: page-desc anchor not found; will insert button "
                "after <h1 class=\"page-title\">")

            h1 = '<h1 class="page-title">Attendance Dashboard</h1>'
            if h1 in content:
                content = content.replace(
                    h1, h1 + "\n" + PERMS_BUTTON_BLOCK, 1,
                )
                changes.append("button(fallback)")

    # ---- 2. Insert modals + JS before the final {% endblock %} ----
    if 'attPermModal' not in content:
        endblock = '{% endblock %}'
        idx = content.rfind(endblock)
        if idx == -1:
            log("  ERROR: could not locate {% endblock %}")
        else:
            content = (
                content[:idx]
                + "\n"
                + ADMIN_MODALS_AND_JS
                + "\n"
                + content[idx:]
            )
            changes.append("modals+js")

    # ---- 3. Replace the auto-marked "Open" button with an Open+Logs pair ----
    if 'AXIS_ADMIN_ATT_PERMS.openLogs' not in content:
        # The exact JS fragment we need to find and replace. The file
        # contains a backslash-quote inside a JS string, so the search
        # string uses a raw Python literal.
        old = (
            r"""'<button type="button" class="open-btn" onclick="event.stopPropagation(); AXIS_ADMIN_ATT.openAutoDate(\'' + esc(d.date) + '\')">Open</button>'"""
        )
        new = (
            r"""'<div style="display:flex; gap:.35rem;">'"""
            + "\n                          +   "
            + r"""'<button type="button" class="open-btn" onclick="event.stopPropagation(); AXIS_ADMIN_ATT.openAutoDate(\'' + esc(d.date) + '\')">Open</button>'"""
            + "\n                          +   "
            + r"""'<button type="button" class="open-btn" style="background:var(--surface-alt); color:var(--text); border-color:var(--border);" onclick="event.stopPropagation(); AXIS_ADMIN_ATT_PERMS.openLogs(' + modalClassId + ', \'' + esc(d.date) + '\')">Logs</button>'"""
            + "\n                          +   "
            + r"""'</div>'"""
        )
        if old in content:
            content = content.replace(old, new, 1)
            changes.append("logs-button")
        else:
            log("  WARN: auto-marked Open button anchor not found; "
                "Logs button will not be added.")

    if content == original:
        log(f"  SKIP (no anchors matched): {path}")
        return False

    log(f"  Changes applied: {', '.join(changes)}")
    return write_file(path, content, args.dry_run,
                      "fix tenant attendance template")


# =====================================================================
# 7. VERIFY staff_attendence.py is up to date
# =====================================================================

def verify_staff_views(root, args):
    path = root / "axis_saas" / "views" / "staff_attendence.py"
    content = read_file(path)
    if content is None:
        return False
    if "STAFF_ATTENDANCE_OVERHAUL_V1" in content:
        log(f"  OK (overhaul present): {path}")
        return True
    log(f"  MISSING — {path} does not have the overhaul. "
        f"You must run the full original patcher first.")
    return False


# =====================================================================
# main
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            f"{MARKER} — repairs the admin attendance template that the "
            f"previous patcher failed to modify, and verifies every other "
            f"file."
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
        ("Models: ClassTeacher* models",           patch_models),
        ("Migration: 0034",                        create_migration),
        ("Admin views: permission + logs APIs",    patch_admin_views),
        ("URLs: public_urls.py",                   patch_public_urls),
        ("URLs: staff_urls.py",                    patch_staff_urls),
        ("Template: tenant/attendence.html (MAIN)", fix_tenant_template),
        ("Verify: staff_attendence.py overhaul",   verify_staff_views),
    ]

    results = []
    for label, fn in steps:
        log(f"--- {label} ---")
        try:
            ok = fn(root, args)
        except Exception as exc:
            log(f"  EXCEPTION: {exc.__class__.__name__}: {exc}")
            ok = False
        results.append((label, ok))

    log("=" * 65)
    for label, ok in results:
        log(f"  {'OK  ' if ok else 'FAIL'}  {label}")

    all_ok = all(ok for _, ok in results)
    if all_ok:
        log("All steps completed successfully.")
        if args.dry_run:
            log("Re-run without --dry-run to apply.")
        else:
            log("Next steps:")
            log("  1. python manage.py makemigrations --check axis_saas")
            log("  2. python manage.py migrate_schemas --tenant")
            log("  3. Hard-refresh the admin attendance page in your browser "
                "(Ctrl+Shift+R) to bust the template cache.")
        return 0
    log("One or more steps failed. See messages above.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
