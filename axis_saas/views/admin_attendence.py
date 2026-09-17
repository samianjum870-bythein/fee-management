"""AXIS views — Attendance (admin side).

ATTENDANCE_PRODUCTION_V2
------------------------
Admin has universal access. Every change is audit-logged.

Endpoints:
  GET  /attendance/                              admin_attendance_view
  GET  /api/attendance/students/                 admin_attendance_students_api
  POST /api/attendance/mark/                     admin_attendance_mark_api
  POST /api/attendance/bulk-mark/                admin_attendance_bulk_mark_api
  GET  /api/attendance/records/                  admin_attendance_records_api
  GET  /api/attendance/summary/                  admin_attendance_summary_api
  GET  /api/attendance/student/<id>/history/     admin_attendance_student_history_api
  GET  /api/attendance/compliance/               admin_attendance_compliance_api
  GET  /api/attendance/audit/                    admin_attendance_audit_api
  GET  /api/attendance/policy/                   admin_attendance_policy_get_api
  POST /api/attendance/policy/save/              admin_attendance_policy_save_api
  GET  /api/attendance/low-defaulters/           admin_attendance_low_defaulters_api
"""
import json
import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    SchoolClass, Student, Staff, StudentAttendance, StaffAttendance,
    StudentLeave, AttendancePolicy, AttendanceAuditLog,
    PeriodTeacherAssignment, WeeklyHoliday, AnnualHoliday, Vacation,
)
from .helpers import (
    get_tenant, require_tenant_type, require_school_feature,
)
from axis_saas.utils.class_display import get_class_display_name

logger = logging.getLogger(__name__)
ATTENDANCE_STATUSES = (
    'present', 'absent', 'late', 'half_day', 'excused', 'holiday',
)
# ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-4 + BUG-8):
# 'holiday' is only ever set on a whole class by the calendar /
# cron jobs.  A single student's mark must NEVER be able to flip
# the entire day into a holiday.  The mark APIs therefore accept
# only MARKABLE_STATUSES; the records/filter APIs keep the full
# ATTENDANCE_STATUSES list so 'holiday' rows can still be
# queried explicitly.
MARKABLE_STATUSES = (
    'present', 'absent', 'late', 'half_day', 'excused',
)


# ------------------------------------------------------------------ helpers

def _today():
    return timezone.localdate()


def _parse_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _parse_int(v):
    if v in (None, '', 'null', 'None'):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _is_holiday(on_date):
    """Return (is_holiday: bool, reason: str) for ``on_date``.

    ATTENDANCE_AUTO_MARK_AND_LOCK_V1
    --------------------------------
    Historical-aware.  A holiday *rule* that did not yet exist on
    ``on_date`` must NOT make ``on_date`` a holiday.  We therefore
    respect ``created_at`` on WeeklyHoliday / AnnualHoliday.

    Vacation rows already carry explicit ``start_date`` /
    ``end_date`` ranges, so they apply unconditionally within that
    range.  Finally, any StudentAttendance row whose status is
    ``'holiday'`` acts as historical evidence for that date.
    """
    dow = on_date.weekday()

    # Weekly holiday — only if the rule existed by ``on_date``.
    try:
        for wh in WeeklyHoliday.objects.filter(day_of_week=dow):
            ca = getattr(wh, 'created_at', None)
            # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-6): created_at is
            # stored in UTC.  Compare in the project's local
            # timezone so a rule created at 23:00 PKT does not
            # land on the wrong local date.
            ca_local = timezone.localtime(ca).date() if ca else None
            if ca_local is None or ca_local <= on_date:
                return True, f"Weekly holiday ({wh.label or 'Weekend'})"
    except Exception:
        pass

    # Annual holiday — same created_at check.
    try:
        for ah in AnnualHoliday.objects.filter(
            month=on_date.month, day=on_date.day,
        ):
            ca = getattr(ah, 'created_at', None)
            # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-6): UTC -> local.
            ca_local = timezone.localtime(ca).date() if ca else None
            if ca_local is None or ca_local <= on_date:
                return True, f"Annual holiday ({ah.label})"
    except Exception:
        pass

    # Vacation — explicit date range.
    try:
        vac = Vacation.objects.filter(
            start_date__lte=on_date, end_date__gte=on_date,
        ).first()
        if vac:
            return True, f"Vacation ({vac.name})"
    except Exception:
        pass


    return False, ''


def _audit(*, attendance, action, old_status='', new_status='',
           staff=None, reason=''):
    try:
        AttendanceAuditLog.objects.create(
            attendance=attendance,
            student_id_snapshot=getattr(attendance, 'student_id', None),
            date_snapshot=getattr(attendance, 'date', None),
            period_snapshot=getattr(attendance, 'period_order', None),
            action=action, old_status=old_status or '',
            new_status=new_status or '', changed_by=staff,
            changed_by_name=(staff.full_name if staff else 'admin'),
            reason=reason or '',
        )
    except Exception as exc:
        logger.warning('admin attendance audit write failed: %s', exc)


# ------------------------------------------------------------------ page

@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_view(request, schema_name):
    """ADMIN_ATTENDANCE_DASHBOARD_V1 — class-overview dashboard.

    Renders an Excel-style table of every active class. For each class
    the template shows:

      * student count
      * how many students were marked today (full-day)
      * today's status: Completed / Partial / Pending / No Students
      * cumulative missing attendance days since the class was created
        (working days only — skips weekly / annual holidays + vacations)

    The existing mark / edit APIs are still used; this view only
    computes the summary data and hands it to the template as JSON.
    """
    tenant = get_tenant(request, schema_name)
    today = _today()

    with schema_context(schema_name):
        # ---------- holiday context ----------
        try:
            holiday_dows = set(
                WeeklyHoliday.objects.values_list('day_of_week', flat=True)
            )
        except Exception:
            holiday_dows = set()

        try:
            annual_holidays = set(
                AnnualHoliday.objects.values_list('month', 'day')
            )
        except Exception:
            annual_holidays = set()

        try:
            vacations = list(
                Vacation.objects.values_list('start_date', 'end_date')
            )
        except Exception:
            vacations = []

        def _is_working(day):
            if day.weekday() in holiday_dows:
                return False
            if (day.month, day.day) in annual_holidays:
                return False
            for s, e in vacations:
                if s <= day <= e:
                    return False
            return True

        # ---------- classes ----------
        classes = list(
            SchoolClass.objects
            .filter(is_active=True)
            .select_related(
                'class_teacher',
                'wing_category',
                'wing_category__parent',
            )
            .order_by('name', 'section')
        )
        class_ids = [c.id for c in classes]

        # ---------- student count per class ----------
        student_counts = dict(
            Student.objects
            .filter(status='active', school_class_id__in=class_ids)
            .values('school_class_id')
            .annotate(n=Count('id'))
            .values_list('school_class_id', 'n')
        )

        # ---------- today's marks per class (full-day) ----------
        today_full_map = {}
        for cid, sid in (
            StudentAttendance.objects
            .filter(date=today, period_order__isnull=True,
                    school_class_id__in=class_ids)
            .values_list('school_class_id', 'student_id')
            .distinct()
        ):
            today_full_map.setdefault(cid, set()).add(sid)

        # ---------- today's marks per class (period-wise) ----------
        today_period_map = {}
        for cid, sid in (
            StudentAttendance.objects
            .filter(date=today, period_order__isnull=False,
                    school_class_id__in=class_ids)
            .values_list('school_class_id', 'student_id')
            .distinct()
        ):
            today_period_map.setdefault(cid, set()).add(sid)

        # ---------- all full-day dates ever marked, per class ----------
        # Batched: ONE query for all classes instead of one per class.
        marked_by_class = {}
        if class_ids:
            for cid, d in (
                StudentAttendance.objects
                .filter(period_order__isnull=True,
                        school_class_id__in=class_ids)
                .values_list('school_class_id', 'date')
                .distinct()
            ):
                marked_by_class.setdefault(cid, set()).add(d)

        # ---------- ATTENDANCE_AUTO_MARK_COLUMN_V1 ----------
        # The auto-mark column replaces the old "missing days" column.
        # We need, per class:
        #   * auto_marked_dates  — distinct dates where at least one row
        #                          was written by the auto-present cron
        #                          (source='auto_system'), full-day only.
        #   * manual_marked_dates— distinct full-day dates written by a
        #                          human (source NOT starting with 'auto_').
        # Both are batched into two queries for the whole page.
        auto_marked_by_class = {}
        if class_ids:
            for cid, d in (
                StudentAttendance.objects
                .filter(
                    period_order__isnull=True,
                    school_class_id__in=class_ids,
                    source='auto_system',
                )
                .values_list('school_class_id', 'date')
                .distinct()
            ):
                auto_marked_by_class.setdefault(cid, set()).add(d)

        manual_marked_by_class = {}
        if class_ids:
            for cid, d in (
                StudentAttendance.objects
                .filter(period_order__isnull=True,
                        school_class_id__in=class_ids)
                .exclude(source__startswith='auto_')
                .values_list('school_class_id', 'date')
                .distinct()
            ):
                manual_marked_by_class.setdefault(cid, set()).add(d)

        # ---------- build rows ----------
        class_rows = []
        summary = {
            'total_classes': 0,
            'total_students': 0,
            'total_marked_today': 0,
            'classes_completed': 0,
            'classes_partial': 0,
            'classes_pending': 0,
            'classes_no_students': 0,
            # ATTENDANCE_AUTO_MARK_COLUMN_V1: renamed from
            # 'total_missing_days'.  Counts distinct dates where the
            # auto-present cron filled unmarked attendance.
            'total_auto_marked_days': 0,
        }

        for cls in classes:
            sc = student_counts.get(cls.id, 0)
            today_marked = len(today_full_map.get(cls.id, set()))
            today_period_marked = len(today_period_map.get(cls.id, set()))

            if sc == 0:
                status = 'no_students'
                status_label = 'No Students'
                summary['classes_no_students'] += 1
            elif today_marked >= sc:
                status = 'completed'
                status_label = 'Completed'
                summary['classes_completed'] += 1
            elif today_marked > 0:
                status = 'partial'
                status_label = 'Partial'
                summary['classes_partial'] += 1
            else:
                status = 'pending'
                status_label = 'Pending'
                summary['classes_pending'] += 1

            # ---------- auto-mark column (ATTENDANCE_AUTO_MARK_COLUMN_V1) ----------
            # Replaces the old "missing days" count.  The cron command
            # `attendance_auto_present` already backfills every past
            # unmarked date with source='auto_system', so the useful
            # number to show the admin is "how many days did the system
            # fill in for this class?", not "how many days are missing?".
            auto_set = auto_marked_by_class.get(cls.id, set())
            auto_marked_days = len(auto_set)
            auto_recent = sorted(auto_set, reverse=True)[:5]

            manual_marked_days = len(
                manual_marked_by_class.get(cls.id, set())
            )

            summary['total_classes'] += 1
            summary['total_students'] += sc
            summary['total_marked_today'] += today_marked
            summary['total_auto_marked_days'] += auto_marked_days

            try:
                display_name = get_class_display_name(
                    cls, tenant.tenant_type,
                )
            except Exception:
                display_name = str(cls)

            class_rows.append({
                'id': cls.id,
                'name': str(cls),
                'display_name': display_name,
                'teacher': (
                    cls.class_teacher.full_name
                    if cls.class_teacher else ''
                ),
                'teacher_id': cls.class_teacher_id or 0,
                'student_count': sc,
                'today_marked': today_marked,
                'today_period_marked': today_period_marked,
                'status': status,
                'status_label': status_label,
                # ATTENDANCE_AUTO_MARK_COLUMN_V1
                'auto_marked_days': auto_marked_days,
                'auto_recent': [
                    d.isoformat() for d in auto_recent
                ],
                'manual_marked_days': manual_marked_days,
                'created_at': cls.created_at.date().isoformat(),
            })

        # ---------- teacher list for the filter ----------
        teachers = [
            {'id': t.id, 'name': t.full_name, 'job_title': t.job_title or ''}
            for t in Staff.objects.filter(status='active').order_by('full_name')
        ]

        # ---------- policy (kept for compatibility with the page's
        # existing JS that reads window.__AXIS_ATT_POLICY__) ----------
        try:
            policy = AttendancePolicy.current()
            policy_data = {
                'attendance_mode': policy.attendance_mode,
                'late_threshold_minutes': policy.late_threshold_minutes,
                'low_attendance_threshold': float(policy.low_attendance_threshold),
                'auto_mark_absent_at': (
                    policy.auto_mark_absent_at.strftime('%H:%M')
                    if policy.auto_mark_absent_at else ''
                ),
                'notify_parents_on_absent': policy.notify_parents_on_absent,
                'notify_after_periods': policy.notify_after_periods,
                'allow_teacher_backdate_days': policy.allow_teacher_backdate_days,
                'require_admin_approval': policy.require_admin_approval,
            }
        except Exception:
            policy_data = {
                'attendance_mode': 'both',
                'late_threshold_minutes': 10,
                'low_attendance_threshold': 75.0,
                'auto_mark_absent_at': '',
                'notify_parents_on_absent': True,
                'notify_after_periods': 2,
                'allow_teacher_backdate_days': 1,
                'require_admin_approval': False,
            }

        # Completion percentage for the KPI card.
        if summary['total_students']:
            summary['attendance_pct_today'] = round(
                summary['total_marked_today']
                / summary['total_students'] * 100, 1,
            )
        else:
            summary['attendance_pct_today'] = 0.0

    context = {
        'tenant': tenant,
        'today': today.isoformat(),
        'class_rows_json': json.dumps(class_rows),
        'teachers_json': json.dumps(teachers),
        'policy_json': json.dumps(policy_data),
        'summary': summary,
        'logo_url': (
            tenant.school_logo.url if tenant.school_logo else None
        ),
    }
    response = render(request, 'tenant/attendence.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


# ------------------------------------------------------------------ students

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_students_api(request, schema_name):
    """GET the students of a class for a given date (and optional period).

    ATTENDANCE_AUTO_MARK_AND_LOCK_V1
    --------------------------------
    Response now carries two additional flags:

    * ``locked``      — True when ANY full-day (or matching-period) mark
                        already exists for this slot.  The frontend
                        renders the form read-only until the admin
                        clicks "Edit Attendance".
    * ``auto_marked`` — True when the existing marks were written by the
                        system (source == 'auto_system').  A banner is
                        shown to explain the rows.

    On a holiday, the response short-circuits with
    ``is_holiday=True`` and an empty student list, so the frontend can
    display the holiday banner and disable Save.
    """
    class_id = _parse_int(request.GET.get('class_id'))
    att_date = _parse_date(request.GET.get('date'))
    period_order = _parse_int(request.GET.get('period_order'))
    if not class_id or not att_date:
        return JsonResponse(
            {'ok': False, 'error': 'class_id and date (YYYY-MM-DD) required'},
            status=400,
        )

    with schema_context(schema_name):
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse({'ok': False, 'error': 'Class not found'},
                                status=404)

        is_hol, holiday_reason = _is_holiday(att_date)

        # ---------- holiday short-circuit ----------
        if is_hol:
            return JsonResponse({
                'ok': True,
                'is_holiday': True,
                'holiday_reason': holiday_reason,
                'date': att_date.isoformat(),
                'class_id': school_class.id,
                'class_name': str(school_class),
                'period_order': period_order,
                'locked': True,
                'auto_marked': False,
                'students': [],
            })

        # ---------- students ----------
        students = list(
            Student.objects
            .filter(school_class=school_class, status='active')
            .order_by('roll_number', 'name')
        )

        qs = StudentAttendance.objects.filter(
            school_class=school_class, date=att_date,
        )
        if period_order is None:
            qs = qs.filter(period_order__isnull=True)
        else:
            qs = qs.filter(period_order=period_order)
        marks = {m.student_id: m for m in qs}

        leave_ids = set(
            StudentLeave.objects
            .filter(status='approved',
                    start_date__lte=att_date,
                    end_date__gte=att_date)
            .values_list('student_id', flat=True)
        )

        locked = bool(marks)
        auto_marked = any(
            getattr(m, 'source', '') == 'auto_system'
            for m in marks.values()
        )

        payload = []
        for s in students:
            m = marks.get(s.id)
            default_status = 'excused' if s.id in leave_ids else 'present'
            payload.append({
                'id': s.id,
                'roll_number': s.roll_number or '',
                'name': s.name,
                'father_name': s.father_name or '',
                'status': m.status if m else default_status,
                'remarks': m.remarks if m else '',
                'already_marked': bool(m),
                'on_leave': s.id in leave_ids,
                'marked_by': (
                    m.marked_by.full_name if m and m.marked_by
                    else (m.teacher.full_name if m and m.teacher else '')
                ),
                'source': m.source if m else '',
                'is_auto': bool(m and m.source == 'auto_system'),
            })

    return JsonResponse({
        'ok': True,
        'is_holiday': False,
        'holiday_reason': '',
        'date': att_date.isoformat(),
        'class_id': school_class.id,
        'class_name': str(school_class),
        'period_order': period_order,
        'locked': locked,
        'auto_marked': auto_marked,
        'students': payload,
    })


# ------------------------------------------------------------------ auto-marked

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_auto_marked_dates_api(request, schema_name):
    """Return the dates on which the system auto-marked a class.

    ATTENDANCE_AUTO_MARK_AND_LOCK_V1 — powers the modal's
    "Auto-Marked by System" tab.  Grouped by ``date``; a row exists only
    when at least one StudentAttendance row for that class+date carries
    ``source='auto_system'`` and ``period_order IS NULL``.
    """
    class_id = _parse_int(request.GET.get('class_id'))
    if not class_id:
        return JsonResponse(
            {'ok': False, 'error': 'class_id required'}, status=400,
        )

    with schema_context(schema_name):
        rows = (
            StudentAttendance.objects
            .filter(
                school_class_id=class_id,
                source='auto_system',
                period_order__isnull=True,
            )
            .values('date')
            .annotate(
                total=Count('id'),
                present=Count('id', filter=Q(status='present')),
                absent=Count('id', filter=Q(status='absent')),
                late=Count('id', filter=Q(status='late')),
                excused=Count('id', filter=Q(status='excused')),
            )
            .order_by('-date')
        )
        dates = [{
            'date': r['date'].isoformat(),
            'day_name': r['date'].strftime('%A'),
            'total': r['total'],
            'present': r['present'],
            'absent': r['absent'],
            'late': r['late'],
            'excused': r['excused'],
        } for r in rows]

        return JsonResponse({'ok': True, 'dates': dates})


# ------------------------------------------------------------------ mark

@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_mark_api(request, schema_name):
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    class_id = _parse_int(payload.get('class_id'))
    att_date = _parse_date(payload.get('date'))
    period_order = _parse_int(payload.get('period_order'))
    records = payload.get('records') or []
    reason = (payload.get('reason') or '').strip()

    if not class_id or not att_date or not isinstance(records, list):
        return JsonResponse(
            {'ok': False, 'error': 'class_id, date and records[] required'},
            status=400,
        )
    if att_date > _today():
        return JsonResponse(
            {'ok': False, 'error': 'Cannot mark attendance for future dates.'},
            status=400,
        )

    saved = 0
    with schema_context(schema_name):
        admin_staff = None
        # Admin is not always a Staff row; store session username in audit.
        admin_name = request.session.get('school_admin_username', 'admin')

        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse({'ok': False, 'error': 'Class not found'},
                                status=404)

        student_ids = set(
            Student.objects
            .filter(school_class=school_class, status='active')
            .values_list('id', flat=True)
        )

        with transaction.atomic():
            for rec in records:
                sid = _parse_int(rec.get('student_id'))
                if sid not in student_ids:
                    continue
                status = (rec.get('status') or 'present').strip().lower()
                # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-4 + BUG-8):
                # never accept 'holiday' from a single-student
                # mark — that would flip the whole day.
                if status not in MARKABLE_STATUSES:
                    status = 'present'
                remarks = (rec.get('remarks') or '')[:500]

                lookup = {
                    'student_id': sid,
                    'date': att_date,
                    'period_order': period_order,
                }
                existing = StudentAttendance.objects.filter(**lookup).first()
                if existing is None:
                    row = StudentAttendance.objects.create(
                        **lookup,
                        school_class=school_class,
                        status=status,
                        marked_by=admin_staff,
                        marked_at=timezone.now(),
                        source='admin',
                        remarks=remarks,
                    )
                    _audit(attendance=row, action='create',
                           new_status=status, staff=admin_staff,
                           reason=reason or f'admin:{admin_name}')
                else:
                    old_status = existing.status
                    existing.status = status
                    existing.remarks = remarks
                    existing.modified_by = admin_staff
                    existing.modified_at = timezone.now()
                    existing.source = 'admin'
                    existing.school_class = school_class
                    existing.save(update_fields=[
                        'status', 'remarks', 'modified_by', 'modified_at',
                        'source', 'school_class', 'updated_at',
                    ])
                    _audit(attendance=existing, action='update',
                           old_status=old_status, new_status=status,
                           staff=admin_staff,
                           reason=reason or f'admin:{admin_name}')
                saved += 1
    return JsonResponse({'ok': True, 'saved': saved})


@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_bulk_mark_api(request, schema_name):
    """Bulk-set every active student in a class to one status.

    Body: { class_id, date, period_order, status, reason }
    """
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    class_id = _parse_int(payload.get('class_id'))
    att_date = _parse_date(payload.get('date'))
    period_order = _parse_int(payload.get('period_order'))
    status = (payload.get('status') or 'present').strip().lower()
    reason = (payload.get('reason') or '').strip() or 'admin bulk'

    if not class_id or not att_date:
        return JsonResponse(
            {'ok': False, 'error': 'class_id and date required'}, status=400,
        )
    # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-4 + BUG-8):
    # 'holiday' is reserved for whole-class calendar marks.
    if status not in MARKABLE_STATUSES:
        return JsonResponse({'ok': False, 'error': 'Invalid status'},
                            status=400)

    with schema_context(schema_name):
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse({'ok': False, 'error': 'Class not found'},
                                status=404)
        students = list(
            Student.objects
            .filter(school_class=school_class, status='active')
        )
        count = 0
        with transaction.atomic():
            for s in students:
                lookup = {
                    'student_id': s.id,
                    'date': att_date,
                    'period_order': period_order,
                }
                existing = StudentAttendance.objects.filter(**lookup).first()
                if existing is None:
                    row = StudentAttendance.objects.create(
                        **lookup,
                        school_class=school_class,
                        status=status,
                        marked_at=timezone.now(),
                        source='admin',
                    )
                    _audit(attendance=row, action='bulk_update',
                           new_status=status, reason=reason)
                else:
                    old_status = existing.status
                    existing.status = status
                    existing.modified_at = timezone.now()
                    existing.source = 'admin'
                    existing.save(update_fields=[
                        'status', 'modified_at', 'source', 'updated_at',
                    ])
                    _audit(attendance=existing, action='bulk_update',
                           old_status=old_status, new_status=status,
                           reason=reason)
                count += 1
    return JsonResponse({'ok': True, 'updated': count})


# ------------------------------------------------------------------ records

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_records_api(request, schema_name):
    class_id = _parse_int(request.GET.get('class_id'))
    student_id = _parse_int(request.GET.get('student_id'))
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    status = (request.GET.get('status') or '').strip()
    period_order = _parse_int(request.GET.get('period_order'))

    try:
        page = max(1, int(request.GET.get('page', '1') or 1))
    except ValueError:
        page = 1
    try:
        page_size = min(200, max(1, int(request.GET.get('page_size', '50') or 50)))
    except ValueError:
        page_size = 50

    with schema_context(schema_name):
        qs = (
            StudentAttendance.objects
            .select_related('student', 'school_class', 'teacher',
                            'marked_by', 'modified_by')
            .order_by('-date', 'student__roll_number')
        )
        if class_id:
            qs = qs.filter(school_class_id=class_id)
        if student_id:
            qs = qs.filter(student_id=student_id)
        if status in ATTENDANCE_STATUSES:
            qs = qs.filter(status=status)
        if start_date:
            qs = qs.filter(date__gte=start_date)
        if end_date:
            qs = qs.filter(date__lte=end_date)
        if period_order is not None:
            qs = qs.filter(period_order=period_order)

        total = qs.count()
        offset = (page - 1) * page_size
        rows = list(qs[offset:offset + page_size])

        data = [{
            'id': r.id,
            'date': r.date.isoformat(),
            'student_id': r.student_id,
            'student_name': r.student.name if r.student else '',
            'roll_number': r.student.roll_number if r.student else '',
            'class_name': str(r.school_class) if r.school_class else '',
            'period_order': r.period_order,
            'status': r.status,
            'remarks': r.remarks or '',
            'marked_by': (
                r.marked_by.full_name if r.marked_by
                else (r.teacher.full_name if r.teacher else '')
            ),
            'modified_by': r.modified_by.full_name if r.modified_by else '',
            'source': r.source,
            'marked_at': r.marked_at.isoformat() if r.marked_at else '',
        } for r in rows]

        num_pages = (total + page_size - 1) // page_size if page_size else 1
    return JsonResponse({
        'ok': True,
        'records': data,
        'pagination': {
            'page': page, 'page_size': page_size,
            'total': total, 'num_pages': num_pages,
        },
    })


# ------------------------------------------------------------------ summary

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_summary_api(request, schema_name):
    target_date = _parse_date(request.GET.get('date')) or _today()
    period_order = _parse_int(request.GET.get('period_order'))

    with schema_context(schema_name):
        qs = StudentAttendance.objects.filter(date=target_date)
        if period_order is None:
            qs = qs.filter(period_order__isnull=True)
        else:
            qs = qs.filter(period_order=period_order)

        present = qs.filter(status='present').count()
        absent = qs.filter(status='absent').count()
        late = qs.filter(status='late').count()
        half_day = qs.filter(status='half_day').count()
        excused = qs.filter(status='excused').count()
        holiday = qs.filter(status='holiday').count()
        total_marked = present + absent + late + half_day + excused + holiday
        total_students = Student.objects.filter(status='active').count()
        unmarked = max(0, total_students - total_marked)

        per_class = []
        for cls in SchoolClass.objects.filter(is_active=True).order_by('name', 'section'):
            ids = list(
                Student.objects
                .filter(school_class=cls, status='active')
                .values_list('id', flat=True)
            )
            cqs = StudentAttendance.objects.filter(
                date=target_date, student_id__in=ids,
            )
            if period_order is None:
                cqs = cqs.filter(period_order__isnull=True)
            else:
                cqs = cqs.filter(period_order=period_order)
            per_class.append({
                'class_id': cls.id,
                'class_name': str(cls),
                'total': len(ids),
                'marked': cqs.count(),
                'present': cqs.filter(status='present').count(),
                'absent': cqs.filter(status='absent').count(),
                'late': cqs.filter(status='late').count(),
            })

    return JsonResponse({
        'ok': True,
        'date': target_date.isoformat(),
        'period_order': period_order,
        'summary': {
            'present': present, 'absent': absent, 'late': late,
            'half_day': half_day, 'excused': excused, 'holiday': holiday,
            'total_marked': total_marked, 'total_students': total_students,
            'unmarked': unmarked,
        },
        'per_class': per_class,
    })


# ------------------------------------------------------------------ student history

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_student_history_api(request, schema_name, student_id):
    with schema_context(schema_name):
        student = (
            Student.objects
            .filter(id=student_id)
            .select_related('school_class')
            .first()
        )
        if not student:
            return JsonResponse({'ok': False, 'error': 'Student not found'},
                                status=404)
        qs = (
            StudentAttendance.objects
            .filter(student=student)
            .select_related('teacher', 'marked_by')
            .order_by('-date')[:365]
        )
        records = [{
            'date': r.date.isoformat(),
            'period_order': r.period_order,
            'status': r.status,
            'remarks': r.remarks or '',
            'teacher': (
                r.marked_by.full_name if r.marked_by
                else (r.teacher.full_name if r.teacher else '')
            ),
        } for r in qs]
        agg = StudentAttendance.objects.filter(student=student).aggregate(
            present=Count('id', filter=Q(status='present')),
            absent=Count('id', filter=Q(status='absent')),
            late=Count('id', filter=Q(status='late')),
            half_day=Count('id', filter=Q(status='half_day')),
            excused=Count('id', filter=Q(status='excused')),
            holiday=Count('id', filter=Q(status='holiday')),
        )
        total = sum(v or 0 for v in agg.values())
        # Percentage of present + late over counted days.
        present_like = (agg.get('present', 0) or 0) + (agg.get('late', 0) or 0)
        pct = round((present_like / total) * 100, 2) if total else 0.0

    return JsonResponse({
        'ok': True,
        'student': {
            'id': student.id,
            'name': student.name,
            'roll_number': student.roll_number,
            'class_name': str(student.school_class) if student.school_class else '',
        },
        'records': records,
        'summary': agg,
        'total': total,
        'attendance_percentage': pct,
    })


# ------------------------------------------------------------------ compliance

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_compliance_api(request, schema_name):
    """Which class teachers have / haven't marked today."""
    target_date = _parse_date(request.GET.get('date')) or _today()
    with schema_context(schema_name):
        rows = []
        for cls in SchoolClass.objects.filter(is_active=True).order_by('name', 'section'):
            marked = StudentAttendance.objects.filter(
                school_class=cls, date=target_date, period_order__isnull=True,
            ).exists()
            rows.append({
                'class_id': cls.id,
                'class_name': str(cls),
                'class_teacher': cls.class_teacher.full_name if cls.class_teacher else '',
                'class_teacher_id': cls.class_teacher_id,
                'marked': marked,
            })
        total = len(rows)
        done = sum(1 for r in rows if r['marked'])
    return JsonResponse({
        'ok': True,
        'date': target_date.isoformat(),
        'total_classes': total,
        'marked_classes': done,
        'pending_classes': total - done,
        'classes': rows,
    })


# ------------------------------------------------------------------ audit

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_audit_api(request, schema_name):
    try:
        page = max(1, int(request.GET.get('page', '1') or 1))
    except ValueError:
        page = 1
    try:
        page_size = min(200, max(1, int(request.GET.get('page_size', '50') or 50)))
    except ValueError:
        page_size = 50
    student_id = _parse_int(request.GET.get('student_id'))

    with schema_context(schema_name):
        qs = AttendanceAuditLog.objects.all().order_by('-changed_at')
        if student_id:
            qs = qs.filter(student_id_snapshot=student_id)
        total = qs.count()
        offset = (page - 1) * page_size
        rows = list(qs[offset:offset + page_size])
        data = [{
            'id': r.id,
            'action': r.action,
            'student_id': r.student_id_snapshot,
            'date': r.date_snapshot.isoformat() if r.date_snapshot else '',
            'period_order': r.period_snapshot,
            'old_status': r.old_status,
            'new_status': r.new_status,
            'changed_by': r.changed_by_name or (
                r.changed_by.full_name if r.changed_by else ''
            ),
            'changed_at': r.changed_at.isoformat(),
            'reason': r.reason or '',
        } for r in rows]
    return JsonResponse({
        'ok': True, 'records': data,
        'pagination': {'page': page, 'page_size': page_size, 'total': total},
    })


# ------------------------------------------------------------------ low defaulters

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_low_defaulters_api(request, schema_name):
    days = _parse_int(request.GET.get('days')) or 30
    days = max(7, min(365, days))
    since = _today() - timedelta(days=days)

    with schema_context(schema_name):
        policy = AttendancePolicy.current()
        threshold = float(policy.low_attendance_threshold or 75)

        rows = []
        for s in Student.objects.filter(status='active').select_related('school_class'):
            qs = StudentAttendance.objects.filter(student=s, date__gte=since)
            total = qs.count()
            if total == 0:
                continue
            present_like = qs.filter(status__in=['present', 'late']).count()
            pct = round((present_like / total) * 100, 2)
            if pct < threshold:
                rows.append({
                    'student_id': s.id,
                    'name': s.name,
                    'roll_number': s.roll_number,
                    'class_name': str(s.school_class) if s.school_class else '',
                    'parent_mobile': s.parent_mobile or '',
                    'percentage': pct,
                    'total_days': total,
                    'present_days': present_like,
                })
        rows.sort(key=lambda r: r['percentage'])
    return JsonResponse({
        'ok': True, 'threshold': threshold, 'days': days,
        'students': rows,
    })


# ------------------------------------------------------------------ policy

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_policy_get_api(request, schema_name):
    with schema_context(schema_name):
        p = AttendancePolicy.current()
        return JsonResponse({
            'ok': True,
            'attendance_mode': p.attendance_mode,
            'late_threshold_minutes': p.late_threshold_minutes,
            'low_attendance_threshold': float(p.low_attendance_threshold),
            'auto_mark_absent_at': (
                p.auto_mark_absent_at.strftime('%H:%M')
                if p.auto_mark_absent_at else ''
            ),
            'notify_parents_on_absent': p.notify_parents_on_absent,
            'notify_after_periods': p.notify_after_periods,
            'allow_teacher_backdate_days': p.allow_teacher_backdate_days,
            'require_admin_approval': p.require_admin_approval,
        })


@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_policy_save_api(request, schema_name):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    with schema_context(schema_name):
        with transaction.atomic():
            policy = AttendancePolicy.objects.select_for_update().filter(
                is_singleton=True,
            ).first() or AttendancePolicy.current()

            mode = (body.get('attendance_mode') or policy.attendance_mode).strip()
            if mode not in ('daily', 'period_wise', 'both'):
                mode = policy.attendance_mode

            try:
                policy.attendance_mode = mode
                policy.late_threshold_minutes = max(0, int(
                    body.get('late_threshold_minutes',
                             policy.late_threshold_minutes) or 0))
                policy.low_attendance_threshold = float(
                    body.get('low_attendance_threshold',
                             policy.low_attendance_threshold) or 75.0)
                auto_time = (body.get('auto_mark_absent_at') or '').strip()
                if auto_time:
                    try:
                        policy.auto_mark_absent_at = datetime.strptime(
                            auto_time, '%H:%M').time()
                    except ValueError:
                        pass
                else:
                    policy.auto_mark_absent_at = None
                policy.notify_parents_on_absent = bool(
                    body.get('notify_parents_on_absent',
                             policy.notify_parents_on_absent))
                policy.notify_after_periods = max(0, int(
                    body.get('notify_after_periods',
                             policy.notify_after_periods) or 0))
                policy.allow_teacher_backdate_days = max(0, int(
                    body.get('allow_teacher_backdate_days',
                             policy.allow_teacher_backdate_days) or 0))
                policy.require_admin_approval = bool(
                    body.get('require_admin_approval',
                             policy.require_admin_approval))
                policy.save()
            except (TypeError, ValueError) as exc:
                return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    return JsonResponse({'ok': True})


# ------------------------------------------------------------------ staff attendance

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_staff_attendance_list_api(request, schema_name):
    """Daily staff attendance register for admins."""
    target_date = _parse_date(request.GET.get('date')) or _today()
    with schema_context(schema_name):
        staff_qs = Staff.objects.filter(status='active').order_by('full_name')
        rows = []
        for s in staff_qs:
            rec = StaffAttendance.objects.filter(staff=s, date=target_date).first()
            rows.append({
                'staff_id': s.id,
                'name': s.full_name,
                'job_title': s.job_title or '',
                'status': rec.status if rec else 'absent',
                'check_in': rec.check_in.strftime('%H:%M') if rec and rec.check_in else '',
                'check_out': rec.check_out.strftime('%H:%M') if rec and rec.check_out else '',
                'late_minutes': rec.late_minutes if rec else 0,
                'source': rec.source if rec else '',
            })
    return JsonResponse({'ok': True, 'date': target_date.isoformat(),
                         'rows': rows})


@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_staff_attendance_mark_api(request, schema_name):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    staff_id = _parse_int(body.get('staff_id'))
    att_date = _parse_date(body.get('date'))
    status = (body.get('status') or 'present').strip()
    check_in = (body.get('check_in') or '').strip()
    check_out = (body.get('check_out') or '').strip()
    remarks = (body.get('remarks') or '')[:500]

    if not staff_id or not att_date:
        return JsonResponse({'ok': False, 'error': 'staff_id and date required'},
                            status=400)

    valid = ('present', 'absent', 'late', 'half_day', 'on_leave', 'holiday', 'weekend')
    if status not in valid:
        status = 'present'

    with schema_context(schema_name):
        staff = Staff.objects.filter(id=staff_id).first()
        if not staff:
            return JsonResponse({'ok': False, 'error': 'Staff not found'},
                                status=404)

        def _parse_time(t):
            try:
                return datetime.strptime(f"{att_date.isoformat()} {t}",
                                         '%Y-%m-%d %H:%M')
            except (ValueError, TypeError):
                return None

        ci = _parse_time(check_in) if check_in else None
        co = _parse_time(check_out) if check_out else None

        rec, created = StaffAttendance.objects.update_or_create(
            staff=staff, date=att_date,
            defaults={
                'status': status,
                'check_in': ci,
                'check_out': co,
                'remarks': remarks,
                'source': 'admin',
            },
        )
        if ci and co:
            try:
                worked = int((co - ci).total_seconds() // 60)
                rec.worked_minutes = max(0, worked)
                rec.save(update_fields=['worked_minutes'])
            except Exception:
                pass

    return JsonResponse({'ok': True, 'id': rec.id})


# =====================================================================
# STAFF_ATTENDANCE_OVERHAUL_V1 — class-teacher permission management
# ---------------------------------------------------------------------
# The school admin uses these endpoints to control, per class:
#   * what the class teacher can do with past attendance
#   * how many times the teacher can edit a given date
#   * how far back the teacher can view history
#   * how far back the teacher can edit history
# =====================================================================


@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_class_teacher_permissions_list_api(request, schema_name):
    """List every class that has a class teacher, with its current
    attendance permission settings."""
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
        'backdate_access_choices': [
            {'value': 'none', 'label': "None - Only today's attendance"},
            {'value': 'read', 'label': 'Read only - View past, cannot edit'},
            {'value': 'read_write', 'label': 'Read & Write - View and edit past'},
        ],
    })


@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_class_teacher_permissions_save_api(request, schema_name):
    """Save the attendance permission row for a single class."""
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
                'updated_at': (
                    p.updated_at.isoformat() if p.updated_at else ''
                ),
                'updated_by': p.updated_by or '',
            },
        })


@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_daily_logs_api(request, schema_name):
    """Return the full audit log for a specific (class, date)."""
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
        logs = []
        for r in logs_qs:
            logs.append({
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
            })

        from ..models import (
            ClassTeacherEditQuota, ClassTeacherAttendancePermission,
        )
        try:
            quota = ClassTeacherEditQuota.objects.filter(
                school_class_id=class_id, date=on_date,
            ).first()
        except Exception:
            quota = None

        try:
            perm = ClassTeacherAttendancePermission.objects.filter(
                school_class_id=class_id,
            ).first()
        except Exception:
            perm = None

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
