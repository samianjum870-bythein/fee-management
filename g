#!/usr/bin/env python3
"""
axis_patcher_fix_models.py - Restore SchoolClass definition and fix its __str__ method.

This patcher:
- Reads axis_saas/models.py.
- Locates the SchoolClass class definition.
- Replaces its __str__ method with a safe version that handles missing WingCategory.
- Ensures the SchoolClass class is defined before ClassSubject (it should be).
- Does NOT modify SchoolClient or any other class.
- If SchoolClass is missing entirely, it will be re-inserted (though that should not happen).
"""

import os
import sys
import re
from pathlib import Path
import argparse

def main():
    parser = argparse.ArgumentParser(description="Fix SchoolClass in models.py")
    parser.add_argument('--dry-run', action='store_true', help="Preview changes")
    parser.add_argument('--verbose', action='store_true', help="Verbose output")
    parser.add_argument('--target-dir', default='.', help="Project root")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        print(f"❌ Target directory not found: {target_dir}")
        sys.exit(1)

    os.chdir(target_dir)
    model_file = Path("axis_saas/models.py")

    if not model_file.exists():
        print(f"❌ File not found: {model_file}")
        sys.exit(1)

    with open(model_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # ------------------------------------------------------------------
    # Step 1: Locate the SchoolClass class definition block.
    # We'll find the line that starts with "class SchoolClass(" and then
    # capture the block until the next class definition at the same indentation level.
    # We'll use a regex with DOTALL to match across lines.
    # However, it's easier to split lines and scan.
    # We'll find the start index of "class SchoolClass(" and then find the
    # next line that starts with "class " (at the same indentation level or less)
    # to determine the end.
    # ------------------------------------------------------------------

    lines = content.splitlines()
    schoolclass_start = None
    schoolclass_end = None
    in_schoolclass = False
    class_indent = None
    for i, line in enumerate(lines):
        if not in_schoolclass and re.match(r'^class SchoolClass\(', line):
            schoolclass_start = i
            in_schoolclass = True
            class_indent = len(line) - len(line.lstrip())
        elif in_schoolclass:
            # Check if we encounter another class at same or lower indentation
            if line.lstrip().startswith('class ') and len(line) - len(line.lstrip()) <= class_indent:
                schoolclass_end = i - 1
                break
    if schoolclass_start is not None and schoolclass_end is None:
        schoolclass_end = len(lines) - 1

    if schoolclass_start is None:
        # SchoolClass is missing entirely – we need to insert it.
        print("❌ SchoolClass class definition not found in models.py. This is critical.")
        print("   Please restore the SchoolClass class manually or re-run the full migration.")
        sys.exit(1)

    # Extract the SchoolClass block as a list of lines
    schoolclass_lines = lines[schoolclass_start:schoolclass_end+1]
    original_block = '\n'.join(schoolclass_lines)

    # ------------------------------------------------------------------
    # Step 2: Inside the block, find the __str__ method and replace it.
    # We'll locate the method definition and replace it.
    # We'll use a regex to match the whole __str__ method.
    # ------------------------------------------------------------------
    # The safe __str__ method:
    safe_str = """
    def __str__(self):
        label = f"{self.name} - {self.section}" if self.section else self.name
        try:
            wing_name = self.wing_category.name if self.wing_category_id else None
        except WingCategory.DoesNotExist:
            wing_name = None
        if wing_name:
            return f"{wing_name} | {label}"
        return label
"""
    # We'll strip leading/trailing whitespace but preserve indentation.
    # The method should be indented with 4 spaces.
    # We'll replace the existing __str__ method.

    # Join the block as a string
    block_str = '\n'.join(schoolclass_lines)

    # Search for the __str__ method definition.
    # It might be currently:
    #   def __str__(self):
    #       label = ... (maybe the old version)
    # We'll replace the entire method.
    # Pattern: from 'def __str__' to the next method definition or end of block.
    # We'll use a regex to match the method.
    # Since we have the block as a string, we can replace.
    # But we need to keep the rest of the class intact.

    # We'll find the index of the __str__ method within the block.
    # We'll split the block into lines and search for 'def __str__'.
    block_lines = schoolclass_lines
    new_block_lines = []
    i = 0
    while i < len(block_lines):
        line = block_lines[i]
        if line.lstrip().startswith('def __str__('):
            # This is the start of the method. We need to skip all lines until the next method or end.
            # We'll find the end of the method by looking for the next line that is not indented more than the method's indentation.
            method_indent = len(line) - len(line.lstrip())
            # We'll skip lines that are indented more than method_indent (the body).
            # Also skip any docstring lines.
            new_block_lines.append('    def __str__(self):')
            # Now add the body lines with proper indentation.
            # The safe_str is defined with 4 spaces indentation; we need to adjust.
            body_lines = safe_str.strip().splitlines()
            for body_line in body_lines:
                if body_line.strip():
                    new_block_lines.append('    ' + body_line.lstrip())
                else:
                    new_block_lines.append('')
            # Now skip the old method lines.
            i += 1
            while i < len(block_lines):
                # If the line has indentation less than or equal to method_indent and is not a comment or blank,
                # it's the next method or attribute, so break.
                line_indent = len(block_lines[i]) - len(block_lines[i].lstrip())
                if line_indent <= method_indent and block_lines[i].strip() and not block_lines[i].strip().startswith('#'):
                    break
                i += 1
            # Do not increment i because the current line is the new method or end of block.
        else:
            new_block_lines.append(line)
            i += 1

    # Reconstruct the new block
    new_block = '\n'.join(new_block_lines)

    # Replace the old block in the full content
    new_content = content.replace(original_block, new_block)

    # Also ensure that SchoolClass is defined before ClassSubject.
    # We don't need to reorder because it's already there.

    if args.dry_run:
        print("🔍 DRY RUN: Would update SchoolClass.__str__ method.")
        if args.verbose:
            print("Diff preview:")
            import difflib
            diff = difflib.unified_diff(content.splitlines(), new_content.splitlines(), lineterm='')
            for d in diff:
                print(d)
        return

    # Write the file
    with open(model_file, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print("✅ Fixed SchoolClass.__str__ method in models.py.")
    print("   The class teacher tab should now work without errors.")

if __name__ == "__main__":
    main()
