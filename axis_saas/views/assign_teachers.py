"""
AXIS views - assign periods to teachers (ASSIGN_TEACHERS_v1).

Lets the admin pick a class (that already has a periods timetable assigned)
and, for each period slot, choose a subject. The subject's teacher in this
class (from ClassSubject.teacher) is saved alongside.
"""
import json
import logging

from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    SchoolClass, ClassTimetableAssignment,
    ClassSubject, PeriodTeacherAssignment,
)
from .helpers import get_tenant, require_tenant_type, require_school_feature
from axis_saas.utils.class_display import get_class_display_name


logger = logging.getLogger(__name__)


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def timetable_assign_teachers(request, schema_name):
    """Main page: shows classes that have a periods timetable + a
    button to open the assignment modal."""
    tenant = get_tenant(request, schema_name)

    with schema_context(schema_name):
        class_assignments = list(
            ClassTimetableAssignment.objects
            .select_related('school_class__wing_category', 'timetable')
            .order_by('school_class__name', 'school_class__section')
        )

        class_rows = []
        for a in class_assignments:
            cls = a.school_class
            display = get_class_display_name(cls, tenant.tenant_type)
            total_periods = 0
            for d in (a.timetable.days or []):
                try:
                    total_periods += int(d.get('periods_count') or 0)
                except (TypeError, ValueError):
                    pass
            assigned_count = PeriodTeacherAssignment.objects.filter(
                school_class=cls, subject__isnull=False,
            ).count()
            class_rows.append({
                'class_id': cls.id,
                'class_display_name': display,
                'timetable_title': a.timetable.title,
                # TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX: label is a FK now.
                'timetable_label': a.timetable.label.name if a.timetable.label_id else '',
                'total_periods': total_periods,
                'assigned_count': assigned_count,
            })

        assigned_class_ids = {a.school_class_id for a in class_assignments}
        classes_without_timetable = []
        for c in SchoolClass.objects.filter(is_active=True).order_by('name', 'section'):
            if c.id not in assigned_class_ids:
                classes_without_timetable.append({
                    'id': c.id,
                    'display_name': get_class_display_name(c, tenant.tenant_type),
                })

    context = {
        'tenant': tenant,
        'class_rows': class_rows,
        'classes_without_timetable': classes_without_timetable,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
    response = render(request, 'tenant/timetable_assign_teachers.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_get_teacher_assignments(request, schema_name, class_id):
    """Return data needed to render the grid for a given class.

    TEACHER_TIMETABLE_CONFLICT_V2
    -----------------------------
    Also returns a `teacher_busy` map of
        "teacher_id|day|period" -> {"class": <other class display>,
                                    "teacher_name": <teacher full name>}
    so the UI can display "⚠ <FirstName> busy in <Class>" BEFORE the
    admin presses Save. The server is still the source of truth.
    """
    tenant = get_tenant(request, schema_name)

    with schema_context(schema_name):
        school_class = get_object_or_404(SchoolClass, id=class_id, is_active=True)
        assignment = (
            ClassTimetableAssignment.objects
            .select_related('timetable')
            .filter(school_class=school_class)
            .first()
        )

        class_display = get_class_display_name(school_class, tenant.tenant_type)

        if not assignment or not assignment.timetable:
            return JsonResponse({
                'has_timetable': False,
                'class_id': school_class.id,
                'class_display': class_display,
            })

        tt = assignment.timetable

        # Subjects of this class whose teacher is set (these are the only
        # valid choices for period assignment)
        subjects = []
        for cs in (
            ClassSubject.objects
            .filter(school_class=school_class, is_active=True, teacher__isnull=False)
            .select_related('subject', 'teacher')
            .order_by('subject__name')
        ):
            subjects.append({
                'subject_id': cs.subject_id,
                'subject_name': cs.subject.name,
                'teacher_id': cs.teacher_id,
                'teacher_name': cs.teacher.full_name if cs.teacher else '',
            })

        existing = {}
        for pta in PeriodTeacherAssignment.objects.filter(school_class=school_class):
            key = f"{pta.day_of_week}|{pta.period_order}"
            existing[key] = {
                'subject_id': pta.subject_id,
            }

        # ---- TEACHER_TIMETABLE_CONFLICT_V2: build busy map --------------
        # Every (teacher, day, period) already occupied in SOME OTHER class.
        # Value carries both the other class display AND the teacher name
        # so the client can render "⚠ Ayesha busy in 10-A".
        teacher_busy = {}
        _other_rows = (
            PeriodTeacherAssignment.objects
            .filter(teacher__isnull=False)
            .exclude(school_class=school_class)
            .select_related('school_class', 'school_class__wing_category', 'teacher')
        )
        for _pta in _other_rows:
            _cls_display = get_class_display_name(_pta.school_class, tenant.tenant_type)
            _key = f"{_pta.teacher_id}|{_pta.day_of_week}|{_pta.period_order}"
            teacher_busy[_key] = {
                'class': _cls_display,
                'teacher_name': _pta.teacher.full_name if _pta.teacher else '',
            }

        # ---- INLINE_TIMETABLE_ASSIGN_V1: edit-form data -------------
        # Everything the client needs to render the "Edit Timetable"
        # slot-selection form INLINE (no extra round trip).
        from ..models import (
            DaySchedule as _TT_DS,
            ScheduleLabel as _TT_SL,
            PeriodsTimetable as _TT_PTT,
        )

        _slots_by_label = {}
        try:
            for _lbl in _TT_SL.objects.all().order_by('name'):
                _scheds = _TT_DS.objects.filter(label=_lbl).order_by('day_of_week', 'order')
                _slots_by_label[_lbl.name] = [
                    {
                        'id': _ds.id,
                        'day': _ds.day_of_week,
                        'day_label': _ds.get_day_of_week_display(),
                        'start': _ds.start_time.strftime('%H:%M'),
                        'end': _ds.end_time.strftime('%H:%M'),
                        'periods': _ds.periods,
                        'duration': _ds.duration,
                    }
                    for _ds in _scheds
                ]
        except Exception as _exc:
            logger.warning('INLINE_TIMETABLE_ASSIGN_V1: slots_by_label failed: %s', _exc)
            _slots_by_label = {}

        _all_timetables = []
        try:
            for _t in _TT_PTT.objects.order_by('id'):
                _all_timetables.append({
                    'id': _t.id,
                    'title': _t.title,
                    # TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX: label is a FK now.
                    'label': _t.label.name if _t.label_id else '',
                    'break_duration': _t.break_duration or 0,
                    'days': _t.days or [],
                })
        except Exception as _exc:
            logger.warning('INLINE_TIMETABLE_ASSIGN_V1: all_timetables failed: %s', _exc)
            _all_timetables = []

        return JsonResponse({
            'has_timetable': True,
            'class_id': school_class.id,
            'class_display': class_display,
            'timetable_id': tt.id,
            'timetable_title': tt.title,
            'timetable_label': tt.label.name if tt.label_id else '',
            'timetable_days': tt.days or [],
            'timetable_break_duration': tt.break_duration or 0,
            # Backwards-compat alias: the existing template reads
            # `data.break_duration` when deciding whether to render
            # break columns. Previously this key was missing, so breaks
            # never showed on this page. Fixed here.
            'break_duration': tt.break_duration or 0,
            'subjects': subjects,
            'existing': existing,
            'teacher_busy': teacher_busy,
            'slots_by_label': _slots_by_label,
            'all_timetables': _all_timetables,
        })


@csrf_exempt
@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_save_teacher_assignments(request, schema_name, class_id):
    """Persist the full set of (day, period) -> subject assignments for a class."""
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    items = data.get('assignments', [])
    if not isinstance(items, list):
        return JsonResponse({'success': False, 'error': 'assignments must be a list'}, status=400)

    with schema_context(schema_name):
        school_class = get_object_or_404(SchoolClass, id=class_id, is_active=True)

        # Only subjects whose teacher is already set for this class are valid
        class_subject_teacher = {
            cs.subject_id: cs.teacher_id
            for cs in ClassSubject.objects.filter(
                school_class=school_class, is_active=True, teacher__isnull=False,
            )
        }

        existing_map = {
            (pta.day_of_week, pta.period_order): pta
            for pta in PeriodTeacherAssignment.objects.filter(school_class=school_class)
        }

        saved = 0
        removed = 0
        skipped = 0

        for item in items:
            try:
                day = int(item.get('day'))
                order = int(item.get('order'))
            except (TypeError, ValueError):
                skipped += 1
                continue

            subject_id_raw = item.get('subject_id')
            if subject_id_raw in (None, '', 'null', 0, '0'):
                subject_id = None
            else:
                try:
                    subject_id = int(subject_id_raw)
                except (TypeError, ValueError):
                    skipped += 1
                    continue

            key = (day, order)

            if subject_id is None:
                existing = existing_map.get(key)
                if existing is not None:
                    existing.delete()
                    removed += 1
                continue

            if subject_id not in class_subject_teacher:
                # Teacher not assigned for this subject in this class -> skip
                skipped += 1
                continue

            teacher_id = class_subject_teacher[subject_id]
            existing = existing_map.get(key)
            if existing is not None:
                existing.subject_id = subject_id
                existing.teacher_id = teacher_id
                existing.save(update_fields=['subject', 'teacher', 'updated_at'])
            else:
                PeriodTeacherAssignment.objects.create(
                    school_class=school_class,
                    day_of_week=day,
                    period_order=order,
                    subject_id=subject_id,
                    teacher_id=teacher_id,
                )
            saved += 1

        return JsonResponse({
            'success': True,
            'saved': saved,
            'removed': removed,
            'skipped': skipped,
        })


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
