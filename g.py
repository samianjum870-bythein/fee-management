#!/usr/bin/env python3
"""
axis_patcher.py

Patches the staff profile page to fix the biometric consent modal.
- Removes inline `display:grid` from the modal's style so it respects the `hidden` attribute.
- Adds a CSS rule to show the modal as a grid when not hidden.
- Ensures the modal is hidden by default and only appears on enabling biometric.

Usage:
    python axis_patcher.py [--dry-run] [--verbose] [--target-dir PATH]
"""

import os
import re
import sys
import json
import shutil
from pathlib import Path
from datetime import datetime
import argparse
import traceback

# ----------------------------------------------------------------------
# Logging helpers
# ----------------------------------------------------------------------
def log(message, verbose=False, timestamp=True):
    if not verbose:
        return
    if timestamp:
        dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{dt}] {message}")
    else:
        print(message)

def log_error(message):
    dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{dt}] ERROR: {message}", file=sys.stderr)

# ----------------------------------------------------------------------
# Patch definitions
# ----------------------------------------------------------------------
def get_patches():
    """Return a list of patch actions."""
    patches = []

    # 1. Modal div replacement in templates/mobile/staff/profile.html
    patches.append({
        "file": "templates/mobile/staff/profile.html",
        "action": "replace_modal",
        "description": "Remove inline display:grid from biometric consent modal",
        "pattern": r'(<div id="biometricConsentModal"[^>]*style="[^"]*?)display:grid;\s*',
        "replacement": r'\1',
        "count": 1,
    })

    # 2. Add CSS rule inside extra_head block to show modal when not hidden
    patches.append({
        "file": "templates/mobile/staff/profile.html",
        "action": "insert_style",
        "description": "Insert CSS rule to display modal as grid when not hidden",
        "insert_after": r'({% block extra_head %}\s*)',
        "content": r'\1<style>\n#biometricConsentModal:not([hidden]) {\n    display: grid;\n    place-items: center;\n}\n</style>\n',
        "check_pattern": r'#biometricConsentModal:not\(\[hidden\]\)\s*\{',  # avoid duplicate
    })

    return patches

# ----------------------------------------------------------------------
# Patch execution
# ----------------------------------------------------------------------
def apply_patch(file_path, patch, dry_run, verbose):
    """Apply a single patch to a file."""
    if not os.path.isfile(file_path):
        log_error(f"File not found: {file_path}")
        return False

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content
    patch_type = patch.get("action")

    if patch_type == "replace_modal":
        pattern = patch["pattern"]
        replacement = patch["replacement"]
        count = patch.get("count", 0)
        new_content, num_subs = re.subn(pattern, replacement, content, count=count)
        if num_subs == 0:
            log(f"  No change: pattern not found in {file_path}", verbose)
            return True  # not an error, just no change
        content = new_content
        log(f"  Replaced {num_subs} occurrence(s) in {file_path}", verbose)

    elif patch_type == "insert_style":
        insert_after = patch["insert_after"]
        content_to_insert = patch["content"]
        check_pattern = patch.get("check_pattern")
        # Check if already inserted
        if check_pattern and re.search(check_pattern, content):
            log(f"  Style already present in {file_path}, skipping", verbose)
            return True
        # Find the insertion point
        match = re.search(insert_after, content)
        if not match:
            log_error(f"  Could not find insert_after pattern in {file_path}")
            return False
        # Insert after the matched group
        insert_pos = match.end()
        new_content = content[:insert_pos] + content_to_insert + content[insert_pos:]
        content = new_content
        log(f"  Inserted style block in {file_path}", verbose)

    else:
        log_error(f"Unknown action: {patch_type}")
        return False

    if content == original_content:
        log(f"  No changes made to {file_path}", verbose)
        return True

    if dry_run:
        log(f"  [DRY RUN] Would write changes to {file_path}", verbose)
        # Optionally show diff? Not necessary.
    else:
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            log(f"  Updated {file_path}", verbose)
        except Exception as e:
            log_error(f"  Failed to write {file_path}: {e}")
            return False

    return True

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Patch the staff profile biometric modal.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current)")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.is_dir():
        log_error(f"Target directory does not exist: {target_dir}")
        sys.exit(1)

    os.chdir(target_dir)
    log(f"Working directory: {os.getcwd()}", args.verbose)

    patches = get_patches()
    success = True

    for patch in patches:
        file_path = patch["file"]
        abs_path = Path(file_path)
        if not abs_path.is_absolute():
            abs_path = target_dir / abs_path

        log(f"Processing: {patch['description']} ({patch['action']})", args.verbose)
        if not apply_patch(str(abs_path), patch, args.dry_run, args.verbose):
            success = False
            log_error(f"Failed to apply patch: {patch['description']}")

    if success:
        log("All patches applied successfully.", args.verbose)
        sys.exit(0)
    else:
        log_error("Some patches failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
