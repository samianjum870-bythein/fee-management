#!/usr/bin/env python3
"""
fix_monday_save.py
==================
Fixes "slot disappears after refresh" bug.

ROOT CAUSE:
    performSave() in timetable_management.html has:
        if (day && label && start && end && periods && duration) {
            schedules.push({ day, label, ... });
        }
    Monday is day=0 which is FALSY in JavaScript, so Monday rows
    are silently dropped from the POST payload. Server receives
    empty list, deletes everything, creates nothing.

FIX:
    Replace falsy check with explicit integer range check.
    Add a defensive console.warn when a row is skipped.
    Also warn user if server responds with created=0 while local
    rows exist.

Usage:
    python3 fix_monday_save.py [--dry-run] [--no-backup] [--target-dir=.]
"""

import sys
import shutil
from pathlib import Path
from datetime import datetime

DRY_RUN = False
BACKUP = True


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": "  ", "OK": "  ✓ ", "WARN": "  ! ", "ERR": "  ✗ ", "DRY": "  ~ "}
    print(f"[{ts}] {icons.get(level, '  ')}{msg}")


def read(p: Path):
    return p.read_text(encoding="utf-8") if p.exists() else None


def write(p: Path, content: str):
    if DRY_RUN:
        log(f"DRY-RUN would write {p}", "DRY")
        return True
    if BACKUP:
        bak = p.with_suffix(p.suffix + ".bak3")
        if not bak.exists():
            shutil.copy2(p, bak)
            log(f"backup -> {bak.name}")
    p.write_text(content, encoding="utf-8")
    log(f"wrote {p}", "OK")
    return True


def replace_once(text, old, new, label):
    if old not in text:
        log(f"pattern NOT FOUND: {label}", "WARN")
        return text, False
    return text.replace(old, new, 1), True


# =====================================================================
def patch_template(path: Path) -> bool:
    log(f"patching {path}")
    content = read(path)
    if content is None:
        log(f"file not found: {path}", "ERR")
        return False

    original = content
    changes = 0

    # ---------- FIX 1: performSave falsy check (THE bug) ----------
    old_block = (
        "        async function performSave() {\n"
        "            const rows = dayScheduleBody.querySelectorAll('tr[data-day]');\n"
        "            const schedules = [];\n"
        "            rows.forEach(row => {\n"
        "                const cells = row.querySelectorAll('td');\n"
        "                const day = parseInt(row.dataset.day);\n"
        "                const label = cells[1].textContent;\n"
        "                const start = cells[2].textContent;\n"
        "                const end = cells[3].textContent;\n"
        "                const periods = parseInt(cells[4].textContent);\n"
        "                const duration = parseInt(cells[5].textContent);\n"
        "                if (day && label && start && end && periods && duration) {\n"
        "                    schedules.push({ day, label, start, end, periods, duration });\n"
        "                }\n"
        "            });\n"
    )
    new_block = (
        "        async function performSave() {\n"
        "            const rows = dayScheduleBody.querySelectorAll('tr[data-day]');\n"
        "            const schedules = [];\n"
        "            rows.forEach(row => {\n"
        "                const cells = row.querySelectorAll('td');\n"
        "                const day = parseInt(row.dataset.day, 10);\n"
        "                const label = (cells[1].textContent || '').trim();\n"
        "                const start = (cells[2].textContent || '').trim();\n"
        "                const end   = (cells[3].textContent || '').trim();\n"
        "                const periods  = parseInt(cells[4].textContent, 10);\n"
        "                const duration = parseInt(cells[5].textContent, 10);\n"
        "\n"
        "                // FIX: Monday is day=0 (falsy!). Use explicit range check.\n"
        "                const dayOk = Number.isInteger(day) && day >= 0 && day <= 6;\n"
        "                const numsOk = Number.isInteger(periods) && periods > 0 &&\n"
        "                               Number.isInteger(duration) && duration > 0;\n"
        "                if (dayOk && label && start && end && numsOk) {\n"
        "                    schedules.push({ day, label, start, end, periods, duration });\n"
        "                } else {\n"
        "                    console.warn('[Timetable] SKIPPING row from payload:', {\n"
        "                        day: day, label: label, start: start, end: end,\n"
        "                        periods: periods, duration: duration,\n"
        "                    });\n"
        "                }\n"
        "            });\n"
        "            console.log('[Timetable] POST payload ->', schedules);\n"
    )
    content, ok = replace_once(content, old_block, new_block, "performSave falsy-day fix")
    changes += int(ok)

    # Fallback: if user already had `parseInt(row.dataset.day)` without radix
    if not ok:
        # Try minimal targeted fix
        old_min = (
            "                if (day && label && start && end && periods && duration) {\n"
            "                    schedules.push({ day, label, start, end, periods, duration });\n"
            "                }"
        )
        new_min = (
            "                // FIX: Monday=0 is falsy. Use explicit integer check.\n"
            "                const dayOk = Number.isInteger(day) && day >= 0 && day <= 6;\n"
            "                if (dayOk && label && start && end && periods && duration) {\n"
            "                    schedules.push({ day, label, start, end, periods, duration });\n"
            "                } else {\n"
            "                    console.warn('[Timetable] SKIPPING row from payload:', {day, label, start, end, periods, duration});\n"
            "                }"
        )
        content, ok = replace_once(content, old_min, new_min, "performSave falsy-day fix (minimal)")
        changes += int(ok)

    # ---------- FIX 2: warn user if server says created=0 but we sent rows ----------
    old_success = (
        "                if (data && data.success) {\n"
        "                    setStatus('All changes saved ✓', 'success');\n"
        "                } else {\n"
        "                    throw new Error(data?.error || 'Unknown error');\n"
        "                }"
    )
    new_success = (
        "                if (data && data.success) {\n"
        "                    if (schedules.length > 0 && (data.created || 0) === 0) {\n"
        "                        console.error('[Timetable] Server accepted POST but created=0. Payload:', schedules, 'Response:', data);\n"
        "                        setStatus('⚠️ Server saved 0 rows (check console)', 'error');\n"
        "                        await showAlert('Server returned success but saved 0 rows. Open DevTools console for the payload.', 'Unexpected');\n"
        "                    } else {\n"
        "                        setStatus('All changes saved ✓ (' + (data.created || 0) + ' rows)', 'success');\n"
        "                    }\n"
        "                } else {\n"
        "                    throw new Error(data?.error || 'Unknown error');\n"
        "                }"
    )
    content, ok = replace_once(content, old_success, new_success, "created=0 warning")
    changes += int(ok)

    # ---------- FIX 3: same falsy bug in addHoliday weekly (day=0) ----------
    old_weekly = (
        "        document.getElementById('weeklyModalSaveBtn').addEventListener('click', async function() {\n"
        "            const day = parseInt(document.getElementById('weeklyDay').value);\n"
        "            const label = document.getElementById('weeklyLabel').value.trim();\n"
        "            if (isNaN(day) || !label) {\n"
        "                await showAlert('Please select a day and enter a label.', 'Missing Data');\n"
        "                return;\n"
        "            }"
    )
    new_weekly = (
        "        document.getElementById('weeklyModalSaveBtn').addEventListener('click', async function() {\n"
        "            const rawDay = document.getElementById('weeklyDay').value;\n"
        "            const day = parseInt(rawDay, 10);\n"
        "            const label = document.getElementById('weeklyLabel').value.trim();\n"
        "            if (rawDay === '' || isNaN(day) || day < 0 || day > 6 || !label) {\n"
        "                await showAlert('Please select a day and enter a label.', 'Missing Data');\n"
        "                return;\n"
        "            }"
    )
    content, ok = replace_once(content, old_weekly, new_weekly, "weekly holiday Monday fix")
    changes += int(ok)

    if content != original:
        write(path, content)
        log(f"{changes} change(s) applied", "OK")
        return True
    log("no changes needed (already fixed?)", "WARN")
    return True


# =====================================================================
def main():
    global DRY_RUN, BACKUP
    target = Path.cwd()

    for a in sys.argv[1:]:
        if a == "--dry-run":
            DRY_RUN = True
        elif a == "--no-backup":
            BACKUP = False
        elif a.startswith("--target-dir="):
            target = Path(a.split("=", 1)[1]).resolve()
        else:
            print(f"unknown arg: {a}", file=sys.stderr)
            sys.exit(1)

    print("=" * 60)
    print("  fix_monday_save.py")
    print(f"  target : {target}")
    print(f"  dry-run: {DRY_RUN}")
    print(f"  backup : {BACKUP}")
    print("=" * 60)

    if not (target / "manage.py").exists():
        log("manage.py not found -- wrong directory?", "ERR")
        sys.exit(2)

    tmpl = target / "templates" / "tenant" / "timetable_management.html"
    patch_template(tmpl)

    print("=" * 60)
    print("DONE.")
    print("")
    print("Verify:")
    print("  1. python3 manage.py runserver")
    print("  2. Ctrl+Shift+R (hard refresh)")
    print("  3. F12 -> Console")
    print("  4. Add Slot -> Monday -> Label='Test' -> Save")
    print("     Console should show:")
    print("       [Timetable] Save clicked. modalDay.value = \"0\"")
    print("       [Timetable] Validation OK -> {day: 0, ...}")
    print("       [Timetable] POST payload -> [{day: 0, label: 'Test', ...}]")
    print("  5. Server terminal should show:")
    print("       POST .../day-schedules/ 200 <size>")
    print("     where <size> is ~55 bytes (created: 1).")
    print("  6. Refresh page -> Monday row should still be there.")
    print("=" * 60)


if __name__ == "__main__":
    main()
