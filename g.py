#!/usr/bin/env python3
"""
axis_patcher.py - Patcher for AXIS Fee Management Staff Portal.

This script applies a fix to the verify-passkey flow so that it uses the same
WebAuthn authentication options logic as the working "Login with Passkey" button.

The change ensures that when the session has staff_pending_passkey=True, the
authentication options endpoint ignores the pending identity and instead uses
the provided username to look up credentials. This makes the verify-passkey flow
behave identically to the login page's passkey login, which is known to work.

Usage:
    python axis_patcher.py [--dry-run] [--verbose] [--target-dir /path/to/project]
"""

import os
import re
import sys
import shutil
from pathlib import Path
from datetime import datetime
import argparse

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

TARGET_FILE = "axis_saas/views/staff_portal.py"

# The exact lines to locate in the function
ASSIGN_LINE_1 = "resolved_staff_id = pending_staff_id or staff_id"
ASSIGN_LINE_2 = "resolved_schema_name = pending_schema_name or staff_schema_name"

# The lines to insert after the assignments
INSERT_LINES = [
    "        # If this is a pending passkey verification, use the provided username instead of resolved identity.",
    "        if request.session.get('staff_pending_passkey'):",
    "            resolved_staff_id = None",
    "            resolved_schema_name = None",
    "            logger.info('AUTH OPTIONS - Pending passkey verification, forcing username-based lookup')",
]

# Check for idempotency: if this line already exists, skip.
IDEMPOTENCY_CHECK = "if request.session.get('staff_pending_passkey'):"

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

def log(msg, verbose=False, force=False):
    if verbose or force:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

# -----------------------------------------------------------------------------
# Patcher
# -----------------------------------------------------------------------------

def patch_file(filepath, dry_run=False, verbose=False):
    """Apply the patch to the target file."""
    if not filepath.exists():
        log(f"ERROR: Target file not found: {filepath}", force=True)
        return False

    log(f"Reading {filepath}", verbose)
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Find the function definition
    func_start = None
    func_indent = None
    for i, line in enumerate(lines):
        if re.match(r'^def staff_webauthn_authentication_options\(request\):', line):
            func_start = i
            # Determine the indentation of the function body (should be 4 spaces)
            # The next non-empty line will have the indentation.
            for j in range(i+1, len(lines)):
                if lines[j].strip():
                    func_indent = len(lines[j]) - len(lines[j].lstrip())
                    break
            break

    if func_start is None:
        log("ERROR: Could not find function staff_webauthn_authentication_options", force=True)
        return False

    if func_indent is None:
        log("ERROR: Could not determine function body indentation", force=True)
        return False

    log(f"Found function at line {func_start+1}, indentation = {func_indent} spaces", verbose)

    # Now find the assignment lines within the function
    assign_line_index_1 = None
    assign_line_index_2 = None
    for i in range(func_start+1, len(lines)):
        line = lines[i]
        if ASSIGN_LINE_1 in line and not line.strip().startswith('#'):
            assign_line_index_1 = i
            break

    if assign_line_index_1 is None:
        log("ERROR: Could not find line containing '{ASSIGN_LINE_1}'", force=True)
        return False

    # Look for the second assignment line after the first
    for i in range(assign_line_index_1+1, len(lines)):
        line = lines[i]
        if ASSIGN_LINE_2 in line and not line.strip().startswith('#'):
            assign_line_index_2 = i
            break

    if assign_line_index_2 is None:
        log("ERROR: Could not find line containing '{ASSIGN_LINE_2}'", force=True)
        return False

    log(f"Found assignment lines at {assign_line_index_1+1} and {assign_line_index_2+1}", verbose)

    # Check if the patch is already applied (look for the idempotency check line after the assignments)
    patch_already_applied = False
    for i in range(assign_line_index_2+1, min(assign_line_index_2+10, len(lines))):
        if IDEMPOTENCY_CHECK in lines[i]:
            patch_already_applied = True
            break

    if patch_already_applied:
        log("Patch already applied; skipping.", verbose)
        return True  # success, nothing to do

    # Build the new lines to insert
    # The insert lines should have the same indentation as the function body
    # The assignments are already indented to func_indent, so we insert at that same level.
    insert_indented = [f"{' ' * func_indent}{line.lstrip()}" for line in INSERT_LINES]

    # Insert after the second assignment line
    insert_index = assign_line_index_2 + 1
    new_lines = lines[:insert_index] + insert_indented + lines[insert_index:]

    if dry_run:
        log("DRY RUN: Would write changes to {filepath}", force=True)
        # Show a diff snippet
        log("Changes:", force=True)
        for i in range(max(0, assign_line_index_1-1), min(len(new_lines), assign_line_index_1+10)):
            if i < len(lines):
                old_line = lines[i].rstrip()
            else:
                old_line = ""
            if i < len(new_lines):
                new_line = new_lines[i].rstrip()
            else:
                new_line = ""
            if old_line != new_line:
                log(f"  - {old_line}", force=True)
                log(f"  + {new_line}", force=True)
        return True

    # Write the file
    log(f"Writing changes to {filepath}", verbose)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

    log("Patch applied successfully.", force=True)
    return True

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AXIS Fee Management Patcher")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current directory)")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        log(f"ERROR: Target directory '{target_dir}' does not exist.", force=True)
        sys.exit(1)

    target_file = target_dir / TARGET_FILE
    success = patch_file(target_file, dry_run=args.dry_run, verbose=args.verbose)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
