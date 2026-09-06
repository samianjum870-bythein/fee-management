#!/usr/bin/env python3
"""
axis_patcher_fix_wing_display.py - Fix wing dropdown to show hierarchical "Parent (Child)" names.

This patcher updates the wing filter dropdown in the modal (and anywhere else) 
to display the full hierarchical name using the WingCategory __str__ method.
It replaces {{ wing.name }} with {{ wing }} in the wing_school_class_management.html template.

Run with --dry-run to preview changes.
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime
import argparse

# ----------------------------------------------------------------------
# Configuration
TEMPLATE_WING = "templates/tenant/wing_school_class_management.html"

# ----------------------------------------------------------------------
def log(message, verbose=False):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if verbose:
        print(f"[{timestamp}] {message}")
    else:
        print(message)

def read_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        log(f"❌ Error reading {file_path}: {e}", True)
        return None

def write_file(file_path, content, dry_run=False, verbose=False):
    if dry_run:
        log(f"🔍 DRY RUN: Would write to {file_path}", verbose)
        return True
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        log(f"✅ Updated {file_path}", verbose)
        return True
    except Exception as e:
        log(f"❌ Error writing {file_path}: {e}", True)
        return False

# ----------------------------------------------------------------------
def patch_template(file_path, dry_run=False, verbose=False):
    if not os.path.isfile(file_path):
        log(f"❌ File not found: {file_path}", verbose)
        return False

    content = read_file(file_path)
    if content is None:
        return False

    # Replace {{ wing.name }} with {{ wing }} in the wing filter dropdowns.
    # We'll look for patterns in the modal and possibly the tab content.
    # The current wing filter selects appear as:
    #   <option value="{{ wing.id }}">{{ wing.name }}</option>
    # We want to change to:
    #   <option value="{{ wing.id }}">{{ wing }}</option>

    # Use regex to replace all occurrences of '{{ wing.name }}' with '{{ wing }}'
    # but only inside the template, not in the Django template comments or scripts.
    # We'll do a global replacement.
    pattern = r'\{\{\s*wing\.name\s*\}\}'
    replacement = '{{ wing }}'

    new_content = re.sub(pattern, replacement, content)

    # Also check for the modal's wing filter where it might be written differently:
    # <option value="{{ wing.id }}" ...>{{ wing.name }}</option>
    # The pattern above should catch it.

    if new_content == content:
        log(f"⏭️  No changes needed in {file_path}", verbose)
        return True

    return write_file(file_path, new_content, dry_run, verbose)

# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Fix wing dropdown display to show hierarchical names.")
    parser.add_argument('--dry-run', action='store_true', help="Preview changes without applying.")
    parser.add_argument('--verbose', action='store_true', help="Show detailed output.")
    parser.add_argument('--target-dir', default='.', help="Project root directory (default: current).")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        log(f"❌ Target directory does not exist: {target_dir}")
        sys.exit(1)

    os.chdir(target_dir)

    log("🚀 Starting patcher to fix wing dropdown display...", args.verbose)
    if args.dry_run:
        log("🧪 DRY RUN mode - no changes will be applied.", args.verbose)

    success = True

    if not patch_template(TEMPLATE_WING, dry_run=args.dry_run, verbose=args.verbose):
        success = False

    if success:
        log("🎯 Patcher completed successfully.", args.verbose)
        log("   The wing dropdown will now show hierarchical names like 'Parent (Child)'.")
    else:
        log("❌ Patcher encountered errors. Check verbose output for details.", args.verbose)
        sys.exit(1)

if __name__ == "__main__":
    main()
