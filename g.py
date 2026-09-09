#!/usr/bin/env python3
"""
axis_patcher_fix_addday.py – Fix the "Add Day" button in Time‑Table Management.

The existing HTML has a JS syntax error (unescaped newline in alert) that prevents
all JavaScript from running. This patcher overwrites the template with a corrected
version and adds the sidebar link if missing.

Usage:
    python axis_patcher_fix_addday.py [--dry-run] [--verbose] [--target-dir /path/to/project]
"""

import os
import re
import sys
import argparse
from datetime import datetime

VERBOSE = False
DRY_RUN = False

def log(msg, level="INFO"):
    prefix = {"ERROR": "❌", "WARNING": "⚠️", "SUCCESS": "✅"}.get(level, "ℹ️")
    if VERBOSE or level in ("ERROR", "SUCCESS"):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {prefix} {msg}")

def error_exit(msg):
    log(msg, "ERROR")
    sys.exit(1)

def read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None

def write_file(path, content):
    if DRY_RUN:
        log(f"Would write {path}", "INFO")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    log(f"Written {path}", "SUCCESS")

# ========== CORRECTED TIMETABLE HTML ==========
FIXED_HTML = """{% extends 'tenant/base.html' %}
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
    .form-control { width: 100%; padding: 0.5rem; border-radius: 0.5rem; border: 1px solid var(--border); background: var(--surface-alt); color: var(--text); }
    .form-control:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 2px rgba(59,130,246,0.15); }
    .form-control-sm { width: auto; display: inline-block; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
    .flex { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
    .mt-1 { margin-top: 0.5rem; }
    .mb-1 { margin-bottom: 0.5rem; }
    .text-muted { color: var(--muted); }

    /* ----- Day schedule table ----- */
    .schedule-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    .schedule-table th, .schedule-table td { border: 1px solid var(--border); padding: 0.5rem; text-align: center; vertical-align: middle; }
    .schedule-table th { background: var(--surface-alt); font-weight: 600; }
    .schedule-table input[type="time"], .schedule-table input[type="number"] { width: 90px; padding: 0.2rem 0.4rem; border: 1px solid var(--border); border-radius: 0.3rem; background: var(--surface); }
    .schedule-table .day-label { font-weight: 600; }
    .remove-day-btn { cursor: pointer; color: #dc2626; background: none; border: none; font-size: 1.2rem; }
    .empty-row { color: var(--muted); text-align: center; padding: 1rem; }

    /* ----- Universal row ----- */
    .universal-row { background: #f0f7ff; }
    .universal-row input { background: white; }

    /* ----- Status ----- */
    #saveStatus { font-size: 0.9rem; color: var(--muted); }
    #saveSpinner { display: none; font-size: 1.2rem; }
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
        <span style="margin-left:auto; font-size:0.85rem; color:var(--muted);">All changes auto‑saved</span>
    </div>

    <!-- Universal settings row -->
    <div class="universal-row" style="padding:0.5rem; border-radius:0.5rem; margin-bottom:1rem; background:var(--surface-alt);">
        <div class="flex" style="justify-content:space-between; align-items:center;">
            <div class="flex">
                <span style="font-weight:600;">Universal:</span>
                <label>Start</label>
                <input type="time" id="universalStart" value="08:00" class="form-control form-control-sm" style="width:100px;">
                <label>End</label>
                <input type="time" id="universalEnd" value="14:00" class="form-control form-control-sm" style="width:100px;">
                <label>Periods</label>
                <input type="number" id="universalPeriods" value="8" class="form-control form-control-sm" style="width:70px;" min="1">
                <button type="button" id="applyUniversalBtn" class="btn-secondary btn-sm">Apply to All</button>
            </div>
            <div>
                <button type="button" id="resetCalendarBtn" class="btn-danger btn-sm">Reset Calendar</button>
                <button type="button" id="saveCalendarBtn" class="btn-success btn-sm">Save Calendar</button>
            </div>
        </div>
    </div>

    <!-- Add day dropdown -->
    <div class="flex mb-1">
        <select id="daySelector" class="form-control" style="width:200px;">
            <option value="">-- Select Day --</option>
            {% for val, label in days_of_week %}
            <option value="{{ val }}">{{ label }}</option>
            {% endfor %}
        </select>
        <button type="button" id="addDayBtn" class="btn-primary btn-sm">+ Add Day</button>
    </div>

    <!-- Day schedule table -->
    <div id="dayScheduleContainer">
        <table class="schedule-table" id="dayScheduleTable">
            <thead>
                <tr>
                    <th>Day</th>
                    <th>Start</th>
                    <th>End</th>
                    <th>Periods</th>
                    <th>Duration (min)</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody id="dayScheduleBody">
                {% for day, schedule in day_schedules.items %}
                <tr data-day="{{ day }}">
                    <td class="day-label">{{ schedule.get_day_of_week_display }}</td>
                    <td><input type="time" name="start" value="{{ schedule.start_time|time:'H:i' }}" class="form-control schedule-input" data-day="{{ day }}" data-field="start"></td>
                    <td><input type="time" name="end" value="{{ schedule.end_time|time:'H:i' }}" class="form-control schedule-input" data-day="{{ day }}" data-field="end"></td>
                    <td><input type="number" name="periods" value="{{ schedule.periods }}" class="form-control schedule-input" data-day="{{ day }}" data-field="periods" min="1"></td>
                    <td><input type="number" name="duration" value="{{ schedule.duration }}" class="form-control schedule-input" data-day="{{ day }}" data-field="duration" min="1"></td>
                    <td><button type="button" class="btn-danger btn-sm remove-day-btn" data-day="{{ day }}">×</button></td>
                </tr>
                {% empty %}
                <tr><td colspan="6" class="empty-row">No days added yet. Select a day and click "Add Day".</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="mt-1" style="display: flex; align-items: center; gap: 1rem;">
        <span id="saveStatus">All changes saved</span>
        <span id="saveSpinner">⏳</span>
    </div>
</div>

<!-- ====== NOTE: OTHER SECTIONS (Holidays, Periods, Timetable Assignments) will be added later ====== -->
<div class="card">
    <div class="card-header">
        <h3>⚠️ Under Construction</h3>
    </div>
    <p>The Holidays, Periods, and Timetable Assignment sections will be added in the next phase. Stay tuned.</p>
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
        const daySelector = document.getElementById('daySelector');
        const addDayBtn = document.getElementById('addDayBtn');
        const dayScheduleBody = document.getElementById('dayScheduleBody');
        const universalStart = document.getElementById('universalStart');
        const universalEnd = document.getElementById('universalEnd');
        const universalPeriods = document.getElementById('universalPeriods');
        const applyUniversalBtn = document.getElementById('applyUniversalBtn');
        const resetCalendarBtn = document.getElementById('resetCalendarBtn');
        const saveCalendarBtn = document.getElementById('saveCalendarBtn');
        const saveStatus = document.getElementById('saveStatus');
        const saveSpinner = document.getElementById('saveSpinner');

        console.log('[Timetable] Refs:', { daySelector, addDayBtn, dayScheduleBody });

        // ====== HELPERS ======
        function calculateDuration(startStr, endStr, periods) {
            if (!startStr || !endStr || !periods) return 45;
            const start = new Date('1970-01-01T' + startStr + ':00');
            const end = new Date('1970-01-01T' + endStr + ':00');
            const diff = (end - start) / 60000;
            if (diff <= 0) return 45;
            return Math.round(diff / periods);
        }

        function updateDuration(row) {
            const start = row.querySelector('input[name="start"]');
            const end = row.querySelector('input[name="end"]');
            const periods = row.querySelector('input[name="periods"]');
            const duration = row.querySelector('input[name="duration"]');
            if (start && end && periods && duration) {
                const val = calculateDuration(start.value, end.value, periods.value);
                if (val > 0) duration.value = val;
            }
        }

        // ====== AUTO-SAVE ======
        let saveTimeout = null;
        function autoSave() {
            if (saveTimeout) clearTimeout(saveTimeout);
            saveTimeout = setTimeout(() => {
                saveTimeout = null;
                performSave();
            }, 800);
        }

        function performSave() {
            const rows = dayScheduleBody.querySelectorAll('tr:not(.empty-row)');
            const schedules = [];
            rows.forEach(row => {
                const day = parseInt(row.dataset.day);
                const start = row.querySelector('input[name="start"]')?.value;
                const end = row.querySelector('input[name="end"]')?.value;
                const periods = parseInt(row.querySelector('input[name="periods"]')?.value);
                const duration = parseInt(row.querySelector('input[name="duration"]')?.value);
                if (day && start && end && periods && duration) {
                    schedules.push({ day, start, end, periods, duration });
                }
            });
            if (schedules.length === 0) {
                setStatus('No days to save', 'info');
                return;
            }
            setStatus('Saving…', 'info');
            saveSpinner.style.display = 'inline';

            fetch(`/portal/${schema}/api/timetable/day-schedules/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ schedules: schedules })
            })
            .then(async res => {
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
                return data;
            })
            .then(data => {
                if (data.success) {
                    setStatus('All changes saved ✓', 'success');
                } else {
                    throw new Error(data.error || 'Unknown error');
                }
            })
            .catch(err => {
                console.error('Save error:', err);
                setStatus('⚠️ Save failed – see console', 'error');
                if (err.message && (err.message.includes('relation') || err.message.includes('does not exist'))) {
                    alert('Database table for day schedules is missing.\\nPlease run migrations for this tenant.\\nContact your system administrator.');
                }
            })
            .finally(() => {
                saveSpinner.style.display = 'none';
            });
        }

        function setStatus(msg, type) {
            saveStatus.textContent = msg;
            saveStatus.style.color = type === 'error' ? '#dc2626' : type === 'success' ? '#10b981' : 'var(--muted)';
        }

        // ====== ADD DAY ======
        if (addDayBtn) {
            addDayBtn.addEventListener('click', function(e) {
                e.preventDefault();
                console.log('[Timetable] Add Day button clicked');
                const day = parseInt(daySelector.value);
                if (isNaN(day) || day === '') {
                    alert('Please select a day.');
                    return;
                }
                if (document.querySelector(`.schedule-table tr[data-day="${day}"]`)) {
                    alert('Day already added.');
                    return;
                }
                const dayLabel = daySelector.options[daySelector.selectedIndex].text;
                const startVal = universalStart.value || '08:00';
                const endVal = universalEnd.value || '14:00';
                const periodsVal = universalPeriods.value || 8;
                const durationVal = calculateDuration(startVal, endVal, periodsVal);

                const tr = document.createElement('tr');
                tr.dataset.day = day;
                tr.innerHTML = `
                    <td class="day-label">${dayLabel}</td>
                    <td><input type="time" name="start" value="${startVal}" class="form-control schedule-input" data-day="${day}" data-field="start"></td>
                    <td><input type="time" name="end" value="${endVal}" class="form-control schedule-input" data-day="${day}" data-field="end"></td>
                    <td><input type="number" name="periods" value="${periodsVal}" class="form-control schedule-input" data-day="${day}" data-field="periods" min="1"></td>
                    <td><input type="number" name="duration" value="${durationVal}" class="form-control schedule-input" data-day="${day}" data-field="duration" min="1"></td>
                    <td><button type="button" class="btn-danger btn-sm remove-day-btn" data-day="${day}">×</button></td>
                `;
                dayScheduleBody.appendChild(tr);
                // remove empty row if exists
                const emptyRow = dayScheduleBody.querySelector('.empty-row');
                if (emptyRow) emptyRow.remove();

                // attach remove event
                tr.querySelector('.remove-day-btn').addEventListener('click', function() {
                    removeDay(day);
                });

                daySelector.value = '';
                autoSave();
                console.log('[Timetable] Day added:', dayLabel);
            });
        } else {
            console.error('[Timetable] addDayBtn not found');
        }

        // ====== REMOVE DAY ======
        function removeDay(day) {
            if (!confirm('Remove this day from schedule?')) return;
            const row = dayScheduleBody.querySelector(`tr[data-day="${day}"]`);
            if (row) row.remove();
            if (dayScheduleBody.children.length === 0) {
                dayScheduleBody.innerHTML = '<tr><td colspan="6" class="empty-row">No days added yet. Select a day and click "Add Day".</td></tr>';
            }
            autoSave();
        }

        // Delegate remove events
        document.addEventListener('click', function(e) {
            if (e.target.classList.contains('remove-day-btn')) {
                const day = parseInt(e.target.dataset.day);
                removeDay(day);
            }
        });

        // ====== AUTO-UPDATE DURATION ON CHANGE ======
        document.addEventListener('change', function(e) {
            const target = e.target;
            if (target.classList.contains('schedule-input')) {
                const row = target.closest('tr');
                if (row) {
                    const field = target.dataset.field;
                    if (field === 'start' || field === 'end' || field === 'periods') {
                        updateDuration(row);
                    }
                    autoSave();
                }
            }
        });

        // Also on input (for number fields)
        document.addEventListener('input', function(e) {
            const target = e.target;
            if (target.classList.contains('schedule-input') && (target.type === 'number' || target.type === 'time')) {
                const row = target.closest('tr');
                if (row && target.dataset.field === 'periods') {
                    updateDuration(row);
                }
                autoSave();
            }
        });

        // ====== APPLY UNIVERSAL ======
        applyUniversalBtn.addEventListener('click', function(e) {
            e.preventDefault();
            console.log('[Timetable] Apply Universal clicked');
            const start = universalStart.value;
            const end = universalEnd.value;
            const periods = universalPeriods.value;
            const rows = dayScheduleBody.querySelectorAll('tr:not(.empty-row)');
            rows.forEach(row => {
                const startInput = row.querySelector('input[name="start"]');
                const endInput = row.querySelector('input[name="end"]');
                const periodsInput = row.querySelector('input[name="periods"]');
                if (startInput) startInput.value = start;
                if (endInput) endInput.value = end;
                if (periodsInput) periodsInput.value = periods;
                updateDuration(row);
            });
            autoSave();
        });

        // ====== SAVE CALENDAR (manual) ======
        saveCalendarBtn.addEventListener('click', function(e) {
            e.preventDefault();
            if (confirm('Save current calendar settings to database?')) {
                performSave();
            }
        });

        // ====== RESET CALENDAR ======
        resetCalendarBtn.addEventListener('click', function(e) {
            e.preventDefault();
            if (!confirm('Are you sure you want to reset the calendar? All unsaved changes will be lost.')) return;
            // Reload the page to reset to server state
            location.reload();
        });

        // ====== INITIAL LOAD: populate universal values from first row ======
        const firstRow = dayScheduleBody.querySelector('tr:not(.empty-row)');
        if (firstRow) {
            const start = firstRow.querySelector('input[name="start"]')?.value;
            const end = firstRow.querySelector('input[name="end"]')?.value;
            const periods = firstRow.querySelector('input[name="periods"]')?.value;
            if (start) universalStart.value = start;
            if (end) universalEnd.value = end;
            if (periods) universalPeriods.value = periods;
        }
        // Set initial status
        setStatus('Ready', 'info');
        console.log('[Timetable] Initialization complete.');
    });
</script>
{% endblock %}
"""

def patch_sidebar(target_dir):
    """Add Time‑Table Management link to the sidebar if not present."""
    base_path = os.path.join(target_dir, "templates", "tenant", "base.html")
    if not os.path.exists(base_path):
        log("base.html not found; skipping sidebar injection", "WARNING")
        return

    content = read_file(base_path)
    if content is None:
        return

    if re.search(r'Time[- ]Table|timetable', content, re.I):
        log("Sidebar link already exists, skipping", "INFO")
        return

    # Inject before the closing </nav> of sidebar-nav
    pattern = r'(<nav class="sidebar-nav">.*?)(</nav>)'
    match = re.search(pattern, content, re.DOTALL | re.I)
    if match:
        nav_content = match.group(1)
        closing = match.group(2)
        new_link = '''                <a href="{% url 'timetable_management' schema_name=tenant.schema_name %}" class="nav-item {% if 'timetable' in request.resolver_match.url_name %}active{% endif %}">
                    <svg class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                    <span>Time‑Table</span>
                </a>
'''
        new_content = content.replace(match.group(0), nav_content + new_link + closing)
        write_file(base_path, new_content)
        log("Added sidebar link to Time‑Table Management", "SUCCESS")
    else:
        log("Could not find sidebar nav; please add link manually.", "WARNING")

def main():
    parser = argparse.ArgumentParser(description="Fix Add Day button in Time‑Table Management")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current)")
    args = parser.parse_args()

    global VERBOSE, DRY_RUN
    VERBOSE = args.verbose
    DRY_RUN = args.dry_run

    target_dir = os.path.abspath(args.target_dir)
    if not os.path.isdir(target_dir):
        error_exit(f"Target directory does not exist: {target_dir}")

    log(f"Starting patcher (dry-run={DRY_RUN})", "INFO")
    log(f"Target directory: {target_dir}")

    # 1. Overwrite timetable HTML with fixed version
    html_path = os.path.join(target_dir, "templates", "tenant", "timetable_management.html")
    write_file(html_path, FIXED_HTML)

    # 2. Add sidebar link if missing
    patch_sidebar(target_dir)

    # 3. Reminder about migrations
    log("\n⚠️  IMPORTANT: If the DaySchedule table is missing in tenant schemas,\n"
        "run migrations for all tenants:\n"
        "   python manage.py migrate_schemas\n"
        "or for a specific tenant:\n"
        "   python manage.py migrate --schema=your_schema\n"
        "This patcher does not run migrations.\n", "WARNING")

    log("Patcher completed. The 'Add Day' button should now work.", "SUCCESS")

if __name__ == "__main__":
    main()
