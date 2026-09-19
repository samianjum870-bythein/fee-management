"""AXIS views — Attendance (staff / teacher side).

STAFF_ATTENDANCE_OVERHAUL_V1
----------------------------
Complete rewrite of the class-teacher-facing attendance system.

Features
--------
* Holiday-aware (weekly, annual, vacation) — never lets a teacher mark
  attendance on a holiday.
* Class teacher sees their assigned classes on a dedicated dashboard.
* Today's attendance is always available to class teachers.
* Past attendance is subject to admin-controlled per-class permissions:
    - backdate_access = none / read / read_write
    - view_history_days limits how far back the teacher can view
    - edit_history_days limits how far back the teacher can edit
    - max_edits_per_date limits how many times the teacher can edit
      a single date before it locks permanently for the teacher
* Auto-marked days (source='auto_system') are flagged with a badge.
  Teachers can still edit them (subject to the same quota) — the edit
  is then attributed to the teacher, not the system.
* Confirmation dialog before saving.
* Every edit is audit-logged (AttendanceAuditLog).
"""
import json
import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    SchoolClass, Student, Staff, StudentAttendance,
    StudentLeave, AttendancePolicy, AttendanceAuditLog,
    PeriodTeacherAssignment,
    WeeklyHoliday, AnnualHoliday, Vacation,
    ClassTeacherAttendancePermission, ClassTeacherEditQuota,
)
from .staff_portal import require_staff_login, require_staff_feature

logger = logging.getLogger(__name__)
ATTENDANCE_STATUSES = (
    'present', 'absent', 'late', 'half_day', 'excused', 'holiday',
)
# ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-8): class teachers may only
# set one of these statuses on an individual student.  'holiday'
# is reserved for whole-class calendar marks.
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
    """Return (is_holiday, reason). Historical-aware: only rules that
    existed by ``on_date`` apply."""
    dow = on_date.weekday()
    try:
        for wh in WeeklyHoliday.objects.filter(day_of_week=dow):
            ca = getattr(wh, 'created_at', None)
            # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-6): created_at is
            # stored in UTC; compare in local time.
            ca_local = timezone.localtime(ca).date() if ca else None
            if ca_local is None or ca_local <= on_date:
                return True, f"Weekly holiday ({wh.label or 'Weekend'})"
    except Exception:
        pass
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
    try:
        vac = Vacation.objects.filter(
            start_date__lte=on_date, end_date__gte=on_date,
        ).first()
        if vac:
            return True, f"Vacation ({vac.name})"
    except Exception:
        pass
    return False, ''


def _class_teacher_classes(staff):
    return SchoolClass.objects.filter(
        class_teacher=staff, is_active=True,
    ).order_by('name', 'section')


def _is_class_teacher_of(staff, school_class):
    return school_class.class_teacher_id == staff.pk


def _active_student_leave_ids(on_date):
    return set(
        StudentLeave.objects.filter(
            status='approved',
            start_date__lte=on_date,
            end_date__gte=on_date,
        ).values_list('student_id', flat=True)
    )


def _record_audit(*, attendance, action, old_status='', new_status='',
                  staff=None, reason=''):
    try:
        AttendanceAuditLog.objects.create(
            attendance=attendance,
            student_id_snapshot=getattr(attendance, 'student_id', None),
            date_snapshot=getattr(attendance, 'date', None),
            period_snapshot=getattr(attendance, 'period_order', None),
            action=action,
            old_status=old_status or '',
            new_status=new_status or '',
            changed_by=staff,
            changed_by_name=(staff.full_name if staff else ''),
            reason=reason or '',
        )
    except Exception as exc:
        logger.warning('attendance audit write failed: %s', exc)


def _compute_permission_payload(staff, school_class, on_date):
    """Return a dict describing what this teacher can do on this date
    for this class."""
    today = _today()
    is_ct = _is_class_teacher_of(staff, school_class)
    perm = ClassTeacherAttendancePermission.for_class(school_class)

    if not is_ct:
        return {
            'is_class_teacher': False,
            'can_view': True,
            'can_edit': False,
            'backdate_access': 'none',
            'reason': 'Subject teachers mark their own period today.',
            'quota_used': 0,
            'quota_max': 0,
            'quota_remaining': 0,
            'view_history_days': 0,
            'edit_history_days': 0,
        }

    days_ago = (today - on_date).days

    if days_ago <= 0:
        return {
            'is_class_teacher': True,
            'can_view': True,
            'can_edit': True,
            'backdate_access': perm.backdate_access,
            'reason': '',
            'quota_used': 0,
            'quota_max': perm.max_edits_per_date,
            'quota_remaining': perm.max_edits_per_date,
            'view_history_days': perm.view_history_days,
            'edit_history_days': perm.edit_history_days,
        }

    if days_ago > perm.view_history_days:
        return {
            'is_class_teacher': True,
            'can_view': False,
            'can_edit': False,
            'backdate_access': perm.backdate_access,
            'reason': (
                f"This date is older than the "
                f"{perm.view_history_days}-day view window."
            ),
            'quota_used': 0,
            'quota_max': perm.max_edits_per_date,
            'quota_remaining': 0,
            'view_history_days': perm.view_history_days,
            'edit_history_days': perm.edit_history_days,
        }

    quota = ClassTeacherEditQuota.objects.filter(
        school_class=school_class, date=on_date,
    ).first()
    used = quota.teacher_edit_count if quota else 0

    if perm.backdate_access == 'none':
        return {
            'is_class_teacher': True,
            'can_view': True,
            'can_edit': False,
            'backdate_access': perm.backdate_access,
            'reason': (
                'Your admin has set this class to "today only". '
                'Past attendance is read-only for you.'
            ),
            'quota_used': used,
            'quota_max': perm.max_edits_per_date,
            'quota_remaining': 0,
            'view_history_days': perm.view_history_days,
            'edit_history_days': perm.edit_history_days,
        }

    if perm.backdate_access == 'read':
        return {
            'is_class_teacher': True,
            'can_view': True,
            'can_edit': False,
            'backdate_access': perm.backdate_access,
            'reason': 'Past attendance is read-only for this class.',
            'quota_used': used,
            'quota_max': perm.max_edits_per_date,
            'quota_remaining': 0,
            'view_history_days': perm.view_history_days,
            'edit_history_days': perm.edit_history_days,
        }

    if days_ago > perm.edit_history_days:
        return {
            'is_class_teacher': True,
            'can_view': True,
            'can_edit': False,
            'backdate_access': perm.backdate_access,
            'reason': (
                f"Editing is only allowed for the last "
                f"{perm.edit_history_days} day(s)."
            ),
            'quota_used': used,
            'quota_max': perm.max_edits_per_date,
            'quota_remaining': 0,
            'view_history_days': perm.view_history_days,
            'edit_history_days': perm.edit_history_days,
        }

    if perm.max_edits_per_date <= 0 or used >= perm.max_edits_per_date:
        return {
            'is_class_teacher': True,
            'can_view': True,
            'can_edit': False,
            'backdate_access': perm.backdate_access,
            'reason': (
                f"You have already used all {perm.max_edits_per_date} "
                f"edit(s) for this date."
            ),
            'quota_used': used,
            'quota_max': perm.max_edits_per_date,
            'quota_remaining': 0,
            'view_history_days': perm.view_history_days,
            'edit_history_days': perm.edit_history_days,
        }

    return {
        'is_class_teacher': True,
        'can_view': True,
        'can_edit': True,
        'backdate_access': perm.backdate_access,
        'reason': '',
        'quota_used': used,
        'quota_max': perm.max_edits_per_date,
        'quota_remaining': perm.max_edits_per_date - used,
        'view_history_days': perm.view_history_days,
        'edit_history_days': perm.edit_history_days,
    }


# ------------------------------------------------------------------ page

@require_staff_login
@require_staff_feature('staff_attendance')
def staff_attendance_view(request):
    schema_name = request.session['staff_schema_name']

    # ATTENDANCE_AUTO_MARK_LAZY_V1
    # ------------------------------------------------------
    # Same lazy catch-up as the admin dashboard. Runs first so
    # the numbers the teacher sees already reflect any missed
    # days. Rate-limited to once per hour per tenant.
    try:
        from axis_saas.utils.attendance_auto_mark import (
            trigger_lazy_auto_mark,
        )
        trigger_lazy_auto_mark(schema_name)
    except Exception:
        logger.exception(
            'ATTENDANCE_AUTO_MARK_LAZY_V1: staff trigger failed '
            'for schema=%s', schema_name,
        )

    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        today = _today()
        is_hol, holiday_reason = _is_holiday(today)

        ct_classes = []
        for c in _class_teacher_classes(staff):
            perm = ClassTeacherAttendancePermission.for_class(c)
            total_students = Student.objects.filter(
                school_class=c, status='active',
            ).count()
            marked = StudentAttendance.objects.filter(
                school_class=c, date=today, period_order__isnull=True,
            ).count()
            auto_marked = StudentAttendance.objects.filter(
                school_class=c, date=today, period_order__isnull=True,
                source='auto_system',
            ).exists()
            ct_classes.append({
                'id': c.id,
                'name': str(c),
                'student_count': total_students,
                'marked_today': marked,
                'status': (
                    'completed' if total_students and marked >= total_students
                    else 'partial' if marked > 0
                    else 'pending'
                ),
                'auto_marked': auto_marked,
                'backdate_access': perm.backdate_access,
                'max_edits_per_date': perm.max_edits_per_date,
                'view_history_days': perm.view_history_days,
                'edit_history_days': perm.edit_history_days,
            })

        dow = today.weekday()
        subject_periods = list(
            PeriodTeacherAssignment.objects
            .filter(teacher=staff, day_of_week=dow)
            .select_related('school_class', 'subject')
            .order_by('period_order')
        )
        subject_list = []
        for ap in subject_periods:
            marked = StudentAttendance.objects.filter(
                school_class=ap.school_class,
                date=today,
                period_order=ap.period_order,
            ).exists()
            subject_list.append({
                'period_order': ap.period_order,
                'class_id': ap.school_class.id,
                'class_name': str(ap.school_class),
                'subject': ap.subject.name if ap.subject else '',
                'marked': marked,
            })

    context = {
        'staff': staff,
        'today': today.isoformat(),
        'day_name': today.strftime('%A'),
        'is_holiday': is_hol,
        'holiday_reason': holiday_reason,
        'class_teacher_classes_json': json.dumps(ct_classes),
        'subject_periods_json': json.dumps(subject_list),
        'has_class_teacher_classes': bool(ct_classes),
        'has_subject_periods': bool(subject_list),
    }
    response = render(request, 'mobile/staff/attendence.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


# ------------------------------------------------------------------ dates list

@require_staff_login
@require_staff_feature('staff_attendance')
@require_http_methods(['GET'])
def staff_attendance_dates_api(request):
    """List of dates for a class teacher's class, filtered by the
    permission window. Each row includes status, source, and the
    teacher's remaining edit quota."""
    class_id = _parse_int(request.GET.get('class_id'))
    if not class_id:
        return JsonResponse(
            {'ok': False, 'error': 'class_id required'}, status=400,
        )

    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if not staff:
            return JsonResponse(
                {'ok': False, 'error': 'Staff not found'}, status=404,
            )
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'Class not found'}, status=404,
            )
        if not _is_class_teacher_of(staff, school_class):
            return JsonResponse(
                {'ok': False, 'error': 'Not your class'}, status=403,
            )

        perm = ClassTeacherAttendancePermission.for_class(school_class)
        today = _today()
        lookback = max(0, perm.view_history_days)
        total_students = Student.objects.filter(
            school_class=school_class, status='active',
        ).count()

        dates = []
        for i in range(lookback + 1):
            d = today - timedelta(days=i)
            is_hol, hol_reason = _is_holiday(d)

            att_qs = StudentAttendance.objects.filter(
                school_class=school_class, date=d,
                period_order__isnull=True,
            )
            marked = att_qs.count()
            auto_count = att_qs.filter(source='auto_system').count()
            teacher_marked = att_qs.exclude(source__startswith='auto_').count()

            quota = ClassTeacherEditQuota.objects.filter(
                school_class=school_class, date=d,
            ).first()
            used = quota.teacher_edit_count if quota else 0

            permission_payload = _compute_permission_payload(
                staff, school_class, d,
            )

            dates.append({
                'date': d.isoformat(),
                'day_name': d.strftime('%A'),
                'is_today': d == today,
                'is_holiday': is_hol,
                'holiday_reason': hol_reason,
                'total_students': total_students,
                'marked': marked,
                'auto_marked': auto_count,
                'teacher_marked': teacher_marked,
                'status': (
                    'holiday' if is_hol
                    else 'completed' if total_students and marked >= total_students
                    else 'partial' if marked > 0
                    else 'pending'
                ),
                'quota_used': permission_payload['quota_used'],
                'quota_max': permission_payload['quota_max'],
                'quota_remaining': permission_payload['quota_remaining'],
                'can_view': permission_payload['can_view'],
                'can_edit': permission_payload['can_edit'],
                'lock_reason': permission_payload['reason'],
            })

        return JsonResponse({
            'ok': True,
            'class_id': school_class.id,
            'class_name': str(school_class),
            'permission': {
                'backdate_access': perm.backdate_access,
                'max_edits_per_date': perm.max_edits_per_date,
                'view_history_days': perm.view_history_days,
                'edit_history_days': perm.edit_history_days,
            },
            'dates': dates,
        })


# ------------------------------------------------------------------ students API

@require_staff_login
@require_staff_feature('staff_attendance')
@require_http_methods(['GET'])
def staff_attendance_students_api(request):
    class_id = _parse_int(request.GET.get('class_id'))
    att_date = _parse_date(request.GET.get('date'))
    period_order = _parse_int(request.GET.get('period_order'))

    if not class_id or not att_date:
        return JsonResponse(
            {'ok': False, 'error': 'class_id and date required'},
            status=400,
        )

    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if not staff:
            return JsonResponse(
                {'ok': False, 'error': 'Staff not found'}, status=404,
            )
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'Class not found'}, status=404,
            )

        is_ct = _is_class_teacher_of(staff, school_class)
        is_period_teacher = False
        if period_order is not None:
            is_period_teacher = PeriodTeacherAssignment.objects.filter(
                teacher=staff,
                school_class=school_class,
                day_of_week=att_date.weekday(),
                period_order=period_order,
            ).exists()
        if not (is_ct or is_period_teacher):
            return JsonResponse(
                {'ok': False, 'error': 'Not authorised for this class/period'},
                status=403,
            )

        permission = _compute_permission_payload(
            staff, school_class, att_date,
        )

        if not is_ct and att_date != _today():
            return JsonResponse({
                'ok': False,
                'error': "Subject teachers can only mark today's periods.",
            }, status=403)

        if not permission['can_view']:
            return JsonResponse({
                'ok': False,
                'error': permission['reason'] or 'Not authorised to view.',
            }, status=403)

        is_hol, holiday_reason = _is_holiday(att_date)
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
                'lock_reason': '',
                'auto_marked': False,
                'permission': permission,
                'students': [],
            })

        students = list(Student.objects.filter(
            school_class=school_class, status='active',
        ).order_by('roll_number', 'name'))

        qs = StudentAttendance.objects.filter(
            school_class=school_class, date=att_date,
        )
        if period_order is None:
            qs = qs.filter(period_order__isnull=True)
        else:
            qs = qs.filter(period_order=period_order)
        marks = {m.student_id: m for m in qs}

        leave_ids = _active_student_leave_ids(att_date)

        locked = False
        lock_reason = ''
        if not permission['can_edit']:
            locked = True
            lock_reason = permission['reason']

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
                'source': m.source if m else '',
                'is_auto': bool(m and m.source == 'auto_system'),
                'marked_by': (
                    m.marked_by.full_name if m and m.marked_by
                    else (m.teacher.full_name if m and m.teacher else '')
                ),
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
        'lock_reason': lock_reason,
        'auto_marked': auto_marked,
        'permission': permission,
        'students': payload,
    })


# ------------------------------------------------------------------ mark API

@require_staff_login
@require_staff_feature('staff_attendance')
@require_http_methods(['POST'])
def staff_attendance_mark_api(request):
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    class_id = _parse_int(payload.get('class_id'))
    att_date = _parse_date(payload.get('date'))
    period_order = _parse_int(payload.get('period_order'))
    records = payload.get('records') or []

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

    schema_name = request.session['staff_schema_name']
    saved = 0
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if not staff:
            return JsonResponse(
                {'ok': False, 'error': 'Staff not found'}, status=404,
            )

        is_hol, holiday_reason = _is_holiday(att_date)
        if is_hol:
            return JsonResponse({
                'ok': False,
                'error': f'{att_date} is a holiday ({holiday_reason}).',
            }, status=400)

        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'Class not found'}, status=404,
            )

        is_ct = _is_class_teacher_of(staff, school_class)
        is_period_teacher = False
        if period_order is not None:
            is_period_teacher = PeriodTeacherAssignment.objects.filter(
                teacher=staff,
                school_class=school_class,
                day_of_week=att_date.weekday(),
                period_order=period_order,
            ).exists()
        if not (is_ct or is_period_teacher):
            return JsonResponse(
                {'ok': False, 'error': 'Not authorised'}, status=403,
            )

        permission = _compute_permission_payload(
            staff, school_class, att_date,
        )
        if not permission['can_edit']:
            return JsonResponse({
                'ok': False,
                'error': permission['reason'] or 'Edit not allowed.',
                'permission': permission,
            }, status=403)

        student_ids = set(Student.objects.filter(
            school_class=school_class, status='active',
        ).values_list('id', flat=True))

        with transaction.atomic():
            for rec in records:
                sid = _parse_int(rec.get('student_id'))
                if sid not in student_ids:
                    continue
                status = (rec.get('status') or 'present').strip().lower()
                # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-8): reject
                # 'holiday' from a per-student mark.
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
                        teacher=staff,
                        marked_by=staff,
                        marked_at=timezone.now(),
                        source='teacher',
                        remarks=remarks,
                    )
                    _record_audit(
                        attendance=row, action='create',
                        new_status=status, staff=staff,
                        reason=f'teacher:{staff.full_name}',
                    )
                else:
                    old_status = existing.status
                    existing.status = status
                    existing.remarks = remarks
                    existing.modified_by = staff
                    existing.modified_at = timezone.now()
                    # Teacher now owns the row even if it was auto/admin.
                    existing.source = 'teacher'
                    existing.school_class = school_class
                    existing.teacher = staff
                    existing.save(update_fields=[
                        'status', 'remarks', 'modified_by', 'modified_at',
                        'source', 'school_class', 'teacher', 'updated_at',
                    ])
                    _record_audit(
                        attendance=existing, action='update',
                        old_status=old_status, new_status=status,
                        staff=staff,
                        reason=f'teacher:{staff.full_name}',
                    )
                saved += 1

        # ATTENDANCE_SYSTEM_BUGFIX_V1 (BUG-2 + BUG-3 + BUG-7):
        # Consume a backdate edit only when the caller is the
        # class teacher, at least one row was actually written,
        # AND the target date is strictly in the past.  Today's
        # attendance is always editable regardless of the backdate
        # quota and must NOT create or increment a quota row.
        #
        # The row is locked with select_for_update() inside an
        # atomic block so two concurrent saves cannot both read
        # the same pre-increment value and both write count+1.
        today = _today()
        if is_ct and saved > 0 and att_date < today:
            with transaction.atomic():
                quota, _ = (
                    ClassTeacherEditQuota.objects
                    .select_for_update()
                    .get_or_create(
                        school_class=school_class, date=att_date,
                    )
                )
                quota.teacher_edit_count = (
                    quota.teacher_edit_count or 0
                ) + 1
                quota.last_teacher_edit_at = timezone.now()
                quota.last_teacher_edit_by_id = staff.pk
                quota.last_teacher_edit_by_name = staff.full_name
                quota.save()
            permission = _compute_permission_payload(
                staff, school_class, att_date,
            )

    return JsonResponse({
        'ok': True,
        'saved': saved,
        'permission': permission,
    })


# ------------------------------------------------------------------ copy API

@require_staff_login
@require_staff_feature('staff_attendance')
@require_http_methods(['POST'])
def staff_attendance_copy_api(request):
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    class_id = _parse_int(payload.get('class_id'))
    target_date = _parse_date(payload.get('date'))
    period_order = _parse_int(payload.get('period_order'))
    source = (payload.get('source') or 'yesterday').strip()

    if not class_id or not target_date:
        return JsonResponse(
            {'ok': False, 'error': 'class_id and date required'}, status=400,
        )

    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if not staff:
            return JsonResponse(
                {'ok': False, 'error': 'Staff not found'}, status=404,
            )
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'Class not found'}, status=404,
            )

        permission = _compute_permission_payload(
            staff, school_class, target_date,
        )
        if not permission['can_edit']:
            return JsonResponse({
                'ok': False,
                'error': permission['reason'] or 'Edit not allowed.',
            }, status=403)

        if source == 'yesterday':
            src_date = target_date - timedelta(days=1)
            while src_date.weekday() == 6 and (
                src_date > target_date - timedelta(days=7)
            ):
                src_date -= timedelta(days=1)
            src_period = period_order
        elif source == 'last_period':
            src_date = target_date
            src_period = (period_order - 1) if period_order else None
            if src_period is not None and src_period < 1:
                return JsonResponse(
                    {'ok': False, 'error': 'No previous period available.'},
                    status=400,
                )
        else:
            return JsonResponse(
                {'ok': False, 'error': 'Invalid source'}, status=400,
            )

        src_qs = StudentAttendance.objects.filter(
            school_class=school_class, date=src_date,
        )
        if src_period is None:
            src_qs = src_qs.filter(period_order__isnull=True)
        else:
            src_qs = src_qs.filter(period_order=src_period)

        if not src_qs.exists():
            return JsonResponse(
                {'ok': False,
                 'error': f'No source records at {src_date} '
                          f'(period={src_period}).'},
                status=404,
            )

        copied = 0
        with transaction.atomic():
            for row in src_qs:
                lookup = {
                    'student_id': row.student_id,
                    'date': target_date,
                    'period_order': period_order,
                }
                existing = StudentAttendance.objects.filter(**lookup).first()
                if existing:
                    continue
                new_row = StudentAttendance.objects.create(
                    **lookup,
                    school_class=school_class,
                    status=row.status,
                    teacher=staff,
                    marked_by=staff,
                    marked_at=timezone.now(),
                    source='teacher',
                    remarks=row.remarks,
                )
                _record_audit(
                    attendance=new_row, action='create',
                    new_status=row.status, staff=staff,
                    reason=f'copied from {source}',
                )
                copied += 1

    return JsonResponse({'ok': True, 'copied': copied})


# ------------------------------------------------------------------ records

@require_staff_login
@require_staff_feature('staff_attendance')
@require_http_methods(['GET'])
def staff_attendance_records_api(request):
    class_id = _parse_int(request.GET.get('class_id'))
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    status = (request.GET.get('status') or '').strip()
    period_order = _parse_int(request.GET.get('period_order'))

    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if not staff:
            return JsonResponse(
                {'ok': False, 'error': 'Staff not found'}, status=404,
            )
        my_class_ids = list(
            _class_teacher_classes(staff).values_list('id', flat=True)
        ) + list(
            PeriodTeacherAssignment.objects
            .filter(teacher=staff)
            .values_list('school_class_id', flat=True)
            .distinct()
        )
        my_class_ids = list(set(my_class_ids))
        if not my_class_ids:
            return JsonResponse({'ok': True, 'records': []})

        qs = (
            StudentAttendance.objects
            .filter(school_class_id__in=my_class_ids)
            .select_related('student', 'school_class')
            .order_by('-date', 'student__roll_number')
        )
        if class_id and class_id in my_class_ids:
            qs = qs.filter(school_class_id=class_id)
        if status in ATTENDANCE_STATUSES:
            qs = qs.filter(status=status)
        if start_date:
            qs = qs.filter(date__gte=start_date)
        if end_date:
            qs = qs.filter(date__lte=end_date)
        if period_order is not None:
            qs = qs.filter(period_order=period_order)

        rows = list(qs[:500])
        data = [{
            'id': r.id,
            'date': r.date.isoformat(),
            'student_name': r.student.name if r.student else '',
            'roll_number': r.student.roll_number if r.student else '',
            'class_name': str(r.school_class) if r.school_class else '',
            'period_order': r.period_order,
            'status': r.status,
            'remarks': r.remarks or '',
        } for r in rows]
    return JsonResponse({'ok': True, 'records': data})


# ------------------------------------------------------------------ missed days

@require_staff_login
@require_staff_feature('staff_attendance')
@require_http_methods(['GET'])
def staff_attendance_missed_days_api(request):
    class_id = _parse_int(request.GET.get('class_id'))
    if not class_id:
        return JsonResponse(
            {'ok': False, 'error': 'class_id required'}, status=400,
        )

    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if not staff:
            return JsonResponse(
                {'ok': False, 'error': 'Staff not found'}, status=404,
            )
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True, class_teacher=staff,
        ).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'Not your class'}, status=403,
            )

        today = _today()
        marked_dates = set(
            StudentAttendance.objects
            .filter(school_class=school_class, period_order__isnull=True)
            .values_list('date', flat=True)
        )
        missed = []
        for i in range(30):
            d = today - timedelta(days=i)
            if d.weekday() == 6:
                continue
            is_hol, _ = _is_holiday(d)
            if is_hol:
                continue
            if d not in marked_dates:
                missed.append(d.isoformat())

    return JsonResponse({
        'ok': True,
        'missed_days': missed,
        'class_id': class_id,
    })


# ------------------------------------------------------------------ policy

@require_staff_login
@require_staff_feature('staff_attendance')
@require_http_methods(['GET'])
def staff_attendance_policy_api(request):
    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        policy = AttendancePolicy.current()
        return JsonResponse({
            'ok': True,
            'attendance_mode': policy.attendance_mode,
            'late_threshold_minutes': policy.late_threshold_minutes,
            'low_attendance_threshold': float(policy.low_attendance_threshold),
            'allow_teacher_backdate_days': policy.allow_teacher_backdate_days,
            'require_admin_approval': policy.require_admin_approval,
            'notify_parents_on_absent': policy.notify_parents_on_absent,
        })
