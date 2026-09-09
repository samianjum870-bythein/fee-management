#!/usr/bin/env python3
"""
axis_patcher_final_vacation_overlap.py - Add overlap validation to vacation update.

This patcher adds overlap checking to the `api_update_holiday` function
(excluding the vacation being updated), preventing overlapping vacation dates.

It also ensures that the view handles database errors gracefully and that
`next_vacation` is always defined.

Usage:
    python axis_patcher_final_vacation_overlap.py [--dry-run] [--verbose] [--target-dir PATH]

Idempotent: safe to run multiple times.
"""

import os
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
TARGET_DIR = Path.cwd()
DRY_RUN = False
VERBOSE = False
LOG = []


def log(msg: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.append(f"[{timestamp}] {level}: {msg}")
    if VERBOSE or level in ("ERROR", "WARNING"):
        print(f"{level}: {msg}")


def read_file(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_file(path: Path, content: str) -> bool:
    if DRY_RUN:
        log(f"DRY-RUN: would write {path}", "DRY")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    log(f"Written: {path}")
    return True


def patch_view(path: Path) -> bool:
    content = read_file(path)
    if content is None:
        log(f"View file not found: {path}", "ERROR")
        return False

    # Check if overlap validation is already present in api_update_holiday.
    if "overlapping = Vacation.objects.filter" in content and "exclude(id=hid)" in content:
        log("Overlap validation already present in api_update_holiday; skipping patch.", "INFO")
        return True

    # We'll insert the overlap check into the `elif htype == 'vacation':` block of api_update_holiday.
    # We'll locate the function, then the branch, then insert before the assignments.

    # First, find the start of api_update_holiday.
    update_start = content.find("def api_update_holiday")
    if update_start == -1:
        log("api_update_holiday not found.", "ERROR")
        return False

    # Within that function, find the `elif htype == 'vacation':` branch.
    branch_pos = content.find("elif htype == 'vacation':", update_start)
    if branch_pos == -1:
        log("Could not find vacation branch in api_update_holiday.", "ERROR")
        return False

    # Now we need to locate the line `holiday.name = name` which is where we'll insert before.
    # We'll search for `holiday.name = name` after the branch.
    assign_pos = content.find("holiday.name = name", branch_pos)
    if assign_pos == -1:
        log("Could not find 'holiday.name = name' line in vacation branch.", "ERROR")
        return False

    # Find the newline before that line to insert after the previous line.
    prev_newline = content.rfind('\n', 0, assign_pos)
    if prev_newline == -1:
        prev_newline = 0

    # Build the overlap check code with proper indentation.
    # We'll extract the indentation from the line containing `holiday.name = name`.
    # Determine the indentation level: count spaces before that line.
    line_start = content.rfind('\n', 0, assign_pos) + 1
    indent = content[line_start:assign_pos]  # get the spaces before 'holiday.name = name'
    # If the line might have trailing spaces, we'll use that as the base indent.
    # We'll add one more level (4 spaces) for the filter statements? Actually the check block should be indented at the same level as the lines before it.
    # The lines before are indented with 16 spaces (from the file). We'll use the same indent for the check.
    # We'll build the check with that indent.
    check_code = f"""{indent}# ---- Check for overlapping vacations (excluding self) ----
{indent}overlapping = Vacation.objects.filter(
{indent}    start_date__lte=end,
{indent}    end_date__gte=start
{indent}).exclude(id=hid).exists()
{indent}if overlapping:
{indent}    return JsonResponse({{'error': 'The selected date range overlaps with an existing vacation.'}}, status=400)
"""
    # Insert before the `holiday.name = name` line.
    new_content = content[:assign_pos] + check_code + content[assign_pos:]

    # Also ensure `next_vacation` is defined in the view even if an exception occurs.
    # In the main view, the try-except sets vacations = [] but doesn't define next_vacation.
    # We'll add a fallback before the try block: next_vacation = None.
    # But the user might already have that; we'll check and add if missing.
    if "next_vacation = None" not in new_content:
        # Find the try block for vacations and insert `next_vacation = None` before it.
        # We'll locate the line `try:` that is immediately after `# Vacations`.
        vac_try = new_content.find("try:\n            vacations = Vacation.objects.all().order_by('start_date')")
        if vac_try != -1:
            # Insert `next_vacation = None` before that try.
            insert_pos = new_content.rfind('\n', 0, vac_try) + 1
            # Find the indentation of the try line.
            indent_try = new_content[insert_pos:vac_try]  # get spaces before try
            # Insert next_vacation = None at that indentation.
            new_content = new_content[:insert_pos] + f"{indent_try}next_vacation = None\n" + new_content[insert_pos:]

    # Ensure the `except` block also sets next_vacation = None (though it's already set before try, so safe).
    # The except block currently does: logger.warning(...) and vacations = []
    # We'll add `next_vacation = None` there as well to be safe.
    # We'll search for `except Exception as e:` and insert `next_vacation = None` after the vacations = [] line.
    except_pos = new_content.find("except Exception as e:")
    if except_pos != -1:
        # Find the line `vacations = []` within that except block.
        vac_assign = new_content.find("vacations = []", except_pos)
        if vac_assign != -1:
            # Find the newline after that line to insert after.
            after_assign = new_content.find('\n', vac_assign)
            if after_assign != -1:
                # Determine indentation of that line.
                line_start = new_content.rfind('\n', 0, vac_assign) + 1
                indent_except = new_content[line_start:vac_assign]
                insert_code = f"{indent_except}next_vacation = None\n"
                new_content = new_content[:after_assign+1] + insert_code + new_content[after_assign+1:]

    if not DRY_RUN:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        log("Added overlap validation to api_update_holiday and ensured next_vacation is defined.")
    else:
        log("DRY-RUN: would add overlap validation and define next_vacation.")

    return True


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    global TARGET_DIR, DRY_RUN, VERBOSE

    args = sys.argv[1:]
    for arg in args:
        if arg == "--dry-run":
            DRY_RUN = True
        elif arg == "--verbose":
            VERBOSE = True
        elif arg.startswith("--target-dir="):
            TARGET_DIR = Path(arg.split("=", 1)[1])
        else:
            print(f"Unknown argument: {arg}", file=sys.stderr)
            sys.exit(1)

    log(f"Starting axis_patcher_final_vacation_overlap.py (dry-run={DRY_RUN}, verbose={VERBOSE})")
    log(f"Target directory: {TARGET_DIR}")

    if not (TARGET_DIR / "manage.py").exists() and not (TARGET_DIR / "axis_saas").exists():
        log("Target directory does not appear to be a Django project root.", "ERROR")
        sys.exit(1)

    view_path = TARGET_DIR / "axis_saas" / "views" / "timetable.py"
    if not view_path.exists():
        log("View file not found.", "ERROR")
        sys.exit(1)

    success = patch_view(view_path)

    if DRY_RUN:
        log("DRY-RUN completed. No changes written.")
    else:
        if success:
            log("Vacation overlap validation added successfully.")
            log("Restart the server to apply changes.")
        else:
            log("Failed to patch view.", "ERROR")
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
