#!/usr/bin/env python3
"""
axis_patcher.py — CLASS_DETAIL_ATTENDANCE_BUTTON_V1
====================================================

Adds a "Manage Attendance" button to both class-detail pages
(``single_class_detailed.html`` and ``wing_class_detailed.html``) and
embeds the exact same Mark / Auto-Marked modal that opens on the admin
attendance dashboard when you click "Mark & History".

The modal is scoped to the class shown on the page — no class dropdown,
no class filter. It reuses the existing JSON APIs:

  * GET  /api/attendance/students/?class_id=…&date=…[&period_order=…]
  * POST /api/attendance/mark/
  * GET  /api/attendance/auto-marked-dates/?class_id=…

so behaviour (holiday gate, lock-by-default, auto-marked banner,
auto-marked dates list) is identical to the dashboard.

What this patch touches
-----------------------
* templates/tenant/single_class_detailed.html
* templates/tenant/wing_class_detailed.html

Only adds a button, a CSS block, a modal block, and one JS IIFE.
Idempotent. No models, migrations, views or URLs touched.

Usage
-----
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py
    python3 axis_patcher.py --target-dir /srv/fee_management
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


MARKER = "CLASS_DETAIL_ATTENDANCE_BUTTON_V1"


# --------------------------------------------------------------------- utils

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log(f"  ERROR: not found: {path}")
        return None
    except Exception as e:
        log(f"  ERROR reading {path}: {e}")
        return None


def write_file(path, content, dry_run=False, label=""):
    if dry_run:
        log(f"  DRY-RUN: would write {path} ({label})")
        return True
    try:
        path.write_text(content, encoding="utf-8")
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as e:
        log(f"  ERROR writing {path}: {e}")
        return False


# ====================================================== CSS

CSS_BLOCK = r'''
    /* ============ CLASS_DETAIL_ATTENDANCE_BUTTON_V1 ============ */
    .cd-att-tabs {
        display: flex;
        gap: 0;
        border-bottom: 1px solid var(--border);
        background: var(--surface-alt);
    }
    .cd-att-tabs button {
        flex: 1;
        padding: .75rem 1rem;
        background: none;
        border: none;
        border-bottom: 2px solid transparent;
        font-weight: 700;
        font-size: .78rem;
        color: var(--muted);
        cursor: pointer;
        text-transform: uppercase;
        letter-spacing: .05em;
        transition: all .15s ease;
        font-family: inherit;
    }
    .cd-att-tabs button.active {
        color: var(--primary);
        border-bottom-color: var(--primary);
        background: var(--surface);
    }
    .cd-att-tabs button:hover:not(.active) { color: var(--text); }

    .cd-att-banner {
        display: flex;
        align-items: center;
        gap: .6rem;
        padding: .8rem 1.2rem;
        font-size: .85rem;
        font-weight: 600;
        border-bottom: 1px solid var(--border);
        line-height: 1.35;
    }
    .cd-att-banner.holiday { background: #fef3c7; color: #92400e; }
    .cd-att-banner.holiday::before { content: '🎉'; font-size: 1.1rem; flex-shrink: 0; }
    .cd-att-banner.locked { background: #e0e7ff; color: #3730a3; }
    .cd-att-banner.locked::before { content: '🔒'; font-size: 1.1rem; flex-shrink: 0; }
    .cd-att-banner.auto { background: #dbeafe; color: #1e40af; }
    .cd-att-banner.auto::before { content: '🤖'; font-size: 1.1rem; flex-shrink: 0; }
    .cd-att-banner button {
        margin-left: auto;
        padding: .35rem .8rem;
        border-radius: .5rem;
        border: 1px solid currentColor;
        background: transparent;
        color: inherit;
        font-weight: 700;
        font-size: .78rem;
        cursor: pointer;
        white-space: nowrap;
        font-family: inherit;
    }
    .cd-att-banner button:hover { background: rgba(255,255,255,.4); }

    .cd-att-toolbar {
        display: flex;
        flex-wrap: wrap;
        gap: .5rem;
        align-items: center;
        padding: .85rem 1.2rem;
        border-bottom: 1px solid var(--border);
        background: var(--surface-alt);
    }
    .cd-att-toolbar label {
        font-size: .7rem;
        text-transform: uppercase;
        color: var(--muted);
        font-weight: 700;
        letter-spacing: .05em;
    }
    .cd-att-toolbar input[type="date"],
    .cd-att-toolbar input[type="number"] {
        padding: .45rem .65rem;
        border-radius: .55rem;
        border: 1px solid var(--border);
        background: var(--surface);
        color: var(--text);
        font-size: .82rem;
    }
    .cd-att-toolbar button {
        padding: .45rem .85rem;
        border-radius: .55rem;
        border: 1px solid var(--border);
        background: var(--surface);
        color: var(--text);
        font-weight: 700;
        font-size: .78rem;
        cursor: pointer;
        font-family: inherit;
    }
    .cd-att-toolbar button.primary {
        background: var(--primary);
        border-color: var(--primary);
        color: #fff;
    }

    .cd-att-body {
        padding: 0;
        overflow-y: auto;
        flex: 1 1 auto;
        min-height: 200px;
        max-height: 62vh;
        -webkit-overflow-scrolling: touch;
    }
    .cd-att-empty {
        text-align: center;
        color: var(--muted);
        padding: 2.5rem 1rem;
        font-size: .9rem;
    }
    .cd-att-row {
        display: grid;
        grid-template-columns: 56px 1fr auto;
        gap: .75rem;
        align-items: center;
        padding: .6rem 1.2rem;
        border-bottom: 1px solid var(--border);
    }
    .cd-att-row:last-child { border-bottom: none; }
    .cd-att-row .roll {
        font-weight: 700;
        color: var(--muted);
        font-size: .82rem;
    }
    .cd-att-row .student-name { font-weight: 600; font-size: .9rem; }
    .cd-att-row .student-meta {
        font-size: .72rem;
        color: var(--muted);
        margin-top: .05rem;
    }
    .cd-att-row .student-meta .leave-tag {
        display: inline-block;
        margin-left: .35rem;
        padding: .05rem .4rem;
        background: #0ea5e9;
        color: #fff;
        border-radius: .3rem;
        font-size: .62rem;
        font-weight: 800;
        letter-spacing: .05em;
    }
    .cd-att-status { display: flex; gap: .3rem; }
    .cd-att-status input { display: none; }
    .cd-att-status label {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 42px;
        padding: .4rem .55rem;
        border-radius: .55rem;
        font-size: .72rem;
        font-weight: 800;
        cursor: pointer;
        border: 1px solid var(--border);
        background: var(--surface-alt);
        color: var(--muted);
        user-select: none;
    }
    .cd-att-status input[value="present"]:checked + label  { background:#10b981; color:#fff; border-color:#10b981; }
    .cd-att-status input[value="absent"]:checked + label   { background:#ef4444; color:#fff; border-color:#ef4444; }
    .cd-att-status input[value="late"]:checked + label     { background:#f59e0b; color:#fff; border-color:#f59e0b; }
    .cd-att-status input[value="half_day"]:checked + label { background:#8b5cf6; color:#fff; border-color:#8b5cf6; }
    .cd-att-status input[value="excused"]:checked + label  { background:#0ea5e9; color:#fff; border-color:#0ea5e9; }
    .cd-att-status input:disabled + label { opacity: .55; cursor: not-allowed; }

    .cd-att-footer {
        display: flex;
        gap: .6rem;
        align-items: center;
        justify-content: flex-end;
        padding: .85rem 1.2rem;
        border-top: 1px solid var(--border);
        background: var(--surface-alt);
    }
    .cd-att-footer button {
        padding: .55rem 1.1rem;
        border-radius: .6rem;
        font-weight: 700;
        font-size: .82rem;
        cursor: pointer;
        border: 1px solid var(--border);
        background: var(--surface);
        color: var(--text);
        font-family: inherit;
    }
    .cd-att-footer button.primary {
        background: var(--primary);
        border-color: var(--primary);
        color: #fff;
    }
    .cd-att-footer .save-msg {
        margin-right: auto;
        font-size: .82rem;
        color: var(--muted);
    }
    .cd-att-footer .save-msg.ok { color: #10b981; font-weight: 700; }
    .cd-att-footer .save-msg.err { color: #ef4444; font-weight: 700; }

    .cd-att-auto-list { padding: 0; }
    .cd-att-auto-row {
        display: grid;
        grid-template-columns: 130px 1fr auto;
        gap: .75rem;
        align-items: center;
        padding: .75rem 1.2rem;
        border-bottom: 1px solid var(--border);
        cursor: pointer;
        transition: background .12s ease;
    }
    .cd-att-auto-row:hover { background: var(--surface-alt); }
    .cd-att-auto-row:last-child { border-bottom: none; }
    .cd-att-auto-row .date-cell { font-weight: 800; font-size: .9rem; }
    .cd-att-auto-row .date-cell .day-name {
        font-size: .68rem;
        color: var(--muted);
        font-weight: 700;
        margin-top: .05rem;
        text-transform: uppercase;
        letter-spacing: .05em;
    }
    .cd-att-auto-row .stats { font-size: .78rem; color: var(--muted); }
    .cd-att-auto-row .stats .stat-pill {
        display: inline-block;
        margin-right: .3rem;
        padding: .15rem .5rem;
        border-radius: .4rem;
        font-weight: 800;
        background: var(--surface-alt);
        border: 1px solid var(--border);
    }
    .cd-att-auto-row .stats .stat-pill.p { color: #10b981; }
    .cd-att-auto-row .stats .stat-pill.a { color: #ef4444; }
    .cd-att-auto-row .stats .stat-pill.l { color: #f59e0b; }
    .cd-att-auto-row .stats .stat-pill.e { color: #0ea5e9; }
    .cd-att-auto-row .open-btn {
        padding: .4rem .8rem;
        border-radius: .5rem;
        border: 1px solid var(--primary);
        background: var(--primary);
        color: #fff;
        font-weight: 700;
        font-size: .75rem;
        cursor: pointer;
        font-family: inherit;
    }
    .cd-att-auto-row .open-btn:hover { filter: brightness(1.07); }

    .cd-att-auto-pager {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 1rem;
        padding: .85rem 1.2rem;
        border-top: 1px solid var(--border);
        background: var(--surface-alt);
    }
    .cd-att-auto-pager .pager-btn {
        padding: .45rem .9rem;
        border-radius: .55rem;
        border: 1px solid var(--border);
        background: var(--surface);
        color: var(--text);
        font-weight: 700;
        font-size: .78rem;
        cursor: pointer;
        font-family: inherit;
    }
    .cd-att-auto-pager .pager-btn:hover:not(:disabled) {
        background: var(--primary);
        color: #fff;
        border-color: var(--primary);
    }
    .cd-att-auto-pager .pager-btn:disabled { opacity: .45; cursor: not-allowed; }
    .cd-att-auto-pager .pager-info {
        font-size: .78rem;
        color: var(--muted);
        font-weight: 600;
        white-space: nowrap;
    }
    /* ============ END CLASS_DETAIL_ATTENDANCE_BUTTON_V1 ============ */
'''


# ====================================================== Button

BUTTON_OLD = '''        <button type="button" class="btn-manage-timetable" id="manageTimetableBtn">
            &#128197; Manage Periods Timetable
        </button>'''

BUTTON_NEW = '''        <button type="button" class="btn-manage-timetable" id="manageTimetableBtn">
            &#128197; Manage Periods Timetable
        </button>
        <button type="button" class="btn-manage-timetable" id="manageAttendanceBtn">
            &#128203; Manage Attendance
        </button>'''


# ====================================================== Modal + JS

MODAL_HTML = r'''
<!-- ============ CLASS_DETAIL_ATTENDANCE_BUTTON_V1 : Manage Attendance modal ============ -->
<div id="cdAttendanceModal" class="timetable-modal-overlay">
    <div class="timetable-modal-content" style="max-width:820px;">
        <div class="timetable-modal-header">
            <h2>Manage Attendance &mdash; {{ class_display_name }}</h2>
            <button type="button" class="timetable-modal-close" id="cdAttCloseBtn" aria-label="Close">&times;</button>
        </div>

        <!-- tabs -->
        <div class="cd-att-tabs">
            <button type="button" id="cdAttTabBtnMark" class="active"
                    onclick="CD_ATT.showTab('mark')">Mark Attendance</button>
            <button type="button" id="cdAttTabBtnAuto"
                    onclick="CD_ATT.showTab('auto')">Auto-Marked by System</button>
        </div>

        <!-- TAB 1: MARK -->
        <div id="cdAttTabMark">
            <div id="cdAttHolidayBanner" class="cd-att-banner holiday" style="display:none;">
                <span id="cdAttHolidayText"></span>
            </div>
            <div id="cdAttLockedBanner" class="cd-att-banner locked" style="display:none;">
                <span>Attendance already submitted &mdash; locked.</span>
                <button type="button" onclick="CD_ATT.unlockForm()">Edit Attendance</button>
            </div>
            <div id="cdAttAutoBanner" class="cd-att-banner auto" style="display:none;">
                <span>This attendance was auto-marked by the system. Click "Edit Attendance" to adjust it.</span>
            </div>

            <div class="cd-att-toolbar">
                <label>Date</label>
                <input type="date" id="cdAttDate">
                <label>Period</label>
                <input type="number" id="cdAttPeriod" min="1"
                       placeholder="full day" style="width:100px;">
                <button class="primary" type="button"
                        onclick="CD_ATT.reload()">Load</button>
                <button type="button" id="cdAttBulkP"
                        onclick="CD_ATT.bulk('present')">All Present</button>
                <button type="button" id="cdAttBulkA"
                        onclick="CD_ATT.bulk('absent')">All Absent</button>
            </div>
            <div class="cd-att-body" id="cdAttBody">
                <div class="cd-att-empty">Loading&hellip;</div>
            </div>
            <div class="cd-att-footer">
                <span class="save-msg" id="cdAttSaveMsg"></span>
                <button type="button" onclick="CD_ATT.close()">Cancel</button>
                <button class="primary" type="button" id="cdAttSaveBtn"
                        onclick="CD_ATT.save()">Save Attendance</button>
            </div>
        </div>

        <!-- TAB 2: AUTO-MARKED -->
        <div id="cdAttTabAuto" style="display:none;">
            <div class="cd-att-body">
                <div id="cdAttAutoList" class="cd-att-auto-list">
                    <div class="cd-att-empty">Loading&hellip;</div>
                </div>
            </div>
            <div class="cd-att-footer">
                <button type="button" onclick="CD_ATT.close()">Close</button>
            </div>
        </div>
    </div>
</div>

<script>
window.CD_ATT = (function () {
    'use strict';
    var SCHEMA = '{{ tenant.schema_name|escapejs }}';
    var CLASS_ID = {{ class_obj.id }};

    // Compute today in the browser's local timezone. The admin's
    // browser and the school are in the same timezone in practice.
    // (The class-detail view does not pass `today` into the template.)
    var TODAY = (function () {
        var d = new Date();
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var dd = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + dd;
    })();

    var students = [];
    var locked = false;
    var autoMarked = false;
    var isHoliday = false;
    var currentPeriod = null;

    function q(s) { return document.querySelector(s); }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
        });
    }

    function csrf() {
        var m = document.querySelector('meta[name="csrf-token"]');
        if (m && m.getAttribute('content')) return m.getAttribute('content');
        var parts = (document.cookie || '').split(';');
        for (var i = 0; i < parts.length; i++) {
            var c = parts[i].trim();
            if (c.indexOf('csrftoken=') === 0) return c.substring(10);
        }
        return '';
    }

    function open() {
        q('#cdAttendanceModal').classList.add('active');
        q('#cdAttDate').value = TODAY;
        q('#cdAttPeriod').value = '';
        q('#cdAttSaveMsg').textContent = '';
        q('#cdAttSaveMsg').className = 'save-msg';
        showTab('mark');
        reload();
    }

    function close() {
        q('#cdAttendanceModal').classList.remove('active');
    }

    function showTab(which) {
        var m = q('#cdAttTabMark'), a = q('#cdAttTabAuto');
        var bm = q('#cdAttTabBtnMark'), ba = q('#cdAttTabBtnAuto');
        if (which === 'mark') {
            m.style.display = 'block'; a.style.display = 'none';
            bm.classList.add('active'); ba.classList.remove('active');
        } else {
            m.style.display = 'none'; a.style.display = 'block';
            bm.classList.remove('active'); ba.classList.add('active');
            loadAutoDates(1);
        }
    }

    function hideBanners() {
        q('#cdAttHolidayBanner').style.display = 'none';
        q('#cdAttLockedBanner').style.display = 'none';
        q('#cdAttAutoBanner').style.display = 'none';
    }

    function setDisabled(d) {
        var r = document.querySelectorAll('#cdAttBody input[type="radio"]');
        for (var i = 0; i < r.length; i++) r[i].disabled = d;
        var bp = q('#cdAttBulkP'), ba = q('#cdAttBulkA');
        if (bp) { bp.disabled = d; bp.style.opacity = d ? '0.5' : '1'; }
        if (ba) { ba.disabled = d; ba.style.opacity = d ? '0.5' : '1'; }
    }

    function unlockForm() {
        locked = false;
        setDisabled(false);
        q('#cdAttLockedBanner').style.display = 'none';
    }

    function reload() {
        var d = q('#cdAttDate').value;
        var p = q('#cdAttPeriod').value;
        currentPeriod = p ? parseInt(p, 10) : null;
        var url = '/portal/' + SCHEMA + '/api/attendance/students/?class_id='
                + CLASS_ID + '&date=' + encodeURIComponent(d);
        if (currentPeriod) url += '&period_order=' + currentPeriod;

        q('#cdAttBody').innerHTML = '<div class="cd-att-empty">Loading&hellip;</div>';
        hideBanners();
        var sb = q('#cdAttSaveBtn');
        if (sb) { sb.disabled = true; sb.style.opacity = '0.5'; }

        fetch(url, { headers: {'X-Requested-With': 'XMLHttpRequest'} })
            .then(function (r) { return r.json(); })
            .then(function (j) {
                if (!j.ok) {
                    q('#cdAttBody').innerHTML =
                        '<div class="cd-att-empty">' + esc(j.error || 'Failed') + '</div>';
                    return;
                }
                if (j.is_holiday) {
                    isHoliday = true;
                    students = [];
                    locked = true;
                    q('#cdAttHolidayText').textContent =
                        'This date is a holiday (' + (j.holiday_reason || 'holiday')
                        + '). You can still mark a previous non-holiday date.';
                    q('#cdAttHolidayBanner').style.display = 'flex';
                    q('#cdAttBody').innerHTML =
                        '<div class="cd-att-empty">No attendance is expected on a holiday.</div>';
                    setDisabled(true);
                    return;
                }
                isHoliday = false;
                students = j.students || [];
                locked = !!j.locked;
                autoMarked = !!j.auto_marked;
                renderStudents();
                setDisabled(locked);
                if (sb) { sb.disabled = false; sb.style.opacity = '1'; }
                if (autoMarked) q('#cdAttAutoBanner').style.display = 'flex';
                if (locked) q('#cdAttLockedBanner').style.display = 'flex';
            })
            .catch(function () {
                q('#cdAttBody').innerHTML =
                    '<div class="cd-att-empty">Network error.</div>';
            });
    }

    function renderStudents() {
        var body = q('#cdAttBody');
        if (!students.length) {
            body.innerHTML = '<div class="cd-att-empty">No active students in this class.</div>';
            return;
        }
        var html = '';
        students.forEach(function (s) {
            var leave = s.on_leave ? '<span class="leave-tag">ON LEAVE</span>' : '';
            var auto = s.is_auto ? '<span class="leave-tag" style="background:#1e40af;">AUTO</span>' : '';
            html += '<div class="cd-att-row">'
                  +   '<div class="roll">' + esc(s.roll_number || '—') + '</div>'
                  +   '<div><div class="student-name">' + esc(s.name) + '</div>'
                  +        '<div class="student-meta">' + esc(s.father_name || '') + leave + auto + '</div></div>'
                  +   '<div class="cd-att-status">'
                  +     radio(s, 'present', 'P')
                  +     radio(s, 'absent', 'A')
                  +     radio(s, 'late', 'L')
                  +     radio(s, 'half_day', 'H')
                  +     radio(s, 'excused', 'Lv')
                  +   '</div>'
                  + '</div>';
        });
        body.innerHTML = html;
    }

    function radio(s, status, label) {
        var id = 'cd_' + s.id + '_' + status;
        var checked = s.status === status ? 'checked' : '';
        return '<input type="radio" name="cd_' + s.id + '" id="' + id
             + '" value="' + status + '" ' + checked + '>'
             + '<label for="' + id + '">' + label + '</label>';
    }

    function bulk(status) {
        if (locked) return;
        students.forEach(function (s) {
            var el = document.getElementById('cd_' + s.id + '_' + status);
            if (el) el.checked = true;
        });
    }

    function save() {
        if (isHoliday) {
            var m0 = q('#cdAttSaveMsg');
            m0.className = 'save-msg err';
            m0.textContent = 'Cannot save on a holiday.';
            return;
        }
        var d = q('#cdAttDate').value;
        var records = students.map(function (s) {
            var el = document.querySelector('input[name="cd_' + s.id + '"]:checked');
            return { student_id: s.id, status: el ? el.value : 'present' };
        });
        var payload = { class_id: CLASS_ID, date: d, records: records };
        if (currentPeriod) payload.period_order = currentPeriod;

        var msg = q('#cdAttSaveMsg');
        msg.className = 'save-msg';
        msg.textContent = 'Saving…';

        fetch('/portal/' + SCHEMA + '/api/attendance/mark/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrf(),
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(payload)
        })
        .then(function (r) { return r.json(); })
        .then(function (j) {
            if (!j.ok) {
                msg.className = 'save-msg err';
                msg.textContent = j.error || 'Save failed.';
                return;
            }
            msg.className = 'save-msg ok';
            msg.textContent = 'Saved ' + j.saved + ' record(s).';
            // Refresh so "locked" / "auto-marked" banners update.
            setTimeout(reload, 400);
        })
        .catch(function () {
            msg.className = 'save-msg err';
            msg.textContent = 'Network error.';
        });
    }

    function loadAutoDates(page) {
        page = page || 1;
        var url = '/portal/' + SCHEMA
            + '/api/attendance/auto-marked-dates/?class_id=' + CLASS_ID
            + '&page=' + page;
        q('#cdAttAutoList').innerHTML = '<div class="cd-att-empty">Loading&hellip;</div>';
        fetch(url, { headers: {'X-Requested-With': 'XMLHttpRequest'} })
            .then(function (r) { return r.json(); })
            .then(function (j) {
                if (!j.ok) {
                    q('#cdAttAutoList').innerHTML =
                        '<div class="cd-att-empty">' + esc(j.error || 'Failed') + '</div>';
                    return;
                }
                var dates = j.dates || [];
                var pg = j.pagination || {};
                var cur = pg.page || 1;
                var totalPages = pg.num_pages || 1;
                var totalRows = pg.total || 0;

                if (!dates.length && totalRows === 0) {
                    q('#cdAttAutoList').innerHTML =
                        '<div class="cd-att-empty">No auto-marked dates yet for this class.</div>';
                    return;
                }

                var html = '';
                dates.forEach(function (d) {
                    html += '<div class="cd-att-auto-row" onclick="CD_ATT.openAutoDate(\'' + esc(d.date) + '\')">'
                          +   '<div class="date-cell">' + esc(d.date)
                          +     '<div class="day-name">' + esc(d.day_name) + '</div>'
                          +   '</div>'
                          +   '<div class="stats">'
                          +     '<span class="stat-pill p">P ' + d.present + '</span>'
                          +     '<span class="stat-pill a">A ' + d.absent + '</span>'
                          +     '<span class="stat-pill l">L ' + d.late + '</span>'
                          +     '<span class="stat-pill e">Lv ' + d.excused + '</span>'
                          +     '<span style="margin-left:.4rem;">Total: ' + d.total + '</span>'
                          +   '</div>'
                          +   '<button type="button" class="open-btn" onclick="event.stopPropagation(); CD_ATT.openAutoDate(\'' + esc(d.date) + '\')">Open</button>'
                          + '</div>';
                });

                html += '<div class="cd-att-auto-pager">';
                if (totalPages > 1) {
                    var prevD = cur <= 1 ? ' disabled' : '';
                    var nextD = cur >= totalPages ? ' disabled' : '';
                    html += '<button type="button" class="pager-btn"' + prevD
                          + ' onclick="CD_ATT.loadAutoDates(' + (cur - 1) + ')">&lsaquo; Prev</button>';
                    html += '<span class="pager-info">Page ' + cur + ' / ' + totalPages
                          + '  &middot;  ' + totalRows + ' date' + (totalRows === 1 ? '' : 's') + '</span>';
                    html += '<button type="button" class="pager-btn"' + nextD
                          + ' onclick="CD_ATT.loadAutoDates(' + (cur + 1) + ')">Next &rsaquo;</button>';
                } else {
                    html += '<span class="pager-info">' + totalRows + ' date'
                          + (totalRows === 1 ? '' : 's') + '</span>';
                }
                html += '</div>';

                q('#cdAttAutoList').innerHTML = html;
            })
            .catch(function () {
                q('#cdAttAutoList').innerHTML =
                    '<div class="cd-att-empty">Network error.</div>';
            });
    }

    function openAutoDate(dateStr) {
        q('#cdAttDate').value = dateStr;
        q('#cdAttPeriod').value = '';
        showTab('mark');
        reload();
    }

    document.addEventListener('DOMContentLoaded', function () {
        var openBtn = document.getElementById('manageAttendanceBtn');
        if (openBtn) openBtn.addEventListener('click', open);
        var closeBtn = document.getElementById('cdAttCloseBtn');
        if (closeBtn) closeBtn.addEventListener('click', close);
        var overlay = document.getElementById('cdAttendanceModal');
        if (overlay) {
            overlay.addEventListener('click', function (e) {
                if (e.target === overlay) close();
            });
        }
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && overlay && overlay.classList.contains('active')) {
                close();
            }
        });
    });

    return {
        open: open,
        close: close,
        showTab: showTab,
        reload: reload,
        unlockForm: unlockForm,
        bulk: bulk,
        save: save,
        openAutoDate: openAutoDate,
        loadAutoDates: loadAutoDates
    };
})();
</script>
<!-- ============ END CLASS_DETAIL_ATTENDANCE_BUTTON_V1 ============ -->
'''


# ====================================================== per-file patch

def patch_template(root, filename, args):
    path = root / "templates" / "tenant" / filename
    content = read_file(path)
    if content is None:
        return False

    if "CLASS_DETAIL_ATTENDANCE_BUTTON_V1" in content:
        log(f"  SKIP (already applied): {filename}")
        return True

    # 1. CSS — insert before the single </style> in extra_head.
    if "</style>" not in content:
        log(f"  WARN: no </style> in {filename}")
        return False
    content = content.replace("</style>", CSS_BLOCK + "\n</style>", 1)

    # 2. Button — insert right after "Manage Periods Timetable".
    if BUTTON_OLD not in content:
        log(f"  WARN: 'Manage Periods Timetable' button not found in {filename}")
        return False
    content = content.replace(BUTTON_OLD, BUTTON_NEW, 1)

    # 3. Modal + JS — insert just before the last {% endblock %}.
    idx = content.rfind("{% endblock %}")
    if idx == -1:
        log(f"  WARN: no closing {{% endblock %}} in {filename}")
        return False
    content = content[:idx] + MODAL_HTML + "\n" + content[idx:]

    return write_file(path, content, args.dry_run,
                      f"{filename} Manage Attendance button")


# ====================================================== MAIN

def main():
    parser = argparse.ArgumentParser(
        description=(
            "CLASS_DETAIL_ATTENDANCE_BUTTON_V1 — add a 'Manage Attendance' "
            "button to the class-detail pages that opens the same "
            "Mark / Auto-Marked modal as the admin attendance dashboard, "
            "scoped to the current class."
        )
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing.")
    parser.add_argument("--verbose", action="store_true",
                        help="Verbose output.")
    parser.add_argument("--target-dir", default=".",
                        help="Project root (default: current directory).")
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not (root / "manage.py").is_file():
        log(f"ERROR: manage.py not found in {root}")
        return 1

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")
    log(f"Patch:  {MARKER}")

    steps = [
        ("Template: single_class_detailed.html",
         lambda r, a: patch_template(r, "single_class_detailed.html", a)),
        ("Template: wing_class_detailed.html",
         lambda r, a: patch_template(r, "wing_class_detailed.html", a)),
    ]

    results = []
    for label, fn in steps:
        log(f"--- {label} ---")
        try:
            ok = fn(root, args)
        except Exception as exc:
            log(f"  EXCEPTION: {exc}")
            ok = False
        results.append((label, ok))

    log("=" * 65)
    for label, ok in results:
        log(f"  {'OK' if ok else 'FAIL'}  {label}")

    all_ok = all(ok for _, ok in results)
    if all_ok:
        log("All steps completed successfully.")
        if args.dry_run:
            log("Re-run without --dry-run to apply.")
        else:
            log("Hard-refresh the class page (Ctrl+Shift+R) to pick up the new JS.")
        return 0
    log("One or more steps failed. See messages above.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
