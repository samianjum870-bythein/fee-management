"""
AXIS views – periods (lectures) management module.
Multi-timetable generator with edit support.
"""

import json
import logging
from datetime import datetime

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import DaySchedule, AcademicCalendar, ScheduleLabel
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


def _load_timetables(request, schema_name):
    """Load the list of timetables from session (safe)."""
    key = _get_session_key(schema_name)
    value = request.session.get(key)
    if not isinstance(value, list):
        # Migrate / reset legacy single-object if present, or empty.
        return []
    # Sanitize: keep only dicts with required keys
    clean = []
    for item in value:
        if isinstance(item, dict) and 'days' in item and 'label' in item:
            clean.append(item)
    return clean


def _save_timetables(request, schema_name, timetables):
    key = _get_session_key(schema_name)
    request.session[key] = timetables
    request.session.modified = True


def _reconcile_timetables(request, schema_name):
    """
    Reconcile session-stored timetables against current DaySchedule rows.

    Rules (per timetable, per day entry):
      * No matching DaySchedule (same label + day_of_week) -> remove the day.
      * start_time / end_time changed -> remove the day from the timetable.
      * Only periods count changed (start/end same) -> recompute per-period
        timings; break_after / break_duration preserved.
      * If a timetable ends up with zero days, remove it entirely.
    """
    timetables = _load_timetables(request, schema_name)
    if not timetables:
        return timetables

    with schema_context(schema_name):
        schedules = list(DaySchedule.objects.all())

    schedule_map = {}
    for ds in schedules:
        key = ((ds.label or '').strip().lower(), ds.day_of_week)
        schedule_map[key] = {
            'start': ds.start_time.strftime('%H:%M'),
            'end': ds.end_time.strftime('%H:%M'),
            'periods': ds.periods,
        }

    changed = False
    new_timetables = []
    for tt in timetables:
        label = (tt.get('label') or '').strip()
        try:
            break_duration = int(tt.get('break_duration') or 0)
        except (TypeError, ValueError):
            break_duration = 0

        new_days = []
        for day in (tt.get('days') or []):
            day_of_week = day.get('day_of_week')
            key = (label.lower(), day_of_week)
            sched = schedule_map.get(key)

            # 1) Slot no longer exists -> drop the day.
            if not sched:
                changed = True
                continue

            # 2) Timing changed -> drop the day.
            if sched['start'] != day.get('start') or sched['end'] != day.get('end'):
                changed = True
                continue

            # 3) Same timing; check periods count.
            try:
                old_count = int(day.get('periods_count') or 0)
            except (TypeError, ValueError):
                old_count = 0

            if sched['periods'] != old_count:
                try:
                    start_t = datetime.strptime(sched['start'], '%H:%M').time()
                    end_t = datetime.strptime(sched['end'], '%H:%M').time()
                except Exception:
                    changed = True
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
                day['periods_count'] = sched['periods']
                day['periods'] = periods_data
                day['break_after'] = break_after
                changed = True

            new_days.append(day)

        tt['days'] = new_days
        if new_days:
            new_timetables.append(tt)
        else:
            changed = True

    if changed:
        _save_timetables(request, schema_name, new_timetables)
    return new_timetables


# ---------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def periods_management(request, schema_name):
    tenant = get_tenant(request, schema_name)

    with schema_context(schema_name):
        labels = list(ScheduleLabel.objects.all().order_by('name'))

        slots_by_label = {}
        for lbl in labels:
            schedules = DaySchedule.objects.filter(
                label=lbl.name,
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

    timetables = _load_timetables(request, schema_name)

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
# Mounted at .../api/timetable/periods/bunch/add/
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
        "edit_index": 2,            # optional; if present, replace that item
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
    label = (data.get('label') or '').strip()
    try:
        break_duration = int(data.get('break_duration') or 0)
    except (TypeError, ValueError):
        break_duration = 0
    days = data.get('days') or []

    edit_index = data.get('edit_index')
    if edit_index is not None:
        try:
            edit_index = int(edit_index)
        except (TypeError, ValueError):
            edit_index = None

    if not title:
        return JsonResponse({'error': 'Title is required'}, status=400)
    if not label:
        return JsonResponse({'error': 'Label is required'}, status=400)
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

    entry = {
        'title': title,
        'label': label,
        'break_duration': break_duration,
        'days': computed_days,
    }

    timetables = _load_timetables(request, schema_name)

    if edit_index is not None and 0 <= edit_index < len(timetables):
        timetables[edit_index] = entry
    else:
        timetables.append(entry)

    _save_timetables(request, schema_name, timetables)

    return JsonResponse({
        'success': True,
        'timetables': timetables,
        'timetable': entry,
    })


# ---------------------------------------------------------------------
# API: delete a timetable by index (nice to have)
# Mounted at .../api/timetable/periods/bunch/delete/ (patched in public_urls)
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
    idx = data.get('index')
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'index required'}, status=400)

    timetables = _load_timetables(request, schema_name)
    if 0 <= idx < len(timetables):
        timetables.pop(idx)
        _save_timetables(request, schema_name, timetables)
    return JsonResponse({'success': True, 'timetables': timetables})


# ---------------------------------------------------------------------
# API: keep legacy break-update endpoint functional
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
