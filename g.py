#!/usr/bin/env python3
"""
fix_indentation_final.py

Fixes indentation errors in fee_collection.py and vouchers.py
where the 'fee_struct' line has extra indentation after 'display_grade'.

Usage:
    python fix_indentation_final.py [--dry-run] [--verbose] [--target-dir PATH]
"""

import re
import sys
from pathlib import Path
from datetime import datetime
import argparse

def log(message, verbose=False, always=True):
    if always or verbose:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}")

def patch_file(file_path, pattern, replacement, dry_run=False, verbose=False):
    """Apply regex substitution to a file."""
    if not file_path.exists():
        log(f"File not found: {file_path}", verbose)
        return False

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content, count = re.subn(pattern, replacement, content, flags=re.MULTILINE)
    if count == 0:
        log(f"No match found in {file_path}", verbose)
        return False

    if dry_run:
        log(f"Would modify {file_path} ({count} replacement(s))", always=True)
        if verbose:
            import difflib
            diff = difflib.unified_diff(
                content.splitlines(),
                new_content.splitlines(),
                fromfile=file_path.name,
                tofile=file_path.name + ' (patched)'
            )
            for line in diff:
                print(line)
    else:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        log(f"Patched {file_path} ({count} replacement(s))", always=True)
    return True

def main():
    parser = argparse.ArgumentParser(
        description="Fix indentation for fee_struct assignment."
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output.")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current).")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        log(f"Target directory does not exist: {target_dir}", verbose=args.verbose)
        sys.exit(1)

    # Pattern to catch: display_grade = ...  followed by a line that starts with spaces and 'fee_struct ='
    # We want to remove the extra indentation from the fee_struct line.
    # The line should have the same indentation as the display_grade line.
    # We'll capture the indentation of display_grade and use it for fee_struct.
    # This pattern matches:
    #   (indentation)display_grade = get_student_display_grade(student)
    #   (extra spaces)fee_struct = FeeStructure.objects.filter(grade=display_grade).first()
    # We'll replace with: same indentation for both lines.
    pattern = r'^(\s*)(display_grade = get_student_display_grade\(student\))\s*\n\s*(fee_struct = FeeStructure\.objects\.filter\(grade=display_grade\)\.first\(\))'
    replacement = r'\1\2\n\1\3'

    # Apply to fee_collection.py
    fee_collection = target_dir / "axis_saas" / "views" / "fee_collection.py"
    if fee_collection.exists():
        patch_file(fee_collection, pattern, replacement, args.dry_run, args.verbose)
    else:
        log(f"fee_collection.py not found: {fee_collection}", verbose=args.verbose)

    # Apply to vouchers.py if it has similar pattern
    vouchers = target_dir / "axis_saas" / "views" / "vouchers.py"
    if vouchers.exists():
        patch_file(vouchers, pattern, replacement, args.dry_run, args.verbose)
    else:
        log(f"vouchers.py not found: {vouchers}", verbose=args.verbose)

    # Also check manual_generate_single_api in fee_collection.py – it may have a similar pattern
    # Pattern for manual_generate_single_api: display_grade = get_student_display_grade(student)
    # followed by fee_struct = ...
    # We'll use a broader pattern: any line with 'display_grade = get_student_display_grade' followed by 'fee_struct =' on next line.
    # But we already handled the generic pattern. Since we used the pattern that captures any occurrence, it should work.

    if args.dry_run:
        log("Dry run completed. No files were changed.")
    else:
        log("Indentation fixes applied. Restart your Django server.")

if __name__ == "__main__":
    main()
