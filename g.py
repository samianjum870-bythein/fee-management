#!/usr/bin/env python3
"""
axis_patcher_add_holiday_js.py - Add missing holiday management JavaScript to timetable template.

This patcher inserts the holiday management event handlers and helper functions
inside the existing DOMContentLoaded block so the Add/Delete buttons work.

Usage:
    python axis_patcher_add_holiday_js.py [--dry-run] [--verbose] [--target-dir PATH]

Idempotent: safe to run multiple times.
"""

import os
import re
import sys
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


def patch_template(path: Path) -> bool:
    content = read_file(path)
    if content is None:
        log(f"Template not found: {path}", "ERROR")
        return False

    # We need to find the DOMContentLoaded block and insert our code inside it.
    # The block looks like:
    # document.addEventListener('DOMContentLoaded', function() {
    #     ... existing code ...
    # });
    # We'll insert before the final closing brace of that function.
    # We'll search for the pattern and insert our code before the last '}' of the function.

    # Find the start of the DOMContentLoaded block.
    start_pattern = r"document\.addEventListener\s*\(\s*['\"]DOMContentLoaded['\"]\s*,\s*function\s*\(\)\s*\{"
    match_start = re.search(start_pattern, content, re.DOTALL)
    if not match_start:
        log("Could not find DOMContentLoaded block start", "ERROR")
        return False

    # Now we need to find the matching closing brace. We'll count braces from the start.
    start_pos = match_start.end()  # position after the opening brace
    brace_count = 1  # we just passed the opening brace of the function
    pos = start_pos
    while pos < len(content):
        if content[pos] == '{':
            brace_count += 1
        elif content[pos] == '}':
            brace_count -= 1
            if brace_count == 0:
                # Found the closing brace of the function
                insert_pos = pos  # we want to insert before this brace
                break
        pos += 1
    else:
        log("Could not find matching closing brace for DOMContentLoaded", "ERROR")
        return False

    # Now we have the position to insert. We'll insert our code before that brace.
    holiday_js = """
        // ====== HOLIDAY MANAGEMENT ======
        // --- Helper to add holiday ---
        async function addHoliday(type, data) {
            try {
                const res = await fetch(`/portal/${schema}/api/timetable/holiday/add/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ type, ...data })
                });
                const result = await res.json();
                if (!res.ok) {
                    await showAlert(result.error || 'Error adding holiday', 'Error');
                    return false;
                }
                return result;
            } catch (err) {
                console.error(err);
                await showAlert('Network error', 'Error');
                return false;
            }
        }

        // --- Helper to delete holiday ---
        async function deleteHoliday(type, id) {
            try {
                const res = await fetch(`/portal/${schema}/api/timetable/holiday/delete/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ type, id })
                });
                const result = await res.json();
                if (!res.ok) {
                    await showAlert(result.error || 'Error deleting holiday', 'Error');
                    return false;
                }
                return true;
            } catch (err) {
                console.error(err);
                await showAlert('Network error', 'Error');
                return false;
            }
        }

        // --- Weekly Holiday ---
        document.getElementById('addWeeklyHolidayBtn').addEventListener('click', function() {
            document.getElementById('weeklyHolidayModal').classList.add('active');
        });
        document.getElementById('weeklyModalCancelBtn').addEventListener('click', function() {
            document.getElementById('weeklyHolidayModal').classList.remove('active');
        });
        document.getElementById('weeklyModalSaveBtn').addEventListener('click', async function() {
            const day = parseInt(document.getElementById('weeklyDay').value);
            const label = document.getElementById('weeklyLabel').value.trim();
            if (!day || !label) {
                await showAlert('Please select a day and enter a label.', 'Missing Data');
                return;
            }
            const result = await addHoliday('weekly', { day_of_week: day, label });
            if (result && result.success) {
                document.getElementById('weeklyHolidayModal').classList.remove('active');
                window.location.reload(); // simple refresh
            }
        });

        // --- Annual Holiday ---
        document.getElementById('addAnnualHolidayBtn').addEventListener('click', function() {
            document.getElementById('annualHolidayModal').classList.add('active');
        });
        document.getElementById('annualModalCancelBtn').addEventListener('click', function() {
            document.getElementById('annualHolidayModal').classList.remove('active');
        });
        document.getElementById('annualModalSaveBtn').addEventListener('click', async function() {
            const month = parseInt(document.getElementById('annualMonth').value);
            const day = parseInt(document.getElementById('annualDay').value);
            const label = document.getElementById('annualLabel').value.trim();
            if (!month || !day || !label) {
                await showAlert('Please fill all fields.', 'Missing Data');
                return;
            }
            const result = await addHoliday('annual', { month, day, label });
            if (result && result.success) {
                document.getElementById('annualHolidayModal').classList.remove('active');
                window.location.reload();
            }
        });

        // --- Vacation ---
        document.getElementById('addVacationBtn').addEventListener('click', function() {
            document.getElementById('vacationModal').classList.add('active');
        });
        document.getElementById('vacationModalCancelBtn').addEventListener('click', function() {
            document.getElementById('vacationModal').classList.remove('active');
        });
        document.getElementById('vacationModalSaveBtn').addEventListener('click', async function() {
            const name = document.getElementById('vacationName').value.trim();
            const start_date = document.getElementById('vacationStart').value;
            const end_date = document.getElementById('vacationEnd').value;
            const description = document.getElementById('vacationDescription').value.trim();
            if (!name || !start_date || !end_date) {
                await showAlert('Please fill name, start, and end dates.', 'Missing Data');
                return;
            }
            const result = await addHoliday('vacation', { name, start_date, end_date, description });
            if (result && result.success) {
                document.getElementById('vacationModal').classList.remove('active');
                window.location.reload();
            }
        });

        // --- Delete buttons (attach to existing rows) ---
        document.querySelectorAll('.delete-holiday-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                const type = this.dataset.type;
                const id = this.dataset.id;
                const confirmed = await showConfirm('Delete this holiday?', 'Confirm Deletion');
                if (confirmed) {
                    const success = await deleteHoliday(type, id);
                    if (success) {
                        window.location.reload();
                    }
                }
            });
        });

        // Close modals on overlay click
        document.querySelectorAll('.slot-modal-overlay').forEach(overlay => {
            overlay.addEventListener('click', function(e) {
                if (e.target === this) {
                    this.classList.remove('active');
                }
            });
        });
"""

    # Insert the code at the insert_pos
    new_content = content[:insert_pos] + holiday_js + content[insert_pos:]

    if not DRY_RUN:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

    log("Inserted holiday management JavaScript into DOMContentLoaded block.")
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

    log(f"Starting axis_patcher_add_holiday_js.py (dry-run={DRY_RUN}, verbose={VERBOSE})")
    log(f"Target directory: {TARGET_DIR}")

    if not (TARGET_DIR / "manage.py").exists() and not (TARGET_DIR / "axis_saas").exists():
        log("Target directory does not appear to be a Django project root.", "ERROR")
        sys.exit(1)

    template_path = TARGET_DIR / "templates" / "tenant" / "timetable_management.html"
    if not template_path.exists():
        log("Template file not found.", "ERROR")
        sys.exit(1)

    success = patch_template(template_path)

    if DRY_RUN:
        log("DRY-RUN completed. No changes written.")
    else:
        if success:
            log("Holiday management JavaScript added successfully.")
            log("Refresh the page and the Add buttons should now work.")
        else:
            log("Failed to patch template.", "ERROR")
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
