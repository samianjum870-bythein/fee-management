#!/usr/bin/env python3
"""
axis_patcher.py
===============
TEACHER_TIMETABLE_CONFLICT_V2_FULLNAME
--------------------------------------
Flip the conflict UI from first-name to FULL teacher name.

Before:
    ⚠ Ayesha busy in 10-A
    Ayesha is already teaching 10-A on Monday P1.

After:
    ⚠ Ayesha Khan busy in 10-A
    Ayesha Khan is already teaching 10-A on Monday P1.

Touches only templates/tenant/timetable_assign_teachers.html:
  1. refreshConflictForSelect()  -> use conflict.teacher_name directly
  2. saveAssignments() precheck  -> use other.teacher_name directly
  3. Removes the now-unused firstName() helper (safe: nothing else calls it)

Idempotent: skips if marker "TEACHER_TIMETABLE_CONFLICT_V2_FULLNAME_DONE"
is already present.

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
        log.err(f"{label}: anchor not found")
        return False
    if content.count(old) > 1:
        log.err(f"{label}: anchor not unique ({content.count(old)} hits)")
        return False
    return write_file(path, content.replace(old, new, 1), log)


# ======================================================================
# Anchor A: hint rendering — drop firstName(), use full teacher_name
# ======================================================================
OLD_HINT = """            if (conflict) {
                var _fname = firstName(conflict.teacher_name) || 'Teacher';
                var _otherCls = conflict.class || 'another class';
                hint.textContent = '\\u26a0 ' + _fname + ' busy in ' + _otherCls;
                hint.className = 'teacher-hint has-conflict';
                cell.classList.add('has-conflict');
            } else {
"""

NEW_HINT = """            if (conflict) {
                var _tFull = conflict.teacher_name || 'Teacher';
                var _otherCls = conflict.class || 'another class';
                hint.textContent = '\\u26a0 ' + _tFull + ' busy in ' + _otherCls;
                hint.className = 'teacher-hint has-conflict';
                cell.classList.add('has-conflict');
            } else {
"""


# ======================================================================
# Anchor B: save precheck — drop firstName(), use full teacher_name
# ======================================================================
OLD_SAVE = """            var other = _busyMap[tid + '|' + day + '|' + order];
            if (other) {
                var _fname = firstName(other.teacher_name) ||
                             firstName(CURRENT_SUBJECT_NAME_MAP[sel.value]) ||
                             ('Teacher #' + tid);
                var _otherCls = other.class || 'another class';
                _conflicts.push(
                    _fname + ' is already teaching ' + _otherCls +
                    ' on ' + dayName(day) + ' P' + order + '.'
                );
            }
"""

NEW_SAVE = """            var other = _busyMap[tid + '|' + day + '|' + order];
            if (other) {
                var _tFull = other.teacher_name ||
                             CURRENT_SUBJECT_NAME_MAP[sel.value] ||
                             ('Teacher #' + tid);
                var _otherCls = other.class || 'another class';
                _conflicts.push(
                    _tFull + ' is already teaching ' + _otherCls +
                    ' on ' + dayName(day) + ' P' + order + '.'
                );
            }
"""


# ======================================================================
# Anchor C: remove the now-unused firstName() helper
# ======================================================================
OLD_HELPER = """    function firstName(full) {
        if (!full) return '';
        return String(full).trim().split(/\\s+/)[0] || '';
    }

    var DAY_NAMES = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
"""

NEW_HELPER = """    // Full teacher names are used verbatim in conflict messages
    // (see TEACHER_TIMETABLE_CONFLICT_V2_FULLNAME) so no name
    // truncation helper is needed.

    var DAY_NAMES = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
"""


# ======================================================================
# Marker bump — so the file records the final state
# ======================================================================
OLD_MARKER = "    // TEACHER_TIMETABLE_CONFLICT_V2_DONE\n"
NEW_MARKER = (
    "    // TEACHER_TIMETABLE_CONFLICT_V2_DONE\n"
    "    // TEACHER_TIMETABLE_CONFLICT_V2_FULLNAME_DONE\n"
)


def patch_template(project_root: Path, log: Log):
    path = project_root / 'templates' / 'tenant' / 'timetable_assign_teachers.html'
    log.info(f"Patching {path}")

    content = read_file(path, log)
    if content is None:
        return

    if 'TEACHER_TIMETABLE_CONFLICT_V2_FULLNAME_DONE' in content:
        log.info("template: FULLNAME marker already present — nothing to do")
        return

    # --- A: hint text ------------------------------------------------
    try:
        replace_exact(path, OLD_HINT, NEW_HINT, log,
                      'template[A]: full-name conflict hint')
    except Exception as exc:
        log.err(f"template[A] failed: {exc}")

    # --- B: save precheck message ------------------------------------
    try:
        replace_exact(path, OLD_SAVE, NEW_SAVE, log,
                      'template[B]: full-name save message')
    except Exception as exc:
        log.err(f"template[B] failed: {exc}")

    # --- C: remove unused firstName helper ---------------------------
    try:
        replace_exact(path, OLD_HELPER, NEW_HELPER, log,
                      'template[C]: drop unused firstName()')
    except Exception as exc:
        log.err(f"template[C] failed: {exc}")

    # --- Marker: append DONE tag (only if V2 marker is still there) --
    content2 = read_file(path, log)
    if content2 is None:
        return
    if 'TEACHER_TIMETABLE_CONFLICT_V2_DONE' in content2 \
       and 'TEACHER_TIMETABLE_CONFLICT_V2_FULLNAME_DONE' not in content2:
        replace_exact(path, OLD_MARKER, NEW_MARKER, log,
                      'template[marker]: record FULLNAME done')


# ======================================================================
# Entry point
# ======================================================================
def main():
    parser = argparse.ArgumentParser(
        description='TEACHER_TIMETABLE_CONFLICT_V2_FULLNAME patcher',
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

    try:
        patch_template(project_root, log)
    except Exception as exc:
        log.err(f"template patch failed: {exc}")

    print()
    log.info(f"Done. changes={log.changes} errors={log.errors}")
    if args.dry_run:
        log.info("DRY-RUN. Re-run without --dry-run to apply.")
    if log.errors:
        sys.exit(2)


if __name__ == '__main__':
    main()
