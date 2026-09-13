#!/usr/bin/env python3
"""
axis_patcher.py
===============
INLINE_TEACHER_CONFLICT_V1
--------------------------
Extends the TEACHER_TIMETABLE_CONFLICT validation to the class-detailed
pages' inline "Manage Subjects" grid (used by both single and wing
school class pages, opened via "📅 Manage Periods Timetable" ->
"Manage and Edit Subjects").

Where it applies:
  * templates/tenant/single_class_detailed.html
  * templates/tenant/wing_class_detailed.html

Each file gets:
  1. New CSS for `.teacher-hint.has-conflict` and `td.has-conflict`.
  2. Rewritten `_buildInlineSubjectsGrid()` wire-up that:
       - builds subject -> teacher_id map
       - builds subject -> teacher_name map
       - stores `teacher_busy` on the container
       - refreshes each cell's conflict hint on load AND on change
       - shows "⚠ Ayesha Khan busy in 10-A" (FULL teacher name)
  3. Rewritten `_saveInlineSubjectsGrid()` precheck that refuses to
     POST when a chosen subject's teacher is already booked in
     another class at the same (day, period), and alerts with a
     human-readable list. Server still re-validates with HTTP 409.

The standalone page templates/tenant/timetable_assign_teachers.html
already has these patches (V2 + FULLNAME) and is left untouched.

Idempotent via marker comment: INLINE_TEACHER_CONFLICT_V1_DONE

Run:
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


def _ts():
    return datetime.now().strftime('%H:%M:%S')


class Log:
    def __init__(self, verbose=False, dry_run=False):
        self.verbose = verbose
        self.dry_run = dry_run
        self.changes = 0
        self.errors = 0

    def info(self, msg):
        print(f"[{_ts()}] {msg}")

    def debug(self, msg):
        if self.verbose:
            print(f"[{_ts()}]   . {msg}")

    def ok(self, msg):
        self.changes += 1
        print(f"[{_ts()}] {'DRY ' if self.dry_run else 'OK  '}{msg}")

    def err(self, msg):
        self.errors += 1
        print(f"[{_ts()}] ERR {msg}")

    def warn(self, msg):
        print(f"[{_ts()}] WARN {msg}")


def read_file(path: Path, log: Log):
    if not path.exists():
        log.err(f"file not found: {path}")
        return None
    try:
        return path.read_text(encoding='utf-8')
    except Exception as exc:
        log.err(f"cannot read {path}: {exc}")
        return None


def write_file(path: Path, content: str, log: Log):
    if log.dry_run:
        log.ok(f"would write {path} ({len(content)} bytes)")
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        log.ok(f"wrote {path} ({len(content)} bytes)")
        return True
    except Exception as exc:
        log.err(f"cannot write {path}: {exc}")
        return False


def replace_exact(path: Path, old: str, new: str, log: Log, label: str) -> bool:
    content = read_file(path, log)
    if content is None:
        return False
    if old not in content:
        log.err(f"{label}: anchor not found in {path}")
        return False
    if content.count(old) > 1:
        log.err(f"{label}: anchor not unique ({content.count(old)} hits) in {path}")
        return False
    return write_file(path, content.replace(old, new, 1), log)


# ======================================================================
# Patch content
# ======================================================================

CONFLICT_CSS = """    /* ============ INLINE_TEACHER_CONFLICT_V1: conflict styling ============ */
    .timetable-modal-content .assign-grid .teacher-hint.has-conflict {
        color: #dc2626;
        font-weight: 700;
    }
    .timetable-modal-content .assign-grid td.has-conflict {
        background: #fef2f2;
        box-shadow: inset 0 0 0 2px #fca5a5;
    }
    /* ============ END INLINE_TEACHER_CONFLICT_V1 ============ */
"""

CSS_ANCHOR = "    /* ============ END ASSIGN_TEACHERS_v1 grid CSS ============ */\n\n</style>"


JS_WIRE_OLD = """        var selectMap = {};
        subjects.forEach(function (s) { selectMap[String(s.subject_id)] = s.teacher_name || ''; });

        container.querySelectorAll('.cell-subject').forEach(function (sel) {
            sel.addEventListener('change', function () {
                var hint = sel.parentNode.querySelector('.teacher-hint');
                var t = selectMap[sel.value] || '';
                hint.textContent = t ? '(' + t + ')' : '';
                hint.className = 'teacher-hint' + (t ? ' has-teacher' : '');
            });
        });
"""

JS_WIRE_NEW = """        var selectMap = {};
        var teacherIdMap = {};
        subjects.forEach(function (s) {
            selectMap[String(s.subject_id)] = s.teacher_name || '';
            teacherIdMap[String(s.subject_id)] = s.teacher_id || null;
        });

        // ===== INLINE_TEACHER_CONFLICT_V1 =====
        // Detect whether the chosen subject's teacher is already booked
        // in ANOTHER class at the same (day, period). The API returns
        // teacher_busy = { "teacher_id|day|period":
        //                  { "class": <other class>, "teacher_name": <full> } }
        // so we can show "⚠ <Full Name> busy in <Class>".
        var _busyMap = (data && data.teacher_busy) || {};
        container._teacherIdMap    = teacherIdMap;
        container._subjectNameMap  = selectMap;
        container._teacherBusyMap  = _busyMap;

        function _refreshInlineConflict(sel) {
            var hint = sel.parentNode.querySelector('.teacher-hint');
            var cell = sel.parentNode;
            var tid  = teacherIdMap[sel.value];
            var day   = parseInt(sel.getAttribute('data-day'), 10);
            var order = parseInt(sel.getAttribute('data-order'), 10);
            var conflict = '';
            if (tid && !isNaN(day) && !isNaN(order)) {
                conflict = _busyMap[tid + '|' + day + '|' + order] || '';
            }
            if (conflict) {
                var _tFull    = conflict.teacher_name || 'Teacher';
                var _otherCls = conflict.class || 'another class';
                hint.textContent = '\\u26a0 ' + _tFull + ' busy in ' + _otherCls;
                hint.className = 'teacher-hint has-conflict';
                cell.classList.add('has-conflict');
            } else {
                cell.classList.remove('has-conflict');
                var t = selectMap[sel.value] || '';
                hint.textContent = t ? '(' + t + ')' : '';
                hint.className = 'teacher-hint' + (t ? ' has-teacher' : '');
            }
        }

        container.querySelectorAll('.cell-subject').forEach(function (sel) {
            _refreshInlineConflict(sel);
            sel.addEventListener('change', function () {
                _refreshInlineConflict(sel);
            });
        });
        // ===== END INLINE_TEACHER_CONFLICT_V1 =====
"""


JS_SAVE_OLD = """    function _saveInlineSubjectsGrid(container, saveBtn) {
        var assignments = [];
        container.querySelectorAll('.cell-subject').forEach(function (sel) {
            var day = parseInt(sel.getAttribute('data-day'), 10);
            var order = parseInt(sel.getAttribute('data-order'), 10);
            var subjectId = sel.value ? parseInt(sel.value, 10) : null;
            assignments.push({ day: day, order: order, subject_id: subjectId });
        });
"""

JS_SAVE_NEW = """    function _saveInlineSubjectsGrid(container, saveBtn) {
        // ===== INLINE_TEACHER_CONFLICT_V1 =====
        // Refuse to POST when a chosen subject's teacher is already
        // booked in another class at the same (day, period). The server
        // still re-validates with HTTP 409, but this saves a round trip
        // and lets the user see every conflict before submitting.
        var _busyMap    = container._teacherBusyMap  || {};
        var _teacherMap = container._teacherIdMap    || {};
        var _nameMap    = container._subjectNameMap  || {};
        var _DAY_NAMES  = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
        var _conflicts  = [];
        container.querySelectorAll('.cell-subject').forEach(function (sel) {
            var tid = _teacherMap[sel.value];
            if (!tid) return;
            var day   = parseInt(sel.getAttribute('data-day'), 10);
            var order = parseInt(sel.getAttribute('data-order'), 10);
            var other = _busyMap[tid + '|' + day + '|' + order];
            if (other) {
                var _tFull    = other.teacher_name || _nameMap[sel.value] || ('Teacher #' + tid);
                var _otherCls = other.class || 'another class';
                var _dayStr   = _DAY_NAMES[day] || ('Day ' + day);
                _conflicts.push(
                    _tFull + ' is already teaching ' + _otherCls +
                    ' on ' + _dayStr + ' P' + order + '.'
                );
            }
        });
        if (_conflicts.length) {
            alert(
                'Teacher conflict detected \\u2014 a teacher can only be in ' +
                'one class at a time:\\n\\n' + _conflicts.join('\\n')
            );
            return;
        }
        // ===== END INLINE_TEACHER_CONFLICT_V1 =====

        var assignments = [];
        container.querySelectorAll('.cell-subject').forEach(function (sel) {
            var day = parseInt(sel.getAttribute('data-day'), 10);
            var order = parseInt(sel.getAttribute('data-order'), 10);
            var subjectId = sel.value ? parseInt(sel.value, 10) : null;
            assignments.push({ day: day, order: order, subject_id: subjectId });
        });
"""


MARKER_LINE = "    // INLINE_TEACHER_CONFLICT_V1_DONE\n"
IIFE_ANCHOR = "    // ===== END EDIT_PERIODS_TIMETABLE_v1 =====\n"


# ======================================================================
# File patcher
# ======================================================================
def patch_class_detailed(project_root: Path, filename: str, log: Log):
    path = project_root / 'templates' / 'tenant' / filename
    log.info(f"Patching {path}")

    if not path.exists():
        log.warn(f"{filename}: not present in this repo — skipping")
        return

    content = read_file(path, log)
    if content is None:
        return

    if 'INLINE_TEACHER_CONFLICT_V1_DONE' in content:
        log.info(f"{filename}: marker present — already patched, skipping")
        return

    # Safety check: this template must have the inline-grid pattern we
    # intend to patch. If not, skip so we never half-patch.
    if '_buildInlineSubjectsGrid' not in content or '_saveInlineSubjectsGrid' not in content:
        log.warn(
            f"{filename}: does not contain the inline subjects grid "
            f"(_buildInlineSubjectsGrid / _saveInlineSubjectsGrid) — skipping"
        )
        return

    ok = True

    # --- 1. CSS -----------------------------------------------------------
    if CSS_ANCHOR not in content:
        log.err(f"{filename}: CSS anchor not found")
        ok = False
    else:
        if not replace_exact(
            path,
            CSS_ANCHOR,
            "    /* ============ END ASSIGN_TEACHERS_v1 grid CSS ============ */\n\n"
            + CONFLICT_CSS + "\n</style>",
            log,
            f"{filename}[A]: conflict CSS",
        ):
            ok = False

    # --- 2. _buildInlineSubjectsGrid wire-up ------------------------------
    if not replace_exact(path, JS_WIRE_OLD, JS_WIRE_NEW, log,
                         f"{filename}[B]: wire-up"):
        ok = False

    # --- 3. _saveInlineSubjectsGrid precheck ------------------------------
    if not replace_exact(path, JS_SAVE_OLD, JS_SAVE_NEW, log,
                         f"{filename}[C]: save precheck"):
        ok = False

    # --- 4. Marker --------------------------------------------------------
    if ok:
        try:
            replace_exact(path, IIFE_ANCHOR, IIFE_ANCHOR + MARKER_LINE, log,
                          f"{filename}[marker]: record done")
        except Exception as exc:
            log.err(f"{filename}[marker] failed: {exc}")


# ======================================================================
# Entry point
# ======================================================================
def main():
    parser = argparse.ArgumentParser(
        description='INLINE_TEACHER_CONFLICT_V1 patcher',
    )
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--target-dir', default='.')
    args = parser.parse_args()

    log = Log(verbose=args.verbose, dry_run=args.dry_run)
    project_root = Path(args.target_dir).resolve()

    log.info(f"Target directory: {project_root}")
    log.info(f"Mode: {'DRY-RUN' if args.dry_run else 'APPLY'}")
    print()

    if not (project_root / 'axis_saas').is_dir():
        log.err("does not look like the AXIS project root")
        sys.exit(1)

    for fname in ('single_class_detailed.html', 'wing_class_detailed.html'):
        try:
            patch_class_detailed(project_root, fname, log)
        except Exception as exc:
            log.err(f"{fname} patch failed: {exc}")
        print()

    log.info(f"Done. changes={log.changes} errors={log.errors}")
    if args.dry_run:
        log.info("DRY-RUN. Re-run without --dry-run to apply.")
    if log.errors:
        sys.exit(2)


if __name__ == '__main__':
    main()
