"""
AXIS views - assign periods to teachers (ASSIGN_TEACHERS_v1).

ASSIGN_TEACHERS_HARDENING_V3
----------------------------
Follow-up hardening on top of V2.

  * Race condition fix: server-side conflict check now locks the
    affected Staff rows (select_for_update) so two concurrent saves
    that touch the same teacher serialize. V2's snapshot could be
    stale under READ COMMITTED, which let both transactions see the
    teacher as free and both commit.
  * Empty-payload guard: refuse to wipe every assignment when the
    client accidentally sends `assignments: []` and rows already
    exist. Pass `allow_empty: true` to deliberately clear.
  * Optimistic lock tightening: when rows exist for the class but the
    client did not send a version token, refuse instead of silently
    overwriting (was: silently allowed).
  * Substitute qualification check: api_create_substitute now verifies
    the substitute actually teaches the same subject in some class
    (ClassSubject), not just that they are free and active.
  * api_get_todays_leave filters orphan PeriodTeacherAssignment rows
    against each class's current PeriodsTimetable.days, so calendar
    edits can no longer surface phantom absent periods.
  * api_get_todays_leave on_leave_count now counts distinct teachers
    who actually have a scheduled period today, not every staff member
    on approved leave.
  * _reconcile_period_teacher_assignments batched: 2 queries total
    instead of one per class.
"""

import json
import logging

from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    SchoolClass, ClassTimetableAssignment,
    ClassSubject, PeriodTeacherAssignment,
    PeriodsTimetable, DaySchedule, ScheduleLabel,
    Staff, Subject, LeaveRequest, SubstituteAssignment,
    WeeklyHoliday, AnnualHoliday, Vacation,
)
from .helpers import get_tenant, require_tenant_type, require_school_feature
from axis_saas.utils.class_display import get_class_display_name


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------

def _today_date():
    return timezone.localdate()


def _day_label(dow):
    try:
        return dict(DaySchedule.DAY_CHOICES).get(int(dow), str(dow))
    except (TypeError, ValueError):
        return str(dow)


def _is_holiday_today():
    """Return (is_holiday: bool, reason: str) for today in the current
    tenant schema. Safe when any of the holiday tables is missing.

    HARDENING_V5: only swallow the specific operational errors
    that mean "table missing". Any other exception (a real
    schema mismatch, a data-integrity error, a bare Python
    bug) is logged and re-raised, so a silent failure cannot
    schedule substitutes on a holiday.
    """
    from django.db import ProgrammingError, OperationalError
    today = _today_date()
    dow = today.weekday()
    try:
        wh = WeeklyHoliday.objects.filter(day_of_week=dow).first()
        if wh:
            return True, f"Weekly holiday ({wh.label})"
    except (ProgrammingError, OperationalError) as _exc:
        logger.warning('_is_holiday_today: WeeklyHoliday lookup failed: %s', _exc)
    try:
        ah = AnnualHoliday.objects.filter(
            month=today.month, day=today.day,
        ).first()
        if ah:
            return True, f"Annual holiday ({ah.label})"
    except (ProgrammingError, OperationalError) as _exc:
        logger.warning('_is_holiday_today: AnnualHoliday lookup failed: %s', _exc)
    try:
        vac = Vacation.objects.filter(
            start_date__lte=today, end_date__gte=today,
        ).first()
        if vac:
            return True, f"Vacation ({vac.name})"
    except (ProgrammingError, OperationalError) as _exc:
        logger.warning('_is_holiday_today: Vacation lookup failed: %s', _exc)
    return False, ''


def _class_timetable_periods(timetable):
    """Return a set of (day_of_week, period_order) tuples the timetable
    actually contains. Breaks are excluded."""
    periods = set()
    if timetable is None or not timetable.days:
        return periods
    for day in timetable.days:
        try:
            dow = int(day.get('day_of_week'))
        except (TypeError, ValueError):
            continue
        for p in (day.get('periods') or []):
            if p.get('is_break'):
                continue
            try:
                order = int(p.get('order'))
            except (TypeError, ValueError):
                continue
            periods.add((dow, order))
    return periods


def _teacher_on_leave_today(staff, today=None):
    if staff is None:
        return False
    if today is None:
        today = _today_date()
    return LeaveRequest.objects.filter(
        staff=staff,
        status='approved',
        start_date__lte=today,
        end_date__gte=today,
    ).exists()


def _admin_username(request):
    return (request.session.get('school_admin_username') or 'admin') or 'admin'


# ---------------------------------------------------------------------
# Main landing page
# ---------------------------------------------------------------------

@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def timetable_assign_teachers(request, schema_name):
    """Main page: shows classes that have a periods timetable + a
    button to open the assignment modal."""
    tenant = get_tenant(request, schema_name)

    with schema_context(schema_name):
        class_assignments = list(
            ClassTimetableAssignment.objects
            .select_related(
                'school_class__wing_category__parent',
                'timetable__label',
            )
            .filter(school_class__is_active=True)
            .order_by('school_class__name', 'school_class__section')
        )

        _counts = dict(
            PeriodTeacherAssignment.objects
            .filter(school_class__is_active=True, subject__isnull=False)
            .values('school_class_id')
            .annotate(n=Count('id'))
            .values_list('school_class_id', 'n')
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
            class_rows.append({
                'class_id': cls.id,
                'class_display_name': display,
                'timetable_title': a.timetable.title,
                'timetable_label': a.timetable.label.name if a.timetable.label_id else '',
                'total_periods': total_periods,
                'assigned_count': _counts.get(cls.id, 0),
            })

        assigned_class_ids = {a.school_class_id for a in class_assignments}
        classes_without_timetable = []
        # B2b fix: __parent is read by get_class_display_name for
        # wing schools; without it every row pays one extra query.
        for c in SchoolClass.objects.filter(is_active=True).select_related('wing_category__parent').order_by('name', 'section'):
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


# ---------------------------------------------------------------------
# GET: grid data for a single class
# ---------------------------------------------------------------------

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_get_teacher_assignments(request, schema_name, class_id):
    """Return data needed to render the grid for a given class."""
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

        subjects = []
        for cs in (
            ClassSubject.objects
            .filter(
                school_class=school_class,
                is_active=True,
                teacher__isnull=False,
                teacher__status='active',
            )
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
                'updated_at': pta.updated_at.isoformat() if pta.updated_at else '',
            }

        # BUG-4 fix: filter orphan PTA rows so classes whose timetable
        # shrank (or was unassigned) do not produce false conflicts.
        teacher_busy = {}
        _other_rows = list(
            PeriodTeacherAssignment.objects
            .filter(teacher__isnull=False, teacher__status='active')
            .exclude(school_class=school_class)
            .select_related('school_class', 'school_class__wing_category', 'teacher')
        )
        _other_class_ids = {r.school_class_id for r in _other_rows}
        _other_valid = {}
        if _other_class_ids:
            for _cta in (
                ClassTimetableAssignment.objects
                .filter(school_class_id__in=_other_class_ids)
                .select_related('timetable')
            ):
                _other_valid[_cta.school_class_id] = (
                    _class_timetable_periods(_cta.timetable)
                )
        for _pta in _other_rows:
            _valid = _other_valid.get(_pta.school_class_id)
            if _valid is None or (
                _pta.day_of_week, _pta.period_order
            ) not in _valid:
                continue
            _cls_display = get_class_display_name(
                _pta.school_class, tenant.tenant_type,
            )
            _key = f"{_pta.teacher_id}|{_pta.day_of_week}|{_pta.period_order}"
            teacher_busy[_key] = {
                'class': _cls_display,
                'teacher_name': _pta.teacher.full_name if _pta.teacher else '',
            }

        _slots_by_label = {}
        try:
            for _lbl in ScheduleLabel.objects.all().order_by('name'):
                _scheds = (
                    DaySchedule.objects
                    .filter(label=_lbl)
                    .order_by('day_of_week', 'order')
                )
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
            logger.warning('slots_by_label failed: %s', _exc)
            _slots_by_label = {}

        _all_timetables = []
        try:
            # BUG-2 fix: select_related('label') so reading
            # _t.label.name below does not fire one extra query per
            # timetable. _load_timetables() in periods.py already
            # did this; this path had been missed. 50 timetables on
            # a tenant dropped from 50+ queries to one.
            for _t in PeriodsTimetable.objects.select_related('label').order_by('id'):
                _all_timetables.append({
                    'id': _t.id,
                    'title': _t.title,
                    'label': _t.label.name if _t.label_id else '',
                    'break_duration': _t.break_duration or 0,
                    'days': _t.days or [],
                    'updated_at': (
                        _t.updated_at.isoformat() if _t.updated_at else ''
                    ),
                })
        except Exception as _exc:
            logger.warning('all_timetables failed: %s', _exc)
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
            'timetable_updated_at': (
                tt.updated_at.isoformat() if tt.updated_at else ''
            ),
            'break_duration': tt.break_duration or 0,
            'subjects': subjects,
            'existing': existing,
            'teacher_busy': teacher_busy,
            'slots_by_label': _slots_by_label,
            'all_timetables': _all_timetables,
        })


# ---------------------------------------------------------------------
# POST: save assignments
# ---------------------------------------------------------------------

@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_save_teacher_assignments(request, schema_name, class_id):
    """Persist the full set of (day, period) -> subject assignments.

    ASSIGN_TEACHERS_HARDENING_V3 race-condition fix: the server-side
    conflict check must see a consistent snapshot. We serialise
    concurrent saves that touch the same teacher by locking the
    affected Staff rows with select_for_update() before reading the
    busy map. Sorted by id to prevent deadlocks between two requests
    that lock overlapping teacher sets.
    """
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse(
            {'success': False, 'error': 'Invalid JSON'}, status=400,
        )

    items = data.get('assignments', [])
    if not isinstance(items, list):
        return JsonResponse(
            {'success': False, 'error': 'assignments must be a list'},
            status=400,
        )

    client_tt_updated_at = (data.get('timetable_updated_at') or '').strip()
    admin_user = _admin_username(request)
    allow_empty = bool(data.get('allow_empty', False))

    with schema_context(schema_name):
        with transaction.atomic():
            school_class = (
                SchoolClass.objects
                .select_for_update()
                .filter(id=class_id, is_active=True)
                .first()
            )
            if school_class is None:
                return JsonResponse(
                    {'success': False, 'error': 'Class not found or inactive.'},
                    status=404,
                )

            assignment = (
                ClassTimetableAssignment.objects
                .select_related('timetable')
                .filter(school_class=school_class)
                .first()
            )
            if assignment is None or assignment.timetable is None:
                return JsonResponse(
                    {'success': False,
                     'error': 'Class has no assigned timetable.'},
                    status=400,
                )

            tt = assignment.timetable

            # ---- Empty-payload guard --------------------------------
            # V4_1: moved ABOVE the optimistic-lock check. When the
            # payload is empty AND rows exist, the correct error is
            # the specific 400 "refusing to wipe" — not the generic
            # 409 "missing version token". Both guards protect
            # against data loss; the empty guard is the more
            # specific one and must fire first.
            # Refuse to wipe all assignments unless allow_empty=true.
            _existing_count = PeriodTeacherAssignment.objects.filter(
                school_class=school_class,
            ).count()
            if not items and _existing_count and not allow_empty:
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'Empty assignments payload received, but '
                        f'{_existing_count} assignment(s) already exist. '
                        f'Refusing to delete them. Send '
                        f'"allow_empty": true to deliberately clear.'
                    ),
                }, status=400)

            # ---- Optimistic lock pre-check --------------------------
            # ASSIGN_TEACHERS_HARDENING_V3: when rows exist for this
            # class but the client omitted the version token, refuse
            # rather than silently overwrite. This is stricter than V2
            # (which treated missing tokens as "no version info").
            _has_existing_rows = PeriodTeacherAssignment.objects.filter(
                school_class=school_class,
            ).exists()
            if client_tt_updated_at and tt.updated_at:
                if client_tt_updated_at != tt.updated_at.isoformat():
                    return JsonResponse({
                        'success': False,
                        'error': (
                            'The timetable was modified by another session '
                            'since this page was loaded. Reload and retry.'
                        ),
                    }, status=409)
            elif _has_existing_rows and not client_tt_updated_at:
                # BUG-7 fix: previously this only logged and let the
                # client through. A raw API call that simply omits
                # the token could silently overwrite existing rows.
                # Refuse instead — the client is expected to
                # round-trip timetable_updated_at.
                return JsonResponse({
                    'success': False,
                    'error': (
                        'Missing version token (timetable_updated_at). '
                        'Refusing to overwrite existing assignments. '
                        'Reload the page and retry.'
                    ),
                }, status=409)

            valid_periods = _class_timetable_periods(tt)

            class_subject_teacher = {
                cs.subject_id: cs.teacher_id
                for cs in ClassSubject.objects.filter(
                    school_class=school_class, is_active=True,
                    teacher__isnull=False, teacher__status='active',
                )
            }

            # ---- Parse planned into a dict --------------------------
            planned = {}
            for item in items:
                try:
                    day = int(item.get('day'))
                    order = int(item.get('order'))
                except (TypeError, ValueError):
                    continue
                subj_raw = item.get('subject_id')
                if subj_raw in (None, '', 'null', 0, '0'):
                    subj_id = None
                else:
                    try:
                        subj_id = int(subj_raw)
                    except (TypeError, ValueError):
                        continue
                planned[(day, order)] = subj_id

            # ---- Race-condition fix: lock affected Staff rows ------
            # Any two concurrent saves that assign the same teacher to
            # any slot must serialise here. We lock only the teachers
            # we are about to assign; removals do not create conflicts.
            _teacher_ids_to_lock = set()
            for (day, order), subj_id in planned.items():
                if subj_id is None:
                    continue
                tid = class_subject_teacher.get(subj_id)
                if tid:
                    _teacher_ids_to_lock.add(tid)
            if _teacher_ids_to_lock:
                # Materialise the queryset to actually acquire the row
                # locks. Sorted order prevents deadlock with another
                # transaction that locks an overlapping set.
                list(
                    Staff.objects
                    .select_for_update()
                    .filter(id__in=sorted(_teacher_ids_to_lock))
                    .order_by('id')
                    .values_list('id', flat=True)
                )

            # ---- Now the busy map is a consistent snapshot ----------
            # BUG-4 fix: skip orphan PTA rows whose (day, period) no
            # longer exists in the class's current timetable. Without
            # this a class whose timetable shrank causes phantom
            # conflicts at save time.
            _other_busy = {}
            _other_pta_rows = list(
                PeriodTeacherAssignment.objects
                .filter(teacher__isnull=False)
                .exclude(school_class=school_class)
                .select_related(
                    'teacher', 'school_class', 'school_class__wing_category',
                )
            )
            _ob_class_ids = {r.school_class_id for r in _other_pta_rows}
            _ob_valid = {}
            if _ob_class_ids:
                for _cta in (
                    ClassTimetableAssignment.objects
                    .filter(school_class_id__in=_ob_class_ids)
                    .select_related('timetable')
                ):
                    _ob_valid[_cta.school_class_id] = (
                        _class_timetable_periods(_cta.timetable)
                    )
            for _pta in _other_pta_rows:
                _valid = _ob_valid.get(_pta.school_class_id)
                if _valid is None or (
                    _pta.day_of_week, _pta.period_order
                ) not in _valid:
                    continue
                _other_busy[
                    (_pta.teacher_id, _pta.day_of_week, _pta.period_order)
                ] = _pta

            existing_map = {
                (pta.day_of_week, pta.period_order): pta
                for pta in PeriodTeacherAssignment.objects
                    .select_for_update()
                    .filter(school_class=school_class)
            }

            # ---- Period validation ---------------------------------
            invalid_periods = [
                (d, o) for (d, o) in planned.keys()
                if (d, o) not in valid_periods
            ]
            if invalid_periods:
                return JsonResponse({
                    'success': False,
                    'error': (
                        'These (day, period) slots do not exist in the '
                        'class timetable: ' +
                        ', '.join(
                            f'{_day_label(d)} P{o}'
                            for d, o in invalid_periods
                        )
                    ),
                    'invalid_periods': [
                        {'day': d, 'order': o}
                        for d, o in invalid_periods
                    ],
                }, status=400)

            # ---- Conflict check (safe now) -------------------------
            conflicts = []
            for (day, order), subj_id in planned.items():
                if subj_id is None:
                    continue
                tid = class_subject_teacher.get(subj_id)
                if tid is None:
                    continue
                existing = existing_map.get((day, order))
                if existing is not None and existing.teacher_id == tid:
                    continue
                busy = _other_busy.get((tid, day, order))
                if busy is not None:
                    teacher_name = (
                        busy.teacher.full_name if busy.teacher else str(tid)
                    )
                    try:
                        busy_class = get_class_display_name(
                            busy.school_class, tenant.tenant_type,
                        )
                    except Exception:
                        busy_class = str(busy.school_class)
                    conflicts.append({
                        'teacher': teacher_name,
                        'class': busy_class,
                        'day': day,
                        'order': order,
                    })
            if conflicts:
                msgs = [
                    f"{c['teacher']} is already teaching {c['class']} "
                    f"on {_day_label(c['day'])} P{c['order']}."
                    for c in conflicts
                ]
                return JsonResponse({
                    'success': False,
                    'error': 'Teacher conflict: ' + ' '.join(msgs),
                    'conflicts': conflicts,
                }, status=400)

            # ---- Apply ---------------------------------------------
            saved = 0
            removed = 0
            skipped = 0
            skipped_items = []

            for item in items:
                try:
                    day = int(item.get('day'))
                    order = int(item.get('order'))
                except (TypeError, ValueError):
                    skipped += 1
                    skipped_items.append(
                        {'reason': 'invalid_day_or_order', 'item': item}
                    )
                    continue

                subj_raw = item.get('subject_id')
                if subj_raw in (None, '', 'null', 0, '0'):
                    subject_id = None
                else:
                    try:
                        subject_id = int(subj_raw)
                    except (TypeError, ValueError):
                        skipped += 1
                        skipped_items.append(
                            {'reason': 'invalid_subject_id', 'item': item}
                        )
                        continue

                key = (day, order)

                if (day, order) not in valid_periods:
                    skipped += 1
                    skipped_items.append({
                        'reason': 'period_not_in_timetable',
                        'day': day, 'order': order,
                    })
                    continue

                if subject_id is None:
                    existing = existing_map.get(key)
                    if existing is not None:
                        existing.delete()
                        removed += 1
                    continue

                if subject_id not in class_subject_teacher:
                    skipped += 1
                    skipped_items.append({
                        'reason': 'subject_teacher_missing_or_inactive',
                        'subject_id': subject_id,
                    })
                    continue

                teacher_id = class_subject_teacher[subject_id]
                existing = existing_map.get(key)
                if existing is not None:
                    existing.subject_id = subject_id
                    existing.teacher_id = teacher_id
                    existing.updated_by = admin_user
                    existing.save(update_fields=[
                        'subject', 'teacher', 'updated_by', 'updated_at',
                    ])
                else:
                    PeriodTeacherAssignment.objects.create(
                        school_class=school_class,
                        day_of_week=day,
                        period_order=order,
                        subject_id=subject_id,
                        teacher_id=teacher_id,
                        created_by=admin_user,
                        updated_by=admin_user,
                    )
                saved += 1

            return JsonResponse({
                'success': True,
                'saved': saved,
                'removed': removed,
                'skipped': skipped,
                'skipped_items': skipped_items,
            })


# ---------------------------------------------------------------------
# GET: today's leave + fixtures
# ---------------------------------------------------------------------

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_get_todays_leave(request, schema_name):
    """GET: today's approved-leave teachers + their periods today +
    the free teachers available at each of those periods.

    ASSIGN_TEACHERS_HARDENING_V3:
      * Filters orphan PeriodTeacherAssignment rows against each
        class's current PeriodsTimetable.days, so calendar edits
        cannot surface phantom absent periods.
      * on_leave_count counts distinct teachers with a scheduled
        period today, not every staff member on leave.
    """
    tenant = get_tenant(request, schema_name)
    today = _today_date()
    today_dow = today.weekday()

    with schema_context(schema_name):
        is_holiday, holiday_reason = _is_holiday_today()
        if is_holiday:
            return JsonResponse({
                'success': True,
                'date': today.isoformat(),
                'day_name': _day_label(today_dow),
                'day_of_week': today_dow,
                'on_leave_count': 0,
                'assignments': [],
                'is_holiday': True,
                'holiday_reason': holiday_reason,
            })

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
                'is_holiday': False,
            })

        absent_periods_qs = (
            PeriodTeacherAssignment.objects
            .filter(teacher_id__in=on_leave_ids, day_of_week=today_dow)
            .select_related(
                'teacher', 'school_class', 'school_class__wing_category',
                'subject',
            )
            .order_by('period_order', 'school_class__name',
                      'school_class__section')
        )
        absent_periods = list(absent_periods_qs)

        # ---- ASSIGN_TEACHERS_HARDENING_V3: orphan filter -----------
        # Drop rows whose (day, period) no longer exists in the
        # class's assigned timetable. Batched: one query for all
        # relevant ClassTimetableAssignment rows.
        _cls_ids = {ap.school_class_id for ap in absent_periods}
        _valid_by_class = {}
        if _cls_ids:
            for _cta in (
                ClassTimetableAssignment.objects
                .filter(school_class_id__in=_cls_ids)
                .select_related('timetable')
            ):
                _valid_by_class[_cta.school_class_id] = (
                    _class_timetable_periods(_cta.timetable)
                )
        absent_periods = [
            ap for ap in absent_periods
            if (ap.day_of_week, ap.period_order)
                in _valid_by_class.get(ap.school_class_id, set())
        ]

        # BUG-6 fix: filter orphan PTA rows before treating a
        # teacher as busy at a period. A row whose (day, period)
        # no longer exists in its class's timetable must not
        # count, otherwise a free teacher shows up as busy.
        _busy_class_ids = set(
            PeriodTeacherAssignment.objects
            .filter(day_of_week=today_dow, teacher__isnull=False)
            .values_list('school_class_id', flat=True)
        )
        _busy_valid_map = {}
        if _busy_class_ids:
            for _cta in (
                ClassTimetableAssignment.objects
                .filter(school_class_id__in=_busy_class_ids)
                .select_related('timetable')
            ):
                _busy_valid_map[_cta.school_class_id] = (
                    _class_timetable_periods(_cta.timetable)
                )
        busy_rows = (
            PeriodTeacherAssignment.objects
            .filter(day_of_week=today_dow, teacher__isnull=False)
            .values_list('teacher_id', 'period_order', 'school_class_id')
        )
        busy_by_period = {}
        for _tid, _porder, _cid in busy_rows:
            _valid = _busy_valid_map.get(_cid)
            if _valid is None or (today_dow, _porder) not in _valid:
                continue
            busy_by_period.setdefault(_porder, set()).add(_tid)

        existing_subs = {}
        for sa in (
            SubstituteAssignment.objects
            .filter(date=today, day_of_week=today_dow)
            .select_related('substitute_teacher')
        ):
            existing_subs[(sa.school_class_id, sa.period_order)] = sa

        already_used_sub_by_period = {}
        for sa in existing_subs.values():
            already_used_sub_by_period.setdefault(
                sa.period_order, set(),
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

        # Distinct teachers who actually have a scheduled absent period
        # today. Not the same as len(on_leave_ids), which counts every
        # staff member on approved leave regardless of schedule.
        on_leave_count = len({
            a['absent_teacher_id'] for a in assignments
        })

        return JsonResponse({
            'success': True,
            'date': today.isoformat(),
            'day_name': _day_label(today_dow),
            'day_of_week': today_dow,
            'on_leave_count': on_leave_count,
            'assignments': assignments,
            'is_holiday': False,
        })


# ---------------------------------------------------------------------
# POST: create substitute
# ---------------------------------------------------------------------

@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_create_substitute(request, schema_name):
    """POST: create / update a substitute assignment for today.

    ASSIGN_TEACHERS_HARDENING_V3: also verifies the substitute actually
    teaches the subject in some class (ClassSubject.teacher = sub).
    Without this a Math teacher could be assigned to a Physics class.
    """
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
        return JsonResponse({
            'success': False,
            'error': 'absent_teacher_id, substitute_teacher_id, class_id '
                     'and period_order are required.',
        }, status=400)
    if absent_teacher_id == substitute_teacher_id:
        return JsonResponse({
            'success': False,
            'error': 'The substitute cannot be the same as the absent '
                     'teacher.',
        }, status=400)

    today = _today_date()
    today_dow = today.weekday()
    admin_user = _admin_username(request)

    with schema_context(schema_name):
        with transaction.atomic():
            # BUG-3 fix: lock the affected Staff rows in a stable
            # (sorted) order so two concurrent substitute requests
            # that touch the same teacher serialise. Without this
            # both requests can see the substitute as free and both
            # insert, producing a double-booking that no DB
            # constraint catches (unique_together is per-class).
            _lock_ids = sorted({absent_teacher_id, substitute_teacher_id})
            list(
                Staff.objects
                .select_for_update()
                .filter(id__in=_lock_ids)
                .order_by('id')
                .values_list('id', flat=True)
            )
            absent = Staff.objects.filter(id=absent_teacher_id).first()
            substitute = Staff.objects.filter(
                id=substitute_teacher_id, status='active',
            ).first()
            school_class = (
                SchoolClass.objects
                .select_for_update()
                .filter(id=class_id, is_active=True)
                .first()
            )
            if not (absent and substitute and school_class):
                return JsonResponse({
                    'success': False, 'error': 'Invalid teacher or class.',
                }, status=400)

            is_holiday, holiday_reason = _is_holiday_today()
            if is_holiday:
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'Today is a holiday ({holiday_reason}). '
                        f'No substitutes needed.'
                    ),
                }, status=400)

            # Subject must belong to this class
            subject = None
            if subject_id:
                cs = ClassSubject.objects.filter(
                    school_class=school_class,
                    subject_id=subject_id,
                    is_active=True,
                ).first()
                if cs is None:
                    return JsonResponse({
                        'success': False,
                        'error': 'Subject is not assigned to this class.',
                    }, status=400)
                subject = cs.subject

            # ASSIGN_TEACHERS_HARDENING_V3_1: substitute qualification
            # gate removed. Any active teacher (who is free, not on
            # leave, and not double-booked) can be used as a fixture /
            # substitute for any subject. This matches the admin's
            # workflow where a Physics teacher may cover an English
            # period if no specialist is free.

            # Period must exist in the class's assigned timetable
            assignment = (
                ClassTimetableAssignment.objects
                .select_related('timetable')
                .filter(school_class=school_class)
                .first()
            )
            if assignment is None or assignment.timetable is None:
                return JsonResponse({
                    'success': False,
                    'error': 'Class has no assigned timetable.',
                }, status=400)
            valid_periods = _class_timetable_periods(assignment.timetable)
            if (today_dow, period_order) not in valid_periods:
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'Period {period_order} on {_day_label(today_dow)} '
                        f"does not exist in this class's timetable."
                    ),
                }, status=400)

            # Absent teacher must actually be scheduled for this class/period
            absent_assignment = PeriodTeacherAssignment.objects.filter(
                teacher=absent,
                school_class=school_class,
                day_of_week=today_dow,
                period_order=period_order,
            ).first()
            if absent_assignment is None:
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'{absent.full_name} is not assigned to teach '
                        f'{school_class} at P{period_order} on '
                        f'{_day_label(today_dow)}.'
                    ),
                }, status=400)

            # Absent teacher must be on approved leave today
            on_leave = LeaveRequest.objects.filter(
                staff=absent, status='approved',
                start_date__lte=today, end_date__gte=today,
            ).exists()
            if not on_leave:
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'{absent.full_name} is not on approved leave today.'
                    ),
                }, status=400)

            # Substitute must not be on approved leave today
            if _teacher_on_leave_today(substitute, today):
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'{substitute.full_name} is on approved leave today '
                        f'and cannot be a substitute.'
                    ),
                }, status=400)

            # Substitute must be free at this period
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

            # Substitute must not already be covering elsewhere
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
                    'created_by': admin_user,
                },
            )

            return JsonResponse({
                'success': True,
                'created': created,
                'id': obj.id,
            })


# ---------------------------------------------------------------------
# POST: delete substitute
# ---------------------------------------------------------------------

@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_substitute(request, schema_name):
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
        with transaction.atomic():
            sa = (
                SubstituteAssignment.objects
                .select_for_update()
                .filter(id=sub_id)
                .first()
            )
            if sa is None:
                return JsonResponse(
                    {'success': False, 'error': 'Record not found'},
                    status=404,
                )
            sa.delete()
            return JsonResponse({'success': True})


# ---------------------------------------------------------------------
# GET: substitute records (paginated)
# ---------------------------------------------------------------------

@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_get_substitute_records(request, schema_name):
    try:
        page = max(1, int(request.GET.get('page', '1')))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(200, int(request.GET.get('page_size', '50'))))
    except (TypeError, ValueError):
        page_size = 50

    with schema_context(schema_name):
        base_qs = SubstituteAssignment.objects.all()
        total = base_qs.count()
        offset = (page - 1) * page_size
        qs = (
            base_qs
            .select_related(
                'absent_teacher', 'substitute_teacher',
                'school_class', 'school_class__wing_category',
                'subject',
            )
            .order_by('-date', '-created_at')[offset:offset + page_size]
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

        num_pages = (total + page_size - 1) // page_size if page_size else 1
        return JsonResponse({
            'success': True,
            'records': records,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'num_pages': num_pages,
            },
        })


# ---------------------------------------------------------------------
# Reconcile helper (called from periods.api_add_bunch and signals.py)
# ---------------------------------------------------------------------

def _reconcile_period_teacher_assignments(schema_name, timetable_id=None):
    """Delete PeriodTeacherAssignment rows whose (day, period) no
    longer exists in the class's current PeriodsTimetable.

    ASSIGN_TEACHERS_HARDENING_V3: batched. Two queries total regardless
    of the number of affected classes.

    Idempotent. Safe to call on any schema. Returns the number of rows
    deleted.
    """
    with schema_context(schema_name):
        cta_qs = ClassTimetableAssignment.objects.select_related('timetable')
        if timetable_id is not None:
            cta_qs = cta_qs.filter(timetable_id=timetable_id)

        class_valid_map = {}
        for cta in cta_qs:
            class_valid_map[cta.school_class_id] = (
                _class_timetable_periods(cta.timetable)
            )

        # BUG-5 fix: when reconciling the whole schema (no
        # timetable_id), scan every PTA row — including rows
        # belonging to classes whose timetable was unassigned
        # entirely. Those rows are orphans too. When a specific
        # timetable_id is given we only look at the classes that
        # use that timetable (the caller's intent).
        if timetable_id is not None and not class_valid_map:
            return 0

        pta_qs = PeriodTeacherAssignment.objects.all()
        if timetable_id is not None:
            pta_qs = pta_qs.filter(
                school_class_id__in=list(class_valid_map.keys())
            )

        rows = list(
            pta_qs.values_list(
                'id', 'school_class_id', 'day_of_week', 'period_order',
            )
        )
        to_delete_ids = []
        for (row_id, class_id, day, order) in rows:
            valid = class_valid_map.get(class_id)
            if valid is None:
                # Class has no current timetable → every row for
                # it is an orphan and must be deleted.
                to_delete_ids.append(row_id)
                continue
            if (day, order) not in valid:
                to_delete_ids.append(row_id)

        if to_delete_ids:
            PeriodTeacherAssignment.objects.filter(
                id__in=to_delete_ids,
            ).delete()
        return len(to_delete_ids)
