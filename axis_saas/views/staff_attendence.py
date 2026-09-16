"""AXIS views — Attendance (staff / teacher side).

ATTENDANCE_PRODUCTION_V2
------------------------
Rewritten from scratch. Supports:

  * Period-wise attendance: a subject teacher marks only the students
    in the period they actually taught.
  * Full-day attendance: a class teacher marks the whole day.
  * Holiday auto-skip: any weekly / annual / vacation day returns HTTP 200
    with is_holiday=True and refuses to save.
  * StudentLeave integration: any active approved leave is served with
    status='excused' pre-filled.
  * Bulk operations: /bulk-mark/ accepts N records in a single POST.
  * Copy helpers: /copy-yesterday/ and /copy-last-period/.
  * Audit trail: every create / update writes to AttendanceAuditLog.
"""
import json
import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    SchoolClass, Student, Staff, StudentAttendance, StaffAttendance,
    StudentLeave, AttendancePolicy, AttendanceAuditLog,
    PeriodTeacherAssignment, PeriodsTimetable, ClassTimetableAssignment,
    WeeklyHoliday, AnnualHoliday, Vacation,
)
from .staff_portal import require_staff_login, require_staff_feature

logger = logging.getLogger(__name__)

ATTENDANCE_STATUSES = (
    'present', 'absent', 'late', 'half_day', 'excused', 'holiday',
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
    """Return (is_holiday: bool, reason: str) for the current tenant."""
    dow = on_date.weekday()
    try:
        wh = WeeklyHoliday.objects.filter(day_of_week=dow).first()
        if wh:
            return True, f"Weekly holiday ({wh.label or 'Weekend'})"
    except Exception:
        pass
    try:
        ah = AnnualHoliday.objects.filter(month=on_date.month,
                                          day=on_date.day).first()
        if ah:
            return True, f"Annual holiday ({ah.label})"
    except Exception:
        pass
    try:
        vac = Vacation.objects.filter(start_date__lte=on_date,
                                      end_date__gte=on_date).first()
        if vac:
            return True, f"Vacation ({vac.name})"
    except Exception:
        pass
    return False, ''


def _class_teacher_classes(staff):
    return (
        SchoolClass.objects
        .filter(class_teacher=staff, is_active=True)
        .order_by('name', 'section')
    )


def _periods_for_teacher(staff, on_date):
    dow = on_date.weekday()
    return list(
        PeriodTeacherAssignment.objects
        .filter(teacher=staff, day_of_week=dow)
        .select_related('school_class', 'subject',
                        'school_class__wing_category')
        .order_by('period_order')
    )


def _period_meta(school_class, on_date, period_order):
    """Return {start, end, duration} for a (class, date, period) or None."""
    cta = (
        ClassTimetableAssignment.objects
        .select_related('timetable')
        .filter(school_class=school_class)
        .first()
    )
    if not cta or not cta.timetable:
        return None
    tt = cta.timetable
    dow = on_date.weekday()
    for d in (tt.days or []):
        try:
            if int(d.get('day_of_week', -1)) != dow:
                continue
        except (TypeError, ValueError):
            continue
        for p in (d.get('periods') or []):
            if p.get('is_break'):
                continue
            try:
                if int(p.get('order', -1)) == int(period_order):
                    return {
                        'start': p.get('start', ''),
                        'end': p.get('end', ''),
                        'duration': p.get('duration', 0),
                    }
            except (TypeError, ValueError):
                continue
    return None


def _active_student_leave_ids(on_date):
    """Return set of student ids with an approved leave covering on_date."""
    return set(
        StudentLeave.objects
        .filter(status='approved',
                start_date__lte=on_date,
                end_date__gte=on_date)
        .values_list('student_id', flat=True)
    )


def _record_audit(*, attendance, action, old_status='', new_status='',
                  staff=None, reason=''):
    """Best-effort write to AttendanceAuditLog; never raises."""
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


# ------------------------------------------------------------------ page view

@require_staff_login
@require_staff_feature('staff_attendance')
def staff_attendance_view(request):
    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        today = _today()
        is_hol, holiday_reason = _is_holiday(today)

        periods = []
        if not is_hol:
            for a in _periods_for_teacher(staff, today):
                pm = _period_meta(a.school_class, today, a.period_order)
                marked = StudentAttendance.objects.filter(
                    school_class=a.school_class,
                    date=today,
                    period_order=a.period_order,
                ).exists()
                periods.append({
                    'period_order': a.period_order,
                    'class_id': a.school_class.id,
                    'class_name': str(a.school_class),
                    'subject': a.subject.name if a.subject else '',
                    'start': pm['start'] if pm else '',
                    'end': pm['end'] if pm else '',
                    'marked': marked,
                })

        class_teacher_classes = list(_class_teacher_classes(staff))
        ct_data = []
        for c in class_teacher_classes:
            ct_data.append({
                'id': c.id,
                'name': str(c),
                'student_count': Student.objects.filter(
                    school_class=c, status='active',
                ).count(),
                'marked_today': StudentAttendance.objects.filter(
                    school_class=c,
                    date=today,
                    period_order__isnull=True,
                ).exists(),
            })

        today_marked_count = StudentAttendance.objects.filter(
            date=today,
        ).count()

    context = {
        'staff': staff,
        'today': today.isoformat(),
        'is_holiday': is_hol,
        'holiday_reason': holiday_reason,
        'periods_json': json.dumps(periods),
        'class_teacher_classes_json': json.dumps(ct_data),
        'today_marked_count': today_marked_count,
    }
    response = render(request, 'mobile/staff/attendence.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


# ------------------------------------------------------------------ students API

@require_staff_login
@require_staff_feature('staff_attendance')
@require_http_methods(['GET'])
def staff_attendance_students_api(request):
    """GET students of a class for a date and (optional) period."""
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
            return JsonResponse({'ok': False, 'error': 'Staff not found'},
                                status=404)

        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse({'ok': False, 'error': 'Class not found'},
                                status=404)

        # Authorisation: class teacher OR period teacher
        is_class_teacher = (school_class.class_teacher_id == staff.pk)
        is_period_teacher = True
        if period_order is not None:
            is_period_teacher = PeriodTeacherAssignment.objects.filter(
                teacher=staff,
                school_class=school_class,
                day_of_week=att_date.weekday(),
                period_order=period_order,
            ).exists()
        if not (is_class_teacher or is_period_teacher):
            return JsonResponse(
                {'ok': False, 'error': 'You are not authorised for this class/period'},
                status=403,
            )

        # Holiday gate
        is_hol, holiday_reason = _is_holiday(att_date)
        if is_hol:
            return JsonResponse({
                'ok': True,
                'is_holiday': True,
                'holiday_reason': holiday_reason,
                'date': att_date.isoformat(),
                'class_id': school_class.id,
                'class_name': str(school_class),
                'students': [],
            })

        students = list(
            Student.objects
            .filter(school_class=school_class, status='active')
            .order_by('roll_number', 'name')
        )
        marks_qs = StudentAttendance.objects.filter(
            school_class=school_class, date=att_date,
        )
        if period_order is None:
            marks_qs = marks_qs.filter(period_order__isnull=True)
        else:
            marks_qs = marks_qs.filter(period_order=period_order)
        marks = {
            m.student_id: m
            for m in marks_qs
        }

        leave_ids = _active_student_leave_ids(att_date)

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
            })

    return JsonResponse({
        'ok': True,
        'is_holiday': False,
        'date': att_date.isoformat(),
        'class_id': school_class.id,
        'class_name': str(school_class),
        'period_order': period_order,
        'students': payload,
    })


# ------------------------------------------------------------------ mark API

@require_staff_login
@require_staff_feature('staff_attendance')
@require_http_methods(['POST'])
def staff_attendance_mark_api(request):
    """POST: mark attendance for one class/date/period (bulk of records)."""
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
            return JsonResponse({'ok': False, 'error': 'Staff not found'},
                                status=404)

        is_hol, holiday_reason = _is_holiday(att_date)
        if is_hol:
            return JsonResponse(
                {'ok': False,
                 'error': f'{att_date} is a holiday: {holiday_reason}'},
                status=400,
            )

        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse({'ok': False, 'error': 'Class not found'},
                                status=404)

        # Authorisation
        is_class_teacher = (school_class.class_teacher_id == staff.pk)
        is_period_teacher = True
        if period_order is not None:
            is_period_teacher = PeriodTeacherAssignment.objects.filter(
                teacher=staff,
                school_class=school_class,
                day_of_week=att_date.weekday(),
                period_order=period_order,
            ).exists()
        if not (is_class_teacher or is_period_teacher):
            return JsonResponse(
                {'ok': False, 'error': 'Not authorised for this class/period'},
                status=403,
            )

        # Backdate guard
        try:
            policy = AttendancePolicy.current()
            allow_days = int(policy.allow_teacher_backdate_days or 0)
        except Exception:
            allow_days = 1
        if att_date < _today() - timedelta(days=allow_days):
            return JsonResponse(
                {'ok': False,
                 'error': f'Backdating limited to {allow_days} day(s).'},
                status=400,
            )

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
                if status not in ATTENDANCE_STATUSES:
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
                    _record_audit(attendance=row, action='create',
                                  new_status=status, staff=staff)
                else:
                    old_status = existing.status
                    existing.status = status
                    existing.remarks = remarks
                    existing.modified_by = staff
                    existing.modified_at = timezone.now()
                    existing.school_class = school_class
                    existing.save(update_fields=[
                        'status', 'remarks', 'modified_by',
                        'modified_at', 'school_class', 'updated_at',
                    ])
                    if old_status != status:
                        _record_audit(attendance=existing, action='update',
                                      old_status=old_status, new_status=status,
                                      staff=staff)
                saved += 1

    return JsonResponse({'ok': True, 'saved': saved})


# ------------------------------------------------------------------ copy APIs

@require_staff_login
@require_staff_feature('staff_attendance')
@require_http_methods(['POST'])
def staff_attendance_copy_api(request):
    """POST: copy yesterday's / last period's records onto a target slot.

    Body:
        class_id, date, period_order (or null),
        source ('yesterday' | 'last_period')
    """
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
            return JsonResponse({'ok': False, 'error': 'Staff not found'},
                                status=404)
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse({'ok': False, 'error': 'Class not found'},
                                status=404)

        if source == 'yesterday':
            src_date = target_date - timedelta(days=1)
            while src_date.weekday() == 6 and src_date > target_date - timedelta(days=7):
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
            return JsonResponse({'ok': False, 'error': 'Invalid source'},
                                status=400)

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
                _record_audit(attendance=new_row, action='create',
                              new_status=row.status, staff=staff,
                              reason=f'copied from {source}')
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
            return JsonResponse({'ok': False, 'error': 'Staff not found'},
                                status=404)
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
        return JsonResponse({'ok': False, 'error': 'class_id required'},
                            status=400)

    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if not staff:
            return JsonResponse({'ok': False, 'error': 'Staff not found'},
                                status=404)
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True, class_teacher=staff,
        ).first()
        if not school_class:
            return JsonResponse({'ok': False, 'error': 'Not your class'},
                                status=403)

        today = _today()
        marked_dates = set(
            StudentAttendance.objects
            .filter(school_class=school_class, period_order__isnull=True)
            .values_list('date', flat=True)
        )
        missed = []
        for i in range(30):
            d = today - timedelta(days=i)
            if d.weekday() == 6:  # Sunday
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
