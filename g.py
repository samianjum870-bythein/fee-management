#!/usr/bin/env python3
"""
axis_patcher.py – Final class display refactoring including fee_structure.

Applies fixes for:
1. Import error in fee_collection.py
2. Manual class display loop in reports.py
3. Missing {% load class_display %} in collect_fee.html templates
4. Misplaced display_class filter in vouchers.html templates
5. Manual grade building in fee_structure.py (desktop and mobile)

Usage:
    python axis_patcher.py [--dry-run] [--verbose] [--target-dir PATH]

Options:
    --dry-run      Preview changes without writing files.
    --verbose      Show detailed output.
    --target-dir   Project root directory (default: current directory).
"""

import os
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime

# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------

def log(message, verbose=False, always=False):
    if always or verbose:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}")

def apply_regex(filepath, pattern, replacement, dry_run=False, verbose=False, flags=re.MULTILINE | re.DOTALL):
    """Apply a regex substitution to a file. Return True if changed."""
    if not os.path.exists(filepath):
        log(f"File not found: {filepath}", verbose, always=True)
        return False

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content, count = re.subn(pattern, replacement, content, flags=flags)
    if count == 0:
        log(f"No change for {filepath} (pattern not found)", verbose)
        return False

    if dry_run:
        log(f"DRY RUN: Would update {filepath} ({count} replacement(s))", verbose, always=True)
        return True

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    log(f"Updated {filepath} ({count} replacement(s))", verbose, always=True)
    return True

def ensure_load_tag(filepath, dry_run=False, verbose=False):
    """Add {% load class_display %} after {% extends ... %} if missing."""
    if not os.path.exists(filepath):
        log(f"File not found: {filepath}", verbose, always=True)
        return False

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    if re.search(r"{%\s*load\s+class_display\s*%}", content):
        log(f"{filepath} already has class_display loaded", verbose)
        return False

    match = re.search(r"({%\s*extends\s+['\"][^'\"]+['\"]\s*%})", content)
    if not match:
        log(f"{filepath}: No {% extends %} found, skipping", verbose)
        return False

    new_content = content[:match.end()] + "\n{% load class_display %}" + content[match.end():]

    if dry_run:
        log(f"DRY RUN: Would add {% load class_display %} to {filepath}", verbose, always=True)
        return True

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    log(f"Added {% load class_display %} to {filepath}", verbose, always=True)
    return True

# -----------------------------------------------------------------------------
# Main patcher
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Apply class display refactoring patches.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output.")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current).")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.is_dir():
        log(f"Target directory does not exist: {target_dir}", always=True)
        sys.exit(1)

    log(f"Starting patcher in {target_dir} (dry-run={args.dry_run})", args.verbose, always=True)

    # -------------------------------------------------------------------------
    # 1. Fix fee_collection.py import
    # -------------------------------------------------------------------------
    fee_collection = target_dir / "axis_saas" / "views" / "fee_collection.py"
    if fee_collection.exists():
        apply_regex(
            fee_collection,
            r"from \\\.helpers import \*",
            "from .helpers import *",
            args.dry_run,
            args.verbose
        )
    else:
        log("fee_collection.py not found, skipping", args.verbose)

    # -------------------------------------------------------------------------
    # 2. Replace manual display_class loop in reports.py
    # -------------------------------------------------------------------------
    reports_py = target_dir / "axis_saas" / "views" / "reports.py"
    if reports_py.exists():
        with open(reports_py, 'r', encoding='utf-8') as f:
            content = f.read()

        start_marker = "# ---- add display_class to each defaulter ----"
        start_idx = content.find(start_marker)
        if start_idx == -1:
            log("reports.py: comment not found, skipping", args.verbose)
        else:
            newline_idx = content.find('\n', start_idx)
            if newline_idx == -1:
                log("reports.py: malformed content", args.verbose)
            else:
                sort_marker = "defaulters_data.sort(key=lambda x: x['days_overdue'], reverse=True)"
                sort_idx = content.find(sort_marker, start_idx)
                if sort_idx == -1:
                    log("reports.py: sort line not found, skipping", args.verbose)
                else:
                    before = content[:start_idx]
                    after = content[sort_idx + len(sort_marker):]
                    new_block = (
                        "# ---- add display_class to each defaulter ----\n"
                        "    for item in defaulters_data:\n"
                        "        student = item['student']\n"
                        "        item['display_class'] = get_class_display_for_student(student, tenant)\n"
                        "\n"
                        "    defaulters_data.sort(key=lambda x: x['days_overdue'], reverse=True)"
                    )
                    new_content = before + new_block + after

                    if args.dry_run:
                        log("DRY RUN: Would update reports.py (replace manual display_class block)", args.verbose, always=True)
                    else:
                        with open(reports_py, 'w', encoding='utf-8') as f:
                            f.write(new_content)
                        log("Updated reports.py (replaced manual display_class block)", args.verbose, always=True)
    else:
        log("reports.py not found, skipping", args.verbose)

    # -------------------------------------------------------------------------
    # 3. Add {% load class_display %} to collect_fee.html templates
    # -------------------------------------------------------------------------
    collect_mobile = target_dir / "templates" / "mobile" / "collect_fee.html"
    if collect_mobile.exists():
        ensure_load_tag(collect_mobile, args.dry_run, args.verbose)

    collect_tenant = target_dir / "templates" / "tenant" / "collect_fee.html"
    if collect_tenant.exists():
        ensure_load_tag(collect_tenant, args.dry_run, args.verbose)

    # -------------------------------------------------------------------------
    # 4. Fix misplaced display_class filter in vouchers.html templates
    # -------------------------------------------------------------------------
    vouchers_tenant = target_dir / "templates" / "tenant" / "vouchers.html"
    if vouchers_tenant.exists():
        with open(vouchers_tenant, 'r', encoding='utf-8') as f:
            content = f.read()
        pattern = r"(<td>{{ item\.student\.roll_number }}</td>\s*\n\s*)\{\{ item\.student\|display_class:tenant \}\}(\s*\n\s*<td>{{ item\.month }}/{{ item\.year }}</td>)"
        replacement = r"\1<td>{{ item.student|display_class:tenant }}</td>\3"
        new_content, count = re.subn(pattern, replacement, content, flags=re.MULTILINE)
        if count == 0:
            log("tenant/vouchers.html: pattern not found (maybe already fixed)", args.verbose)
        else:
            if args.dry_run:
                log("DRY RUN: Would fix misplaced filter in tenant/vouchers.html", args.verbose, always=True)
            else:
                with open(vouchers_tenant, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                log("Fixed misplaced filter in tenant/vouchers.html", args.verbose, always=True)

    vouchers_mobile = target_dir / "templates" / "mobile" / "vouchers.html"
    if vouchers_mobile.exists():
        with open(vouchers_mobile, 'r', encoding='utf-8') as f:
            content = f.read()
        pattern = r"(<td>{{ item\.student\.roll_number }}</td>\s*\n\s*)\{\{ item\.student\|display_class:tenant \}\}(\s*\n\s*<td>{{ item\.month }}/{{ item\.year }}</td>)"
        replacement = r"\1<td>{{ item.student|display_class:tenant }}</td>\3"
        new_content, count = re.subn(pattern, replacement, content, flags=re.MULTILINE)
        if count == 0:
            log("mobile/vouchers.html: pattern not found (maybe already fixed)", args.verbose)
        else:
            if args.dry_run:
                log("DRY RUN: Would fix misplaced filter in mobile/vouchers.html", args.verbose, always=True)
            else:
                with open(vouchers_mobile, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                log("Fixed misplaced filter in mobile/vouchers.html", args.verbose, always=True)

    # -------------------------------------------------------------------------
    # 5. Fix fee_structure.py – replace manual grade building with get_class_display_name
    # -------------------------------------------------------------------------
    fee_structure = target_dir / "axis_saas" / "views" / "fee_structure.py"
    if fee_structure.exists():
        # Add import if missing
        with open(fee_structure, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check if import exists
        if "from axis_saas.utils.class_display import get_class_display_name" not in content:
            # Insert after existing imports, e.g., after "from .helpers import *"
            # Find the line with "from .helpers import *" and insert after it
            import_pattern = r"(from \.helpers import \*)"
            if re.search(import_pattern, content):
                new_content = re.sub(
                    import_pattern,
                    r"\1\nfrom axis_saas.utils.class_display import get_class_display_name",
                    content,
                    flags=re.MULTILINE
                )
                if args.dry_run:
                    log("DRY RUN: Would add import to fee_structure.py", args.verbose, always=True)
                else:
                    with open(fee_structure, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    log("Added import to fee_structure.py", args.verbose, always=True)
                content = new_content
            else:
                log("fee_structure.py: could not find import insertion point, skipping", args.verbose)

        # Now replace manual grade building in POST handling
        # Pattern for desktop: the block inside if request.method == 'POST':
        # Find the part that builds grade and replace with get_class_display_name(school_class, tenant.tenant_type)
        # The block is:
        # if tenant.tenant_type == 'wing_school' and school_class.wing_category:
        #     main = school_class.wing_category.parent
        #     sub = school_class.wing_category
        #     if school_class.section:
        #         grade = f"{main.name} ({sub.name}) - {school_class.name} - {school_class.section}"
        #     else:
        #         grade = f"{main.name} ({sub.name}) - {school_class.name}"
        # else:
        #     if school_class.section:
        #         grade = f"{school_class.name} - {school_class.section}"
        #     else:
        #         grade = school_class.name
        # Replace with: grade = get_class_display_name(school_class, tenant.tenant_type)
        pattern_post = (
            r"(if tenant\.tenant_type == 'wing_school' and school_class\.wing_category:.*?else:.*?grade = school_class\.name)"
        )
        # Use a simpler approach: replace the entire block with a single line.
        # We'll find the exact pattern that starts with "if tenant.tenant_type ..." and ends with "grade = school_class.name"
        # but we need to be careful with indentation.
        # Instead, we'll use a more specific pattern that matches the exact lines in the file.

        # Because the file has multiple such blocks, we'll replace all occurrences in both desktop and mobile views.
        # We'll target the grade building inside the POST block and also in the grade_to_class_id loop and edit handling.

        # Let's define a function that does the replacement using a more robust approach.
        # We'll look for the pattern:
        # if tenant.tenant_type == 'wing_school' and school_class.wing_category:
        #     ... 
        # else:
        #     ...
        # and replace with a single line.

        # But there are multiple occurrences: in POST handling for desktop, POST for mobile, and in the loop for grade_to_class_id,
        # and in the edit handling for selected_class.

        # We'll define a list of regex patterns to replace each occurrence.

        patterns = [
            # Pattern for POST block (desktop and mobile)
            (
                re.compile(
                    r"(^[\t ]*)if tenant\.tenant_type == 'wing_school' and school_class\.wing_category:\n"
                    r"\1    main = school_class\.wing_category\.parent\n"
                    r"\1    sub = school_class\.wing_category\n"
                    r"\1    if school_class\.section:\n"
                    r"\1        grade = f\"\{main\.name\} \(\{\{sub\.name\}\}\) - \{school_class\.name\} - \{school_class\.section\}\"\n"
                    r"\1    else:\n"
                    r"\1        grade = f\"\{main\.name\} \(\{\{sub\.name\}\}\) - \{school_class\.name\}\"\n"
                    r"\1else:\n"
                    r"\1    if school_class\.section:\n"
                    r"\1        grade = f\"\{school_class\.name\} - \{school_class\.section\}\"\n"
                    r"\1    else:\n"
                    r"\1        grade = school_class\.name",
                    re.MULTILINE
                ),
                r"\1grade = get_class_display_name(school_class, tenant.tenant_type)"
            ),
            # Pattern for grade_to_class_id loop (both desktop and mobile)
            (
                re.compile(
                    r"(^[\t ]*)for cls in classes:\n"
                    r"\1    if tenant\.tenant_type == 'wing_school' and cls\.wing_category:\n"
                    r"\1        main = cls\.wing_category\.parent\n"
                    r"\1        sub = cls\.wing_category\n"
                    r"\1        if cls\.section:\n"
                    r"\1            grade_str = f\"\{main\.name\} \(\{\{sub\.name\}\}\) - \{cls\.name\} - \{cls\.section\}\"\n"
                    r"\1        else:\n"
                    r"\1            grade_str = f\"\{main\.name\} \(\{\{sub\.name\}\}\) - \{cls\.name\}\"\n"
                    r"\1    else:\n"
                    r"\1        if cls\.section:\n"
                    r"\1            grade_str = f\"\{cls\.name\} - \{cls\.section\}\"\n"
                    r"\1        else:\n"
                    r"\1            grade_str = cls\.name\n"
                    r"\1    grade_to_class_id\[grade_str\] = cls\.id",
                    re.MULTILINE
                ),
                r"\1    grade_str = get_class_display_name(cls, tenant.tenant_type)\n"
                r"\1    grade_to_class_id[grade_str] = cls.id"
            ),
            # Pattern for edit handling (selected_class) – both desktop and mobile
            (
                re.compile(
                    r"(^[\t ]*)if tenant\.tenant_type == 'wing_school' and cls\.wing_category:\n"
                    r"\1    main = cls\.wing_category\.parent\n"
                    r"\1    sub = cls\.wing_category\n"
                    r"\1    if cls\.section:\n"
                    r"\1        grade_str = f\"\{main\.name\} \(\{\{sub\.name\}\}\) - \{cls\.name\} - \{cls\.section\}\"\n"
                    r"\1    else:\n"
                    r"\1        grade_str = f\"\{main\.name\} \(\{\{sub\.name\}\}\) - \{cls\.name\}\"\n"
                    r"\1else:\n"
                    r"\1    if cls\.section:\n"
                    r"\1        grade_str = f\"\{cls\.name\} - \{cls\.section\}\"\n"
                    r"\1    else:\n"
                    r"\1        grade_str = cls\.name",
                    re.MULTILINE
                ),
                r"\1grade_str = get_class_display_name(cls, tenant.tenant_type)"
            ),
            # Pattern for the selected_class building when editing (also in form initial)
            (
                re.compile(
                    r"(^[\t ]*)if tenant\.tenant_type == 'wing_school' and selected_class\.wing_category:\n"
                    r"\1    main = selected_class\.wing_category\.parent\n"
                    r"\1    sub = selected_class\.wing_category\n"
                    r"\1    if selected_class\.section:\n"
                    r"\1        grade_str = f\"\{main\.name\} \(\{\{sub\.name\}\}\) - \{selected_class\.name\} - \{selected_class\.section\}\"\n"
                    r"\1    else:\n"
                    r"\1        grade_str = f\"\{main\.name\} \(\{\{sub\.name\}\}\) - \{selected_class\.name\}\"\n"
                    r"\1else:\n"
                    r"\1    if selected_class\.section:\n"
                    r"\1        grade_str = f\"\{selected_class\.name\} - \{selected_class\.section\}\"\n"
                    r"\1    else:\n"
                    r"\1        grade_str = selected_class\.name",
                    re.MULTILINE
                ),
                r"\1grade_str = get_class_display_name(selected_class, tenant.tenant_type)"
            ),
        ]

        # Apply each pattern to the file content (we read it once and apply sequentially)
        # But we need to read the file again after import addition? We already have content.
        # We'll apply to content and then write back.
        modified = False
        for pattern, replacement in patterns:
            new_content, count = pattern.subn(replacement, content)
            if count > 0:
                content = new_content
                modified = True
                log(f"fee_structure.py: applied pattern (replaced {count} occurrence(s))", args.verbose)

        if modified:
            if args.dry_run:
                log("DRY RUN: Would update fee_structure.py with multiple replacements", args.verbose, always=True)
            else:
                with open(fee_structure, 'w', encoding='utf-8') as f:
                    f.write(content)
                log("Updated fee_structure.py (replaced manual grade building)", args.verbose, always=True)
        else:
            log("fee_structure.py: no manual grade building found (already fixed)", args.verbose)

    else:
        log("fee_structure.py not found, skipping", args.verbose)

    # -------------------------------------------------------------------------
    # Final verification hints
    # -------------------------------------------------------------------------
    if not args.dry_run:
        log("Refactoring complete. Run the following checks manually:", args.verbose, always=True)
        log("  python3 manage.py check", args.verbose, always=True)
        log("  grep -r \"tenant.tenant_type == 'wing_school'\" axis_saas/views/ --include=\"*.py\"", args.verbose, always=True)
        log("  grep -r \"tenant.tenant_type == 'wing_school'\" templates/ --include=\"*.html\"", args.verbose, always=True)

    log("Patcher finished.", args.verbose, always=True)

if __name__ == "__main__":
    main()
