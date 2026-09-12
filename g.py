#!/usr/bin/env python3
"""
axis_patcher.py
===============

EDIT_TIMING_v1
--------------

Two changes:

1. Academic Calendar (`timetable_management.html`):
   * Add a new "⏱️ Edit Timing" button next to "+ Add Slot" and
     "🏷️ Manage Labels".
   * Clicking it opens a modal where the admin:
       - Picks a ScheduleLabel
       - Types a new Start / End time (single pair)
       - Sees all days of that label listed with their current timing
       - Clicks "Apply Changes" -> ALL days under that label get the
         new timing in one shot.
   * In the per-row "✎ Edit" popup, freeze Day / Start / End / Duration
     so only Periods is editable.

2. Reconciliation (`axis_saas/views/periods.py`):
   * Previously, if a day's Start/End changed in the Academic Calendar,
     that day was DROPPED from every Periods Timetable that used it.
   * Now: the day is KEPT and only its period timings are recomputed
     using the new Start/End. A day is only removed from a timetable
     when the underlying DaySchedule row itself is deleted.

Also adds:
   * New view `api_batch_update_label_times` in `views/timetable.py`.
   * `slots_by_label_json` context var in `timetable_management`.
   * URL route for the new endpoint.

Idempotent. Safe to run multiple times.

Usage:
    python3 axis_patcher.py [--dry-run] [--verbose] [--target-dir=.]
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


MARKER = "EDIT_TIMING_v1"


def _log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


def _read(path: Path):
    try:
        return path.read_text(encoding='utf-8')
    except Exception as e:
        _log(f"ERROR reading {path}: {e}")
        return None


def _write(path: Path, content: str, dry_run: bool, verbose: bool) -> bool:
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


# =====================================================================
# 1) VIEW: axis_saas/views/timetable.py
# =====================================================================
VIEW_ANCHOR_BEFORE = (
    "        all_day_schedules = DaySchedule.objects.filter(academic_calendar=calendar).order_by('day_of_week', 'order')\n"
    "        day_schedules = {}\n"
    "        for ds in all_day_schedules:\n"
    "            if ds.day_of_week not in weekly_holiday_days:\n"
    "                day_schedules.setdefault(ds.day_of_week, []).append(ds)\n"
)

VIEW_ANCHOR_AFTER = (
    "        all_day_schedules = DaySchedule.objects.filter(academic_calendar=calendar).order_by('day_of_week', 'order')\n"
    "        day_schedules = {}\n"
    "        slots_by_label = {}\n"
    "        for ds in all_day_schedules:\n"
    "            if ds.day_of_week not in weekly_holiday_days:\n"
    "                day_schedules.setdefault(ds.day_of_week, []).append(ds)\n"
    "            _lbl = (ds.label or '').strip()\n"
    "            if _lbl:\n"
    "                slots_by_label.setdefault(_lbl, []).append({\n"
    "                    'id': ds.id,\n"
    "                    'day': ds.day_of_week,\n"
    "                    'day_label': ds.get_day_of_week_display(),\n"
    "                    'start': ds.start_time.strftime('%H:%M'),\n"
    "                    'end': ds.end_time.strftime('%H:%M'),\n"
    "                    'periods': ds.periods,\n"
    "                    'duration': ds.duration,\n"
    "                })\n"
)

VIEW_CONTEXT_ANCHOR = (
    "        'schedule_labels': schedule_labels,\n"
    "        'months': months,\n"
    "        'days': days,\n"
    "    }\n"
)

VIEW_CONTEXT_REPLACE = (
    "        'schedule_labels': schedule_labels,\n"
    "        'months': months,\n"
    "        'days': days,\n"
    "        'slots_by_label_json': json.dumps(slots_by_label),\n"
    "    }\n"
)

# New view function — appended after `api_save_day_schedules`.
VIEW_FUNC_ANCHOR = (
    "# ========== HOLIDAY API ENDPOINTS ==========\n"
)

VIEW_FUNC_BLOCK = (
    "# ========== EDIT_TIMING_v1 : batch-update timing for a label ==========\n"
    "@csrf_exempt\n"
    "@require_http_methods([\"POST\"])\n"
    "@require_tenant_type(['school', 'wing_school', 'single_small_school'])\n"
    "@require_school_feature('timetable_management')\n"
    "def api_batch_update_label_times(request, schema_name):\n"
    "    \"\"\"Batch-update start/end times for all DaySchedule rows of a label.\n\n"
    "    Body: { \"label\": \"Senior\",\n"
    "            \"updates\": [ { \"day_of_week\": 0, \"start\": \"08:00\", \"end\": \"14:00\" }, ... ] }\n"
    "    \"\"\"\n"
    "    try:\n"
    "        data = json.loads(request.body)\n"
    "    except json.JSONDecodeError:\n"
    "        return JsonResponse({'error': 'Invalid JSON'}, status=400)\n\n"
    "    label = (data.get('label') or '').strip()\n"
    "    updates = data.get('updates') or []\n"
    "    if not label:\n"
    "        return JsonResponse({'error': 'Label is required'}, status=400)\n"
    "    if not isinstance(updates, list) or not updates:\n"
    "        return JsonResponse({'error': 'updates must be a non-empty list'}, status=400)\n\n"
    "    with schema_context(schema_name):\n"
    "        calendar, _ = AcademicCalendar.objects.get_or_create(pk=1)\n"
    "        updated_count = 0\n"
    "        for item in updates:\n"
    "            try:\n"
    "                day = int(item.get('day_of_week'))\n"
    "                start_str = item.get('start')\n"
    "                end_str = item.get('end')\n"
    "            except (TypeError, ValueError):\n"
    "                continue\n"
    "            if day is None or not start_str or not end_str:\n"
    "                continue\n"
    "            try:\n"
    "                start_time = datetime.strptime(start_str, '%H:%M').time()\n"
    "                end_time = datetime.strptime(end_str, '%H:%M').time()\n"
    "            except ValueError:\n"
    "                continue\n"
    "            if start_time >= end_time:\n"
    "                return JsonResponse({'error': 'End time must be after start time for day ' + str(day)}, status=400)\n\n"
    "            ds = DaySchedule.objects.filter(\n"
    "                academic_calendar=calendar, label=label, day_of_week=day\n"
    "            ).first()\n"
    "            if not ds:\n"
    "                continue\n"
    "            ds.start_time = start_time\n"
    "            ds.end_time = end_time\n"
    "            total_min = (end_time.hour * 60 + end_time.minute) - (start_time.hour * 60 + start_time.minute)\n"
    "            if total_min > 0 and ds.periods > 0:\n"
    "                ds.duration = max(1, total_min // ds.periods)\n"
    "            ds.save(update_fields=['start_time', 'end_time', 'duration'])\n"
    "            updated_count += 1\n\n"
    "        return JsonResponse({'success': True, 'updated': updated_count})\n"
    "# ========== END EDIT_TIMING_v1 ==========\n\n\n"
    "# ========== HOLIDAY API ENDPOINTS ==========\n"
)


def patch_view_timetable(path: Path, dry_run: bool, verbose: bool) -> bool:
    _log(f"Patching view: {path}")
    content = _read(path)
    if content is None:
        return False

    if "EDIT_TIMING_v1" in content:
        _log("  - already patched, skipping")
        return True

    changed = False

    # --- 1) slots_by_label in day_schedules loop ---
    if "slots_by_label = {}" in content:
        _log("  - slots_by_label already present")
    elif VIEW_ANCHOR_BEFORE in content:
        content = content.replace(VIEW_ANCHOR_BEFORE, VIEW_ANCHOR_AFTER, 1)
        _log("  + injected slots_by_label builder")
        changed = True
    else:
        _log("  WARN: day_schedules loop anchor not found")

    # --- 2) slots_by_label_json in context ---
    if "'slots_by_label_json'" in content:
        _log("  - slots_by_label_json already in context")
    elif VIEW_CONTEXT_ANCHOR in content:
        content = content.replace(VIEW_CONTEXT_ANCHOR, VIEW_CONTEXT_REPLACE, 1)
        _log("  + added slots_by_label_json to context")
        changed = True
    else:
        _log("  WARN: context anchor not found")

    # --- 3) new view function ---
    if "def api_batch_update_label_times" in content:
        _log("  - api_batch_update_label_times already present")
    elif VIEW_FUNC_ANCHOR in content:
        content = content.replace(VIEW_FUNC_ANCHOR, VIEW_FUNC_BLOCK, 1)
        _log("  + injected api_batch_update_label_times")
        changed = True
    else:
        _log("  WARN: HOLIDAY API anchor not found")

    if not changed:
        return True
    return _write(path, content, dry_run, verbose)


# =====================================================================
# 2) VIEW: axis_saas/views/periods.py — reconcile change
# =====================================================================
RECONCILE_OLD = (
    "            if not sched:\n"
    "                days_changed = True\n"
    "                continue\n"
    "            if sched['start'] != day.get('start') or sched['end'] != day.get('end'):\n"
    "                days_changed = True\n"
    "                continue\n"
    "\n"
    "            try:\n"
    "                old_count = int(day.get('periods_count') or 0)\n"
    "            except (TypeError, ValueError):\n"
    "                old_count = 0\n"
    "\n"
    "            if sched['periods'] != old_count:\n"
    "                try:\n"
    "                    start_t = datetime.strptime(sched['start'], '%H:%M').time()\n"
    "                    end_t = datetime.strptime(sched['end'], '%H:%M').time()\n"
    "                except Exception:\n"
    "                    days_changed = True\n"
    "                    continue\n"
    "\n"
    "                break_after = day.get('break_after')\n"
    "                try:\n"
    "                    break_after = int(break_after) if break_after not in (None, '', 'null') else None\n"
    "                except (TypeError, ValueError):\n"
    "                    break_after = None\n"
    "                if break_after is not None and (break_after < 1 or break_after >= sched['periods']):\n"
    "                    break_after = None\n"
    "\n"
    "                periods_data = _compute_periods(\n"
    "                    start_t, end_t, sched['periods'],\n"
    "                    break_after, break_duration,\n"
    "                )\n"
    "                day['periods_count'] = sched['periods']\n"
    "                day['periods'] = periods_data\n"
    "                day['break_after'] = break_after\n"
    "                days_changed = True\n"
    "\n"
    "            new_days.append(day)\n"
)

RECONCILE_NEW = (
    "            if not sched:\n"
    "                days_changed = True\n"
    "                continue\n"
    "\n"
    "            try:\n"
    "                old_count = int(day.get('periods_count') or 0)\n"
    "            except (TypeError, ValueError):\n"
    "                old_count = 0\n"
    "\n"
    "            # EDIT_TIMING_v1: recompute when start/end OR periods_count\n"
    "            # changed. Never drop the day just because its timing\n"
    "            # changed -- update it in place instead.\n"
    "            if (\n"
    "                sched['start'] != day.get('start')\n"
    "                or sched['end'] != day.get('end')\n"
    "                or sched['periods'] != old_count\n"
    "            ):\n"
    "                try:\n"
    "                    start_t = datetime.strptime(sched['start'], '%H:%M').time()\n"
    "                    end_t = datetime.strptime(sched['end'], '%H:%M').time()\n"
    "                except Exception:\n"
    "                    days_changed = True\n"
    "                    continue\n"
    "\n"
    "                break_after = day.get('break_after')\n"
    "                try:\n"
    "                    break_after = int(break_after) if break_after not in (None, '', 'null') else None\n"
    "                except (TypeError, ValueError):\n"
    "                    break_after = None\n"
    "                if break_after is not None and (break_after < 1 or break_after >= sched['periods']):\n"
    "                    break_after = None\n"
    "\n"
    "                periods_data = _compute_periods(\n"
    "                    start_t, end_t, sched['periods'],\n"
    "                    break_after, break_duration,\n"
    "                )\n"
    "                day['periods_count'] = sched['periods']\n"
    "                day['start'] = sched['start']\n"
    "                day['end'] = sched['end']\n"
    "                day['periods'] = periods_data\n"
    "                day['break_after'] = break_after\n"
    "                days_changed = True\n"
    "\n"
    "            new_days.append(day)\n"
)


def patch_view_periods(path: Path, dry_run: bool, verbose: bool) -> bool:
    _log(f"Patching view: {path}")
    content = _read(path)
    if content is None:
        return False

    if RECONCILE_NEW in content:
        _log("  - already patched, skipping")
        return True

    if RECONCILE_OLD not in content:
        _log("  ERROR: could not find _reconcile_timetables block")
        return False

    content = content.replace(RECONCILE_OLD, RECONCILE_NEW, 1)
    _log("  + modified _reconcile_timetables (timing change -> update, not drop)")
    return _write(path, content, dry_run, verbose)


# =====================================================================
# 3) URLS: axis_saas/public_urls.py
# =====================================================================
URL_IMPORT_ANCHOR = (
    "from .views.timetable import (\n"
    "    timetable_management, api_update_calendar, api_add_holiday, api_delete_holiday,\n"
    "    api_add_period, api_delete_period, api_update_period, api_get_timetable,\n"
    "    api_save_timetable, api_save_day_schedules, api_update_holiday,\n"
    "    api_list_labels, api_add_label, api_update_label, api_delete_label,\n"
    ")\n"
)

URL_IMPORT_NEW = (
    "from .views.timetable import (\n"
    "    timetable_management, api_update_calendar, api_add_holiday, api_delete_holiday,\n"
    "    api_add_period, api_delete_period, api_update_period, api_get_timetable,\n"
    "    api_save_timetable, api_save_day_schedules, api_update_holiday,\n"
    "    api_list_labels, api_add_label, api_update_label, api_delete_label,\n"
    "    api_batch_update_label_times,\n"
    ")\n"
)

URL_ROUTE_ANCHOR = (
    "    path('portal/<slug:schema_name>/api/timetable/day-schedules/', "
    "portal_wrapper(login_required_for_schema(api_save_day_schedules)), "
    "name='api_timetable_day_schedules'),\n"
)

URL_ROUTE_NEW = (
    "    path('portal/<slug:schema_name>/api/timetable/day-schedules/', "
    "portal_wrapper(login_required_for_schema(api_save_day_schedules)), "
    "name='api_timetable_day_schedules'),\n"
    "    # ===== EDIT_TIMING_v1 =====\n"
    "    path('portal/<slug:schema_name>/api/timetable/day-schedules/batch-update/', "
    "portal_wrapper(login_required_for_schema(api_batch_update_label_times)), "
    "name='api_timetable_day_schedules_batch_update'),\n"
)


def patch_public_urls(path: Path, dry_run: bool, verbose: bool) -> bool:
    _log(f"Patching URLs: {path}")
    content = _read(path)
    if content is None:
        return False

    if "api_batch_update_label_times" in content:
        _log("  - already patched, skipping")
        return True

    changed = False

    if URL_IMPORT_ANCHOR in content:
        content = content.replace(URL_IMPORT_ANCHOR, URL_IMPORT_NEW, 1)
        _log("  + added api_batch_update_label_times import")
        changed = True
    else:
        _log("  ERROR: timetable import block anchor not found")

    if URL_ROUTE_ANCHOR in content:
        content = content.replace(URL_ROUTE_ANCHOR, URL_ROUTE_NEW, 1)
        _log("  + added batch-update URL route")
        changed = True
    else:
        _log("  ERROR: day-schedules URL anchor not found")

    if not changed:
        return False
    return _write(path, content, dry_run, verbose)


# =====================================================================
# 4) TEMPLATE: templates/tenant/timetable_management.html
# =====================================================================

# ---- 4a) Button ----
BTN_ANCHOR = (
    "    <div class=\"flex mb-1\" style=\"gap:0.5rem;\">\n"
    "        <button type=\"button\" id=\"addSlotBtn\" class=\"btn-primary btn-lg\">+ Add Slot</button>\n"
    "        <button type=\"button\" id=\"manageLabelsBtn\" class=\"btn-secondary btn-lg\" title=\"Create / edit / delete reusable labels\">🏷️ Manage Labels</button>\n"
    "    </div>\n"
)

BTN_REPLACE = (
    "    <div class=\"flex mb-1\" style=\"gap:0.5rem;\">\n"
    "        <button type=\"button\" id=\"addSlotBtn\" class=\"btn-primary btn-lg\">+ Add Slot</button>\n"
    "        <button type=\"button\" id=\"editTimingBtn\" class=\"btn-secondary btn-lg\" title=\"Batch-edit start/end times for a label\">&#9201; Edit Timing</button>\n"
    "        <button type=\"button\" id=\"manageLabelsBtn\" class=\"btn-secondary btn-lg\" title=\"Create / edit / delete reusable labels\">🏷️ Manage Labels</button>\n"
    "    </div>\n"
)

# ---- 4b) Modal HTML + closing script marker ----
MODAL_ANCHOR = (
    "<script>\n"
    "    document.addEventListener('DOMContentLoaded', function() {\n"
)

MODAL_BLOCK = r'''
<!-- ===== EDIT_TIMING_v1 : Edit Timing modal ===== -->
<div id="editTimingModal" class="slot-modal-overlay" style="align-items: flex-start; padding-top: 2rem;">
    <div class="slot-modal-box" style="max-width: 760px;">
        <h3>&#9201; Edit Timing &mdash; Set All Days at Once</h3>
        <p class="text-muted" style="font-size:0.85rem; margin:0 0 1rem 0;">
            Select a label, then enter the new Start / End time. All days under that label will be updated together when you click <strong>Apply Changes</strong>.
        </p>

        <div class="form-group">
            <label for="editTimingLabelSelect">Label</label>
            <select id="editTimingLabelSelect" class="form-control">
                <option value="">-- Select Label --</option>
            </select>
        </div>

        <div style="display:flex; gap:0.75rem; flex-wrap:wrap;">
            <div class="form-group" style="flex:1; min-width:140px;">
                <label for="editTimingNewStart">New Start Time</label>
                <input type="time" id="editTimingNewStart" class="form-control">
            </div>
            <div class="form-group" style="flex:1; min-width:140px;">
                <label for="editTimingNewEnd">New End Time</label>
                <input type="time" id="editTimingNewEnd" class="form-control">
            </div>
        </div>

        <div id="editTimingDaysWrap" style="display:none; margin-top:0.5rem;">
            <h4 style="margin:0 0 0.4rem 0; font-size:0.95rem;">Days under this label (current timing)</h4>
            <div style="max-height:280px; overflow-y:auto; border:1px solid var(--border); border-radius:0.5rem;">
                <table class="schedule-table" style="margin:0;">
                    <thead>
                        <tr>
                            <th>Day</th>
                            <th>Current Start</th>
                            <th>Current End</th>
                            <th>Periods</th>
                        </tr>
                    </thead>
                    <tbody id="editTimingDaysBody"></tbody>
                </table>
            </div>
        </div>

        <div class="modal-actions" style="margin-top:1rem;">
            <button type="button" class="btn-cancel" id="editTimingCancelBtn">Cancel</button>
            <button type="button" class="btn-save" id="editTimingApplyBtn" disabled>Apply Changes</button>
        </div>
    </div>
</div>
<!-- ===== END EDIT_TIMING_v1 ===== -->

<script>
    document.addEventListener('DOMContentLoaded', function() {
'''

# ---- 4c) JS handler injection inside the main DOMContentLoaded ----
JS_HOOK_ANCHOR = (
    "        renderLabelDropdown();\n"
    "        renderLabelsList();\n"
    "    });\n"
)

JS_HOOK_BLOCK = r'''        renderLabelDropdown();
        renderLabelsList();

        // ===== EDIT_TIMING_v1 : batch-edit timings for a label =====
        (function setupEditTiming() {
            var editTimingModal      = document.getElementById('editTimingModal');
            var editTimingBtn        = document.getElementById('editTimingBtn');
            var editTimingLabelSelect= document.getElementById('editTimingLabelSelect');
            var editTimingNewStart   = document.getElementById('editTimingNewStart');
            var editTimingNewEnd     = document.getElementById('editTimingNewEnd');
            var editTimingDaysWrap   = document.getElementById('editTimingDaysWrap');
            var editTimingDaysBody   = document.getElementById('editTimingDaysBody');
            var editTimingApplyBtn   = document.getElementById('editTimingApplyBtn');
            var editTimingCancelBtn  = document.getElementById('editTimingCancelBtn');

            if (!editTimingModal || !editTimingBtn) return;

            var SLOTS_BY_LABEL = {{ slots_by_label_json|safe }} || {};

            function populateLabelDropdown() {
                // Rebuild option list from SLOTS_BY_LABEL keys
                editTimingLabelSelect.innerHTML = '<option value="">-- Select Label --</option>';
                Object.keys(SLOTS_BY_LABEL).forEach(function (lbl) {
                    var opt = document.createElement('option');
                    opt.value = lbl;
                    opt.textContent = lbl;
                    editTimingLabelSelect.appendChild(opt);
                });
            }

            function updateApplyState() {
                var lbl = editTimingLabelSelect.value;
                var slots = SLOTS_BY_LABEL[lbl] || [];
                var s = editTimingNewStart.value;
                var e = editTimingNewEnd.value;
                editTimingApplyBtn.disabled = !(lbl && slots.length && s && e && s < e);
            }

            function renderDays(slots) {
                editTimingDaysBody.innerHTML = '';
                slots.forEach(function (slot) {
                    var tr = document.createElement('tr');
                    tr.innerHTML =
                        '<td class="day-label">' + slot.day_label + '</td>' +
                        '<td>' + slot.start + '</td>' +
                        '<td>' + slot.end + '</td>' +
                        '<td>' + slot.periods + '</td>';
                    editTimingDaysBody.appendChild(tr);
                });
            }

            function openEditTiming() {
                populateLabelDropdown();
                editTimingLabelSelect.value = '';
                editTimingNewStart.value = '';
                editTimingNewEnd.value = '';
                editTimingDaysWrap.style.display = 'none';
                editTimingDaysBody.innerHTML = '';
                editTimingApplyBtn.disabled = true;
                editTimingModal.classList.add('active');
            }

            function closeEditTiming() {
                editTimingModal.classList.remove('active');
            }

            editTimingBtn.addEventListener('click', openEditTiming);
            editTimingCancelBtn.addEventListener('click', closeEditTiming);
            editTimingModal.addEventListener('click', function (e) {
                if (e.target === editTimingModal) closeEditTiming();
            });
            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape' && editTimingModal.classList.contains('active')) {
                    closeEditTiming();
                }
            });

            editTimingLabelSelect.addEventListener('change', function () {
                var lbl = this.value;
                var slots = SLOTS_BY_LABEL[lbl] || [];
                if (slots.length) {
                    var first = slots[0];
                    var allSame = slots.every(function (s) {
                        return s.start === first.start && s.end === first.end;
                    });
                    if (allSame) {
                        editTimingNewStart.value = first.start;
                        editTimingNewEnd.value = first.end;
                    } else {
                        editTimingNewStart.value = '';
                        editTimingNewEnd.value = '';
                    }
                } else {
                    editTimingNewStart.value = '';
                    editTimingNewEnd.value = '';
                }
                renderDays(slots);
                editTimingDaysWrap.style.display = slots.length ? 'block' : 'none';
                updateApplyState();
            });

            editTimingNewStart.addEventListener('change', updateApplyState);
            editTimingNewEnd.addEventListener('change', updateApplyState);

            editTimingApplyBtn.addEventListener('click', async function () {
                var lbl = editTimingLabelSelect.value;
                var newStart = editTimingNewStart.value;
                var newEnd = editTimingNewEnd.value;
                var slots = SLOTS_BY_LABEL[lbl] || [];
                if (!lbl || !newStart || !newEnd || !slots.length) return;
                if (newStart >= newEnd) {
                    await showAlert('End time must be after start time.', 'Invalid Time');
                    return;
                }

                if (typeof HAS_PERIOD_TIMETABLES !== 'undefined' && HAS_PERIOD_TIMETABLES) {
                    var ok = await showConfirm(
                        'Warning: Existing Periods Timetables will have their period timings updated for this label. No day will be removed unless the slot itself is deleted.\n\nContinue?',
                        'Timing Change Warning'
                    );
                    if (!ok) return;
                }

                var updates = slots.map(function (s) {
                    return { day_of_week: s.day, start: newStart, end: newEnd };
                });

                editTimingApplyBtn.disabled = true;
                var _orig = editTimingApplyBtn.textContent;
                editTimingApplyBtn.textContent = 'Saving\u2026';

                try {
                    var res = await fetch('/portal/' + schema + '/api/timetable/day-schedules/batch-update/', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': csrfToken
                        },
                        body: JSON.stringify({ label: lbl, updates: updates })
                    });
                    var data = await res.json();
                    if (!res.ok || !data.success) {
                        await showAlert('Error: ' + (data.error || 'Unknown error'), 'Save Failed');
                        editTimingApplyBtn.disabled = false;
                        editTimingApplyBtn.textContent = _orig;
                        return;
                    }
                    window.location.reload();
                } catch (err) {
                    console.error(err);
                    await showAlert('Network error: ' + err.message, 'Error');
                    editTimingApplyBtn.disabled = false;
                    editTimingApplyBtn.textContent = _orig;
                }
            });
        })();
        // ===== END EDIT_TIMING_v1 =====
    });
'''

# ---- 4d) openModal freeze ----
OPEN_MODAL_OLD = (
    "        function openModal(rowData = null) {\n"
    "            // safety: ensure no stale overlay blocks clicks\n"
    "            document.querySelectorAll('.custom-modal-overlay.active').forEach(function (m) {\n"
    "                m.classList.remove('active');\n"
    "            });\n"
    "            if (rowData) {\n"
    "                modalTitle.textContent = 'Edit Slot';\n"
    "                modalDay.value = rowData.day;\n"
    "                modalDay.disabled = true;\n"
    "                modalLabel.value = rowData.label;\n"
    "                modalStart.value = rowData.start;\n"
    "                modalEnd.value = rowData.end;\n"
    "                modalPeriods.value = rowData.periods;\n"
    "                modalDuration.value = rowData.duration;\n"
    "                editingRow = rowData.rowElement;\n"
    "            } else {\n"
    "                modalTitle.textContent = 'Add Slot';\n"
    "                modalDay.value = '';\n"
    "                modalDay.disabled = false;\n"
    "                modalLabel.value = '';\n"
    "                modalStart.value = universalStart.value || '08:00';\n"
    "                modalEnd.value = universalEnd.value || '14:00';\n"
    "                modalPeriods.value = universalPeriods.value || 8;\n"
    "                updateModalDuration();\n"
    "                editingRow = null;\n"
    "            }\n"
    "            slotModal.classList.add('active');\n"
    "        }\n"
)

OPEN_MODAL_NEW = (
    "        function openModal(rowData = null) {\n"
    "            // safety: ensure no stale overlay blocks clicks\n"
    "            document.querySelectorAll('.custom-modal-overlay.active').forEach(function (m) {\n"
    "                m.classList.remove('active');\n"
    "            });\n"
    "            if (rowData) {\n"
    "                // EDIT_TIMING_v1: on edit, freeze everything except Periods.\n"
    "                modalTitle.textContent = 'Edit Slot (Periods only)';\n"
    "                modalDay.value = rowData.day;\n"
    "                modalDay.disabled = true;\n"
    "                modalLabel.value = rowData.label;\n"
    "                modalLabel.disabled = true;\n"
    "                modalStart.value = rowData.start;\n"
    "                modalStart.disabled = true;\n"
    "                modalEnd.value = rowData.end;\n"
    "                modalEnd.disabled = true;\n"
    "                modalPeriods.value = rowData.periods;\n"
    "                modalPeriods.disabled = false;\n"
    "                modalDuration.value = rowData.duration;\n"
    "                modalDuration.disabled = true;\n"
    "                editingRow = rowData.rowElement;\n"
    "            } else {\n"
    "                modalTitle.textContent = 'Add Slot';\n"
    "                modalDay.value = '';\n"
    "                modalDay.disabled = false;\n"
    "                modalLabel.value = '';\n"
    "                modalLabel.disabled = false;\n"
    "                modalStart.value = universalStart.value || '08:00';\n"
    "                modalStart.disabled = false;\n"
    "                modalEnd.value = universalEnd.value || '14:00';\n"
    "                modalEnd.disabled = false;\n"
    "                modalPeriods.value = universalPeriods.value || 8;\n"
    "                modalPeriods.disabled = false;\n"
    "                updateModalDuration();\n"
    "                modalDuration.disabled = false;\n"
    "                editingRow = null;\n"
    "            }\n"
    "            slotModal.classList.add('active');\n"
    "        }\n"
)


def patch_template(path: Path, dry_run: bool, verbose: bool) -> bool:
    _log(f"Patching template: {path}")
    content = _read(path)
    if content is None:
        return False

    if MARKER in content:
        _log("  - already patched, skipping")
        return True

    changed = False

    # --- 1) Button ---
    if 'id="editTimingBtn"' in content:
        _log("  - Edit Timing button already present")
    elif BTN_ANCHOR in content:
        content = content.replace(BTN_ANCHOR, BTN_REPLACE, 1)
        _log("  + injected Edit Timing button")
        changed = True
    else:
        _log("  WARN: Add Slot / Manage Labels row anchor not found")

    # --- 2) Modal HTML ---
    if 'id="editTimingModal"' in content:
        _log("  - Edit Timing modal already present")
    elif MODAL_ANCHOR in content:
        content = content.replace(MODAL_ANCHOR, MODAL_BLOCK, 1)
        _log("  + injected Edit Timing modal HTML")
        changed = True
    else:
        _log("  WARN: main script anchor for modal not found")

    # --- 3) JS handler hook ---
    if "setupEditTiming" in content:
        _log("  - Edit Timing JS already wired")
    elif JS_HOOK_ANCHOR in content:
        content = content.replace(JS_HOOK_ANCHOR, JS_HOOK_BLOCK, 1)
        _log("  + injected Edit Timing JS handler")
        changed = True
    else:
        _log("  WARN: renderLabelDropdown/renderLabelsList anchor not found")

    # --- 4) openModal freeze ---
    if "Edit Slot (Periods only)" in content:
        _log("  - openModal already freezes non-Periods fields")
    elif OPEN_MODAL_OLD in content:
        content = content.replace(OPEN_MODAL_OLD, OPEN_MODAL_NEW, 1)
        _log("  + froze Day/Label/Start/End/Duration in openModal (edit mode)")
        changed = True
    else:
        _log("  WARN: openModal function anchor not found")

    # --- 5) Marker ---
    if MARKER not in content:
        anchor = "{% block body %}\n"
        if anchor in content:
            content = content.replace(anchor, anchor + "{# " + MARKER + " #}\n", 1)

    if not changed:
        _log("  - no changes applied")
        return True

    return _write(path, content, dry_run, verbose)


# =====================================================================
# main
# =====================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "EDIT_TIMING_v1 -- Add a batch 'Edit Timing' button to the "
            "Academic Calendar page, freeze non-Periods fields in the "
            "per-row edit modal, and change reconciliation so timing "
            "changes update Periods Timetables in place instead of "
            "dropping the day."
        )
    )
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--target-dir', default='.')
    args = parser.parse_args()

    target = Path(args.target_dir).resolve()
    _log(f"Target dir: {target}")
    _log(f"Dry-run:    {args.dry_run}")
    _log(f"Verbose:    {args.verbose}")
    print('-' * 60)

    if not (target / 'manage.py').exists():
        _log("WARNING: manage.py not found at target root. Continuing anyway.")

    ok = True

    print('-' * 60)
    _log("STEP 1: axis_saas/views/timetable.py")
    ok &= patch_view_timetable(
        target / 'axis_saas' / 'views' / 'timetable.py',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 2: axis_saas/views/periods.py")
    ok &= patch_view_periods(
        target / 'axis_saas' / 'views' / 'periods.py',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 3: axis_saas/public_urls.py")
    ok &= patch_public_urls(
        target / 'axis_saas' / 'public_urls.py',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 4: templates/tenant/timetable_management.html")
    ok &= patch_template(
        target / 'templates' / 'tenant' / 'timetable_management.html',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    if ok:
        _log("DONE.")
        if not args.dry_run:
            _log("")
            _log("NEXT STEPS:")
            _log("  1. Restart the Django dev server (to pick up .py view changes).")
            _log("  2. Hard-refresh the browser (Ctrl+F5 / Cmd+Shift+R).")
            _log("")
            _log("  3. Open /portal/<schema>/timetable/")
            _log("     - Academic Calendar section now shows:")
            _log("         [ + Add Slot ]  [ ⏱ Edit Timing ]  [ 🏷 Manage Labels ]")
            _log("")
            _log("  4. Click '⏱ Edit Timing':")
            _log("     - Pick a Label.")
            _log("     - Type new Start / End.")
            _log("     - Click Apply Changes.")
            _log("     - All days under that label get the new timing.")
            _log("     - Periods Timetables using that label update their")
            _log("       period timings in place (no day is dropped).")
            _log("")
            _log("  5. Per-row '✎ Edit' now only allows editing Periods;")
            _log("     Day / Label / Start / End / Duration are frozen.")
            _log("")
            _log("  Tip: Open DevTools Console (F12) to confirm no JS errors.")
        return 0
    _log("FAILED: one or more steps did not complete.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
