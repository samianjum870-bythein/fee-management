#!/usr/bin/env python3
"""
axis_patcher.py
===============

Adds a "Manage Periods Timetable" button to the class detailed page
(/portal/<schema>/my-classes/<id>/). Clicking it opens a modal that renders
the assigned PeriodsTimetable for that specific class - exactly like the
card on /portal/<schema>/timetable/periods/, including:

    - Same periods grid (P1, P2, ... with break column)
    - "See Timings" toggle button (identical behaviour)
    - "Edit" button that jumps to the periods page with ?highlight=<id>

Handles both wing_school and single_small_school class-detail templates.

Idempotent. Safe to run multiple times.

Usage:
    python3 axis_patcher.py [--dry-run] [--verbose] [--target-dir=.]
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


PATCH_MARKER = "MANAGE_PERIODS_TIMETABLE_v1"


def _log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


def _read(path):
    try:
        return path.read_text(encoding='utf-8')
    except Exception as e:
        _log(f"ERROR reading {path}: {e}")
        return None


def _write(path, content, dry_run, verbose):
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
# CSS added to both class-detailed templates (identical to the
# timetable_periods.html styling, scoped inside the modal wrapper).
# =====================================================================
MODAL_CSS = r'''
    /* ============ MANAGE PERIODS TIMETABLE MODAL (MANAGE_PERIODS_TIMETABLE_v1) ============ */
    .btn-manage-timetable {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.6rem 1.2rem;
        border-radius: 0.75rem;
        border: none;
        background: linear-gradient(135deg, var(--primary), var(--primary-dark));
        color: #fff;
        font-weight: 700;
        font-size: 0.9rem;
        cursor: pointer;
        transition: transform 0.2s, box-shadow 0.2s;
        box-shadow: 0 6px 18px -6px rgba(59,130,246,0.55);
        font-family: inherit;
    }
    .btn-manage-timetable:hover {
        transform: translateY(-1px);
        box-shadow: 0 10px 22px -6px rgba(59,130,246,0.65);
    }
    .btn-manage-timetable:active { transform: translateY(0); }

    .timetable-modal-overlay {
        position: fixed;
        inset: 0;
        background: rgba(15,23,42,0.65);
        z-index: 100000;
        display: none;
        align-items: flex-start;
        justify-content: center;
        padding: 2rem 1rem;
        overflow-y: auto;
        backdrop-filter: blur(6px);
    }
    .timetable-modal-overlay.active { display: flex; }
    .timetable-modal-content {
        background: var(--surface, #fff);
        border-radius: 1.1rem;
        padding: 1.5rem;
        max-width: 1200px;
        width: 100%;
        box-shadow: 0 25px 70px rgba(0,0,0,0.45);
        border: 1px solid var(--border);
        animation: ttModalIn 0.18s ease;
    }
    @keyframes ttModalIn {
        from { transform: translateY(8px) scale(0.98); opacity: 0; }
        to   { transform: translateY(0) scale(1); opacity: 1; }
    }
    .timetable-modal-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 1rem;
        padding-bottom: 0.75rem;
        border-bottom: 1px solid var(--border);
    }
    .timetable-modal-header h2 {
        margin: 0;
        font-size: 1.2rem;
        font-weight: 700;
    }
    .timetable-modal-close {
        background: none;
        border: none;
        font-size: 1.8rem;
        line-height: 1;
        color: var(--muted);
        cursor: pointer;
        padding: 0 0.3rem;
        transition: 0.2s;
    }
    .timetable-modal-close:hover {
        color: var(--text);
        transform: rotate(90deg);
    }
    .tt-empty {
        text-align: center;
        padding: 3rem 1rem;
        color: var(--muted);
        font-size: 0.95rem;
        line-height: 1.6;
    }

    /* Timetable grid inside the modal - matches timetable_periods.html */
    .timetable-modal-content .tt-card {
        background: var(--surface);
        border-radius: var(--radius);
        border: 1px solid var(--border);
        padding: 1rem;
        box-shadow: var(--shadow-sm);
    }
    .timetable-modal-content .tt-card-header {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding-bottom: 0.75rem;
        border-bottom: 1px solid var(--border);
        margin-bottom: 1rem;
        flex-wrap: wrap;
        justify-content: space-between;
    }
    .timetable-modal-content .tt-card-header h2 {
        margin: 0;
        font-size: 1.15rem;
        font-weight: 700;
    }
    .timetable-modal-content .tt-card-header .tt-meta {
        display: block;
        font-size: 0.82rem;
        color: var(--muted);
        margin-top: 0.15rem;
    }
    .timetable-modal-content .tt-card-actions {
        display: flex;
        gap: 0.5rem;
        flex-wrap: wrap;
    }
    .timetable-modal-content .btn-sm {
        padding: 0.3rem 0.7rem;
        font-size: 0.78rem;
        border-radius: 2rem;
        font-weight: 600;
        border: none;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        gap: 0.3rem;
        font-family: inherit;
        transition: 0.15s;
    }
    .timetable-modal-content .btn-secondary {
        background: var(--surface-alt);
        color: var(--text);
        border: 1px solid var(--border);
    }
    .timetable-modal-content .btn-secondary:hover {
        border-color: var(--primary);
        color: var(--primary);
    }
    .timetable-modal-content .btn-warning {
        background: #f59e0b;
        color: #fff;
    }
    .timetable-modal-content .btn-warning:hover { background: #d97706; }

    .timetable-grid {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.9rem;
    }
    .timetable-grid th, .timetable-grid td {
        border: 1px solid var(--border);
        padding: 0.5rem 0.35rem;
        text-align: center;
        vertical-align: middle;
    }
    .timetable-grid th {
        background: var(--surface-alt);
        font-weight: 600;
    }
    .timetable-grid .day-label {
        font-weight: 600;
        background: var(--surface-alt);
        white-space: nowrap;
    }
    .timetable-grid .empty-period { color: var(--muted); }
    .timetable-grid .break-col {
        background: #fef3c7;
        border: 2px dashed #f59e0b;
    }
    .timetable-grid .break-cell strong { display: block; color: #92400e; }
    .timetable-grid .period-cell strong { display: block; }
    .timetable-grid .period-cell small {
        display: block;
        color: var(--muted);
        font-size: 0.72rem;
        margin-top: 2px;
    }
    .timing-day-block { margin-bottom: 1.5rem; }
    .timing-day-block h3 {
        margin: 0 0 0.5rem 0;
        font-size: 1.05rem;
    }
    .timing-detail-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.9rem;
    }
    .timing-detail-table th, .timing-detail-table td {
        border: 1px solid var(--border);
        padding: 0.4rem 0.6rem;
        text-align: left;
    }
    .timing-detail-table th {
        background: var(--surface-alt);
        font-weight: 600;
    }
    .timing-detail-table .break-row td {
        background: #fef3c7;
        color: #92400e;
        font-weight: 600;
    }
    /* ============ END MANAGE_PERIODS_TIMETABLE_v1 CSS ============ */
'''


# =====================================================================
# Body block (modal HTML + JS) injected before the final {% endblock %}
# in both class-detailed templates.
# =====================================================================
MODAL_BODY = r'''
<!-- ============ Manage Periods Timetable Modal (MANAGE_PERIODS_TIMETABLE_v1) ============ -->
<div id="timetableModal" class="timetable-modal-overlay">
    <div class="timetable-modal-content">
        <div class="timetable-modal-header">
            <h2>Periods Timetable &mdash; {{ class_display_name }}</h2>
            <button type="button" class="timetable-modal-close" id="closeTimetableModal" aria-label="Close">&times;</button>
        </div>
        <div id="classTimetableContainer">
            <div class="tt-empty">Loading&hellip;</div>
        </div>
    </div>
</div>

<script>
(function () {
    'use strict';

    var SCHEMA = '{{ tenant.schema_name|escapejs }}';
    var CLASS_DISPLAY_NAME = '{{ class_display_name|escapejs }}';
    var ASSIGNED_TIMETABLE = {{ assigned_timetable_json|safe }} || null;

    function esc(s) {
        if (s === null || s === undefined) return '';
        return String(s).replace(/[&<>"']/g, function (c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
        });
    }

    function buildPeriodsTableHTML(tt) {
        var maxPeriods = 0;
        (tt.days || []).forEach(function (d) {
            if (d.periods_count > maxPeriods) maxPeriods = d.periods_count;
        });
        var maxBreakAfter = 0;
        (tt.days || []).forEach(function (d) {
            if (d.break_after && d.break_after > maxBreakAfter) maxBreakAfter = d.break_after;
        });
        var hasBreak = maxBreakAfter > 0 && tt.break_duration > 0;

        var thead = '<thead><tr><th>Day</th>';
        for (var i = 1; i <= maxPeriods; i++) {
            thead += '<th>P' + i + '</th>';
            if (hasBreak && i === maxBreakAfter) thead += '<th class="break-col">Break</th>';
        }
        thead += '</tr></thead>';

        var tbody = '<tbody>';
        (tt.days || []).forEach(function (d) {
            tbody += '<tr>';
            tbody += '<td class="day-label">' + esc(d.day_label) + '</td>';

            var byOrder = {};
            var breakPeriod = null;
            (d.periods || []).forEach(function (p) {
                if (p.is_break) breakPeriod = p;
                else byOrder[p.order] = p;
            });

            for (var i = 1; i <= maxPeriods; i++) {
                var p = byOrder[i];
                if (p) {
                    tbody += '<td><div class="period-cell"><strong>P' + i + '</strong><small>' +
                             esc(p.start) + '-' + esc(p.end) + '</small></div></td>';
                } else {
                    tbody += '<td class="empty-period">&mdash;</td>';
                }
                if (hasBreak && i === maxBreakAfter) {
                    if (breakPeriod && d.break_after === i) {
                        tbody += '<td class="break-col"><div class="break-cell"><strong>Break</strong><small>' +
                                 esc(breakPeriod.start) + '-' + esc(breakPeriod.end) + '</small></div></td>';
                    } else {
                        tbody += '<td class="break-col" style="opacity:0.35;">&mdash;</td>';
                    }
                }
            }
            tbody += '</tr>';
        });
        tbody += '</tbody>';
        return thead + tbody;
    }

    function buildTimingsHTML(tt) {
        var html = '';
        (tt.days || []).forEach(function (d) {
            html += '<div class="timing-day-block">';
            html += '<h3>' + esc(d.day_label) +
                    ' <span style="color:var(--muted); font-size:0.85rem; font-weight:400;">(' +
                    esc(d.start) + ' - ' + esc(d.end) + ' &middot; ' +
                    d.periods_count + ' periods)</span></h3>';
            html += '<table class="timing-detail-table"><thead><tr>' +
                    '<th style="width:120px;">Period</th>' +
                    '<th>Start</th><th>End</th><th>Duration</th></tr></thead><tbody>';
            (d.periods || []).forEach(function (p) {
                if (p.is_break) {
                    html += '<tr class="break-row"><td>Break</td><td>' + esc(p.start) +
                            '</td><td>' + esc(p.end) + '</td><td>' + p.duration + ' min</td></tr>';
                } else {
                    html += '<tr><td>P' + p.order + '</td><td>' + esc(p.start) +
                            '</td><td>' + esc(p.end) + '</td><td>' + p.duration + ' min</td></tr>';
                }
            });
            html += '</tbody></table></div>';
        });
        return html;
    }

    function renderClassTimetable() {
        var container = document.getElementById('classTimetableContainer');
        if (!container) return;

        if (!ASSIGNED_TIMETABLE || !ASSIGNED_TIMETABLE.id) {
            container.innerHTML =
                '<div class="tt-empty">' +
                    '<strong>No periods timetable is assigned to this class yet.</strong><br>' +
                    'Please assign one from the Timetable &rarr; Assign to Classes page.' +
                '</div>';
            return;
        }

        var tt = ASSIGNED_TIMETABLE;

        var card = document.createElement('div');
        card.className = 'tt-card';

        var header = document.createElement('div');
        header.className = 'tt-card-header';
        header.innerHTML =
            '<div>' +
                '<h2>' + esc(tt.title) + '</h2>' +
                '<span class="tt-meta">' +
                    'Label: <strong>' + esc(tt.label) + '</strong>' +
                    (tt.break_duration ? ' &nbsp;|&nbsp; Break: ' + tt.break_duration + ' min' : ' &nbsp;|&nbsp; No break') +
                '</span>' +
            '</div>' +
            '<div class="tt-card-actions">' +
                '<button type="button" class="btn-sm btn-secondary toggle-view-btn">See Timings</button>' +
                '<button type="button" class="btn-sm btn-warning edit-btn">&#9998; Edit</button>' +
            '</div>';
        card.appendChild(header);

        var periodsView = document.createElement('div');
        periodsView.className = 'periods-view';
        periodsView.style.overflowX = 'auto';
        var periodsTable = document.createElement('table');
        periodsTable.className = 'timetable-grid';
        periodsTable.innerHTML = buildPeriodsTableHTML(tt);
        periodsView.appendChild(periodsTable);
        card.appendChild(periodsView);

        var timingsView = document.createElement('div');
        timingsView.className = 'timings-view';
        timingsView.style.display = 'none';
        timingsView.innerHTML = buildTimingsHTML(tt);
        card.appendChild(timingsView);

        // "See Timings" toggle - identical behaviour to timetable_periods.html
        var toggleBtn = header.querySelector('.toggle-view-btn');
        toggleBtn.addEventListener('click', function () {
            var showTimings = timingsView.style.display === 'none';
            timingsView.style.display = showTimings ? 'block' : 'none';
            periodsView.style.display = showTimings ? 'none' : 'block';
            toggleBtn.textContent = showTimings ? 'See Periods Timetable' : 'See Timings';
        });

        // "Edit" button - jumps to periods page with highlight (same UX)
        header.querySelector('.edit-btn').addEventListener('click', function () {
            window.location.href = '/portal/' + SCHEMA + '/timetable/periods/?highlight=' + tt.id;
        });

        container.innerHTML = '';
        container.appendChild(card);
    }

    function openTimetableModal() {
        var overlay = document.getElementById('timetableModal');
        if (!overlay) return;
        overlay.classList.add('active');
    }

    function closeTimetableModal() {
        var overlay = document.getElementById('timetableModal');
        if (!overlay) return;
        overlay.classList.remove('active');
    }

    document.addEventListener('DOMContentLoaded', function () {
        var openBtn = document.getElementById('manageTimetableBtn');
        var closeBtn = document.getElementById('closeTimetableModal');
        var overlay = document.getElementById('timetableModal');

        if (openBtn) openBtn.addEventListener('click', function () {
            renderClassTimetable();
            openTimetableModal();
        });
        if (closeBtn) closeBtn.addEventListener('click', closeTimetableModal);
        if (overlay) overlay.addEventListener('click', function (e) {
            if (e.target === overlay) closeTimetableModal();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && overlay && overlay.classList.contains('active')) {
                closeTimetableModal();
            }
        });
    });
})();
</script>
<!-- ============ END MANAGE_PERIODS_TIMETABLE_v1 ============ -->
'''


# =====================================================================
# View patch helper
# =====================================================================
IMPORT_ANCHOR = "from ..models import SchoolClass, Student, ClassSubject"
IMPORT_REPLACEMENT = (
    "from ..models import SchoolClass, Student, ClassSubject, "
    "ClassTimetableAssignment, PeriodsTimetable\n"
    "import json"
)

CONTEXT_ANCHOR_TEMPLATE = (
    "        display_name = get_class_display_name(school_class, tenant.tenant_type)\n"
    "\n"
    "    return {{\n"
    "        'tenant': tenant,\n"
    "        'class_obj': school_class,\n"
    "        'class_display_name': display_name,"
)

CONTEXT_REPLACEMENT_TEMPLATE = (
    "        display_name = get_class_display_name(school_class, tenant.tenant_type)\n"
    "\n"
    "        # MANAGE_PERIODS_TIMETABLE_v1: assigned periods timetable\n"
    "        try:\n"
    "            _tt_assignment = ClassTimetableAssignment.objects.select_related('timetable').filter(school_class=school_class).first()\n"
    "        except Exception:\n"
    "            _tt_assignment = None\n"
    "        assigned_timetable = None\n"
    "        if _tt_assignment and _tt_assignment.timetable:\n"
    "            _tt = _tt_assignment.timetable\n"
    "            assigned_timetable = {{\n"
    "                'id': _tt.id,\n"
    "                'title': _tt.title,\n"
    "                'label': _tt.label or '',\n"
    "                'break_duration': _tt.break_duration or 0,\n"
    "                'days': _tt.days or [],\n"
    "            }}\n"
    "\n"
    "    return {{\n"
    "        'tenant': tenant,\n"
    "        'class_obj': school_class,\n"
    "        'class_display_name': display_name,\n"
    "        'assigned_timetable': assigned_timetable,\n"
    "        'assigned_timetable_json': json.dumps(assigned_timetable or {{}}),"
)


def _patch_view(view_path, dry_run, verbose):
    _log(f"Patching view: {view_path}")
    content = _read(view_path)
    if content is None:
        return False

    if 'MANAGE_PERIODS_TIMETABLE_v1' in content:
        _log("  - already patched, skipping")
        return True

    changed = False

    # 1) imports
    if 'ClassTimetableAssignment, PeriodsTimetable' in content:
        _log("  - imports already present")
    elif IMPORT_ANCHOR in content:
        content = content.replace(IMPORT_ANCHOR, IMPORT_REPLACEMENT, 1)
        _log("  + added ClassTimetableAssignment / PeriodsTimetable / json imports")
        changed = True
    else:
        _log("  WARN: could not find import anchor")

    # 2) context additions
    anchor = CONTEXT_ANCHOR_TEMPLATE.replace('{{', '{').replace('}}', '}')
    replacement = CONTEXT_REPLACEMENT_TEMPLATE.replace('{{', '{').replace('}}', '}')
    if "'assigned_timetable_json': json.dumps" in content:
        _log("  - context already contains assigned_timetable")
    elif anchor in content:
        content = content.replace(anchor, replacement, 1)
        _log("  + injected assigned_timetable into context")
        changed = True
    else:
        _log("  WARN: could not find context anchor")

    if not changed:
        _log("  no changes needed")
        return True
    return _write(view_path, content, dry_run, verbose)


# =====================================================================
# Template patch helper
# =====================================================================
CSS_ANCHOR = "        .data-table th, .data-table td { padding: 0.6rem 0.7rem; font-size: 0.8rem; }\n    }\n</style>"

CSS_REPLACEMENT = (
    "        .data-table th, .data-table td { padding: 0.6rem 0.7rem; font-size: 0.8rem; }\n"
    "    }\n"
    + MODAL_CSS +
    "</style>"
)

HEADER_ANCHOR = (
    "<div class=\"page-header\">\n"
    "    <h1 class=\"page-title\">{{ class_display_name }}</h1>\n"
    "    <p class=\"page-desc\">Class details, teachers, and students</p>\n"
    "</div>"
)

HEADER_REPLACEMENT = (
    "<div class=\"page-header\" style=\"display:flex; justify-content:space-between; "
    "align-items:flex-start; flex-wrap:wrap; gap:1rem;\">\n"
    "    <div>\n"
    "        <h1 class=\"page-title\">{{ class_display_name }}</h1>\n"
    "        <p class=\"page-desc\">Class details, teachers, and students</p>\n"
    "    </div>\n"
    "    <div>\n"
    "        <button type=\"button\" class=\"btn-manage-timetable\" id=\"manageTimetableBtn\">\n"
    "            &#128197; Manage Periods Timetable\n"
    "        </button>\n"
    "    </div>\n"
    "</div>"
)

# Match the very last section-card close + {% endblock %} at the bottom.
BODY_TAIL_ANCHOR = (
    "    {% else %}\n"
    "    <div class=\"section-body\"><p class=\"empty-inline\">No active or suspended students in this class.</p></div>\n"
    "    {% endif %}\n"
    "</div>\n"
    "{% endblock %}"
)

BODY_TAIL_REPLACEMENT = (
    "    {% else %}\n"
    "    <div class=\"section-body\"><p class=\"empty-inline\">No active or suspended students in this class.</p></div>\n"
    "    {% endif %}\n"
    "</div>\n"
    + MODAL_BODY + "\n"
    "{% endblock %}"
)


def _patch_template(template_path, dry_run, verbose):
    _log(f"Patching template: {template_path}")
    content = _read(template_path)
    if content is None:
        return False

    if 'MANAGE_PERIODS_TIMETABLE_v1' in content:
        _log("  - already patched, skipping")
        return True

    changed = False

    # 1) CSS
    if '.btn-manage-timetable' in content:
        _log("  - CSS already present")
    elif CSS_ANCHOR in content:
        content = content.replace(CSS_ANCHOR, CSS_REPLACEMENT, 1)
        _log("  + injected modal CSS")
        changed = True
    else:
        _log("  WARN: could not find CSS anchor")

    # 2) header button
    if 'id="manageTimetableBtn"' in content:
        _log("  - header button already present")
    elif HEADER_ANCHOR in content:
        content = content.replace(HEADER_ANCHOR, HEADER_REPLACEMENT, 1)
        _log("  + injected Manage Periods Timetable button")
        changed = True
    else:
        _log("  WARN: could not find page-header anchor")

    # 3) modal + JS
    if 'timetableModal' in content:
        _log("  - modal already present")
    elif BODY_TAIL_ANCHOR in content:
        content = content.replace(BODY_TAIL_ANCHOR, BODY_TAIL_REPLACEMENT, 1)
        _log("  + injected timetable modal + JS")
        changed = True
    else:
        _log("  WARN: could not find body-tail anchor ({% endblock %} at bottom)")

    if not changed:
        _log("  no changes needed")
        return True
    return _write(template_path, content, dry_run, verbose)


# =====================================================================
# main
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "Add 'Manage Periods Timetable' button + modal to the class "
            "detailed page, showing that class's assigned PeriodsTimetable."
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

    # ---- Views ----
    print('-' * 60)
    _log("STEP 1: Patch wing_class_detailed.py")
    ok &= _patch_view(
        target / 'axis_saas' / 'views' / 'wing_class_detailed.py',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 2: Patch single_class_detailed.py")
    ok &= _patch_view(
        target / 'axis_saas' / 'views' / 'single_class_detailed.py',
        args.dry_run, args.verbose,
    )

    # ---- Templates ----
    print('-' * 60)
    _log("STEP 3: Patch templates/tenant/wing_class_detailed.html")
    ok &= _patch_template(
        target / 'templates' / 'tenant' / 'wing_class_detailed.html',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    _log("STEP 4: Patch templates/tenant/single_class_detailed.html")
    ok &= _patch_template(
        target / 'templates' / 'tenant' / 'single_class_detailed.html',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    if ok:
        _log("DONE.")
        if not args.dry_run:
            _log("")
            _log("NEXT STEPS:")
            _log("  1. Restart Django dev server (no DB migration required):")
            _log("       python3 manage.py runserver")
            _log("")
            _log("  2. Open /portal/<schema>/my-classes/<id>/")
            _log("     - You should see a 'Manage Periods Timetable' button")
            _log("       in the top-right of the page header.")
            _log("     - Clicking it opens the class's assigned PeriodsTimetable")
            _log("       in the same card layout as the periods page, with")
            _log("       'See Timings' and 'Edit' buttons.")
            _log("     - 'Edit' jumps to /portal/<schema>/timetable/periods/")
            _log("       with ?highlight=<id> so the admin can edit it.")
        return 0
    _log("FAILED: one or more steps did not complete.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
