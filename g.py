#!/usr/bin/env python3
"""
axis_patcher.py

Apply teacher display fix to Django templates:
- Make teacher name bold and slightly larger.
- Wrap 'son of', 'daughter of', 'child of' in parentheses.

Usage:
    python axis_patcher.py [--dry-run] [--verbose] [--target-dir PATH]
"""

import os
import re
import sys
import shutil
from pathlib import Path
from datetime import datetime
import argparse

# Logging helper
def log(message, verbose=False, always=True):
    if always or verbose:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}")

def apply_patch(file_path, dry_run=False, verbose=False):
    """
    Apply the teacher display fix to the given file.
    Returns True if changed, False if no change needed.
    """
    if not file_path.exists():
        log(f"File not found: {file_path}", verbose)
        return False

    log(f"Processing: {file_path}", verbose)

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Define the old pattern (the line we want to replace)
    # Match the teacher option line with any whitespace variation.
    old_pattern = r'(<option value="{{ teacher\.id }}"><b>{{ teacher\.first_name }}</b> {% if teacher\.gender == "male" %}son of{% elif teacher\.gender == "female" %}daughter of{% else %}child of{% endif %} {{ teacher\.last_name }}</option>)'

    # New line with bold style and parentheses
    new_line = '<option value="{{ teacher.id }}"><b style="font-size:1.1em;">{{ teacher.first_name }}</b> {% if teacher.gender == "male" %}(son of){% elif teacher.gender == "female" %}(daughter of){% else %}(child of){% endif %} {{ teacher.last_name }}</option>'

    # Check if the old pattern exists
    if not re.search(old_pattern, content, re.DOTALL):
        log(f"Old pattern not found in {file_path}; skipping.", verbose)
        # Also check if new pattern is already present (idempotency)
        if new_line in content:
            log(f"New pattern already present in {file_path}; no change needed.", verbose)
        return False

    # Perform replacement
    new_content = re.sub(old_pattern, new_line, content, flags=re.DOTALL)

    if dry_run:
        log(f"Would modify: {file_path}", verbose)
        return True

    # Write back
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    log(f"Modified: {file_path}")
    return True

def main():
    parser = argparse.ArgumentParser(description="Apply teacher display fix to Django templates.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output.")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current).")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        log(f"Target directory does not exist: {target_dir}", verbose=args.verbose)
        sys.exit(1)

    # List of files to patch (relative to target_dir)
    files_to_patch = [
        "templates/tenant/wing_school_class_management.html",
        "templates/tenant/class_management.html",
    ]

    changes_made = 0
    for rel_path in files_to_patch:
        file_path = target_dir / rel_path
        if apply_patch(file_path, dry_run=args.dry_run, verbose=args.verbose):
            changes_made += 1

    if args.dry_run:
        log(f"Dry run completed. Would modify {changes_made} file(s).", always=True)
    else:
        log(f"Patched {changes_made} file(s).", always=True)

if __name__ == "__main__":
    main()
