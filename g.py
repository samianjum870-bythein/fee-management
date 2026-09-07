#!/usr/bin/env python3
"""
final_fix_student_teachers.py

Update get_student_profile_context to fallback to finding a SchoolClass
by grade and section if student.school_class is None.

This ensures that students who only have grade/section (without school_class)
still show their class teacher and subject teachers.

Usage:
    python final_fix_student_teachers.py [--dry-run] [--verbose] [--target-dir PATH]
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

def fix_helpers(file_path, dry_run=False, verbose=False):
    """Modify get_student_profile_context to include fallback class lookup."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content
    lines = content.splitlines()
    modified = False

    # Locate the function definition
    func_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith('def get_student_profile_context('):
            func_start = i
            break
    if func_start is None:
        log("Could not find get_student_profile_context function.", verbose)
        return

    # Find the lines where school_class is assigned and the code block that follows.
    # We'll locate the comment "# ---- Get class teacher and subject teachers for this student ----"
    # and the subsequent lines. We'll replace that block with a new version that includes fallback logic.

    block_start = None
    for i in range(func_start, len(lines)):
        if '# ---- Get class teacher and subject teachers for this student ----' in lines[i]:
            block_start = i
            break

    if block_start is None:
        log("Could not find the class teacher block in the function.", verbose)
        return

    # Find the end of the block: we'll look for the next line that has indentation <= function body indent
    # or we can look for the line that starts with 'today = date.today()' (the next logical step)
    # Actually, after the block, there is the line: "today = date.today()"
    # We'll find that line and use it as the end marker.
    end_marker = None
    for i in range(block_start, len(lines)):
        if lines[i].strip().startswith('today = date.today()'):
            end_marker = i
            break
    if end_marker is None:
        log("Could not find 'today = date.today()' line after the block.", verbose)
        return

    # The block starts at block_start and ends at end_marker - 1 (since end_marker is the line after the block)
    # We'll replace lines[block_start:end_marker] with a new block.

    # Build the new block with fallback logic.
    # We'll use the existing code but add a fallback lookup if school_class is None.
    new_block = [
        "        # ---- Get class teacher and subject teachers for this student ----",
        "        school_class = student.school_class",
        "        class_teacher = None",
        "        subject_teachers = []",
        "        # If student.school_class is not set, try to find a class by grade and section",
        "        if not school_class and student.grade and student.section:",
        "            try:",
        "                school_class = SchoolClass.objects.get(",
        "                    name=student.grade, section=student.section, is_active=True",
        "                )",
        "            except SchoolClass.DoesNotExist:",
        "                pass",
        "            except SchoolClass.MultipleObjectsReturned:",
        "                # If multiple, pick the first one (or you could refine with wing_category if available)",
        "                school_class = SchoolClass.objects.filter(",
        "                    name=student.grade, section=student.section, is_active=True",
        "                ).first()",
        "        if school_class:",
        "            class_teacher = school_class.class_teacher",
        "            # Fetch active subject assignments with teacher",
        "            from ..models import ClassSubject",
        "            assignments = ClassSubject.objects.filter(",
        "                school_class=school_class, is_active=True",
        "            ).select_related('subject', 'teacher')",
        "            for ass in assignments:",
        "                if ass.teacher:",
        "                    subject_teachers.append({",
        "                        'subject': ass.subject.name,",
        "                        'teacher': ass.teacher,",
        "                    })",
        "        # End of teacher block",
    ]

    # We need to check if the new block already contains the fallback logic (idempotency)
    # by looking for "if not school_class and student.grade and student.section:"
    for line in new_block:
        if 'if not school_class and student.grade and student.section:' in line:
            # Already patched
            log("Fallback logic already present. No change needed.", verbose)
            return

    # Replace the block
    lines = lines[:block_start] + new_block + lines[end_marker:]

    # Write back
    new_content = '\n'.join(lines)
    if new_content == original:
        log("No changes needed.", verbose)
        return

    if dry_run:
        log("Would write changes to helpers.py", always=True)
        if verbose:
            import difflib
            diff = difflib.unified_diff(original.splitlines(), new_content.splitlines(), fromfile='helpers.py', tofile='helpers.py (fixed)')
            for line in diff:
                print(line)
    else:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        log("helpers.py updated with fallback logic.", always=True)

def main():
    parser = argparse.ArgumentParser(description="Add fallback class lookup for student profile teachers.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output.")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current).")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    helpers_py = target_dir / "axis_saas" / "views" / "helpers.py"
    if not helpers_py.exists():
        log(f"helpers.py not found at {helpers_py}", verbose=args.verbose)
        sys.exit(1)

    fix_helpers(helpers_py, dry_run=args.dry_run, verbose=args.verbose)

if __name__ == "__main__":
    main()
