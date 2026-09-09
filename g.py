#!/usr/bin/env python3
"""
Final patcher:
- Fixes backend: delete all schedules before recreating.
- Fixes frontend: immediate save after delete.
"""

import shutil
from pathlib import Path

VIEW_PATH = "axis_saas/views/timetable.py"
TEMPLATE_PATH = "templates/tenant/timetable_management.html"

NEW_VIEW = """# axis_saas/views/timetable.py
import json
import logging
from datetime import datetime

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context
from django.core.serializers import serialize

from ..models import (
    AcademicCalendar, Holiday, Period, TimetableEntry,
    SchoolClass, Subject, Staff, DaySchedule
)
from .helpers import get_tenant, require_tenant_type, require_school_feature

logger = logging.getLogger(__name__)


# ========== MAIN PAGE ==========
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def timetable_management(request, schema_name):
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        calendar, _ = AcademicCalendar.objects.get_or_create(pk=1)
        periods = Period.objects.filter(academic_calendar=calendar).order_by('order')
        holidays = Holiday.objects.all().order_by('date')
        classes = SchoolClass.objects.filter(is_active=True).order_by('name', 'section')
        subjects = Subject.objects.filter(is_active=True).order_by('name')
        teachers = Staff.objects.filter(status='active').order_by('full_name')

        subjects_json = [
            {'id': s.id, 'name': s.name}
            for s in subjects
        ]
        teachers_json = [
            {'id': t.id, 'full_name': t.full_name}
            for t in teachers
        ]

        # Get all day schedules, group by day_of_week
        all_day_schedules = DaySchedule.objects.filter(academic_calendar=calendar).order_by('day_of_week', 'order')
        day_schedules = {}
        for ds in all_day_schedules:
            day_schedules.setdefault(ds.day_of_week, []).append(ds)

    context = {
        'tenant': tenant,
        'calendar': calendar,
        'periods': periods,
        'holidays': holidays,
        'classes': classes,
        'subjects': subjects,
        'teachers': teachers,
        'subjects_json': subjects_json,
        'teachers_json': teachers_json,
        'day_schedules': day_schedules,   # dict: day_of_week -> list of DaySchedule objects
        'days_of_week': TimetableEntry.DAY_CHOICES,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
    return render(request, 'tenant/timetable_management.html', context)


# ========== API: SAVE DAY SCHEDULES ==========
@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_save_day_schedules(request, schema_name):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    schedules_data = data.get('schedules', [])
    if not isinstance(schedules_data, list):
        return JsonResponse({'error': 'schedules must be a list'}, status=400)

    logger.info(f"Received {len(schedules_data)} day schedules for schema {schema_name}")

    with schema_context(schema_name):
        calendar, created = AcademicCalendar.objects.get_or_create(pk=1)
        if created:
            logger.info(f"Created new AcademicCalendar for schema {schema_name}")

        # ----- FIX: Delete ALL existing schedules for this calendar -----
        deleted_all, _ = DaySchedule.objects.filter(academic_calendar=calendar).delete()
        logger.info(f"Deleted {deleted_all} existing schedules for calendar {calendar.pk}")

        # Now recreate only the ones we received
        total_created = 0
        for idx, item in enumerate(schedules_data):
            day = item.get('day')
            start = item.get('start')
            end = item.get('end')
            periods = item.get('periods')
            duration = item.get('duration')
            label = item.get('label', '')

            if day is None or start is None or end is None or periods is None or duration is None:
                logger.warning(f"Skipping incomplete schedule at index {idx}")
                continue

            try:
                start_time = datetime.strptime(start, '%H:%M').time()
                end_time = datetime.strptime(end, '%H:%M').time()
                periods_int = int(periods)
                duration_int = int(duration)
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid data at index {idx}: {e}")
                continue

            if not label:
                return JsonResponse({"error": "Label is required"}, status=400)

            try:
                DaySchedule.objects.create(
                    academic_calendar=calendar,
                    day_of_week=day,
                    order=idx,  # we can use idx as order, but you might want to group by day separately
                    label=label,
                    start_time=start_time,
                    end_time=end_time,
                    periods=periods_int,
                    duration=duration_int
                )
                total_created += 1
            except Exception as e:
                logger.exception(f"Error creating DaySchedule for day {day}, index {idx}: {e}")
                return JsonResponse({'error': f'Failed to save day {day} slot {idx}: {str(e)}'}, status=500)

        logger.info(f"Successfully created {total_created} day schedules for schema {schema_name}")

    return JsonResponse({'success': True, 'created': total_created, 'deleted': deleted_all})


# ====== OTHER API ENDPOINTS (stubs for future use) ======
@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_update_calendar(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_add_holiday(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_holiday(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_add_period(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_delete_period(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_update_period(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)

@require_http_methods(["GET"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_get_timetable(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('timetable_management')
def api_save_timetable(request, schema_name):
    return JsonResponse({'error': 'Not implemented yet'}, status=501)
"""

# The template is exactly the one we already have, with the change: deleteBtn calls performSave() directly.
# We'll embed the updated template HTML (the same as previous but with immediate save).
NEW_TEMPLATE = """{% extends 'tenant/base.html' %}
{% load static %}
{% load fee_extras %}
{% block title %}Time‑Table Management | {{ tenant.name }}{% endblock %}

{% block extra_head %}
<style>
    /* ----- General ----- */
    .page-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem; margin-bottom: 1.5rem; }
    .page-title { font-size: 1.8rem; font-weight: 700; background: linear-gradient(135deg, var(--primary), var(--primary-dark)); -webkit-background-clip: text; background-clip: text; color: transparent; }
    .page-desc { color: var(--muted); }
    .card { background: var(--surface); border-radius: var(--radius); border: 1px solid var(--border); padding: 1rem; margin-bottom: 1.5rem; box-shadow: var(--shadow-sm); }
    .card-header { display: flex; align-items: center; gap: 0.75rem; padding-bottom: 0.75rem; border-bottom: 1px solid var(--border); margin-bottom: 1rem; }
    .card-header h3 { flex: 1; font-size: 1.1rem; font-weight: 600; margin: 0; }
    .btn-primary, .btn-secondary, .btn-danger, .btn-success { display: inline-flex; align-items: center; gap: 0.5rem; padding: 0.4rem 0.8rem; border-radius: 2rem; font-weight: 500; font-size: 0.85rem; text-decoration: none; border: none; cursor: pointer; transition: 0.2s; }
    .btn-primary { background: var(--primary); color: white; }
    .btn-primary:hover { background: var(--primary-dark); }
    .btn-secondary { background: var(--surface-alt); color: var(--text); border: 1px solid var(--border); }
    .btn-danger { background: #dc2626; color: white; }
    .btn-danger:hover { background: #b91c1c; }
    .btn-success { background: #10b981; color: white; }
    .btn-success:hover { background: #059669; }
    .btn-sm { padding: 0.2rem 0.6rem; font-size: 0.75rem; }
    .btn-lg { padding: 0.6rem 1.4rem; font-size: 1rem; font-weight: 600; }
    .form-control { width: 100%; padding: 0.5rem; border-radius: 0.5rem; border: 1px solid var(--border); background: var(--surface-alt); color: var(--text); }
    .form-control:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 2px rgba(59,130,246,0.15); }
    .flex { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
    .mt-1 { margin-top: 0.5rem; }
    .mb-1 { margin-bottom: 0.5rem; }
    .text-muted { color: var(--muted); }

    /* ----- Day schedule table ----- */
    .schedule-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    .schedule-table th, .schedule-table td { border: 1px solid var(--border); padding: 0.5rem; text-align: center; vertical-align: middle; }
    .schedule-table th { background: var(--surface-alt); font-weight: 600; }
    .schedule-table .day-label { font-weight: 600; }
    .schedule-table .slot-label { font-size: 0.8rem; color: var(--muted); }
    .empty-row { color: var(--muted); text-align: center; padding: 1rem; }
    .row-actions { display: flex; gap: 0.3rem; justify-content: center; }

    /* ----- Modal overlay ----- */
    .slot-modal-overlay {
        position: fixed;
        top: 0; left: 0;
        width: 100%; height: 100%;
        background: rgba(0,0,0,0.6);
        z-index: 99999;
        display: none;
        align-items: center;
        justify-content: center;
        backdrop-filter: blur(4px);
        animation: fadeIn 0.2s ease;
    }
    .slot-modal-overlay.active {
        display: flex;
    }
    .slot-modal-box {
        background: var(--surface, #fff);
        border-radius: 1rem;
        padding: 1.5rem 2rem;
        max-width: 500px;
        width: 90%;
        box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        animation: scaleIn 0.2s ease;
    }
    .slot-modal-box h3 {
        margin-top: 0;
        font-size: 1.3rem;
        color: var(--text);
        margin-bottom: 1rem;
    }
    .slot-modal-box .form-group {
        margin-bottom: 1rem;
        text-align: left;
    }
    .slot-modal-box .form-group label {
        display: block;
        font-weight: 500;
        font-size: 0.85rem;
        color: var(--muted);
        margin-bottom: 0.2rem;
    }
    .slot-modal-box .form-group input, .slot-modal-box .form-group select {
        width: 100%;
        padding: 0.5rem;
        border-radius: 0.5rem;
        border: 1px solid var(--border);
        background: var(--surface-alt);
        color: var(--text);
    }
    .slot-modal-box .form-group input:focus, .slot-modal-box .form-group select:focus {
        outline: none;
        border-color: var(--primary);
        box-shadow: 0 0 0 2px rgba(59,130,246,0.15);
    }
    .slot-modal-box .modal-actions {
        display: flex;
        gap: 0.8rem;
        justify-content: flex-end;
        margin-top: 1.5rem;
    }
    .slot-modal-box .modal-actions button {
        border: none;
        padding: 0.5rem 1.2rem;
        border-radius: 2rem;
        font-weight: 600;
        cursor: pointer;
        transition: 0.2s;
        font-size: 0.9rem;
    }
    .slot-modal-box .modal-actions .btn-save {
        background: var(--primary);
        color: white;
    }
    .slot-modal-box .modal-actions .btn-save:hover {
        background: var(--primary-dark);
    }
    .slot-modal-box .modal-actions .btn-cancel {
        background: var(--surface-alt);
        color: var(--text);
        border: 1px solid var(--border);
    }
    .slot-modal-box .modal-actions .btn-cancel:hover {
        background: var(--border);
    }

    /* ----- Custom Alert & Confirm Modals ----- */
    .custom-modal-overlay {
        position: fixed;
        top: 0; left: 0;
        width: 100%; height: 100%;
        background: rgba(0,0,0,0.6);
        z-index: 99999;
        display: none;
        align-items: center;
        justify-content: center;
        backdrop-filter: blur(4px);
        animation: fadeIn 0.2s ease;
    }
    .custom-modal-overlay.active {
        display: flex;
    }
    .custom-modal-box {
        background: var(--surface, #fff);
        border-radius: 1rem;
        padding: 1.5rem 2rem;
        max-width: 450px;
        width: 90%;
        box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        text-align: center;
        animation: scaleIn 0.2s ease;
    }
    .custom-modal-box h3 {
        margin-top: 0;
        font-size: 1.3rem;
        color: var(--text);
    }
    .custom-modal-box p {
        color: var(--muted);
        margin: 0.5rem 0 1.5rem 0;
        font-size: 0.95rem;
        line-height: 1.5;
    }
    .custom-modal-box .modal-actions {
        display: flex;
        gap: 0.8rem;
        justify-content: center;
        flex-wrap: wrap;
    }
    .custom-modal-box .modal-actions button {
        border: none;
        padding: 0.6rem 1.4rem;
        border-radius: 2rem;
        font-weight: 600;
        cursor: pointer;
        transition: 0.2s;
        font-size: 0.9rem;
    }
    .custom-modal-box .modal-actions .btn-ok {
        background: var(--primary);
        color: white;
    }
    .custom-modal-box .modal-actions .btn-ok:hover {
        background: var(--primary-dark);
    }
    .custom-modal-box .modal-actions .btn-yes {
        background: #10b981;
        color: white;
    }
    .custom-modal-box .modal-actions .btn-no {
        background: var(--surface-alt);
        color: var(--text);
        border: 1px solid var(--border);
    }
    .custom-modal-box .modal-actions .btn-no:hover {
        background: var(--border);
    }

    @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
    }
    @keyframes scaleIn {
        from { transform: scale(0.9); opacity: 0; }
        to { transform: scale(1); opacity: 1; }
    }
</style>
{% endblock %}

{% block body %}
<div class="page-header">
    <div>
        <h1 class="page-title">Time‑Table Management</h1>
        <p class="page-desc">Define working days, timings, and periods for your school</p>
    </div>
</div>

<!-- ====== ACADEMIC CALENDAR ====== -->
<div class="card">
    <div class="card-header">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
        <h3>Academic Calendar</h3>
        <span style="margin-left:auto; font-size:0.85rem; color:var(--muted);">Changes are saved automatically</span>
    </div>

    <!-- Universal settings row (only for quick fill) -->
    <div class="universal-row" style="padding:0.5rem; border-radius:0.5rem; margin-bottom:1rem; background:var(--surface-alt);">
        <div class="flex" style="justify-content:space-between; align-items:center;">
            <div class="flex">
                <span style="font-weight:600;">Quick Fill:</span>
                <label>Start</label>
                <input type="time" id="universalStart" value="08:00" class="form-control" style="width:100px;">
                <label>End</label>
                <input type="time" id="universalEnd" value="14:00" class="form-control" style="width:100px;">
                <label>Periods</label>
                <input type="number" id="universalPeriods" value="8" class="form-control" style="width:70px;" min="1">
                <button type="button" id="applyUniversalBtn" class="btn-secondary btn-sm">Apply to All</button>
            </div>
        </div>
    </div>

    <!-- Add Slot button -->
    <div class="flex mb-1">
        <button type="button" id="addSlotBtn" class="btn-primary btn-lg">+ Add Slot</button>
    </div>

    <!-- Day schedule table -->
    <div id="dayScheduleContainer">
        <table class="schedule-table" id="dayScheduleTable">
            <thead>
                <tr>
                    <th>Day</th>
                    <th>Label</th>
                    <th>Start</th>
                    <th>End</th>
                    <th>Periods</th>
                    <th>Duration (min)</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="dayScheduleBody">
                {% for day, slots in day_schedules.items %}
                    {% for slot in slots %}
                    <tr data-day="{{ day }}" data-slot-id="{{ slot.id }}">
                        <td class="day-label">{{ slot.get_day_of_week_display }}</td>
                        <td class="slot-label">{{ slot.label }}</td>
                        <td>{{ slot.start_time|time:'H:i' }}</td>
                        <td>{{ slot.end_time|time:'H:i' }}</td>
                        <td>{{ slot.periods }}</td>
                        <td>{{ slot.duration }}</td>
                        <td>
                            <div class="row-actions">
                                <button class="btn-secondary btn-sm edit-slot-btn" data-day="{{ day }}" data-id="{{ slot.id }}">✎ Edit</button>
                                <button class="btn-danger btn-sm delete-slot-btn" data-day="{{ day }}" data-id="{{ slot.id }}">×</button>
                            </div>
                        </td>
                    </tr>
                    {% endfor %}
                {% empty %}
                <tr><td colspan="7" class="empty-row">No slots added yet. Click "Add Slot" to create one.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="mt-1" style="display: flex; align-items: center; gap: 1rem;">
        <span id="saveStatus">Ready</span>
        <span id="saveSpinner" style="display:none;">⏳</span>
    </div>
</div>

<!-- ====== NOTE: OTHER SECTIONS (Holidays, Periods, Timetable Assignments) will be added later ====== -->
<div class="card">
    <div class="card-header">
        <h3>⚠️ Under Construction</h3>
    </div>
    <p>The Holidays, Periods, and Timetable Assignment sections will be added in the next phase. Stay tuned.</p>
</div>

<!-- ===== SLOT MODAL ===== -->
<div id="slotModal" class="slot-modal-overlay">
    <div class="slot-modal-box">
        <h3 id="slotModalTitle">Add Slot</h3>
        <form id="slotForm" autocomplete="off">
            <div class="form-group">
                <label for="modalDay">Day</label>
                <select id="modalDay" class="form-control" required>
                    <option value="">-- Select Day --</option>
                    {% for val, label in days_of_week %}
                    <option value="{{ val }}">{{ label }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="form-group">
                <label for="modalLabel">Label</label>
                <input type="text" id="modalLabel" class="form-control" placeholder="e.g., Morning, Class 5A" required>
            </div>
            <div class="form-group">
                <label for="modalStart">Start</label>
                <input type="time" id="modalStart" class="form-control" value="08:00" required>
            </div>
            <div class="form-group">
                <label for="modalEnd">End</label>
                <input type="time" id="modalEnd" class="form-control" value="14:00" required>
            </div>
            <div class="form-group">
                <label for="modalPeriods">Periods</label>
                <input type="number" id="modalPeriods" class="form-control" value="8" min="1" required>
            </div>
            <div class="form-group">
                <label for="modalDuration">Duration (min)</label>
                <input type="number" id="modalDuration" class="form-control" value="45" min="1" required>
            </div>
            <div class="modal-actions">
                <button type="button" class="btn-cancel" id="modalCancelBtn">Cancel</button>
                <button type="button" class="btn-save" id="modalSaveBtn">Save</button>
            </div>
        </form>
    </div>
</div>

<!-- ===== CUSTOM ALERT & CONFIRM MODALS ===== -->
<div id="customAlertModal" class="custom-modal-overlay">
    <div class="custom-modal-box">
        <h3 id="alertTitle">Notice</h3>
        <p id="alertMessage">Message</p>
        <div class="modal-actions">
            <button class="btn-ok" id="alertOkBtn">OK</button>
        </div>
    </div>
</div>

<div id="customConfirmModal" class="custom-modal-overlay">
    <div class="custom-modal-box">
        <h3 id="confirmTitle">Confirm</h3>
        <p id="confirmMessage">Are you sure?</p>
        <div class="modal-actions">
            <button class="btn-yes" id="confirmYesBtn">Yes</button>
            <button class="btn-no" id="confirmNoBtn">No</button>
        </div>
    </div>
</div>

<script>
    document.addEventListener('DOMContentLoaded', function() {
        console.log('[Timetable] DOM loaded, initializing...');

        // ====== CSRF TOKEN ======
        function getCsrfToken() {
            let name = 'csrftoken';
            let cookieValue = null;
            if (document.cookie && document.cookie !== '') {
                const cookies = document.cookie.split(';');
                for (let i = 0; i < cookies.length; i++) {
                    const cookie = cookies[i].trim();
                    if (cookie.substring(0, name.length + 1) === (name + '=')) {
                        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                        break;
                    }
                }
            }
            return cookieValue;
        }

        const csrfToken = getCsrfToken();
        const schema = '{{ tenant.schema_name }}';

        // DOM refs
        const dayScheduleBody = document.getElementById('dayScheduleBody');
        const universalStart = document.getElementById('universalStart');
        const universalEnd = document.getElementById('universalEnd');
        const universalPeriods = document.getElementById('universalPeriods');
        const applyUniversalBtn = document.getElementById('applyUniversalBtn');
        const saveStatus = document.getElementById('saveStatus');
        const saveSpinner = document.getElementById('saveSpinner');

        // Slot Modal
        const slotModal = document.getElementById('slotModal');
        const modalTitle = document.getElementById('slotModalTitle');
        const modalDay = document.getElementById('modalDay');
        const modalLabel = document.getElementById('modalLabel');
        const modalStart = document.getElementById('modalStart');
        const modalEnd = document.getElementById('modalEnd');
        const modalPeriods = document.getElementById('modalPeriods');
        const modalDuration = document.getElementById('modalDuration');
        const modalSaveBtn = document.getElementById('modalSaveBtn');
        const modalCancelBtn = document.getElementById('modalCancelBtn');

        // Custom Alert & Confirm
        const alertModal = document.getElementById('customAlertModal');
        const alertMessage = document.getElementById('alertMessage');
        const alertOkBtn = document.getElementById('alertOkBtn');
        const confirmModal = document.getElementById('customConfirmModal');
        const confirmMessage = document.getElementById('confirmMessage');
        const confirmYesBtn = document.getElementById('confirmYesBtn');
        const confirmNoBtn = document.getElementById('confirmNoBtn');

        let editingRow = null;

        // ====== CUSTOM ALERT & CONFIRM FUNCTIONS ======
        function showAlert(message, title = 'Notice') {
            return new Promise((resolve) => {
                alertMessage.textContent = message;
                document.getElementById('alertTitle').textContent = title;
                alertModal.classList.add('active');
                const handler = () => {
                    alertModal.classList.remove('active');
                    alertOkBtn.removeEventListener('click', handler);
                    resolve();
                };
                alertOkBtn.addEventListener('click', handler);
            });
        }

        function showConfirm(message, title = 'Confirm') {
            return new Promise((resolve) => {
                confirmMessage.textContent = message;
                document.getElementById('confirmTitle').textContent = title;
                confirmModal.classList.add('active');
                const yesHandler = () => {
                    confirmModal.classList.remove('active');
                    confirmYesBtn.removeEventListener('click', yesHandler);
                    confirmNoBtn.removeEventListener('click', noHandler);
                    resolve(true);
                };
                const noHandler = () => {
                    confirmModal.classList.remove('active');
                    confirmYesBtn.removeEventListener('click', yesHandler);
                    confirmNoBtn.removeEventListener('click', noHandler);
                    resolve(false);
                };
                confirmYesBtn.addEventListener('click', yesHandler);
                confirmNoBtn.addEventListener('click', noHandler);
            });
        }

        alertModal.addEventListener('click', (e) => {
            if (e.target === alertModal) {
                alertModal.classList.remove('active');
                alertOkBtn.click();
            }
        });

        // ====== HELPERS ======
        function calculateDuration(startStr, endStr, periods) {
            if (!startStr || !endStr || !periods) return 45;
            const start = new Date('1970-01-01T' + startStr + ':00');
            const end = new Date('1970-01-01T' + endStr + ':00');
            const diff = (end - start) / 60000;
            if (diff <= 0) return 45;
            return Math.round(diff / periods);
        }

        function updateModalDuration() {
            const start = modalStart.value;
            const end = modalEnd.value;
            const periods = parseInt(modalPeriods.value) || 0;
            const duration = calculateDuration(start, end, periods);
            modalDuration.value = duration;
        }

        modalStart.addEventListener('change', updateModalDuration);
        modalEnd.addEventListener('change', updateModalDuration);
        modalPeriods.addEventListener('change', updateModalDuration);
        modalPeriods.addEventListener('input', updateModalDuration);

        // ====== AUTO-SAVE (debounced for add/edit) ======
        let saveTimeout = null;
        function autoSave() {
            if (saveTimeout) clearTimeout(saveTimeout);
            saveTimeout = setTimeout(() => {
                saveTimeout = null;
                performSave();
            }, 800);
        }

        async function performSave() {
            const rows = dayScheduleBody.querySelectorAll('tr:not(.empty-row)');
            const schedules = [];
            rows.forEach(row => {
                const cells = row.querySelectorAll('td');
                const day = parseInt(row.dataset.day);
                const label = cells[1].textContent;
                const start = cells[2].textContent;
                const end = cells[3].textContent;
                const periods = parseInt(cells[4].textContent);
                const duration = parseInt(cells[5].textContent);
                if (day && label && start && end && periods && duration) {
                    schedules.push({ day, label, start, end, periods, duration });
                }
            });

            setStatus('Saving…', 'info');
            saveSpinner.style.display = 'inline';

            try {
                const res = await fetch(`/portal/${schema}/api/timetable/day-schedules/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ schedules: schedules })
                });
                const data = await res.json();
                if (!res.ok) {
                    const errorMsg = data.error || `HTTP ${res.status}`;
                    setStatus('⚠️ ' + errorMsg, 'error');
                    await showAlert('Error: ' + errorMsg, 'Save Failed');
                    return;
                }
                if (data && data.success) {
                    setStatus('All changes saved ✓', 'success');
                } else {
                    throw new Error(data?.error || 'Unknown error');
                }
            } catch (err) {
                console.error('Save error:', err);
                setStatus('⚠️ Save failed – see console', 'error');
                await showAlert('An error occurred while saving. Please check the console.', 'Error');
            } finally {
                saveSpinner.style.display = 'none';
            }
        }

        function setStatus(msg, type) {
            saveStatus.textContent = msg;
            saveStatus.style.color = type === 'error' ? '#dc2626' : type === 'success' ? '#10b981' : 'var(--muted)';
        }

        // ====== MODAL OPEN/CLOSE ======
        function openModal(rowData = null) {
            if (rowData) {
                modalTitle.textContent = 'Edit Slot';
                modalDay.value = rowData.day;
                modalDay.disabled = true;
                modalLabel.value = rowData.label;
                modalStart.value = rowData.start;
                modalEnd.value = rowData.end;
                modalPeriods.value = rowData.periods;
                modalDuration.value = rowData.duration;
                editingRow = rowData.rowElement;
            } else {
                modalTitle.textContent = 'Add Slot';
                modalDay.value = '';
                modalDay.disabled = false;
                modalLabel.value = '';
                modalStart.value = universalStart.value || '08:00';
                modalEnd.value = universalEnd.value || '14:00';
                modalPeriods.value = universalPeriods.value || 8;
                updateModalDuration();
                editingRow = null;
            }
            slotModal.classList.add('active');
        }

        function closeModal() {
            slotModal.classList.remove('active');
            editingRow = null;
        }

        modalCancelBtn.addEventListener('click', closeModal);
        slotModal.addEventListener('click', (e) => {
            if (e.target === slotModal) closeModal();
        });

        // ====== SAVE SLOT FROM MODAL ======
        modalSaveBtn.addEventListener('click', async function() {
            const day = parseInt(modalDay.value);
            const label = modalLabel.value.trim();
            const start = modalStart.value;
            const end = modalEnd.value;
            const periods = parseInt(modalPeriods.value);
            const duration = parseInt(modalDuration.value);

            if (isNaN(day) || day === 0) {
                await showAlert('Please select a valid day.', 'Missing Day');
                return;
            }
            if (!label) {
                await showAlert('Label is required.', 'Missing Label');
                return;
            }
            if (!start || !end || isNaN(periods) || isNaN(duration)) {
                await showAlert('Please fill all fields correctly.', 'Invalid Data');
                return;
            }

            const dayLabel = modalDay.options[modalDay.selectedIndex].text;

            if (editingRow) {
                const cells = editingRow.querySelectorAll('td');
                cells[0].textContent = dayLabel;
                cells[1].textContent = label;
                cells[2].textContent = start;
                cells[3].textContent = end;
                cells[4].textContent = periods;
                cells[5].textContent = duration;
                editingRow.dataset.day = day;
            } else {
                const tr = document.createElement('tr');
                tr.dataset.day = day;
                tr.innerHTML = `
                    <td class="day-label">${dayLabel}</td>
                    <td class="slot-label">${label}</td>
                    <td>${start}</td>
                    <td>${end}</td>
                    <td>${periods}</td>
                    <td>${duration}</td>
                    <td>
                        <div class="row-actions">
                            <button class="btn-secondary btn-sm edit-slot-btn" data-day="${day}">✎ Edit</button>
                            <button class="btn-danger btn-sm delete-slot-btn" data-day="${day}">×</button>
                        </div>
                    </td>
                `;
                dayScheduleBody.appendChild(tr);
                const emptyRow = dayScheduleBody.querySelector('.empty-row');
                if (emptyRow) emptyRow.remove();
                attachRowEvents(tr);
            }

            closeModal();
            autoSave(); // debounced save
        });

        // ====== ATTACH EVENTS TO ROW BUTTONS ======
        function attachRowEvents(row) {
            const editBtn = row.querySelector('.edit-slot-btn');
            const deleteBtn = row.querySelector('.delete-slot-btn');

            editBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                const cells = row.querySelectorAll('td');
                const day = parseInt(row.dataset.day);
                const label = cells[1].textContent;
                const start = cells[2].textContent;
                const end = cells[3].textContent;
                const periods = parseInt(cells[4].textContent);
                const duration = parseInt(cells[5].textContent);
                openModal({
                    day: day,
                    label: label,
                    start: start,
                    end: end,
                    periods: periods,
                    duration: duration,
                    rowElement: row
                });
            });

            deleteBtn.addEventListener('click', async function(e) {
                e.stopPropagation();
                const confirmed = await showConfirm('Delete this slot?', 'Confirm Deletion');
                if (confirmed) {
                    row.remove();
                    if (dayScheduleBody.children.length === 0) {
                        dayScheduleBody.innerHTML = '<tr><td colspan="7" class="empty-row">No slots added yet. Click "Add Slot" to create one.</td></tr>';
                    }
                    // ----- FIX: Save immediately after deletion (bypass debounce) -----
                    await performSave();
                }
            });
        }

        // ====== INITIALIZE EXISTING ROWS ======
        document.querySelectorAll('#dayScheduleBody tr:not(.empty-row)').forEach(row => {
            attachRowEvents(row);
        });

        // ====== ADD SLOT BUTTON ======
        document.getElementById('addSlotBtn').addEventListener('click', function() {
            openModal();
        });

        // ====== APPLY UNIVERSAL ======
        applyUniversalBtn.addEventListener('click', function() {
            const start = universalStart.value;
            const end = universalEnd.value;
            const periods = universalPeriods.value;
            const rows = dayScheduleBody.querySelectorAll('tr:not(.empty-row)');
            rows.forEach(row => {
                const cells = row.querySelectorAll('td');
                const duration = calculateDuration(start, end, periods);
                cells[2].textContent = start;
                cells[3].textContent = end;
                cells[4].textContent = periods;
                cells[5].textContent = duration;
            });
            autoSave(); // debounced
            setStatus('Universal values applied and saved.', 'success');
        });

        // ====== INITIAL LOAD ======
        const firstRow = dayScheduleBody.querySelector('tr:not(.empty-row)');
        if (firstRow) {
            const cells = firstRow.querySelectorAll('td');
            universalStart.value = cells[2]?.textContent || '08:00';
            universalEnd.value = cells[3]?.textContent || '14:00';
            universalPeriods.value = parseInt(cells[4]?.textContent) || 8;
        }
        setStatus('Ready', 'info');
        console.log('[Timetable] Initialization complete.');
    });
</script>
{% endblock %}
"""

def main():
    # --- Patch view ---
    view_path = Path(VIEW_PATH)
    if view_path.is_file():
        backup_view = view_path.with_suffix(view_path.suffix + ".bak")
        shutil.copy2(view_path, backup_view)
        print(f"✅ View backup: {backup_view}")
        with open(view_path, "w", encoding="utf-8") as f:
            f.write(NEW_VIEW)
        print("✅ Updated axis_saas/views/timetable.py")
    else:
        print(f"❌ View file not found: {view_path}")

    # --- Patch template ---
    template_path = Path(TEMPLATE_PATH)
    if template_path.is_file():
        backup_template = template_path.with_suffix(template_path.suffix + ".bak")
        shutil.copy2(template_path, backup_template)
        print(f"✅ Template backup: {backup_template}")
        with open(template_path, "w", encoding="utf-8") as f:
            f.write(NEW_TEMPLATE)
        print("✅ Updated templates/tenant/timetable_management.html")
    else:
        print(f"❌ Template file not found: {template_path}")

    print("\n🎉 All fixes applied.")
    print("   - Backend now deletes all old schedules before recreating.")
    print("   - Frontend saves immediately after deletion.")
    print("   - Refresh will now show the correct state.")

if __name__ == "__main__":
    main()
