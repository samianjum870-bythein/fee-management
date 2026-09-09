#!/usr/bin/env python3
"""
axis_patcher_fix_import.py - Fix broken import lines in public_urls.py.

This patcher fixes the syntax error where the import from timetable and periods
are on the same line incorrectly.

Usage:
    python axis_patcher_fix_import.py [--dry-run] [--verbose] [--target-dir PATH]

Idempotent: safe to run multiple times.
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, List

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
TARGET_DIR = Path.cwd()
DRY_RUN = False
VERBOSE = False
LOG: List[str] = []


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


# ----------------------------------------------------------------------
# Patches
# ----------------------------------------------------------------------

def fix_public_urls_import(urls_path: Path) -> bool:
    """Fix the broken import line in public_urls.py."""
    content = read_file(urls_path)
    if content is None:
        log(f"URLs file not found: {urls_path}", "ERROR")
        return False

    # Check if the line is already fixed (i.e., two separate lines)
    # Look for pattern where we have two import statements on one line
    # Specifically: ... api_update_holidayfrom .views.periods import ...
    # We'll replace that with newline after api_update_holiday
    # Also check if the line already has a newline.
    if "api_update_holidayfrom" not in content:
        log("Import line already appears to be fixed; skipping.")
        return True

    # Replace the erroneous line.
    # We'll locate the line containing "from .views.timetable import" and split it.
    # Safer: split at "from .views.periods" and add newline before it.
    pattern = r'(from \.views\.timetable import .*?api_update_holiday)from \.views\.periods import (.*)'
    replacement = r'\1\nfrom .views.periods import \2'
    new_content = re.sub(pattern, replacement, content)

    if new_content == content:
        log("No changes made; pattern did not match.", "WARNING")
        return False

    return write_file(urls_path, new_content)


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

    log(f"Starting axis_patcher_fix_import.py (dry-run={DRY_RUN}, verbose={VERBOSE})")
    log(f"Target directory: {TARGET_DIR}")

    if not (TARGET_DIR / "manage.py").exists() or not (TARGET_DIR / "axis_saas").exists():
        log("Target directory does not appear to be a Django project root.", "ERROR")
        sys.exit(1)

    urls_path = TARGET_DIR / "axis_saas" / "public_urls.py"
    if not urls_path.exists():
        log(f"public_urls.py not found at {urls_path}", "ERROR")
        sys.exit(1)

    success = fix_public_urls_import(urls_path)

    if success:
        log("Import line fixed successfully.")
        if not DRY_RUN:
            log("Restart the server to apply changes.")
    else:
        log("Failed to fix import line. See logs above.", "ERROR")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
