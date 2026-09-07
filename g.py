#!/usr/bin/env python3
"""
final_fix_staff_templates.py

Fix remaining issues:

Desktop:
- Make each class teacher name in the summary a clickable link to student list
  (filtered by class and wing category if applicable).
- Ensure the link is bold and styled like other meta values.

Mobile:
- Remove the stray {% endif %} and the empty staff-class-sections block.
- Re-add the Assigned Subjects card below the info-grid (with SVG icon).
- Ensure the class teacher info in the hero is also a clickable link.

Usage:
    python final_fix_staff_templates.py [--dry-run] [--verbose] [--target-dir PATH]
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

def patch_desktop(content):
    """Make class teacher names clickable in the profile-meta."""
    # We need to replace the class teacher display block.
    # Currently it's:
    # <span class="value">
    #     {% if class_teacher_classes %}
    #         {% for cls in class_teacher_classes %}
    #             {{ cls.display_name }}{% if not forloop.last %}, {% endif %}
    #         {% endfor %}
    #     {% else %}
    #         &mdash;
    #     {% endif %}
    # </span>
    # We'll wrap each class name in an <a> tag that links to student list with class_id and category_id.
    new_value_block = '''            <span class="value">
                {% if class_teacher_classes %}
                    {% for cls in class_teacher_classes %}
                        <a href="{% url 'student_list' schema_name=tenant.schema_name %}?class_id={{ cls.id }}{% if tenant.tenant_type == 'wing_school' and cls.wing_category %}&category_id={{ cls.wing_category.id }}{% endif %}" style="color: var(--text); text-decoration: none; font-weight: 600;">
                            {{ cls.display_name }}
                        </a>{% if not forloop.last %}, {% endif %}
                    {% endfor %}
                {% else %}
                    &mdash;
                {% endif %}
            </span>'''

    # Find the existing block and replace it.
    # We'll locate the pattern: <span class="value"> ... </span> inside the meta-item for Class Teacher Of.
    # We'll use a regex to find the entire meta-item block.
    pattern = r'(<div class="meta-item">\s*<span class="label">Class Teacher Of</span>\s*<span class="value">.*?</span>\s*</div>)'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        old_block = match.group(1)
        # We'll rebuild the block with the new value.
        # We'll extract the existing label and replace the value.
        new_block = '''        <div class="meta-item">
            <span class="label">Class Teacher Of</span>
''' + new_value_block + '''
        </div>'''
        content = content.replace(old_block, new_block)
    else:
        log("Could not find Class Teacher Of meta-item in desktop template.", verbose=True)

    return content

def patch_mobile(content):
    """Fix mobile template: remove stray endif, add assigned subjects card below info-grid."""
    # 1. Remove the entire staff-class-sections block and the stray endif.
    # We'll remove everything from <!-- ===== STAFF CLASS SECTIONS ... --> to <!-- ===== END ... -->.
    sections_pattern = r'<!-- ===== STAFF CLASS SECTIONS.*? -->.*?<!-- ===== END STAFF CLASS SECTIONS ===== -->'
    content = re.sub(sections_pattern, '', content, flags=re.DOTALL)

    # Also remove any leftover isolated {% endif %} tags that might be floating.
    # We'll remove any {% endif %} that is not part of a proper if block.
    # But we should be careful. In this file, the only endif that could be stray is the one we removed.
    # We'll also clean up any extra whitespace.

    # 2. Add the Assigned Subjects card below the info-grid.
    # We'll insert it after the info-grid div (which ends with </div> after the Notes card).
    # We'll locate the closing tag of the info-grid, which is the last </div> before the end of body.
    # The info-grid is closed with </div> after the Notes card.
    # We'll find the pattern: <div class="info-grid"> ... </div> and insert after that.

    assigned_card = '''
<!-- Assigned Subjects -->
<div class="card" style="background: var(--surface); border-radius: var(--radius); border: 1px solid var(--border); padding: 1rem; box-shadow: var(--shadow-sm); margin-top: 1.5rem;">
    <h3 style="font-size: 1.1rem; font-weight: 600; margin-top: 0; margin-bottom: 0.75rem; border-bottom: 2px solid var(--border); padding-bottom: 0.4rem;">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: middle; margin-right: 0.3rem;"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
        Assigned Subjects
    </h3>
    {% if assigned_classes %}
        <ul style="list-style: none; padding: 0; margin: 0;">
        {% for cls in assigned_classes %}
            <li style="padding: 0.3rem 0; border-bottom: 1px solid var(--border-alt, #f0f0f0);">
                <a href="{% url 'student_list' schema_name=tenant.schema_name %}?class_id={{ cls.school_class.id }}{% if tenant.tenant_type == 'wing_school' and cls.school_class.wing_category %}&category_id={{ cls.school_class.wing_category.id }}{% endif %}" style="color: var(--primary); text-decoration: none; font-weight: 500;">
                    {{ cls.display_name }}
                </a>
                <span style="color: var(--muted); font-size: 0.8rem; margin-left: 0.5rem;">({{ cls.subject.name }})</span>
            </li>
        {% endfor %}
        </ul>
    {% else %}
        <p style="color: var(--muted);">No subject assignments yet.</p>
    {% endif %}
</div>
'''

    # Find the info-grid closing tag. The info-grid is a div with class "info-grid" and contains several info-card divs.
    # We'll find the last </div> that closes the info-grid. We'll use a pattern to match the whole block.
    # The info-grid block is <div class="info-grid"> ... </div>.
    info_grid_pattern = r'(<div class="info-grid">.*?</div>)'
    match = re.search(info_grid_pattern, content, re.DOTALL)
    if match:
        insert_pos = match.end()
        # Insert the assigned card after that, with a newline.
        content = content[:insert_pos] + '\n\n' + assigned_card + '\n' + content[insert_pos:]
    else:
        log("Could not find info-grid in mobile template.", verbose=True)

    # 3. Ensure the class teacher info in the hero is clickable.
    # In the hero we have: Class Teacher: {{ cls.display_name }} ...
    # We'll wrap each class name in an <a> tag with the student list link.
    # The pattern is inside the <div class="class-teacher-info">.
    # We'll replace the existing content with a linked version.
    hero_teacher_pattern = r'(<div class="class-teacher-info".*?>.*?Class Teacher:.*?)({% if class_teacher_classes %}(.*?){% else %}.*?{% endif %})(.*?</div>)'
    # We'll capture the content and replace with linked names.
    # Simpler: we'll search for the entire hero block and replace the class-teacher-info div.
    # We'll locate the div with class "class-teacher-info" and replace its inner content.
    class_teacher_div_pattern = r'(<div class="class-teacher-info" style="[^"]*">)(.*?)(</div>)'
    match = re.search(class_teacher_div_pattern, content, re.DOTALL)
    if match:
        opening = match.group(1)
        inner = match.group(2)
        closing = match.group(3)
        # We'll rebuild the inner with links.
        new_inner = '''
        Class Teacher: 
        {% if class_teacher_classes %}
            {% for cls in class_teacher_classes %}
                <a href="{% url 'student_list' schema_name=tenant.schema_name %}?class_id={{ cls.id }}{% if tenant.tenant_type == 'wing_school' and cls.wing_category %}&category_id={{ cls.wing_category.id }}{% endif %}" style="color: white; text-decoration: underline; font-weight: 500;">
                    {{ cls.display_name }}
                </a>{% if not forloop.last %}, {% endif %}
            {% endfor %}
        {% else %}
            &mdash;
        {% endif %}
        '''
        new_div = opening + new_inner + closing
        content = content[:match.start()] + new_div + content[match.end():]
    else:
        log("Could not find class-teacher-info div in mobile template.", verbose=True)

    return content

def main():
    parser = argparse.ArgumentParser(description="Final fixes for staff profile templates.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output.")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current).")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        log(f"Target directory does not exist: {target_dir}", verbose=args.verbose)
        sys.exit(1)

    desktop_tpl = target_dir / "templates" / "tenant" / "staff_profile.html"
    mobile_tpl = target_dir / "templates" / "mobile" / "staff_profile.html"

    modified = 0

    if desktop_tpl.exists():
        with open(desktop_tpl, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content = patch_desktop(content)
        if new_content != content:
            if args.dry_run:
                log("Would modify desktop template.", verbose=args.verbose)
            else:
                with open(desktop_tpl, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                log("Desktop template updated.", verbose=args.verbose)
            modified += 1
        else:
            log("No changes to desktop template.", verbose=args.verbose)
    else:
        log(f"Desktop template not found: {desktop_tpl}", verbose=args.verbose)

    if mobile_tpl.exists():
        with open(mobile_tpl, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content = patch_mobile(content)
        if new_content != content:
            if args.dry_run:
                log("Would modify mobile template.", verbose=args.verbose)
            else:
                with open(mobile_tpl, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                log("Mobile template updated.", verbose=args.verbose)
            modified += 1
        else:
            log("No changes to mobile template.", verbose=args.verbose)
    else:
        log(f"Mobile template not found: {mobile_tpl}", verbose=args.verbose)

    if args.dry_run:
        log("Dry run completed.", always=True)
    else:
        log(f"Patched {modified} file(s).", always=True)

if __name__ == "__main__":
    main()
