#!/usr/bin/env python3
"""
axis_patcher.py
===============
INLINE_TIMETABLE_ASSIGN_V1
--------------------------
Two additions to /portal/<schema>/timetable/assign-teachers/:

1. RED HIGHLIGHT for empty periods
   Any period slot in the class's timetable grid that has no
   subject (hence no teacher) assigned gets `td.has-empty` and
   a red background, so the admin can see at a glance which
   slots still need a teacher.

2. "✏️ Edit Timetable" button
   A button in the modal footer that opens the periods timetable
   slot-edit form INLINE (same modal, same page). The admin can
   change which calendar slots this class's timetable uses and
   adjust the break duration / break position, then Save. Uses the
   existing /api/timetable/periods/bunch/add/ endpoint with edit_id.

Files touched:
  * axis_saas/views/assign_teachers.py
      - api_get_teacher_assignments now also returns
        break_duration, timetable_id, timetable_break_duration,
        slots_by_label, all_timetables — everything the inline
        edit form needs.
  * templates/tenant/timetable_assign_teachers.html
      - New CSS: .assign-grid td.has-empty
      - New button in modal footer
      - renderGrid() marks empty cells
      - New inline edit-form logic + button wiring

Idempotent via marker: INLINE_TIMETABLE_ASSIGN_V1_DONE

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

    def info(self, msg):  print(f"[{_ts()}] {msg}")
    def debug(self, msg):
        if self.verbose: print(f"[{_ts()}]   . {msg}")
    def ok(self, msg):
        self.changes += 1
        print(f"[{_ts()}] {'DRY ' if self.dry_run else 'OK  '}{msg}")
    def err(self, msg):
        self.errors += 1
        print(f"[{_ts()}] ERR {msg}")
    def warn(self, msg): print(f"[{_ts()}] WARN {msg}")


def read_file(path, log):
    if not path.exists():
        log.err(f"file not found: {path}")
        return None
    try:
        return path.read_text(encoding='utf-8')
    except Exception as exc:
        log.err(f"cannot read {path}: {exc}")
        return None


def write_file(path, content, log):
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


def replace_exact(path, old, new, log, label):
    content = read_file(path, log)
    if content is None:
        return False
    if old not in content:
        log.err(f"{label}: anchor not found in {path}")
        return False
    if content.count(old) > 1:
        log.err(f"{label}: anchor not unique ({content.count(old)} hits)")
        return False
    return write_file(path, content.replace(old, new, 1), log)


# ======================================================================
# BACKEND PATCH — axis_saas/views/assign_teachers.py
# ======================================================================

BACKEND_OLD_RETURN = """        return JsonResponse({
            'has_timetable': True,
            'class_id': school_class.id,
            'class_display': class_display,
            'timetable_title': tt.title,
            # TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX: label is a FK now.
            'timetable_label': tt.label.name if tt.label_id else '',
            'timetable_days': tt.days or [],
            'subjects': subjects,
            'existing': existing,
            'teacher_busy': teacher_busy,
        })
"""

BACKEND_NEW_RETURN = """        # ---- INLINE_TIMETABLE_ASSIGN_V1: edit-form data -------------
        # Everything the client needs to render the "Edit Timetable"
        # slot-selection form INLINE (no extra round trip).
        from ..models import (
            DaySchedule as _TT_DS,
            ScheduleLabel as _TT_SL,
            PeriodsTimetable as _TT_PTT,
        )

        _slots_by_label = {}
        try:
            for _lbl in _TT_SL.objects.all().order_by('name'):
                _scheds = _TT_DS.objects.filter(label=_lbl).order_by('day_of_week', 'order')
                _slots_by_label[_lbl.name] = [
                    {
                        'id': _ds.id,
                        'day': _ds.day_of_week,
                        'day_label': _ds.get_day_of_week_display(),
                        'start': _ds.start_time.strftime('%H:%M'),
                        'end': _ds.end_time.strftime('%H:%M'),
                        'periods': _ds.periods,
                        'duration': _ds.duration,
                    }
                    for _ds in _scheds
                ]
        except Exception as _exc:
            logger.warning('INLINE_TIMETABLE_ASSIGN_V1: slots_by_label failed: %s', _exc)
            _slots_by_label = {}

        _all_timetables = []
        try:
            for _t in _TT_PTT.objects.order_by('id'):
                _all_timetables.append({
                    'id': _t.id,
                    'title': _t.title,
                    # TIMETABLE_FK_REFACTOR_V1_READ_SITE_FIX: label is a FK now.
                    'label': _t.label.name if _t.label_id else '',
                    'break_duration': _t.break_duration or 0,
                    'days': _t.days or [],
                })
        except Exception as _exc:
            logger.warning('INLINE_TIMETABLE_ASSIGN_V1: all_timetables failed: %s', _exc)
            _all_timetables = []

        return JsonResponse({
            'has_timetable': True,
            'class_id': school_class.id,
            'class_display': class_display,
            'timetable_id': tt.id,
            'timetable_title': tt.title,
            'timetable_label': tt.label.name if tt.label_id else '',
            'timetable_days': tt.days or [],
            'timetable_break_duration': tt.break_duration or 0,
            # Backwards-compat alias: the existing template reads
            # `data.break_duration` when deciding whether to render
            # break columns. Previously this key was missing, so breaks
            # never showed on this page. Fixed here.
            'break_duration': tt.break_duration or 0,
            'subjects': subjects,
            'existing': existing,
            'teacher_busy': teacher_busy,
            'slots_by_label': _slots_by_label,
            'all_timetables': _all_timetables,
        })
"""


def patch_backend(project_root, log):
    path = project_root / 'axis_saas' / 'views' / 'assign_teachers.py'
    log.info(f"Patching {path}")
    if not path.exists():
        log.err(f"{path} not found")
        return
    content = read_file(path, log)
    if content is None:
        return
    if 'INLINE_TIMETABLE_ASSIGN_V1' in content:
        log.info("backend: marker present — skipping")
        return
    replace_exact(path, BACKEND_OLD_RETURN, BACKEND_NEW_RETURN, log,
                  'backend: extend api_get_teacher_assignments')


# ======================================================================
# FRONTEND PATCH — templates/tenant/timetable_assign_teachers.html
# ======================================================================

# --- A. CSS: has-empty red highlight ---------------------------------
CSS_ANCHOR = "    .assign-grid td.has-conflict {\n"
CSS_NEW = (
    "    /* INLINE_TIMETABLE_ASSIGN_V1: red highlight on periods with\n"
    "       no teacher assigned in this class. */\n"
    "    .assign-grid td.has-empty {\n"
    "        background:#fef2f2;\n"
    "        box-shadow: inset 0 0 0 2px #fca5a5;\n"
    "    }\n"
    "    .assign-grid td.has-conflict {\n"
)


# --- B. Modal footer: add "Edit Timetable" button --------------------
FOOTER_OLD = """        <div class="form-actions">
            <button type="button" id="cancelAssignBtn" class="btn-secondary">Cancel</button>
            <button type="button" id="saveAssignBtn" class="btn-success" disabled>Save Assignments</button>
        </div>
"""

FOOTER_NEW = """        <div class="form-actions" id="assignFormActions">
            <button type="button" id="editTimetableBtn" class="btn-secondary" style="margin-right:auto;">&#9998; Edit Timetable</button>
            <button type="button" id="cancelAssignBtn" class="btn-secondary">Cancel</button>
            <button type="button" id="saveAssignBtn" class="btn-success" disabled>Save Assignments</button>
        </div>
"""


# --- C. renderGrid: mark empty cells --------------------------------
CELL_OLD = """                html += '<td>' +
                    '<select class="cell-subject" data-day="' + dayNum + '" data-order="' + i + '">' +
                        opts +
                    '</select>' +
                    '<span class="teacher-hint' + (teacherForSel ? ' has-teacher' : '') + '">' +
                        (teacherForSel ? '(' + esc(teacherForSel) + ')' : '') +
                    '</span>' +
                '</td>';
"""

CELL_NEW = """                // INLINE_TIMETABLE_ASSIGN_V1: mark a cell as empty
                // (red) when no subject-with-teacher is assigned.
                var _isEmptyCell = !teacherForSel;
                html += '<td' + (_isEmptyCell ? ' class="has-empty"' : '') + '>' +
                    '<select class="cell-subject" data-day="' + dayNum + '" data-order="' + i + '">' +
                        opts +
                    '</select>' +
                    '<span class="teacher-hint' + (teacherForSel ? ' has-teacher' : '') + '">' +
                        (teacherForSel ? '(' + esc(teacherForSel) + ')' : '') +
                    '</span>' +
                '</td>';
"""


# --- D. New inline edit-form JS --------------------------------------
JS_TAIL_OLD = """    // "Manage" buttons in the table
    document.querySelectorAll('.open-manage-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var cid = btn.getAttribute('data-class-id');
            reset();
            openModal();
            classSel.value = cid;
            loadClass(cid);
        });
    });
})();
"""

JS_TAIL_NEW = """    // "Manage" buttons in the table
    document.querySelectorAll('.open-manage-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var cid = btn.getAttribute('data-class-id');
            reset();
            openModal();
            classSel.value = cid;
            loadClass(cid);
        });
    });

    // ================================================================
    // INLINE_TIMETABLE_ASSIGN_V1_DONE
    // Inline "Edit Timetable" — lets the admin tweak the slots and
    // break of the class's assigned periods timetable, right here in
    // the same modal, without leaving the page.
    // ================================================================
    var _editTtBtn          = document.getElementById('editTimetableBtn');
    var _assignFormActions  = document.getElementById('assignFormActions');

    function _editTtKey(day, start, end) {
        return day + '|' + start + '|' + end;
    }

    function _renderEditTimetableForm() {
        if (!CURRENT_CLASS_ID || !CURRENT_DATA || !CURRENT_DATA.has_timetable) {
            alert('Please select a class that has a periods timetable first.');
            return;
        }
        var data = CURRENT_DATA;
        var tt = {
            id:             data.timetable_id,
            title:          data.timetable_title || '',
            label:          data.timetable_label || '',
            break_duration: data.timetable_break_duration || 0,
            days:           data.timetable_days || []
        };
        var slotsByLabel  = data.slots_by_label  || {};
        var allTimetables = data.all_timetables || [];
        var allSlots      = slotsByLabel[tt.label] || [];

        // Slots already used by OTHER timetables on the same label
        // must not appear (server would refuse the save otherwise).
        var usedByOthers = {};
        allTimetables.forEach(function (t) {
            if (t.id === tt.id) return;
            if (t.label !== tt.label) return;
            (t.days || []).forEach(function (d) {
                usedByOthers[_editTtKey(d.day_of_week, d.start, d.end)] = true;
            });
        });

        var selfMap = {};
        (tt.days || []).forEach(function (d) {
            selfMap[_editTtKey(d.day_of_week, d.start, d.end)] = (d.break_after || null);
        });

        var visibleSlots = allSlots.filter(function (slot) {
            var k = _editTtKey(slot.day, slot.start, slot.end);
            if (selfMap.hasOwnProperty(k)) return true;
            return !usedByOthers[k];
        });

        var html = '';
        html += '<h3 style="margin:0 0 0.5rem; font-size:1.05rem;">Edit Periods Timetable &mdash; ' + esc(tt.title) + '</h3>';
        html += '<p style="margin:0 0 0.75rem; color:var(--muted); font-size:0.82rem;">Adjust which calendar slots this timetable uses and the break.</p>';

        html += '<div class="form-group"><label for="editTtTitle">Timetable Title *</label>';
        html += '<input type="text" id="editTtTitle" class="form-control" value="' + esc(tt.title) + '"></div>';

        html += '<div class="form-group"><label>Label</label>';
        html += '<input type="text" class="form-control" value="' + esc(tt.label) + '" disabled>';
        html += '<small class="text-muted" style="display:block; margin-top:0.35rem; font-size:0.72rem;">Label is locked in edit mode.</small></div>';

        html += '<h4 style="margin:1rem 0 0.4rem; font-size:0.95rem;">Select Slots</h4>';
        html += '<p style="margin:0 0 0.5rem; color:var(--muted); font-size:0.78rem;">Slots already used by another timetable are hidden.</p>';
        html += '<div style="max-height:260px; overflow-y:auto; border:1px solid var(--border); border-radius:0.5rem;">';
        html += '<table style="width:100%; border-collapse:collapse; font-size:0.85rem;">';
        html += '<thead><tr>';
        html += '<th style="border:1px solid var(--border); padding:0.45rem; background:var(--surface-alt); font-weight:600; width:40px;"></th>';
        html += '<th style="border:1px solid var(--border); padding:0.45rem; background:var(--surface-alt); font-weight:600;">Day</th>';
        html += '<th style="border:1px solid var(--border); padding:0.45rem; background:var(--surface-alt); font-weight:600;">Start</th>';
        html += '<th style="border:1px solid var(--border); padding:0.45rem; background:var(--surface-alt); font-weight:600;">End</th>';
        html += '<th style="border:1px solid var(--border); padding:0.45rem; background:var(--surface-alt); font-weight:600;">Periods</th>';
        html += '</tr></thead><tbody>';

        if (visibleSlots.length === 0) {
            html += '<tr><td colspan="5" style="padding:1rem; text-align:center; color:var(--muted);">No available slots.</td></tr>';
        } else {
            visibleSlots.forEach(function (slot) {
                var k = _editTtKey(slot.day, slot.start, slot.end);
                var checked = selfMap.hasOwnProperty(k);
                html += '<tr>';
                html += '<td style="border:1px solid var(--border); padding:0.45rem; text-align:center;">';
                html += '<input type="checkbox" class="edit-slot-cb"' + (checked ? ' checked' : '');
                html += ' data-start="' + slot.start + '" data-end="' + slot.end + '"';
                html += ' data-day="' + slot.day + '" data-periods="' + slot.periods + '"';
                html += ' data-day-label="' + esc(slot.day_label || '') + '" style="width:18px; height:18px; cursor:pointer;">';
                html += '</td>';
                html += '<td style="border:1px solid var(--border); padding:0.45rem; text-align:center;">' + esc(slot.day_label || '') + '</td>';
                html += '<td style="border:1px solid var(--border); padding:0.45rem; text-align:center;">' + slot.start + '</td>';
                html += '<td style="border:1px solid var(--border); padding:0.45rem; text-align:center;">' + slot.end + '</td>';
                html += '<td style="border:1px solid var(--border); padding:0.45rem; text-align:center;">' + slot.periods + '</td>';
                html += '</tr>';
            });
        }
        html += '</tbody></table></div>';

        html += '<div class="form-group" style="margin-top:1rem;"><label for="editTtBreakDur">Break Duration (minutes)</label>';
        html += '<input type="number" id="editTtBreakDur" class="form-control" value="' + (tt.break_duration || 0) + '" min="0" step="1" style="max-width:180px;"></div>';

        html += '<div id="editTtBreakAfterWrap" style="display:none;">';
        html += '<h4 style="margin:1rem 0 0.5rem; font-size:0.95rem;">Break After Which Period?</h4>';
        html += '<p style="margin:0 0 0.5rem; color:var(--muted); font-size:0.78rem;">Choose the break position for each selected day. Pick <em>No break</em> to skip.</p>';
        html += '<div id="editTtBreakAfterList"></div></div>';

        html += '<div class="form-actions">';
        html += '<button type="button" id="editTtCancelBtn" class="btn-secondary">Cancel</button>';
        html += '<button type="button" id="editTtSaveBtn" class="btn-success">Save Changes</button>';
        html += '</div>';

        modalBody.innerHTML = html;
        if (_assignFormActions) _assignFormActions.style.display = 'none';

        document.getElementById('editTtCancelBtn').addEventListener('click', function () {
            renderGrid(data);
            if (_assignFormActions) _assignFormActions.style.display = '';
            saveBtn.disabled = false;
        });

        document.getElementById('editTtSaveBtn').addEventListener('click', function () {
            _saveEditTimetableForm(tt);
        });

        var breakDurEl = document.getElementById('editTtBreakDur');
        breakDurEl.addEventListener('input', function () {
            _updateEditBreakAfterSection(tt, selfMap);
        });

        modalBody.querySelectorAll('.edit-slot-cb').forEach(function (cb) {
            cb.addEventListener('change', function () {
                _updateEditBreakAfterSection(tt, selfMap);
            });
        });

        _updateEditBreakAfterSection(tt, selfMap);
    }

    function _updateEditBreakAfterSection(tt, selfMap) {
        var checked = Array.prototype.slice.call(modalBody.querySelectorAll('.edit-slot-cb')).filter(function (cb) { return cb.checked; });
        var durEl   = document.getElementById('editTtBreakDur');
        var duration= durEl ? (parseInt(durEl.value, 10) || 0) : 0;
        var wrap    = document.getElementById('editTtBreakAfterWrap');
        var list    = document.getElementById('editTtBreakAfterList');
        if (!wrap || !list) return;

        if (checked.length === 0 || duration <= 0) {
            wrap.style.display = 'none';
            list.innerHTML = '';
            return;
        }

        var existing = {};
        list.querySelectorAll('.edit-break-after-sel').forEach(function (sel) {
            existing[sel.getAttribute('data-day-key')] = sel.value;
        });

        wrap.style.display = 'block';
        list.innerHTML = '';

        checked.forEach(function (cb) {
            var dayLabel = cb.getAttribute('data-day-label') || '';
            var periods  = parseInt(cb.getAttribute('data-periods'), 10) || 0;
            var dayKey   = cb.getAttribute('data-day') + '|' + cb.getAttribute('data-start') + '|' + cb.getAttribute('data-end');

            var optionsHtml = '<option value="">No break</option>';
            for (var i = 1; i < periods; i++) {
                optionsHtml += '<option value="' + i + '">After P' + i + '</option>';
            }

            var row = document.createElement('div');
            row.style.cssText = 'display:flex; gap:1rem; align-items:center; margin-bottom:0.5rem; padding:0.5rem; background:var(--surface-alt); border-radius:0.5rem; flex-wrap:wrap;';
            row.innerHTML =
                '<strong style="min-width:100px;">' + esc(dayLabel) + '</strong>' +
                '<span style="font-size:0.85rem; color:var(--muted);">' + periods + ' periods</span>' +
                '<select class="form-control edit-break-after-sel" data-day-key="' + dayKey + '" style="max-width:200px;">' +
                    optionsHtml +
                '</select>';
            list.appendChild(row);

            var sel = row.querySelector('.edit-break-after-sel');
            if (existing[dayKey] !== undefined) {
                sel.value = existing[dayKey];
            } else if (selfMap[dayKey] !== undefined && selfMap[dayKey] !== null) {
                sel.value = String(selfMap[dayKey]);
            } else {
                var mid = Math.floor(periods / 2);
                if (mid >= 1 && mid < periods) sel.value = String(mid);
            }
        });
    }

    function _saveEditTimetableForm(tt) {
        var titleEl = document.getElementById('editTtTitle');
        var durEl   = document.getElementById('editTtBreakDur');
        var title   = titleEl ? titleEl.value.trim() : '';
        var duration= durEl ? (parseInt(durEl.value, 10) || 0) : 0;
        var checked = Array.prototype.slice.call(modalBody.querySelectorAll('.edit-slot-cb')).filter(function (cb) { return cb.checked; });

        if (!title) { alert('Title is required.'); return; }
        if (checked.length === 0) { alert('Select at least one slot.'); return; }

        var days = checked.map(function (cb) {
            var dayKey = cb.getAttribute('data-day') + '|' + cb.getAttribute('data-start') + '|' + cb.getAttribute('data-end');
            var sel = modalBody.querySelector('.edit-break-after-sel[data-day-key="' + dayKey + '"]');
            var breakAfterVal = sel ? sel.value : '';
            return {
                day:         parseInt(cb.getAttribute('data-day'), 10),
                start:       cb.getAttribute('data-start'),
                end:         cb.getAttribute('data-end'),
                periods:     parseInt(cb.getAttribute('data-periods'), 10),
                break_after: breakAfterVal ? parseInt(breakAfterVal, 10) : null
            };
        });

        var payload = {
            title:          title,
            label:          tt.label || '',
            break_duration: duration,
            days:           days,
            edit_id:        tt.id
        };

        var saveBtnEl = document.getElementById('editTtSaveBtn');
        if (saveBtnEl) { saveBtnEl.disabled = true; saveBtnEl.textContent = 'Saving...'; }

        function getCookie(name) {
            var v = null;
            if (document.cookie) {
                document.cookie.split(';').forEach(function (c) {
                    c = c.trim();
                    if (c.substring(0, name.length + 1) === (name + '=')) {
                        v = decodeURIComponent(c.substring(name.length + 1));
                    }
                });
            }
            return v;
        }

        fetch('/portal/' + SCHEMA + '/api/timetable/periods/bunch/add/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken') || '',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(payload)
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data.success) {
                alert('Error: ' + (data.error || 'Unknown error'));
                if (saveBtnEl) { saveBtnEl.disabled = false; saveBtnEl.textContent = 'Save Changes'; }
                return;
            }
            window.location.reload();
        })
        .catch(function (err) {
            alert('Network error: ' + err.message);
            if (saveBtnEl) { saveBtnEl.disabled = false; saveBtnEl.textContent = 'Save Changes'; }
        });
    }

    if (_editTtBtn) {
        _editTtBtn.addEventListener('click', function () {
            _renderEditTimetableForm();
        });
    }
    // ===== END INLINE_TIMETABLE_ASSIGN_V1 =====
})();
"""


def patch_template(project_root, log):
    path = project_root / 'templates' / 'tenant' / 'timetable_assign_teachers.html'
    log.info(f"Patching {path}")
    if not path.exists():
        log.err(f"{path} not found")
        return
    content = read_file(path, log)
    if content is None:
        return
    if 'INLINE_TIMETABLE_ASSIGN_V1_DONE' in content:
        log.info("template: marker present — skipping")
        return

    # A — CSS
    try:
        replace_exact(path, CSS_ANCHOR, CSS_NEW, log, 'template[A]: has-empty CSS')
    except Exception as exc:
        log.err(f"template[A] failed: {exc}")

    # B — modal footer button
    try:
        replace_exact(path, FOOTER_OLD, FOOTER_NEW, log, 'template[B]: Edit button')
    except Exception as exc:
        log.err(f"template[B] failed: {exc}")

    # C — mark empty cells in renderGrid
    try:
        replace_exact(path, CELL_OLD, CELL_NEW, log, 'template[C]: empty cell class')
    except Exception as exc:
        log.err(f"template[C] failed: {exc}")

    # D — append inline edit-form JS
    try:
        replace_exact(path, JS_TAIL_OLD, JS_TAIL_NEW, log, 'template[D]: inline edit JS')
    except Exception as exc:
        log.err(f"template[D] failed: {exc}")


# ======================================================================
# Entry point
# ======================================================================
def main():
    parser = argparse.ArgumentParser(description='INLINE_TIMETABLE_ASSIGN_V1 patcher')
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
        patch_backend(project_root, log)
    except Exception as exc:
        log.err(f"backend patch failed: {exc}")

    print()
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
