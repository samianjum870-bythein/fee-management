#!/usr/bin/env python3
"""
axis_patcher.py
===============
Wipes and rewrites:
  - axis_saas/views/periods.py
  - templates/tenant/timetable_periods.html

New behaviour:
  * /portal/<schema>/timetable/periods/ renders a "Periods Timetable" page.
  * "Create New Timetable" opens an overlay form:
      - Title
      - Label (from ScheduleLabel)
      - Slots grid: Day | Start | End | Periods (read-only), with a checkbox
      - Same (start,end) rule: once 2+ slots with matching timing are ticked,
        other timings freeze until user unticks.
      - Break duration (minutes)
      - Per-selected-slot "Break after which period?" dropdown.
  * POST -> /portal/<schema>/api/timetable/periods/bunch/add/
      Server computes per-period start/end times per day, returns JSON,
      stores the timetable in the user session.
  * Generated timetable is displayed on the same page with a top-right
    toggle button: "See Timings" <-> "See Periods Timetable".

Notes / safe assumptions:
  * We keep the existing function names (`periods_management`,
    `api_update_break`, `api_add_bunch`) because `public_urls.py` imports them.
    `api_add_bunch` is repurposed as the "generate periods timetable" endpoint.
  * Storage is via Django session (no new models / migrations needed).

Usage:
    python3 axis_patcher.py [--dry-run] [--verbose] [--target-dir=.]
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


# =====================================================================
# CONTENT: axis_saas/views/periods.py
# =====================================================================
VIEWS_CONTENT = r'''"""
AXIS views – periods (lectures) management module.
Full rewrite: periods timetable generator.
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
    """
    Compute per-period start/end times.
    Returns a list of dicts where each entry is either a period or a break.
    """
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

    session_key = f'periods_timetable_{schema_name}'
    generated = request.session.get(session_key)

    context = {
        'tenant': tenant,
        'labels': labels,
        'slots_by_label_json': json.dumps(slots_by_label),
        'generated_timetable_json': json.dumps(generated) if generated else 'null',
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
    return render(request, 'tenant/timetable_periods.html', context)


# ---------------------------------------------------------------------
# API: generate periods timetable (mounted at .../api/timetable/periods/bunch/add/)
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

    result = {
        'title': title,
        'label': label,
        'break_duration': break_duration,
        'days': computed_days,
    }

    # Persist for the current user session (per schema)
    session_key = f'periods_timetable_{schema_name}'
    request.session[session_key] = result
    request.session.modified = True

    return JsonResponse({'success': True, 'timetable': result})


# ---------------------------------------------------------------------
# API: keep legacy break-update endpoint functional (updates DaySchedule fields)
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


# ---------------------------------------------------------------------
# API: clear the session-stored timetable (used by the "New" button)
# ---------------------------------------------------------------------
@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_clear_periods_timetable(request, schema_name):
    session_key = f'periods_timetable_{schema_name}'
    if session_key in request.session:
        del request.session[session_key]
        request.session.modified = True
    return JsonResponse({'success': True})
'''


# =====================================================================
# CONTENT: templates/tenant/timetable_periods.html
# =====================================================================
TEMPLATE_CONTENT = r'''{% extends 'tenant/base.html' %}
{% load static %}
{% block title %}Periods Timetable | {{ tenant.name }}{% endblock %}

{% block extra_head %}
<style>
    .page-header { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem; margin-bottom:1.5rem; }
    .page-title { font-size:1.8rem; font-weight:700; background:linear-gradient(135deg,var(--primary),var(--primary-dark)); -webkit-background-clip:text; background-clip:text; color:transparent; }
    .page-desc { color:var(--muted); }

    .card { background:var(--surface); border-radius:var(--radius); border:1px solid var(--border); padding:1rem; margin-bottom:1.5rem; box-shadow:var(--shadow-sm); }
    .card-header { display:flex; align-items:center; gap:0.75rem; padding-bottom:0.75rem; border-bottom:1px solid var(--border); margin-bottom:1rem; }

    .btn-primary, .btn-secondary, .btn-success, .btn-danger {
        display:inline-flex; align-items:center; gap:0.5rem;
        padding:0.4rem 0.9rem; border-radius:2rem; font-weight:500;
        font-size:0.9rem; text-decoration:none; border:none; cursor:pointer;
        transition:0.2s;
    }
    .btn-primary { background:var(--primary); color:white; }
    .btn-primary:hover { background:var(--primary-dark); }
    .btn-secondary { background:var(--surface-alt); color:var(--text); border:1px solid var(--border); }
    .btn-secondary:hover { background:var(--border); }
    .btn-success { background:#10b981; color:white; }
    .btn-success:hover { background:#059669; }
    .btn-success:disabled { opacity:0.5; cursor:not-allowed; }
    .btn-danger { background:#dc2626; color:white; }
    .btn-lg { padding:0.6rem 1.2rem; font-size:1rem; font-weight:600; }

    .form-control { width:100%; padding:0.5rem; border-radius:0.5rem; border:1px solid var(--border); background:var(--surface-alt); color:var(--text); font-size:0.9rem; }
    .form-control:focus { outline:none; border-color:var(--primary); box-shadow:0 0 0 2px rgba(59,130,246,0.15); }
    .form-group { margin-bottom:1rem; }
    .form-group label { display:block; font-weight:500; font-size:0.85rem; color:var(--muted); margin-bottom:0.35rem; }
    .text-muted { color:var(--muted); }

    /* Overlay */
    .overlay {
        position:fixed; inset:0; background:rgba(0,0,0,0.6);
        z-index:99999; display:none; align-items:center; justify-content:center;
        backdrop-filter:blur(4px); padding:1rem;
    }
    .overlay.active { display:flex; }
    .overlay-content {
        background:var(--surface,#fff); border-radius:1rem; padding:1.5rem 2rem;
        max-width:760px; width:100%; max-height:90vh; overflow-y:auto;
        box-shadow:0 20px 60px rgba(0,0,0,0.35);
    }
    .overlay-content h2 { margin-top:0; margin-bottom:1rem; font-size:1.4rem; }

    /* Slots table inside form */
    .slot-select-table { width:100%; border-collapse:collapse; font-size:0.9rem; }
    .slot-select-table th, .slot-select-table td { border:1px solid var(--border); padding:0.5rem; text-align:center; }
    .slot-select-table th { background:var(--surface-alt); font-weight:600; }
    .slot-select-table tr.frozen { opacity:0.35; }
    .slot-select-table tr.frozen td { pointer-events:none; }
    .slot-select-table td input[type="checkbox"] { width:18px; height:18px; cursor:pointer; }

    .break-after-row { display:flex; gap:1rem; align-items:center; margin-bottom:0.5rem; padding:0.5rem; background:var(--surface-alt); border-radius:0.5rem; flex-wrap:wrap; }

    .form-actions { display:flex; justify-content:flex-end; gap:0.75rem; margin-top:1.25rem; padding-top:1rem; border-top:1px solid var(--border); }

    /* Timetable grid */
    .timetable-grid { width:100%; border-collapse:collapse; font-size:0.9rem; }
    .timetable-grid th, .timetable-grid td { border:1px solid var(--border); padding:0.5rem 0.35rem; text-align:center; vertical-align:middle; }
    .timetable-grid th { background:var(--surface-alt); font-weight:600; }
    .timetable-grid .day-label { font-weight:600; background:var(--surface-alt); white-space:nowrap; }
    .timetable-grid .empty-period { color:var(--muted); }
    .timetable-grid .break-col { background:#fef3c7; border:2px dashed #f59e0b; }
    .timetable-grid .break-cell strong { display:block; color:#92400e; }
    .timetable-grid .period-cell strong { display:block; }
    .timetable-grid .period-cell small { display:block; color:var(--muted); font-size:0.72rem; margin-top:2px; }

    /* Timings detail table */
    .timing-day-block { margin-bottom:1.5rem; }
    .timing-day-block h3 { margin:0 0 0.5rem 0; font-size:1.05rem; }
    .timing-detail-table { width:100%; border-collapse:collapse; font-size:0.9rem; }
    .timing-detail-table th, .timing-detail-table td { border:1px solid var(--border); padding:0.4rem 0.6rem; text-align:left; }
    .timing-detail-table th { background:var(--surface-alt); font-weight:600; }
    .timing-detail-table .break-row td { background:#fef3c7; color:#92400e; font-weight:600; }
</style>
{% endblock %}

{% block body %}
<div class="page-header">
    <div>
        <h1 class="page-title">Periods Timetable</h1>
        <p class="page-desc">Generate period-by-period timetables using your Academic&nbsp;Calendar slots.</p>
    </div>
    <button type="button" id="createTimetableBtn" class="btn-primary btn-lg">+ Create New Timetable</button>
</div>

<!-- EMPTY STATE -->
<div id="emptyState" class="card">
    <p class="text-muted" style="margin:0;">No timetable generated yet. Click <strong>Create New Timetable</strong> to start.</p>
</div>

<!-- GENERATED TIMETABLE -->
<div id="timetableWrapper" style="display:none;">
    <div class="card">
        <div class="card-header" style="justify-content:space-between; flex-wrap:wrap;">
            <div>
                <h2 id="displayTitle" style="margin:0; font-size:1.2rem;">&nbsp;</h2>
                <span id="displayLabel" class="text-muted" style="font-size:0.85rem;"></span>
            </div>
            <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
                <button type="button" id="toggleViewBtn" class="btn-secondary">See Timings</button>
                <button type="button" id="regenerateBtn" class="btn-primary">+ New</button>
            </div>
        </div>

        <div id="periodsViewContainer">
            <div style="overflow-x:auto;">
                <table class="timetable-grid" id="periodsGrid"></table>
            </div>
        </div>

        <div id="timingsViewContainer" style="display:none;"></div>
    </div>
</div>

<!-- CREATE FORM OVERLAY -->
<div id="createOverlay" class="overlay">
    <div class="overlay-content">
        <h2>Create Periods Timetable</h2>
        <form id="createForm" autocomplete="off">

            <div class="form-group">
                <label for="formTitle">Timetable Title *</label>
                <input type="text" id="formTitle" class="form-control" required
                       placeholder="e.g., Senior Morning Timetable">
            </div>

            <div class="form-group">
                <label for="formLabel">Label *</label>
                <select id="formLabel" class="form-control" required>
                    <option value="">-- Select Label --</option>
                    {% for lbl in labels %}
                    <option value="{{ lbl.name|escapejs }}">{{ lbl.name }}</option>
                    {% endfor %}
                </select>
                <small class="text-muted" style="display:block; margin-top:0.35rem; font-size:0.72rem;">
                    Labels are managed from the Academic Calendar page.
                </small>
            </div>

            <div id="slotsSection" style="display:none;">
                <h3 style="margin:1rem 0 0.4rem 0; font-size:1.05rem;">Select Slots</h3>
                <p class="text-muted" style="font-size:0.82rem; margin:0 0 0.6rem 0;">
                    Slots shown below are read-only (Day / Start / End / Periods).
                    <br>
                    <strong>Rule:</strong> If you tick <strong>2+ slots with the same start &amp; end time</strong>,
                    other timings freeze until you untick them.
                </p>
                <div style="max-height:300px; overflow-y:auto; border:1px solid var(--border); border-radius:0.5rem;">
                    <table class="slot-select-table">
                        <thead>
                            <tr>
                                <th style="width:40px;"></th>
                                <th>Day</th>
                                <th>Start</th>
                                <th>End</th>
                                <th>Periods</th>
                            </tr>
                        </thead>
                        <tbody id="slotsBody"></tbody>
                    </table>
                </div>

                <div class="form-group" style="margin-top:1rem;">
                    <label for="breakDuration">Break Duration (minutes)</label>
                    <input type="number" id="breakDuration" class="form-control"
                           value="15" min="0" step="1" style="max-width:180px;">
                </div>

                <div id="breakAfterSection" style="display:none;">
                    <h3 style="margin:1rem 0 0.5rem 0; font-size:1.05rem;">Break After Which Period?</h3>
                    <p class="text-muted" style="font-size:0.82rem; margin:0 0 0.6rem 0;">
                        Choose the break position for each selected day. Pick <em>No break</em> to skip.
                    </p>
                    <div id="breakAfterList"></div>
                </div>
            </div>

            <div class="form-actions">
                <button type="button" id="cancelBtn" class="btn-secondary">Cancel</button>
                <button type="submit" id="generateBtn" class="btn-success" disabled>Generate Periods Timetable</button>
            </div>
        </form>
    </div>
</div>

<script>
(function () {
    'use strict';

    const SLOTS_BY_LABEL = {{ slots_by_label_json|safe }};
    const SCHEMA = '{{ tenant.schema_name|escapejs }}';

    let GENERATED = {{ generated_timetable_json|safe }};
    let currentView = 'periods';

    // ---- DOM refs ----
    const createBtn         = document.getElementById('createTimetableBtn');
    const overlay           = document.getElementById('createOverlay');
    const cancelBtn         = document.getElementById('cancelBtn');
    const form              = document.getElementById('createForm');
    const formTitle         = document.getElementById('formTitle');
    const formLabel         = document.getElementById('formLabel');
    const slotsSection      = document.getElementById('slotsSection');
    const slotsBody         = document.getElementById('slotsBody');
    const breakDurationEl   = document.getElementById('breakDuration');
    const breakAfterSection = document.getElementById('breakAfterSection');
    const breakAfterList    = document.getElementById('breakAfterList');
    const generateBtn       = document.getElementById('generateBtn');
    const regenerateBtn     = document.getElementById('regenerateBtn');
    const toggleBtn         = document.getElementById('toggleViewBtn');
    const timetableWrapper  = document.getElementById('timetableWrapper');
    const emptyState        = document.getElementById('emptyState');
    const periodsGrid       = document.getElementById('periodsGrid');
    const periodsView       = document.getElementById('periodsViewContainer');
    const timingsView       = document.getElementById('timingsViewContainer');
    const displayTitle      = document.getElementById('displayTitle');
    const displayLabel      = document.getElementById('displayLabel');

    // ---- CSRF ----
    function getCsrfToken() {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, 'csrftoken='.length) === 'csrftoken=') {
                    cookieValue = decodeURIComponent(cookie.substring('csrftoken='.length));
                    break;
                }
            }
        }
        return cookieValue;
    }

    // ---- Show existing ----
    if (GENERATED) {
        renderTimetable(GENERATED);
        emptyState.style.display = 'none';
        timetableWrapper.style.display = 'block';
    }

    // ---- Overlay control ----
    function openCreateForm() {
        formTitle.value = '';
        formLabel.value = '';
        slotsBody.innerHTML = '';
        slotsSection.style.display = 'none';
        breakAfterSection.style.display = 'none';
        breakAfterList.innerHTML = '';
        breakDurationEl.value = '15';
        generateBtn.disabled = true;
        generateBtn.textContent = 'Generate Periods Timetable';
        overlay.classList.add('active');
    }
    function closeCreateForm() {
        overlay.classList.remove('active');
    }

    createBtn.addEventListener('click', openCreateForm);
    regenerateBtn.addEventListener('click', openCreateForm);
    cancelBtn.addEventListener('click', closeCreateForm);
    overlay.addEventListener('click', function (e) {
        if (e.target === overlay) closeCreateForm();
    });

    // ---- Label change: render slots ----
    formLabel.addEventListener('change', function () {
        const label = this.value;
        slotsBody.innerHTML = '';
        breakAfterSection.style.display = 'none';
        breakAfterList.innerHTML = '';
        generateBtn.disabled = true;

        if (!label) {
            slotsSection.style.display = 'none';
            return;
        }

        const slots = SLOTS_BY_LABEL[label] || [];
        slotsSection.style.display = 'block';

        if (slots.length === 0) {
            const tr = document.createElement('tr');
            tr.innerHTML = '<td colspan="5" style="padding:1rem; text-align:center; color:var(--muted);">No slots defined for this label. Add slots in Academic Calendar first.</td>';
            slotsBody.appendChild(tr);
            return;
        }

        slots.forEach(function (slot) {
            const tr = document.createElement('tr');
            tr.dataset.start = slot.start;
            tr.dataset.end   = slot.end;
            tr.dataset.day   = String(slot.day);
            tr.dataset.periods = String(slot.periods);
            tr.dataset.dayLabel = slot.day_label;

            tr.innerHTML =
                '<td><input type="checkbox" class="slot-checkbox"' +
                ' data-start="' + slot.start + '"' +
                ' data-end="'   + slot.end   + '"' +
                ' data-day="'   + slot.day   + '"' +
                ' data-periods="' + slot.periods + '"' +
                ' data-day-label="' + slot.day_label + '"></td>' +
                '<td>' + slot.day_label + '</td>' +
                '<td>' + slot.start + '</td>' +
                '<td>' + slot.end   + '</td>' +
                '<td>' + slot.periods + '</td>';
            slotsBody.appendChild(tr);
        });

        document.querySelectorAll('#slotsBody .slot-checkbox').forEach(function (cb) {
            cb.addEventListener('change', onSlotChange);
        });
    });

    // ---- Slot checkbox logic ----
    function onSlotChange() {
        applyTimingRule();
        updateBreakAfterSection();
        updateGenerateBtnState();
    }

    function applyTimingRule() {
        const checkboxes = Array.from(document.querySelectorAll('#slotsBody .slot-checkbox'));
        const checked = checkboxes.filter(function (cb) { return cb.checked; });

        const groups = {};
        checked.forEach(function (cb) {
            const key = cb.dataset.start + '|' + cb.dataset.end;
            groups[key] = (groups[key] || 0) + 1;
        });

        let lockedKey = null;
        Object.keys(groups).forEach(function (k) {
            if (groups[k] >= 2 && lockedKey === null) lockedKey = k;
        });

        checkboxes.forEach(function (cb) {
            const key = cb.dataset.start + '|' + cb.dataset.end;
            const row = cb.closest('tr');
            if (lockedKey !== null) {
                if (key !== lockedKey) {
                    cb.disabled = true;
                    row.classList.add('frozen');
                    if (cb.checked) cb.checked = false;
                } else {
                    cb.disabled = false;
                    row.classList.remove('frozen');
                }
            } else {
                cb.disabled = false;
                row.classList.remove('frozen');
            }
        });
    }

    function updateBreakAfterSection() {
        const checked = Array.from(document.querySelectorAll('#slotsBody .slot-checkbox')).filter(function (cb) { return cb.checked; });
        const duration = parseInt(breakDurationEl.value) || 0;

        if (checked.length === 0 || duration <= 0) {
            breakAfterSection.style.display = 'none';
            breakAfterList.innerHTML = '';
            return;
        }

        breakAfterSection.style.display = 'block';
        breakAfterList.innerHTML = '';

        checked.forEach(function (cb) {
            const dayLabel = cb.dataset.dayLabel;
            const periods  = parseInt(cb.dataset.periods, 10);
            const dayKey   = cb.dataset.day + '|' + cb.dataset.start + '|' + cb.dataset.end;

            let optionsHtml = '<option value="">No break</option>';
            for (let i = 1; i < periods; i++) {
                optionsHtml += '<option value="' + i + '">After P' + i + '</option>';
            }

            const row = document.createElement('div');
            row.className = 'break-after-row';
            row.innerHTML =
                '<strong style="min-width:100px;">' + dayLabel + '</strong>' +
                '<span class="text-muted" style="font-size:0.85rem;">' + periods + ' periods</span>' +
                '<select class="form-control break-after-select" data-day-key="' + dayKey + '" style="max-width:200px;">' +
                    optionsHtml +
                '</select>';
            breakAfterList.appendChild(row);

            const sel = row.querySelector('.break-after-select');
            const mid = Math.floor(periods / 2);
            if (mid >= 1 && mid < periods) sel.value = String(mid);
        });
    }

    breakDurationEl.addEventListener('input', function () {
        updateBreakAfterSection();
        updateGenerateBtnState();
    });

    function updateGenerateBtnState() {
        const title = formTitle.value.trim();
        const label = formLabel.value;
        const checked = Array.from(document.querySelectorAll('#slotsBody .slot-checkbox')).filter(function (cb) { return cb.checked; });
        generateBtn.disabled = !(title && label && checked.length > 0);
    }
    formTitle.addEventListener('input', updateGenerateBtnState);

    // ---- Submit ----
    form.addEventListener('submit', async function (e) {
        e.preventDefault();

        const title = formTitle.value.trim();
        const label = formLabel.value;
        const duration = parseInt(breakDurationEl.value) || 0;
        const checked = Array.from(document.querySelectorAll('#slotsBody .slot-checkbox')).filter(function (cb) { return cb.checked; });

        const days = checked.map(function (cb) {
            const dayKey = cb.dataset.day + '|' + cb.dataset.start + '|' + cb.dataset.end;
            const sel = document.querySelector('.break-after-select[data-day-key="' + dayKey + '"]');
            const breakAfterVal = sel ? sel.value : '';
            return {
                day: parseInt(cb.dataset.day, 10),
                start: cb.dataset.start,
                end: cb.dataset.end,
                periods: parseInt(cb.dataset.periods, 10),
                break_after: breakAfterVal ? parseInt(breakAfterVal, 10) : null
            };
        });

        generateBtn.disabled = true;
        generateBtn.textContent = 'Generating...';

        try {
            const res = await fetch('/portal/' + SCHEMA + '/api/timetable/periods/bunch/add/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
                },
                body: JSON.stringify({
                    title: title,
                    label: label,
                    break_duration: duration,
                    days: days
                })
            });
            const data = await res.json();
            if (!res.ok || !data.success) {
                alert('Error: ' + (data.error || 'Unknown error'));
                generateBtn.disabled = false;
                generateBtn.textContent = 'Generate Periods Timetable';
                return;
            }
            GENERATED = data.timetable;
            renderTimetable(GENERATED);
            emptyState.style.display = 'none';
            timetableWrapper.style.display = 'block';
            closeCreateForm();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        } catch (err) {
            alert('Network error: ' + err.message);
            generateBtn.disabled = false;
            generateBtn.textContent = 'Generate Periods Timetable';
        }
    });

    // ---- Rendering ----
    function renderTimetable(tt) {
        displayTitle.textContent = tt.title;
        displayLabel.textContent = 'Label: ' + tt.label + ' | Break: ' + tt.break_duration + ' min';
        renderPeriodsView(tt);
        renderTimingsView(tt);
        setView('periods');
    }

    function setView(v) {
        currentView = v;
        if (v === 'periods') {
            periodsView.style.display = 'block';
            timingsView.style.display = 'none';
            toggleBtn.textContent = 'See Timings';
        } else {
            periodsView.style.display = 'none';
            timingsView.style.display = 'block';
            toggleBtn.textContent = 'See Periods Timetable';
        }
    }

    toggleBtn.addEventListener('click', function () {
        setView(currentView === 'periods' ? 'timings' : 'periods');
    });

    function renderPeriodsView(tt) {
        let maxPeriods = 0;
        tt.days.forEach(function (d) { if (d.periods_count > maxPeriods) maxPeriods = d.periods_count; });

        let maxBreakAfter = 0;
        tt.days.forEach(function (d) { if (d.break_after && d.break_after > maxBreakAfter) maxBreakAfter = d.break_after; });

        const hasBreak = maxBreakAfter > 0 && tt.break_duration > 0;

        // Header
        let thead = '<thead><tr><th>Day</th>';
        for (let i = 1; i <= maxPeriods; i++) {
            thead += '<th>P' + i + '</th>';
            if (hasBreak && i === maxBreakAfter) {
                thead += '<th class="break-col">Break</th>';
            }
        }
        thead += '</tr></thead>';

        // Body
        let tbody = '<tbody>';
        tt.days.forEach(function (d) {
            tbody += '<tr>';
            tbody += '<td class="day-label">' + d.day_label + '</td>';

            const byOrder = {};
            let breakPeriod = null;
            d.periods.forEach(function (p) {
                if (p.is_break) breakPeriod = p;
                else byOrder[p.order] = p;
            });

            for (let i = 1; i <= maxPeriods; i++) {
                const p = byOrder[i];
                if (p) {
                    tbody += '<td><div class="period-cell"><strong>P' + i + '</strong><small>' + p.start + '-' + p.end + '</small></div></td>';
                } else {
                    tbody += '<td class="empty-period">—</td>';
                }
                if (hasBreak && i === maxBreakAfter) {
                    if (breakPeriod && d.break_after === i) {
                        tbody += '<td class="break-col"><div class="break-cell"><strong>Break</strong><small>' + breakPeriod.start + '-' + breakPeriod.end + '</small></div></td>';
                    } else {
                        tbody += '<td class="break-col" style="opacity:0.35;">—</td>';
                    }
                }
            }
            tbody += '</tr>';
        });
        tbody += '</tbody>';

        periodsGrid.innerHTML = thead + tbody;
    }

    function renderTimingsView(tt) {
        let html = '';
        tt.days.forEach(function (d) {
            html += '<div class="timing-day-block">';
            html += '<h3>' + d.day_label +
                    ' <span class="text-muted" style="font-size:0.85rem;">(' +
                    d.start + ' - ' + d.end + ' · ' + d.periods_count + ' periods)</span></h3>';
            html += '<table class="timing-detail-table"><thead><tr>' +
                    '<th style="width:120px;">Period</th>' +
                    '<th>Start</th><th>End</th><th>Duration</th></tr></thead><tbody>';
            d.periods.forEach(function (p) {
                if (p.is_break) {
                    html += '<tr class="break-row"><td>Break</td><td>' + p.start + '</td><td>' + p.end + '</td><td>' + p.duration + ' min</td></tr>';
                } else {
                    html += '<tr><td>P' + p.order + '</td><td>' + p.start + '</td><td>' + p.end + '</td><td>' + p.duration + ' min</td></tr>';
                }
            });
            html += '</tbody></table></div>';
        });
        timingsView.innerHTML = html;
    }
})();
</script>
{% endblock %}
'''


# =====================================================================
# Patcher
# =====================================================================
def _log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


def _write_file(path: Path, content: str, dry_run: bool, verbose: bool) -> bool:
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


def main() -> int:
    parser = argparse.ArgumentParser(description='Rewrite periods.py and timetable_periods.html')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, do not write files.')
    parser.add_argument('--verbose', action='store_true', help='Verbose output.')
    parser.add_argument('--target-dir', default='.', help='Project root (default: current directory).')
    args = parser.parse_args()

    target = Path(args.target_dir).resolve()
    _log(f"Target dir: {target}")
    _log(f"Dry-run:    {args.dry_run}")
    _log(f"Verbose:    {args.verbose}")
    print('-' * 60)

    # Sanity
    if not (target / 'manage.py').exists():
        _log("WARNING: manage.py not found at target root. Continuing anyway.")

    views_path = target / 'axis_saas' / 'views' / 'periods.py'
    template_path = target / 'templates' / 'tenant' / 'timetable_periods.html'

    ok1 = _write_file(views_path, VIEWS_CONTENT, args.dry_run, args.verbose)
    ok2 = _write_file(template_path, TEMPLATE_CONTENT, args.dry_run, args.verbose)

    print('-' * 60)
    if ok1 and ok2:
        _log("DONE. Both files rewritten.")
        if not args.dry_run:
            _log("Restart the dev server (Ctrl+C then `python3 manage.py runserver`).")
            _log("Then hard-refresh the page: Ctrl+Shift+R")
        return 0
    _log("FAILED: one or more files could not be written.")
    return 1


if __name__ == '__main__':
    sys.exit(main())	

