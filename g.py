#!/usr/bin/env python3
"""
axis_patcher_fix_wing_filter_no_redirect.py - Remove page reload on wing filter change.

This patcher updates the "Class Teacher" tab to filter the class dropdown
using client-side JavaScript instead of a full page reload.

It replaces the onchange redirect with a JavaScript event listener that
shows/hides class options based on the selected wing (using data-wing attributes).

Usage:
    python3 axis_patcher_fix_wing_filter_no_redirect.py [--dry-run] [--verbose] [--target-dir PATH]
"""

import os
import re
import sys
from pathlib import Path
import argparse

def main():
    parser = argparse.ArgumentParser(description="Remove page reload on wing filter change")
    parser.add_argument('--dry-run', action='store_true', help="Preview changes without writing")
    parser.add_argument('--verbose', action='store_true', help="Show detailed output")
    parser.add_argument('--target-dir', default='.', help="Project root directory")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        print(f"❌ Target directory does not exist: {target_dir}")
        sys.exit(1)

    os.chdir(target_dir)
    template_file = Path("templates/tenant/wing_school_class_management.html")

    if not template_file.exists():
        print(f"❌ File not found: {template_file}")
        sys.exit(1)

    with open(template_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # ------------------------------------------------------------------
    # 1. Replace the wing filter dropdown: remove onchange and add an id.
    #    Current: <select id="wingFilter" class="form-control" onchange="window.location.href = window.location.pathname + '?wing=' + this.value">
    #    We'll keep the id and remove onchange.
    # ------------------------------------------------------------------
    old_select_pattern = r'<select id="wingFilter" class="form-control" onchange="window\.location\.href = window\.location\.pathname \+ \'\?wing=\' \+ this\.value">'
    new_select = '<select id="wingFilter" class="form-control">'

    if re.search(old_select_pattern, content):
        content = re.sub(old_select_pattern, new_select, content)
        print("✅ Removed onchange attribute from wing filter dropdown.")
    else:
        print("⚠️ Could not find the exact select tag with onchange. Trying a fallback pattern.")
        # Fallback: find any select with id="wingFilter" and remove onchange attribute
        fallback_pattern = r'(<select id="wingFilter"[^>]*)(onchange="[^"]*")([^>]*>)'
        if re.search(fallback_pattern, content):
            content = re.sub(fallback_pattern, r'\1\3', content)
            print("✅ Removed onchange attribute using fallback pattern.")
        else:
            print("❌ Could not find the wingFilter select element. Aborting.")
            sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Add JavaScript to filter class options based on wing selection.
    #    We'll insert a new <script> block after the existing scripts or at the end.
    # ------------------------------------------------------------------
    # We'll insert after the existing <script> block that handles tabs.
    # Find the closing </script> of the main script block and insert after it.
    # Or we can append at the end before {% endblock %}.
    # We'll look for the line containing the modals comment and insert before it.
    script_tag = """
<script>
    (function() {
        const wingFilter = document.getElementById('wingFilter');
        const classSelect = document.getElementById('classSelect');

        if (wingFilter && classSelect) {
            function filterClasses() {
                const selectedWing = wingFilter.value;
                const options = classSelect.options;
                for (let i = 0; i < options.length; i++) {
                    const option = options[i];
                    if (option.value === '') continue;
                    const wing = option.getAttribute('data-wing');
                    if (selectedWing === '' || wing === selectedWing) {
                        option.style.display = '';
                    } else {
                        option.style.display = 'none';
                    }
                }
                // Reset the selected value if the currently selected option is hidden
                const selectedOption = classSelect.options[classSelect.selectedIndex];
                if (selectedOption && selectedOption.style.display === 'none') {
                    classSelect.value = '';
                }
            }

            // Initial filter
            filterClasses();

            // Add event listener
            wingFilter.addEventListener('change', filterClasses);
        }
    })();
</script>
"""

    # Find a suitable insertion point: after the last </script> that is before the modals.
    # We'll insert before the <!-- ===== MODALS ===== --> line.
    modals_marker = '<!-- ===== MODALS ===== -->'
    if modals_marker in content:
        # Insert before the modals marker
        content = content.replace(modals_marker, script_tag + '\n' + modals_marker)
        print("✅ Inserted client-side filter script before modals section.")
    else:
        # Fallback: insert before {% endblock %}
        if '{% endblock %}' in content:
            content = content.replace('{% endblock %}', script_tag + '\n{% endblock %}')
            print("✅ Inserted client-side filter script before {% endblock %}.")
        else:
            print("⚠️ Could not find insertion point; appending to the end.")
            content += '\n' + script_tag

    # ------------------------------------------------------------------
    # 3. Ensure the class select still has data-wing attributes (it already does)
    #    and that the wingFilter select has the correct options.
    #    Also, make sure the selected_wing context variable is not used anymore
    #    (we no longer need it for server-side filtering). We can keep it harmless.
    # ------------------------------------------------------------------
    # We'll also remove the selected_wing from the option tag for wingFilter
    # since we no longer use server-side selected_wing. But we can keep it; it doesn't harm.
    # However, if the user wants to keep the selected value across page loads, we might want to
    # set the selected attribute via JS, but we'll ignore that for now.

    if args.dry_run:
        print("🔍 DRY RUN: Would write changes to templates/tenant/wing_school_class_management.html")
        if args.verbose:
            import difflib
            # Read original content for diff
            with open(template_file, 'r', encoding='utf-8') as f:
                original = f.read()
            diff = difflib.unified_diff(original.splitlines(), content.splitlines(), lineterm='')
            for d in diff:
                print(d)
        return

    with open(template_file, 'w', encoding='utf-8') as f:
        f.write(content)

    print("✅ Successfully updated templates/tenant/wing_school_class_management.html")
    print("   The wing filter now filters class options on the client side without page reload.")
    print("   Restart the server to see the changes.")


if __name__ == "__main__":
    main()
