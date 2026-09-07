#!/usr/bin/env python3
"""
axis_patcher.py

Enhances the staff dashboard to display detailed class information:
- Classes where staff is class teacher (with student strength)
- Classes where staff is subject teacher (with student strength)
- Total students and today's attendance across these classes

Usage:
    python axis_patcher.py [--dry-run] [--verbose] [--target-dir PATH]
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

def patch_file(file_path, search_pattern, replace_str, dry_run=False, verbose=False):
    """Apply a regex substitution to a file."""
    if not file_path.exists():
        log(f"File not found: {file_path}", verbose)
        return False

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check if already patched (idempotent)
    if replace_str in content:
        log(f"Patch already applied to {file_path}", verbose)
        return False

    original = content
    new_content, count = re.subn(search_pattern, replace_str, content, flags=re.DOTALL)

    if count == 0:
        log(f"No match found for pattern in {file_path}", verbose)
        return False

    if new_content == original:
        log(f"No changes made to {file_path}", verbose)
        return False

    if dry_run:
        log(f"Would modify {file_path} ({count} occurrence(s))", always=True)
        if verbose:
            import difflib
            diff = difflib.unified_diff(
                original.splitlines(),
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

def insert_after(content, marker, insert_text, verbose=False):
    """Insert text after the first occurrence of marker."""
    lines = content.splitlines()
    new_lines = []
    inserted = False
    for line in lines:
        new_lines.append(line)
        if not inserted and marker in line:
            new_lines.append(insert_text)
            inserted = True
    if not inserted:
        log(f"Marker '{marker}' not found in file.", verbose=verbose)
        return content
    return '\n'.join(new_lines)

def main():
    parser = argparse.ArgumentParser(
        description="Enhance staff dashboard with class details."
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output.")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current).")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        log(f"Target directory does not exist: {target_dir}", verbose=args.verbose)
        sys.exit(1)

    # ======================================================================
    # 1. PATCH VIEW: axis_saas/views/staff_portal.py
    # ======================================================================
    view_file = target_dir / "axis_saas" / "views" / "staff_portal.py"
    if not view_file.exists():
        log(f"View file not found: {view_file}", verbose=args.verbose)
        sys.exit(1)

    with open(view_file, 'r', encoding='utf-8') as f:
        view_content = f.read()

    # Ensure import of Count
    if 'from django.db.models import Count' not in view_content:
        # Add after existing imports
        lines = view_content.splitlines()
        # Find last import line
        last_import_idx = -1
        for i, line in enumerate(lines):
            if line.startswith('from ') or line.startswith('import '):
                last_import_idx = i
        if last_import_idx != -1:
            lines.insert(last_import_idx + 1, 'from django.db.models import Count')
        else:
            lines.insert(0, 'from django.db.models import Count')
        view_content = '\n'.join(lines)

    # Replace the staff_dashboard function with enhanced version
    # We'll match the function definition and replace its body.
    # We'll use a regex to find the function and its body until the next def or end.
    # But we need to be careful with indentation. We'll replace the whole function.
    # We'll define the new function code.
    new_func = '''@require_staff_login
@require_staff_feature('staff_dashboard')
def staff_dashboard(request):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        # Classes where staff is class teacher
        class_teacher_classes = SchoolClass.objects.filter(
            class_teacher=staff, is_active=True
        ).annotate(student_count=Count('students')).order_by('name', 'section')
        # Classes where staff is subject teacher (distinct)
        subject_teacher_classes = SchoolClass.objects.filter(
            class_subjects__teacher=staff, is_active=True
        ).distinct().annotate(student_count=Count('students')).order_by('name', 'section')
        # Combined for total counts
        all_classes = class_teacher_classes | subject_teacher_classes
        student_count = Student.objects.filter(school_class__in=all_classes).count()
        today = timezone.localdate()
        attendance_today = StudentAttendance.objects.filter(date=today, school_class__in=all_classes).count()
        notifications = Notification.objects.filter(is_read=False).order_by('-created_at')[:5]

    return render(
        request,
        'mobile/staff/dashboard.html',
        {
            'staff': staff,
            'class_teacher_classes': class_teacher_classes,
            'subject_teacher_classes': subject_teacher_classes,
            'student_count': student_count,
            'attendance_today': attendance_today,
            'notifications': notifications,
            'schema_name': schema_name,
        },
    )'''

    # Find the old function definition and replace it.
    # We'll match from the line containing "def staff_dashboard" up to the next function definition at top level or end.
    # We'll use a regex that captures the function block.
    # We'll use a pattern that matches the function header and body until the next line that starts with 'def' or '@' at column 0.
    # But we need to include decorators.
    # Simpler: we'll locate the line index of the function definition and then find the end.
    lines = view_content.splitlines()
    start_idx = -1
    for i, line in enumerate(lines):
        if re.match(r'^\s*def staff_dashboard\s*\(', line):
            # Go back to find decorators
            start_idx = i
            while start_idx > 0 and lines[start_idx - 1].strip().startswith('@'):
                start_idx -= 1
            break
    if start_idx == -1:
        log("Could not find staff_dashboard function in view file", verbose=args.verbose)
        sys.exit(1)

    # Find the end: next top-level def or @
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        if re.match(r'^(def|@)', lines[i]) and not lines[i].startswith(' '):
            end_idx = i
            break

    # Replace lines from start_idx to end_idx-1 with new_func lines
    new_lines = lines[:start_idx] + new_func.splitlines() + lines[end_idx:]
    new_view_content = '\n'.join(new_lines)

    if args.dry_run:
        log(f"Would modify {view_file}", always=True)
        if args.verbose:
            import difflib
            diff = difflib.unified_diff(
                view_content.splitlines(),
                new_view_content.splitlines(),
                fromfile=view_file.name,
                tofile=view_file.name + ' (patched)'
            )
            for line in diff:
                print(line)
    else:
        with open(view_file, 'w', encoding='utf-8') as f:
            f.write(new_view_content)
        log(f"Patched {view_file}", always=True)

    # ======================================================================
    # 2. PATCH TEMPLATE: templates/mobile/staff/dashboard.html
    # ======================================================================
    template_file = target_dir / "templates" / "mobile" / "staff" / "dashboard.html"
    if not template_file.exists():
        log(f"Template file not found: {template_file}. Skipping template patch.", verbose=args.verbose)
    else:
        with open(template_file, 'r', encoding='utf-8') as f:
            template_content = f.read()

        # We'll insert a new section after the <body> tag or after a known element.
        # We'll search for the first <div class="container"> or the main content area.
        # Since we don't know the exact structure, we'll insert a new card after the <body> tag.
        # We'll define the new HTML block.
        new_section = '''
<!-- ===== CLASSES SUMMARY ===== -->
<div class="classes-summary" style="margin-bottom:1.5rem;">
    <div class="card" style="background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:1rem; box-shadow:var(--shadow);">
        <h3 style="margin-top:0; font-weight:700; font-size:1.1rem; color:var(--text);">Your Classes</h3>
        {% if class_teacher_classes %}
            <div style="margin-bottom:0.5rem;">
                <strong style="color:var(--accent);">Class Teacher:</strong>
                <ul style="margin:0.2rem 0; padding-left:1.2rem;">
                    {% for cls in class_teacher_classes %}
                        <li>{{ cls.name }} - {{ cls.section }} ({{ cls.student_count }} students)</li>
                    {% endfor %}
                </ul>
            </div>
        {% endif %}
        {% if subject_teacher_classes %}
            <div>
                <strong style="color:var(--accent);">Subject Teacher:</strong>
                <ul style="margin:0.2rem 0; padding-left:1.2rem;">
                    {% for cls in subject_teacher_classes %}
                        <li>{{ cls.name }} - {{ cls.section }} ({{ cls.student_count }} students)</li>
                    {% endfor %}
                </ul>
            </div>
        {% endif %}
        {% if not class_teacher_classes and not subject_teacher_classes %}
            <p style="color:var(--muted); margin:0;">You are not assigned to any classes.</p>
        {% endif %}
    </div>
</div>
'''

        # We'll insert this after the <body> tag or after the first <div class="container"> or similar.
        # We'll try to insert after the opening <body> tag.
        # But to be safe, we'll insert after the first <div> that likely contains the main content.
        # We'll use a marker: we'll look for "{% block body %}" and insert after it.
        # If not found, insert after <body>.
        marker = '{% block body %}'
        if marker in template_content:
            template_content = insert_after(template_content, marker, new_section, args.verbose)
        else:
            # Fallback: insert after <body>
            template_content = insert_after(template_content, '<body>', new_section, args.verbose)

        if args.dry_run:
            log(f"Would modify {template_file}", always=True)
            if args.verbose:
                import difflib
                with open(template_file, 'r', encoding='utf-8') as f_orig:
                    orig = f_orig.read()
                diff = difflib.unified_diff(
                    orig.splitlines(),
                    template_content.splitlines(),
                    fromfile=template_file.name,
                    tofile=template_file.name + ' (patched)'
                )
                for line in diff:
                    print(line)
        else:
            with open(template_file, 'w', encoding='utf-8') as f:
                f.write(template_content)
            log(f"Patched {template_file}", always=True)

    if args.dry_run:
        log("Dry run completed. No files were changed.")
    else:
        log("Patch applied. Restart your Django server for changes to take effect.")

if __name__ == "__main__":
    main()
