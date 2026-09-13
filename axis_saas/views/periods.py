"""
AXIS views – periods (lectures) management module.
Multi-timetable generator with edit support.

Storage migrated from per-session to DB (PeriodsTimetable model) so that
timetables can be assigned to classes and shared across admin sessions.
"""
import json
import logging
from datetime import datetime

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    DaySchedule, AcademicCalendar, ScheduleLabel,
    PeriodsTimetable, ClassTimetableAssignment,
)
from .helpers import get_tenant, require_tenant_type, require_school_feature

logger = logging.getLogger(__name__)


SESSION_KEY_TEMPLATE = 'periods_timetables_{schema}'


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _time_to_minutes(t):
    return t.hour * 60 + t.minute


def _minutes_to_hhmm(minutes):
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def _compute_periods(start_t, end_t, periods_count, break_after, break_duration):
    """Return list of dicts: each is either a period or a break."""
    total_minutes = _time_to_minutes(end_t) - _time_to_minutes(start_t)
    if periods_count <= 0 or total_minutes <= 0:
        return []

    usable = total_minutes
    if break_after and break_duration:
        if 0 < break_after < periods_count:
            usable -= break_duration
        else:
            break_after = None
            break_duration = 0

    if usable <= 0:
        return []

    base = usable // periods_count
    remainder = usable - base * periods_count

    result = []
    cursor = _time_to_minutes(start_t)
    for i in range(1, periods_count + 1):
        duration = base + (1 if i <= remainder else 0)
        p_start = cursor
        p_end = cursor + duration
        result.append({
            'order': i,
            'start': _minutes_to_hhmm(p_start),
            'end': _minutes_to_hhmm(p_end),
            'duration': duration,
            'is_break': False,
        })
        cursor = p_end
        if break_after == i and break_duration:
            result.append({
                'order': None,
                'start': _minutes_to_hhmm(cursor),
                'end': _minutes_to_hhmm(cursor + break_duration),
                'duration': break_duration,
                'is_break': True,
            })
            cursor += break_duration
    return result


def _get_session_key(schema_name):
    return SESSION_KEY_TEMPLATE.format(schema=schema_name)


def _timetable_to_dict(tt, assigned_count=None):
    """Convert a PeriodsTimetable row into the JSON shape the UI expects."""
    # TIMETABLE_FK_REFACTOR_V1: `label` is now a FK, so serialize the
    # label NAME for the client. `assigned_count` may be supplied by a
    # caller that pre-annotated the queryset (see _load_timetables).
    if assigned_count is None:
        try:
            assigned_count = ClassTimetableAssignment.objects.filter(timetable=tt).count()
        except Exception:
            assigned_count = 0
    return {
        'id': tt.id,
        'title': tt.title,
        'label': tt.label.name if tt.label_id else '',
        'break_duration': tt.break_duration or 0,
        'days': tt.days or [],
        'assigned_class_count': assigned_count,
    }


def _load_timetables():
    """Load all persisted periods timetables.

    TIMETABLE_PERIODS_V2: grouped by label, then title, then id so a
    long list of timetables reads naturally instead of in insertion
    order.

    TIMETABLE_FK_REFACTOR_V1: the per-row assigned-class count is now
    computed with a single aggregate join instead of a COUNT() per
    timetable. On a tenant with 50 timetables this drops the page from
    51 queries to 2.
    """
    from django.db.models import Count
    qs = (
        PeriodsTimetable.objects
        .select_related('label')
        .annotate(_assigned_count=Count('class_assignments'))
        .order_by('label__name', 'title', 'id')
    )
    return [
        _timetable_to_dict(tt, assigned_count=tt._assigned_count)
        for tt in qs
    ]


def _migrate_session_to_db(request, schema_name):
    """One-time migration: move any session-stored timetables into the DB."""
    key = _get_session_key(schema_name)
    value = request.session.get(key)
    if not isinstance(value, list) or not value:
        return
    migrated = 0
    for entry in value:
        if not isinstance(entry, dict):
            continue
        title = (entry.get('title') or '').strip()
        if not title:
            continue
        if PeriodsTimetable.objects.filter(title=title).exists():
            continue
        try:
            bd = int(entry.get('break_duration') or 0)
        except (TypeError, ValueError):
            bd = 0
        # TIMETABLE_FK_REFACTOR_V1: resolve the legacy string label to a
        # ScheduleLabel row (creating one if needed). Entries without a
        # resolvable label are skipped — they cannot be assigned to a
        # class anyway.
        _lbl_text = ((entry.get('label') or '').strip())[:50]
        if not _lbl_text:
            continue
        _lbl = ScheduleLabel.objects.filter(name__iexact=_lbl_text).first()
        if _lbl is None:
            _lbl = ScheduleLabel.objects.create(name=_lbl_text, description='')
        PeriodsTimetable.objects.create(
            title=title[:150],
            label=_lbl,
            break_duration=max(0, bd),
            days=entry.get('days') or [],
        )
        migrated += 1
    request.session.pop(key, None)
    request.session.modified = True
    if migrated:
        logger.info('Migrated %s session timetable(s) to DB for schema %s', migrated, schema_name)


def _reconcile_timetables(schema_name):
    """
    Reconcile persisted timetables against current DaySchedule rows.
    Drops days whose slot is gone or whose timing changed; recomputes periods
    when only the periods count changed. Timetables with zero remaining days
    are deleted.
    """
    with schema_context(schema_name):
        schedules = list(DaySchedule.objects.all())

    # TIMETABLE_FK_REFACTOR_V1: key on label_id (FK) instead of case-folded
    # text. Two labels with different casing cannot collide any more.
    schedule_map = {}
    for ds in schedules:
        if not ds.label_id:
            continue
        key = (ds.label_id, ds.day_of_week)
        schedule_map[key] = {
            'start': ds.start_time.strftime('%H:%M'),
            'end': ds.end_time.strftime('%H:%M'),
            'periods': ds.periods,
        }

    updated_objs = []
    delete_ids = []

    for tt in PeriodsTimetable.objects.all():
        label = tt.label.name if tt.label_id else ''
        try:
            break_duration = int(tt.break_duration or 0)
        except (TypeError, ValueError):
            break_duration = 0

        new_days = []
        days_changed = False

        for day in (tt.days or []):
            day_of_week = day.get('day_of_week')
            key = (tt.label_id, day_of_week)
            sched = schedule_map.get(key)

            if not sched:
                days_changed = True
                continue

            try:
                old_count = int(day.get('periods_count') or 0)
            except (TypeError, ValueError):
                old_count = 0

            # EDIT_TIMING_v1: recompute when start/end OR periods_count
            # changed. Never drop the day just because its timing
            # changed -- update it in place instead.
            if (
                sched['start'] != day.get('start')
                or sched['end'] != day.get('end')
                or sched['periods'] != old_count
            ):
                try:
                    start_t = datetime.strptime(sched['start'], '%H:%M').time()
                    end_t = datetime.strptime(sched['end'], '%H:%M').time()
                except Exception:
                    days_changed = True
                    continue

                break_after = day.get('break_after')
                try:
                    break_after = int(break_after) if break_after not in (None, '', 'null') else None
                except (TypeError, ValueError):
                    break_after = None
                if break_after is not None and (break_after < 1 or break_after >= sched['periods']):
                    break_after = None

                periods_data = _compute_periods(
                    start_t, end_t, sched['periods'],
                    break_after, break_duration,
                )
                # TIMETABLE_HARDENING_V1: if the stored break_after /
                # break_duration swallowed the entire class window,
                # _compute_periods returns [] and the client would
                # render an empty timetable. Reset the break and
                # recompute — this matches the guard in api_add_bunch
                # and closes the same hole for legacy / out-of-band
                # DaySchedule edits.
                if not periods_data and break_after is not None:
                    break_after = None
                    periods_data = _compute_periods(
                        start_t, end_t, sched['periods'],
                        None, 0,
                    )
                day['periods_count'] = sched['periods']
                day['start'] = sched['start']
                day['end'] = sched['end']
                day['periods'] = periods_data
                day['break_after'] = break_after
                days_changed = True

            new_days.append(day)

        if new_days:
            if days_changed:
                tt.days = new_days
                updated_objs.append(tt)
        else:
            delete_ids.append(tt.id)

    if updated_objs:
        PeriodsTimetable.objects.bulk_update(updated_objs, ['days'])
    if delete_ids:
        # TIMETABLE_PERIODS_V2: capture what we're about to delete BEFORE
        # the delete happens, so we can surface a Notification to the
        # admin. Previously these vanished silently along with every
        # ClassTimetableAssignment row that pointed at them.
        _deleted_info = []
        try:
            for _tt in PeriodsTimetable.objects.filter(id__in=delete_ids):
                try:
                    _assigned = ClassTimetableAssignment.objects.filter(
                        timetable=_tt
                    ).count()
                except Exception:
                    _assigned = 0
                _deleted_info.append({
                    'title': _tt.title or '(untitled)',
                    'label': _tt.label or '',
                    'assigned': _assigned,
                })
        except Exception:
            _deleted_info = []

        ClassTimetableAssignment.objects.filter(timetable_id__in=delete_ids).delete()
        PeriodsTimetable.objects.filter(id__in=delete_ids).delete()

        try:
            from ..models import Notification as _Notification
            for _info in _deleted_info:
                _msg = (
                    f"Timetable '{_info['title']}' was auto-deleted because "
                    f"its calendar slots no longer exist in the Academic "
                    f"Calendar."
                )
                if _info['assigned']:
                    _msg += (
                        f" {_info['assigned']} class(es) were unassigned "
                        f"from it."
                    )
                # Keep within the 255-char CharField.
                _Notification.objects.create(
                    message=_msg[:255],
                    link=f'/portal/{schema_name}/timetable/periods/',
                )
        except Exception as _exc:
            logger.warning(
                'TIMETABLE_PERIODS_V2: could not write delete '
                'notification for schema %s: %s', schema_name, _exc,
            )


# ---------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def periods_management(request, schema_name):
    tenant = get_tenant(request, schema_name)

    with schema_context(schema_name):
        # One-time migration from session (old) to DB (new)
        _migrate_session_to_db(request, schema_name)

        labels = list(ScheduleLabel.objects.all().order_by('name'))

        slots_by_label = {}
        for lbl in labels:
            # TIMETABLE_FK_REFACTOR_V1: filter on the FK, not a name string.
            schedules = DaySchedule.objects.filter(
                label=lbl,
            ).order_by('day_of_week', 'order')
            slots_by_label[lbl.name] = [
                {
                    'id': ds.id,
                    'day': ds.day_of_week,
                    'day_label': ds.get_day_of_week_display(),
                    'start': ds.start_time.strftime('%H:%M'),
                    'end': ds.end_time.strftime('%H:%M'),
                    'periods': ds.periods,
                    'duration': ds.duration,
                }
                for ds in schedules
            ]

        # TIMETABLE_OPTIMISTIC_LOCK_V1: reconciliation used to run on every
        # page load here. It is now triggered by DaySchedule post_save /
        # post_delete signals (see axis_saas/signals.py), so a page view
        # no longer pays the O(#timetables × #days) cost on every GET.
        timetables = _load_timetables()

    context = {
        'tenant': tenant,
        'labels': labels,
        'slots_by_label_json': json.dumps(slots_by_label),
        'timetables_json': json.dumps(timetables),
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
    return render(request, 'tenant/timetable_periods.html', context)


# ---------------------------------------------------------------------
# API: generate / edit periods timetable
# ---------------------------------------------------------------------
@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_add_bunch(request, schema_name):
    """
    POST JSON:
    {
        "title": "Senior Timetable",
        "label": "Senior",
        "break_duration": 15,
        "edit_id": 42,              # optional; if present, update that row
        "days": [
            {"day": 0, "start": "08:00", "end": "14:00",
             "periods": 8, "break_after": 4},
            ...
        ]
    }
    """
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    title = (data.get('title') or '').strip()
    label_text = (data.get('label') or '').strip()
    try:
        break_duration = int(data.get('break_duration') or 0)
    except (TypeError, ValueError):
        break_duration = 0
    days = data.get('days') or []

    # TIMETABLE_FK_REFACTOR_V1: resolve the label text to a ScheduleLabel
    # once, up front. Reject the whole save if it doesn't exist.
    _schedule_label = None
    if label_text:
        _schedule_label = ScheduleLabel.objects.filter(name__iexact=label_text).first()

    edit_id = data.get('edit_id')
    if edit_id is not None:
        try:
            edit_id = int(edit_id)
        except (TypeError, ValueError):
            edit_id = None

    if not title:
        return JsonResponse({'error': 'Title is required'}, status=400)
    if not label_text:
        return JsonResponse({'error': 'Label is required'}, status=400)
    if _schedule_label is None:
        return JsonResponse(
            {'error': f"Label '{label_text}' not found."}, status=400,
        )
    if not days:
        return JsonResponse({'error': 'Select at least one slot'}, status=400)

    day_names = dict(DaySchedule.DAY_CHOICES)
    computed_days = []

    for d in days:
        try:
            day_of_week = int(d.get('day'))
            start_str = d.get('start')
            end_str = d.get('end')
            periods = int(d.get('periods'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Invalid day payload'}, status=400)

        break_after_raw = d.get('break_after')
        try:
            break_after = int(break_after_raw) if break_after_raw not in (None, '', 'null') else None
        except (TypeError, ValueError):
            break_after = None

        try:
            start_t = datetime.strptime(start_str, '%H:%M').time()
            end_t = datetime.strptime(end_str, '%H:%M').time()
        except Exception:
            return JsonResponse(
                {'error': f'Invalid time for day {day_of_week}: {start_str}-{end_str}'},
                status=400,
            )

        if break_after is not None and (break_after < 1 or break_after >= periods):
            break_after = None

        periods_data = _compute_periods(
            start_t, end_t, periods,
            break_after, break_duration,
        )

        # TIMETABLE_PERIODS_V3: reject a day whose break swallowed
        # every minute of the class window. Without this guard the
        # day was persisted with an empty periods list, which then
        # rendered as a row of em-dashes and looked like a valid
        # (but empty) timetable. start < end and periods >= 1 have
        # already been validated above, so the ONLY way periods_data
        # is empty here is the break-is-too-large case.
        if not periods_data:
            _day_total = (
                (end_t.hour * 60 + end_t.minute)
                - (start_t.hour * 60 + start_t.minute)
            )
            return JsonResponse({
                'error': (
                    f"Break duration ({break_duration} min) is too "
                    f"large to fit inside day {day_of_week}'s "
                    f"class time ({_day_total} min). Reduce the "
                    f"break or lengthen the class window."
                )
            }, status=400)

        computed_days.append({
            'day_of_week': day_of_week,
            'day_label': day_names.get(day_of_week, str(day_of_week)),
            'start': start_str,
            'end': end_str,
            'periods_count': periods,
            'break_after': break_after,
            'break_duration': break_duration if break_after else 0,
            'periods': periods_data,
        })

    computed_days.sort(key=lambda x: x['day_of_week'])

    with schema_context(schema_name):
        # TIMETABLE_PERIODS_SAVE_V1: verify every requested slot still
        # exists in the tenant's DaySchedule under the given label, with
        # exactly the start / end / periods the client sent. Without
        # this, a stale tab or a direct API call can persist a
        # timetable whose days aren't in the calendar; the next
        # reconcile would then silently drop them. We reject the whole
        # save so the client can refresh and re-apply.
        for _cd in computed_days:
            _day = _cd['day_of_week']
            _sched = (
                DaySchedule.objects
                .filter(label=_schedule_label, day_of_week=_day)
                .first()
            )
            if _sched is None:
                return JsonResponse({
                    'error': (
                        f'Slot for day {_day} not found under label '
                        f'"{label_text}". Reload the page and try again.'
                    )
                }, status=400)
            if (
                _sched.start_time.strftime('%H:%M') != _cd['start']
                or _sched.end_time.strftime('%H:%M') != _cd['end']
                or _sched.periods != _cd['periods_count']
            ):
                return JsonResponse({
                    'error': (
                        f'Slot mismatch for day {_day} under label '
                        f'"{label_text}". The calendar may have changed. '
                        f'Reload the page and try again.'
                    )
                }, status=400)

        if edit_id is not None:
            try:
                tt = PeriodsTimetable.objects.get(id=edit_id)
            except PeriodsTimetable.DoesNotExist:
                return JsonResponse({'error': 'Timetable not found'}, status=404)
            tt.title = title[:150]
            tt.label = _schedule_label
            tt.break_duration = max(0, break_duration)
            tt.days = computed_days
            tt.save()
        else:
            tt = PeriodsTimetable.objects.create(
                title=title[:150],
                label=_schedule_label,
                break_duration=max(0, break_duration),
                days=computed_days,
            )
        timetables = _load_timetables()

    return JsonResponse({
        'success': True,
        'timetables': timetables,
        'timetable': _timetable_to_dict(tt),
    })


# ---------------------------------------------------------------------
# API: delete a timetable by id
# ---------------------------------------------------------------------
@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_bunch(request, schema_name):
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    tt_id = data.get('id')
    try:
        tt_id = int(tt_id)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'id required'}, status=400)

    # TIMETABLE_PERIODS_V2: when classes are assigned, the caller must
    # explicitly pass force=true. This stops a direct API call from
    # silently unassigning every class that uses the timetable. The
    # client always sends force=true after its own confirm dialog.
    _force = bool(data.get('force', False))

    with schema_context(schema_name):
        _tt = PeriodsTimetable.objects.filter(id=tt_id).first()
        if _tt is None:
            return JsonResponse({'error': 'Timetable not found'}, status=404)

        _assigned = ClassTimetableAssignment.objects.filter(
            timetable=_tt
        ).count()
        if _assigned > 0 and not _force:
            return JsonResponse({
                'error': (
                    f'{_assigned} class(es) use this timetable. '
                    f'Confirm deletion with force=true.'
                ),
                'assigned_class_count': _assigned,
                'requires_force': True,
            }, status=409)

        _tt.delete()
        timetables = _load_timetables()
    return JsonResponse({'success': True, 'timetables': timetables})


# ---------------------------------------------------------------------
# API: keep legacy break-update endpoint functional (unchanged)
# ---------------------------------------------------------------------
@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_update_break(request, schema_name):
    """Update break_after / break_duration on an existing DaySchedule row."""
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    schedule_id = data.get('id')
    break_after = data.get('break_after')
    break_duration = data.get('break_duration')

    if schedule_id is None:
        return JsonResponse({'error': 'id required'}, status=400)

    with schema_context(schema_name):
        try:
            ds = DaySchedule.objects.get(id=schedule_id)
        except DaySchedule.DoesNotExist:
            return JsonResponse({'error': 'Schedule not found'}, status=404)

        if break_after is not None:
            try:
                ds.break_after = int(break_after)
            except (TypeError, ValueError):
                ds.break_after = None
        if break_duration is not None:
            try:
                ds.break_duration = int(break_duration)
            except (TypeError, ValueError):
                ds.break_duration = 0
        ds.save(update_fields=['break_after', 'break_duration'])
        return JsonResponse({'success': True})
