#!/usr/bin/env python3
"""
fix_duplicate_msg.py
====================
Only 2 fixes:
  1. views/timetable.py - return friendly 400 on duplicate, instead of 500 + traceback.
  2. timetable_management.html - frontend pre-check: (day,label) duplicate ho to
     request bhejne se pehle hi alert dikhao.

Usage:
    python3 fix_duplicate_msg.py [--dry-run] [--no-backup] [--target-dir=.]
"""

import sys, shutil
from pathlib import Path
from datetime import datetime

DRY_RUN = False
BACKUP = True


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": "  ", "OK": "  ✓ ", "WARN": "  ! ", "ERR": "  ✗ ", "DRY": "  ~ "}
    print(f"[{ts}] {icons.get(level, '  ')}{msg}")


def read(p): return p.read_text(encoding="utf-8") if p.exists() else None


def write(p, content):
    if DRY_RUN:
        log(f"DRY-RUN would write {p}", "DRY"); return True
    if BACKUP:
        bak = p.with_suffix(p.suffix + ".bak_dupmsg")
        if not bak.exists():
            shutil.copy2(p, bak); log(f"backup -> {bak.name}")
    p.write_text(content, encoding="utf-8")
    log(f"wrote {p}", "OK")
    return True


def replace_once(text, old, new, label):
    if old not in text:
        log(f"NOT FOUND: {label}", "WARN"); return text, False
    return text.replace(old, new, 1), True


# =====================================================================
# 1) views/timetable.py
# =====================================================================
def patch_view(path: Path) -> bool:
    log(f"patching {path}")
    content = read(path)
    if content is None:
        log("views/timetable.py not found", "ERR"); return False
    original = content
    n = 0

    # (a) payload pre-check: (day,label) must be unique in the payload itself
    if "Duplicate check: (label, day) must be unique" not in content:
        old = (
            "        deleted_all, _ = DaySchedule.objects.filter(academic_calendar=calendar).delete()\n"
            "        logger.info(f\"Deleted {deleted_all} existing schedules for calendar {calendar.pk}\")\n"
            "\n"
            "        total_created = 0\n"
            "        for idx, item in enumerate(schedules_data):\n"
        )
        new = (
            "        # ---- Duplicate check inside payload ----\n"
            "        seen = {}\n"
            "        for i, item in enumerate(schedules_data):\n"
            "            lbl = (item.get('label') or '').strip()\n"
            "            dy  = item.get('day')\n"
            "            if lbl and dy is not None:\n"
            "                key = (int(dy), lbl.lower())\n"
            "                if key in seen:\n"
            "                    day_name = dict(TimetableEntry.DAY_CHOICES).get(dy, str(dy))\n"
            "                    return JsonResponse({\n"
            "                        'error': f\"Ye label '{lbl}' {day_name} ke liye pehle hi set hai. \"\n"
            "                                 f\"Koi doosra label ya doosra din chunain.\"\n"
            "                    }, status=400)\n"
            "                seen[key] = i\n"
            "\n"
            "        deleted_all, _ = DaySchedule.objects.filter(academic_calendar=calendar).delete()\n"
            "        logger.info(f\"Deleted {deleted_all} existing schedules for calendar {calendar.pk}\")\n"
            "\n"
            "        total_created = 0\n"
            "        for idx, item in enumerate(schedules_data):\n"
        )
        content, ok = replace_once(content, old, new, "payload pre-check"); n += int(ok)

    # (b) catch IntegrityError with friendly message
    old_create = (
        "            try:\n"
        "                DaySchedule.objects.create(\n"
        "                    academic_calendar=calendar,\n"
        "                    day_of_week=day,\n"
        "                    order=idx,\n"
        "                    label=label,\n"
        "                    start_time=start_time,\n"
        "                    end_time=end_time,\n"
        "                    periods=periods_int,\n"
        "                    duration=duration_int\n"
        "                )\n"
        "                total_created += 1\n"
        "            except Exception as e:\n"
        "                logger.exception(f\"Error creating DaySchedule for day {day}, index {idx}: {e}\")\n"
        "                return JsonResponse({'error': f'Failed to save day {day} slot {idx}: {str(e)}'}, status=500)\n"
    )
    new_create = (
        "            try:\n"
        "                DaySchedule.objects.create(\n"
        "                    academic_calendar=calendar,\n"
        "                    day_of_week=day,\n"
        "                    order=idx,\n"
        "                    label=label,\n"
        "                    start_time=start_time,\n"
        "                    end_time=end_time,\n"
        "                    periods=periods_int,\n"
        "                    duration=duration_int\n"
        "                )\n"
        "                total_created += 1\n"
        "            except IntegrityError as e:\n"
        "                day_name = dict(TimetableEntry.DAY_CHOICES).get(day, str(day))\n"
        "                logger.warning(f\"Duplicate schedule for {day_name} / {label}: {e}\")\n"
        "                return JsonResponse({\n"
        "                    'error': f\"Ye label '{label}' {day_name} ke liye pehle hi set hai. \"\n"
        "                             f\"Koi doosra label ya doosra din chunain.\"\n"
        "                }, status=400)\n"
        "            except Exception as e:\n"
        "                logger.exception(f\"Error creating DaySchedule for day {day}, index {idx}: {e}\")\n"
        "                return JsonResponse({'error': f'Failed to save day {day} slot {idx}: {str(e)}'}, status=500)\n"
    )
    content, ok = replace_once(content, old_create, new_create, "IntegrityError friendly handler"); n += int(ok)

    if content != original:
        write(path, content)
        log(f"views/timetable.py: {n} change(s)", "OK")
    else:
        log("views/timetable.py: no changes (already fixed?)", "WARN")
    return True


# =====================================================================
# 2) timetable_management.html
# =====================================================================
def patch_template(path: Path) -> bool:
    log(f"patching {path}")
    content = read(path)
    if content is None:
        log("timetable_management.html not found", "ERR"); return False
    original = content
    n = 0

    if "DUPLICATE (day,label) GUARD" in content:
        log("template already has duplicate guard", "WARN"); return True

    # Insert BEFORE the try{ if(editingRow) ... } catch block
    anchor = (
        "            const dayLabel = modalDay.options[modalDay.selectedIndex].text;\n"
        "\n"
        "            try {\n"
        "            if (editingRow) {\n"
    )
    replacement = (
        "            const dayLabel = modalDay.options[modalDay.selectedIndex].text;\n"
        "\n"
        "            // ---------- DUPLICATE (day,label) GUARD ----------\n"
        "            // Same label + same day = blocked. Same label + different day = OK.\n"
        "            let dupRow = null;\n"
        "            dayScheduleBody.querySelectorAll('tr[data-day]').forEach(function (existingRow) {\n"
        "                if (editingRow && existingRow === editingRow) return;\n"
        "                const existingDay = parseInt(existingRow.dataset.day, 10);\n"
        "                const existingLabel = ((existingRow.querySelectorAll('td')[1] || {}).textContent || '').trim();\n"
        "                if (existingDay === day && existingLabel.toLowerCase() === label.toLowerCase()) {\n"
        "                    dupRow = existingRow;\n"
        "                }\n"
        "            });\n"
        "            if (dupRow) {\n"
        "                await showAlert(\n"
        "                    'Ye label \"' + label + '\" ' + dayLabel + ' ke liye pehle hi set hai.\\n\\n' +\n"
        "                    'Koi doosra label ya doosra din chunain.',\n"
        "                    'Already Set'\n"
        "                );\n"
        "                return;\n"
        "            }\n"
        "            // ---------- end guard ----------\n"
        "\n"
        "            try {\n"
        "            if (editingRow) {\n"
    )
    content, ok = replace_once(content, anchor, replacement, "duplicate guard"); n += int(ok)

    if content != original:
        write(path, content)
        log(f"template: {n} change(s)", "OK")
    else:
        log("template: no changes", "WARN")
    return True


# =====================================================================
def main():
    global DRY_RUN, BACKUP
    target = Path.cwd()
    for a in sys.argv[1:]:
        if a == "--dry-run":   DRY_RUN = True
        elif a == "--no-backup": BACKUP = False
        elif a.startswith("--target-dir="): target = Path(a.split("=",1)[1]).resolve()
        else:
            print(f"unknown arg: {a}", file=sys.stderr); sys.exit(1)

    print("=" * 60)
    print("  fix_duplicate_msg.py")
    print(f"  target : {target}")
    print(f"  dry-run: {DRY_RUN}")
    print("=" * 60)

    if not (target / "manage.py").exists():
        log("manage.py not found", "ERR"); sys.exit(2)

    patch_view(target / "axis_saas" / "views" / "timetable.py")
    patch_template(target / "templates" / "tenant" / "timetable_management.html")

    print("=" * 60)
    print("DONE.")
    print("")
    print("Ab:")
    print("  python3 manage.py runserver")
    print("  Ctrl+Shift+R")
    print("")
    print("Test:")
    print("  • Same day + same label  -> simple alert: 'Ye label ... pehle hi set hai'")
    print("  • Different day ya different label -> save chalega")
    print("=" * 60)


if __name__ == "__main__":
    main()
