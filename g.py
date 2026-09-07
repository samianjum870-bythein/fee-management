#!/usr/bin/env python3
"""
remove_defaulters_cache.py

Remove the @cache_page(60) decorator from the defaulters view
so that the page shows fresh data without a 1‑2 minute delay.

This script modifies axis_saas/views/reports.py.

Usage:
    python remove_defaulters_cache.py [--dry-run] [--verbose] [--target-dir PATH]
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

def remove_cache_decorator(file_path, dry_run=False, verbose=False):
    """Remove @cache_page(60) from the defaulters function."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content
    lines = content.splitlines()

    # Find the line that contains '@cache_page(60)' that is directly above the defaulters function
    # We'll look for the pattern: @cache_page(60) followed by @require_tenant_type and then def defaulters
    modified = False
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Check if this line is @cache_page(60) and the next line(s) contain the defaulters definition
        if line.strip() == '@cache_page(60)':
            # Look ahead to see if there is a def defaulters within the next few lines
            found_defaulters = False
            for j in range(i+1, min(i+5, len(lines))):
                if lines[j].strip().startswith('def defaulters('):
                    found_defaulters = True
                    break
            if found_defaulters:
                # Skip this line (the decorator) and continue
                log("Removing @cache_page(60) decorator.", verbose)
                modified = True
                i += 1
                continue
        new_lines.append(line)
        i += 1

    if not modified:
        log("No @cache_page(60) decorator found for defaulters function.", verbose)
        return False

    new_content = '\n'.join(new_lines)

    if dry_run:
        log("Would modify reports.py", always=True)
        if verbose:
            import difflib
            diff = difflib.unified_diff(original.splitlines(), new_content.splitlines(), fromfile='reports.py', tofile='reports.py (patched)')
            for line in diff:
                print(line)
    else:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        log("reports.py updated – cache decorator removed.", always=True)

    return True

def main():
    parser = argparse.ArgumentParser(description="Remove caching from defaulters page.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output.")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current).")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        log(f"Target directory does not exist: {target_dir}", verbose=args.verbose)
        sys.exit(1)

    reports_py = target_dir / "axis_saas" / "views" / "reports.py"
    if not reports_py.exists():
        log(f"reports.py not found: {reports_py}", verbose=args.verbose)
        sys.exit(1)

    patched = remove_cache_decorator(reports_py, dry_run=args.dry_run, verbose=args.verbose)

    if args.dry_run:
        log("Dry run completed.", always=True)
    else:
        if patched:
            log("Fix applied. Restart your Django server for changes to take effect.", always=True)
        else:
            log("No changes needed.", always=True)

if __name__ == "__main__":
    main()
