#!/usr/bin/env python3
"""
axis_patcher.py
===============

Apply the PER_DAY_BREAK fix to the "Assign Periods to Teachers" grid.

Problem
-------
`templates/tenant/timetable_assign_teachers.html` renders the per-class
period-assignment grid with a single fixed `Break` column placed at the
MAXIMUM `break_after` across all days. Days whose break is at an earlier
or later period than the max show an em-dash in the Break column instead
of their own break — exactly the bug that was fixed for the Periods
Timetable (PER_DAY_BREAK_V1). This patcher applies the same fix here.

Fix (PER_DAY_BREAK_V2)
----------------------
Replace the fixed-column <table> layout in `renderGrid()` with a per-row
flex layout (`.assign-grid-rows` / `.assign-row` / `.assign-cell`).
Each day becomes its own row of cells, and the Break cell is inserted
right after the period index equal to THAT day's `break_after`.

All existing functionality is preserved:
  * <select class="cell-subject"> period assignment
  * teacher-hint span with `has-teacher` / `has-conflict`
  * `has-empty` red highlight on unassigned cells
  * conflict detection / save flow (they key off `sel.parentNode`)

Usage
-----
    python axis_patcher.py --dry-run --verbose
    python axis_patcher.py --target-dir /path/to/project
    python axis_patcher.py                       # apply in-place
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------- log ----
def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ------------------------------------------------------------ constants ---
MARKER = "PER_DAY_BREAK_V2"
TARGET_REL_PATH = Path("templates") / "tenant" / "timetable_assign_teachers.html"

# CSS injected just before </style>
NEW_CSS = """    /* ===== PER_DAY_BREAK_V2: per-row break placement in the assign grid =====
       Each day's break is rendered at that day's own break_after position
       instead of a single fixed Break column that only matched the maximum
       break_after across all days. */
    .assign-grid-rows {
        display: flex;
        flex-direction: column;
        border: 1px solid var(--border);
        border-radius: 0.5rem;
        overflow: hidden;
        background: var(--surface);
        font-size: 0.85rem;
    }
    .assign-grid-rows .assign-row {
        display: flex;
        align-items: stretch;
        border-bottom: 1px solid var(--border);
    }
    .assign-grid-rows .assign-row:last-child { border-bottom: none; }
    .assign-grid-rows .assign-cell {
        flex: 1 1 0;
        padding: 0.4rem;
        text-align: center;
        border-right: 1px solid var(--border);
        min-width: 110px;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        align-items: stretch;
        justify-content: center;
    }
    .assign-grid-rows .assign-cell:last-child { border-right: none; }
    .assign-grid-rows .assign-cell.day-label {
        flex: 0 0 auto;
        width: 100px;
        min-width: 100px;
        font-weight: 600;
        background: var(--surface-alt);
        white-space: nowrap;
        text-align: left;
        align-items: flex-start;
        justify-content: center;
        padding: 0.5rem 0.75rem;
    }
    .assign-grid-rows .assign-cell.empty-period {
        color: var(--muted);
        align-items: center;
    }
    .assign-grid-rows .assign-cell select {
        width: 100%;
        padding: 0.35rem;
        border-radius: 0.4rem;
        border: 1px solid var(--border);
        background: var(--surface);
        color: var(--text);
        font-size: 0.8rem;
        cursor: pointer;
        box-sizing: border-box;
    }
    .assign-grid-rows .assign-cell select:focus {
        outline: none;
        border-color: var(--primary);
        box-shadow: 0 0 0 2px rgba(59,130,246,0.15);
    }
    .assign-grid-rows .assign-cell .teacher-hint {
        display: block;
        font-size: 0.68rem;
        color: var(--muted);
        margin-top: 0.25rem;
        min-height: 0.85rem;
        line-height: 1.1;
        text-align: center;
    }
    .assign-grid-rows .assign-cell .teacher-hint.has-teacher {
        color: var(--primary);
        font-weight: 600;
    }
    .assign-grid-rows .assign-cell .teacher-hint.has-conflict {
        color: #dc2626;
        font-weight: 700;
    }
    .assign-grid-rows .assign-cell.has-empty {
        background: #fef2f2;
        box-shadow: inset 0 0 0 2px #fca5a5;
    }
    .assign-grid-rows .assign-cell.has-conflict {
        background: #fef2f2;
        box-shadow: inset 0 0 0 2px #fca5a5;
    }
    .assign-grid-rows .assign-cell.break-col {
        flex: 0 0 auto;
        width: 70px;
        min-width: 70px;
        background: #fef3c7;
        color: #92400e;
        font-weight: 600;
        padding: 0.4rem 0.35rem;
        align-items: center;
        justify-content: center;
    }
    /* ===== END PER_DAY_BREAK_V2 ===== */
"""


# New JS block that replaces the table-building portion of renderGrid().
NEW_JS_BLOCK = """        var maxPeriods = 0;
        days.forEach(function (d) {
            var pc = parseInt(d.periods_count, 10) || 0;
            if (pc > maxPeriods) maxPeriods = pc;
        });

        /* PER_DAY_BREAK_V2: render each day's break at that day's own
           break_after position instead of a single fixed Break column. */
        var html = '<div style="overflow-x:auto;">';
        html += '<div class="assign-grid-rows">';

        CURRENT_SELECTS = [];

        days.forEach(function (day) {
            var dayNum = day.day_of_week;
            var dayLabel = day.day_label || ('Day ' + dayNum);
            var dayPeriods = day.periods_count || 0;
            var dayBreakAfter = parseInt(day.break_after, 10) || 0;

            html += '<div class="assign-row">';
            html += '<div class="assign-cell day-label">' + esc(dayLabel) + '</div>';

            for (var i = 1; i <= maxPeriods; i++) {
                if (i > dayPeriods) {
                    html += '<div class="assign-cell empty-period">\\u2014</div>';
                } else {
                    var key = dayNum + '|' + i;
                    var selVal = '';
                    if (existing[key] && existing[key].subject_id) {
                        selVal = String(existing[key].subject_id);
                    }
                    var opts = '<option value="">\\u2014 Select \\u2014</option>';
                    var teacherForSel = '';
                    subjects.forEach(function (s) {
                        var selected = (String(s.subject_id) === selVal) ? ' selected' : '';
                        opts += '<option value="' + s.subject_id + '"' + selected + '>' +
                                esc(s.subject_name) + '</option>';
                        if (String(s.subject_id) === selVal) {
                            teacherForSel = s.teacher_name || '';
                        }
                    });

                    // INLINE_TIMETABLE_ASSIGN_V1: mark a cell as empty
                    // (red) when no subject-with-teacher is assigned.
                    var _isEmptyCell = !teacherForSel;
                    html += '<div class="assign-cell' + (_isEmptyCell ? ' has-empty' : '') + '">' +
                        '<select class="cell-subject" data-day="' + dayNum + '" data-order="' + i + '">' +
                            opts +
                        '</select>' +
                        '<span class="teacher-hint' + (teacherForSel ? ' has-teacher' : '') + '">' +
                            (teacherForSel ? '(' + esc(teacherForSel) + ')' : '') +
                        '</span>' +
                    '</div>';
                }

                if (dayBreakAfter === i) {
                    html += '<div class="assign-cell break-col">Break</div>';
                }
            }
            html += '</div>';
        });
        html += '</div></div>';

        html += '<p class="text-muted" style="font-size:0.8rem; margin-top:0.75rem;">' +
                'Only subjects with an assigned teacher in this class can be picked. ' +
                'The teacher name is shown automatically.</p>';

        modalBody.innerHTML = html;
"""


START_MARKER = "        var maxPeriods = 0;\n        var maxBreakAfter = 0;\n"
END_MARKER   = "        modalBody.innerHTML = html;\n"


# ---------------------------------------------------------------- patch ---
def patch_file(path, dry_run, verbose):
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        log(f"ERROR reading {path}: {exc}")
        return False

    if MARKER in content:
        log(f"SKIP (already patched): {path}")
        return True

    original = content

    # -------- 1) inject CSS --------
    if "</style>" not in content:
        log(f"WARN: no </style> in {path}; skipping CSS injection")
    else:
        content = content.replace("</style>", NEW_CSS + "</style>", 1)
        if verbose:
            log(f"Inserted {MARKER} CSS in {path}")

    # -------- 2) replace renderGrid() body --------
    start_idx = content.find(START_MARKER)
    if start_idx == -1:
        log(f"ERROR: start marker not found in {path}")
        return False

    end_idx = content.find(END_MARKER, start_idx)
    if end_idx == -1:
        log(f"ERROR: end marker not found after start in {path}")
        return False

    end_idx += len(END_MARKER)

    old_block = content[start_idx:end_idx]
    if "hasBreak" not in old_block and "assign-grid" not in old_block:
        log(f"ERROR: matched block does not look like the expected renderGrid body")
        return False

    content = content[:start_idx] + NEW_JS_BLOCK + content[end_idx:]
    if verbose:
        log(f"Replaced renderGrid() table builder in {path} "
            f"({len(old_block)} -> {len(NEW_JS_BLOCK)} bytes)")

    if content == original:
        log(f"NO CHANGE: {path}")
        return True

    if dry_run:
        log(f"DRY-RUN: would patch {path} "
            f"({len(original)} -> {len(content)} bytes)")
        return True

    try:
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        log(f"ERROR writing {path}: {exc}")
        return False

    log(f"PATCHED: {path}")
    return True


# ---------------------------------------------------------------- main ----
def main():
    parser = argparse.ArgumentParser(
        description=(
            "Apply the PER_DAY_BREAK_V2 fix to "
            "templates/tenant/timetable_assign_teachers.html so that each "
            "day's break is rendered at that day's own break_after position."
        )
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing anything.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-file detail.")
    parser.add_argument("--target-dir", default=".",
                        help="Project root directory (default: current dir).")
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    target = root / TARGET_REL_PATH

    if not target.is_file():
        log(f"ERROR: file not found: {target}")
        return 1

    log(f"Target: {target}")
    ok = patch_file(target, args.dry_run, args.verbose)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
