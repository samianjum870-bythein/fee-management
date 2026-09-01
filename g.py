#!/usr/bin/env python3
"""
axis_patcher.py - Fix for staff_webauthn_authentication_options broken by previous patch.

This script corrects the malformed insertion in the function that handles passkey
authentication options. It restores the correct logic: when staff_pending_passkey
is True, force username-based lookup (like the login page), but keep it properly
indented and without breaking the login page passkey flow.

Usage:
    python axis_patcher.py [--dry-run] [--verbose] [--target-dir /path/to/project]
"""

import os
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

TARGET_FILE = "axis_saas/views/staff_portal.py"

# The exact function we need to fix
FUNCTION_NAME = "def staff_webauthn_authentication_options(request):"

# The pattern to locate the broken region.
# We'll find the lines:
#     resolved_staff_id = pending_staff_id or staff_id
#     resolved_schema_name = pending_schema_name or staff_schema_name
# Then the malformed stuff, and the line:
#     resolved_username = request.session.get('pending_username') or request.session.get('staff_username') or provided_username
# We'll replace everything from the first assignment to that resolved_username line with corrected code.

# The corrected code block to insert:
CORRECTED_BLOCK = """        resolved_staff_id = pending_staff_id or staff_id
        resolved_schema_name = pending_schema_name or staff_schema_name

        # If this is a pending passkey verification, use the provided username instead of resolved identity.
        if request.session.get('staff_pending_passkey'):
            resolved_staff_id = None
            resolved_schema_name = None
            logger.info('AUTH OPTIONS - Pending passkey verification, forcing username-based lookup')

        resolved_username = request.session.get('pending_username') or request.session.get('staff_username') or provided_username"""

# The broken block may appear with different whitespace; we'll use a robust approach:
# 1. Find the line with the first assignment.
# 2. Find the line with the second assignment.
# 3. Find the line that starts with "resolved_username =".
# 4. Replace everything between (and including) the first assignment and the resolved_username line.

# We'll use a regex to match the whole region, but we can also do line-by-line.

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

    # Find the function start
    func_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith(FUNCTION_NAME):
            func_start = i
            break

    if func_start is None:
        log(f"ERROR: Could not find function {FUNCTION_NAME}", force=True)
        return False

    log(f"Found function at line {func_start+1}", verbose)

    # Now locate the assignment lines and the resolved_username line within the function.
    # We'll look for the exact strings.
    assign1 = "resolved_staff_id = pending_staff_id or staff_id"
    assign2 = "resolved_schema_name = pending_schema_name or staff_schema_name"
    resolved_user = "resolved_username = request.session.get('pending_username') or request.session.get('staff_username') or provided_username"

    assign1_idx = None
    assign2_idx = None
    resolved_user_idx = None

    for i in range(func_start, len(lines)):
        line = lines[i].strip()
        if assign1 in line:
            assign1_idx = i
        if assign2 in line:
            assign2_idx = i
        if resolved_user in line:
            resolved_user_idx = i
        if assign1_idx is not None and assign2_idx is not None and resolved_user_idx is not None:
            break

    if assign1_idx is None or assign2_idx is None or resolved_user_idx is None:
        log("ERROR: Could not find all required lines. The file may not be in the expected state.", force=True)
        return False

    # Ensure assign1 comes before assign2, and assign2 before resolved_user
    if not (assign1_idx < assign2_idx < resolved_user_idx):
        log("ERROR: Line order mismatch. File may be corrupted.", force=True)
        return False

    log(f"Found assign1 at {assign1_idx+1}, assign2 at {assign2_idx+1}, resolved_user at {resolved_user_idx+1}", verbose)

    # Now we want to replace from assign1_idx to resolved_user_idx inclusive with the corrected block.
    # However, we must preserve the indentation of the function body. The corrected block should be indented
    # with the same number of spaces as the original assignments. We'll extract the indentation from the first assignment line.
    indent_match = re.match(r'^(\s*)', lines[assign1_idx])
    if not indent_match:
        log("ERROR: Could not determine indentation of the function body.", force=True)
        return False

    indent = indent_match.group(1)
    # The corrected block lines should be indented with that indent.
    corrected_lines = [indent + line for line in CORRECTED_BLOCK.split('\n')]
    # Ensure the lines have the correct newline endings.
    corrected_lines = [line + '\n' for line in corrected_lines]

    # Build the new file content
    new_lines = lines[:assign1_idx] + corrected_lines + lines[resolved_user_idx+1:]

    # Check if the patch is already applied correctly. We can check if the resolved_user line is immediately
    # after the if block with proper indentation. But we'll just apply it anyway; it's idempotent.
    # We'll also check if the broken code is still there; if not, we can skip.
    # A simple check: if we find the broken pattern (the malformed if on one line) we'll replace.
    # We can scan the file for "if request.session.get('staff_pending_passkey'):    resolved_staff_id"
    # If not found, we might already have correct code.
    broken_pattern = r"if request\.session\.get\('staff_pending_passkey'\):\s+resolved_staff_id = None"
    # If we find that pattern, we need to fix it.
    # But we can just always replace; it's idempotent because we replace exactly that region.
    # However, if the code is already corrected, the region may not match our identified lines.
    # The identified lines are based on exact string match; if the code is corrected, the lines may be different.
    # We'll check if the resolved_user line is present and the if block is already correctly placed.
    # But to be safe, we'll only apply if we detect the broken pattern.
    # Let's search for the broken pattern in the original lines.

    broken_found = False
    for line in lines:
        if "if request.session.get('staff_pending_passkey'):" in line and "resolved_staff_id = None" in line:
            broken_found = True
            break

    if not broken_found:
        log("The broken pattern was not found. The file might already be fixed. Skipping.", verbose)
        return True

    if dry_run:
        log("DRY RUN: Would write changes to {filepath}", force=True)
        # Show diff snippet
        log("Changes:", force=True)
        # Show the affected lines
        for i in range(max(0, assign1_idx-1), min(len(lines), resolved_user_idx+2)):
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
    parser = argparse.ArgumentParser(description="AXIS Fee Management Patcher - Fix staff_webauthn_authentication_options")
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
