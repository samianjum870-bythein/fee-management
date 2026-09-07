#!/usr/bin/env python3
"""
add_father_name_mobile_fee_collection.py

Add father name display in mobile fee collection list.

Changes:
- In templates/mobile/fee_collection.html, add the father name after the student name
  with a slash separator and lighter styling.

Usage:
    python add_father_name_mobile_fee_collection.py [--dry-run] [--verbose] [--target-dir PATH]
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

def patch_template(file_path, dry_run=False, verbose=False):
    if not file_path.exists():
        log(f"Template not found: {file_path}", verbose)
        return False

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content

    # We want to add father name in the top-row div.
    # Current top-row pattern:
    # <div class="top-row">
    #   <div class="name">{{ student.name }}</div>
    #   <div class="pending-amount">₹{{ student.pending_total|floatformat:2 }}</div>
    # </div>
    # We'll change it to:
    # <div class="top-row">
    #   <div>
    #     <span class="name">{{ student.name }}</span>
    #     <span class="father-name"> / {{ student.father_name }}</span>
    #   </div>
    #   <div class="pending-amount">₹{{ student.pending_total|floatformat:2 }}</div>
    # </div>

    # We'll find the top-row div that contains the name and pending-amount.
    # We'll use a regex to match the entire top-row div and replace it.

    # Pattern: <div class="top-row">.*?</div> (non-greedy)
    # We'll match the whole div, then replace inside.

    pattern = r'(<div class="top-row">)(.*?)(</div>)'
    def replace_top_row(match):
        opening = match.group(1)
        inner = match.group(2)
        closing = match.group(3)

        # Check if this is the top-row we want (contains name and pending-amount)
        if '{{ student.name }}' in inner and '{{ student.pending_total' in inner:
            # We'll replace the inner with new structure.
            # Extract the name div and pending-amount div.
            # We'll use a simple approach: replace the inner with new content.
            # We'll keep the pending-amount div as is, and wrap the name with father name.
            # We'll use regex to capture the name div and pending-amount div.
            name_div_pattern = r'<div class="name">(.*?)</div>'
            name_match = re.search(name_div_pattern, inner, re.DOTALL)
            if not name_match:
                return match.group(0)  # fallback

            name_content = name_match.group(1)
            # Build new top-row
            # We'll replace the name div with a combined div.
            new_inner = f'''
      <div style="display: flex; align-items: baseline; gap: 0.2rem; flex-wrap: wrap;">
        <span class="name" style="font-weight: 700; font-size: 0.85rem; color: var(--text);">{{ student.name }}</span>
        <span style="color: var(--muted); font-size: 0.7rem;">/ {{ student.father_name }}</span>
      </div>
      <div class="pending-amount" style="font-weight: 700; font-size: 0.9rem; color: #EF4444; white-space: nowrap;">₹{{ student.pending_total|floatformat:2 }}</div>
'''
            # But we need to preserve the pending-amount div exactly.
            # We'll extract the pending-amount div from inner.
            pending_div_pattern = r'(<div class="pending-amount".*?>.*?</div>)'
            pending_match = re.search(pending_div_pattern, inner, re.DOTALL)
            if pending_match:
                pending_div = pending_match.group(1)
                # Rebuild inner with new name display and the pending div.
                new_inner = f'''
      <div style="display: flex; align-items: baseline; gap: 0.2rem; flex-wrap: wrap;">
        <span class="name" style="font-weight: 700; font-size: 0.85rem; color: var(--text);">{{ student.name }}</span>
        <span style="color: var(--muted); font-size: 0.7rem;">/ {{ student.father_name }}</span>
      </div>
      {pending_div}
'''
            else:
                # Fallback
                new_inner = inner  # keep as is

            return opening + new_inner + closing
        else:
            return match.group(0)

    new_content = re.sub(pattern, replace_top_row, content, flags=re.DOTALL)

    if new_content == original:
        log(f"No changes needed for {file_path}", verbose)
        return False

    if dry_run:
        log(f"Would modify {file_path}", always=True)
        if verbose:
            import difflib
            diff = difflib.unified_diff(original.splitlines(), new_content.splitlines(), fromfile=file_path.name, tofile=file_path.name + ' (patched)')
            for line in diff:
                print(line)
    else:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        log(f"Updated {file_path}", always=True)

    return True

def main():
    parser = argparse.ArgumentParser(description="Add father name to mobile fee collection list.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output.")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current).")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        log(f"Target directory does not exist: {target_dir}", verbose=args.verbose)
        sys.exit(1)

    template_path = target_dir / "templates" / "mobile" / "fee_collection.html"
    if not template_path.exists():
        log(f"Template not found: {template_path}", verbose=args.verbose)
        sys.exit(1)

    patched = patch_template(template_path, dry_run=args.dry_run, verbose=args.verbose)

    if args.dry_run:
        log("Dry run completed.", always=True)
    else:
        log("Patch applied. Restart your Django server for changes to take effect.", always=True)

if __name__ == "__main__":
    main()
