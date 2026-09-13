#!/usr/bin/env python3
"""
axis_leave_sidebar_fix.py
=========================

Fixes: Leave Management feature enabled in admin panel but sidebar item
not appearing.

Root cause (verified via analysis):
    The previous LEAVE_MANAGEMENT_V1 patcher's anchor for
    SCHOOL_FEATURE_CHOICES in axis_saas/models.py used a literal
    unicode hyphen (U+2011) inside 'Time-Table Management'. When the
    anchor did not byte-match, the addition of 'leave_management' to
    SCHOOL_FEATURE_CHOICES was silently skipped. As a result:

        * admin form has no 'Leave Management' checkbox,
        * therefore the tenant's enabled_features never contains it,
        * therefore `{% if tenant|has_feature:'leave_management' %}`
          in the sidebar is always False.

This fixer:
    1. Adds 'leave_management' to SCHOOL_FEATURE_CHOICES (idempotent,
       regex-based anchor tolerant of any hyphen variant).
    2. Adds 'staff_leave_management' to STAFF_PORTAL_FEATURE_CHOICES.
    3. Creates axis_saas/management/commands/enable_leave_feature.py
       to enable the feature on existing tenants' DB rows directly.
    4. Verifies all other pieces are in place (read-only diagnostics).

Usage:
    python3 axis_leave_sidebar_fix.py --dry-run --verbose
    python3 axis_leave_sidebar_fix.py
    python3 axis_leave_sidebar_fix.py --target-dir /path/to/project
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ==================================================================
#  Patch helpers
# ==================================================================

# Tolerant pattern: matches 'timetable_management' entry regardless of
# hyphen character(s) between "Time" and "Table".
TIMETABLE_CHOICE_PATTERN = re.compile(
    r"(\(['\"]timetable_management['\"],\s*['\"]Time[^'\"]{0,6}Table Management['\"]\),)"
)

STAFF_MORE_CHOICE_PATTERN = re.compile(
    r"(\(['\"]staff_more['\"],\s*['\"]More['\"]\),)"
)


def patch_school_feature_choices(models_path, dry_run, verbose):
    """Add 'leave_management' to SCHOOL_FEATURE_CHOICES if absent."""
    try:
        content = models_path.read_text(encoding="utf-8")
    except Exception as exc:
        log(f"ERROR reading {models_path}: {exc}")
        return False

    # Idempotency: already added?
    if re.search(r"\(\s*['\"]leave_management['\"]\s*,\s*['\"]Leave Management['\"]\s*\)", content):
        log("OK: SCHOOL_FEATURE_CHOICES already contains 'leave_management'")
        return True

    match = TIMETABLE_CHOICE_PATTERN.search(content)
    if not match:
        log(f"WARN: could not find timetable_management anchor in {models_path}")
        log("      Skipping. Add 'leave_management' to SCHOOL_FEATURE_CHOICES manually.")
        return False

    anchor = match.group(1)
    replacement = (
        anchor + "\n    ('leave_management', 'Leave Management'),"
    )

    new_content = content.replace(anchor, replacement, 1)

    # Extra safety: ensure we didn't create a duplicate line
    if new_content.count("'leave_management', 'Leave Management'") != 1:
        log("REFUSE: replacement would create duplicate or zero entries")
        return False

    if dry_run:
        log("DRY-RUN: would add 'leave_management' to SCHOOL_FEATURE_CHOICES")
        return True

    try:
        models_path.write_text(new_content, encoding="utf-8")
        log("PATCHED: added 'leave_management' to SCHOOL_FEATURE_CHOICES")
        return True
    except Exception as exc:
        log(f"ERROR writing {models_path}: {exc}")
        return False


def patch_staff_portal_feature_choices(models_path, dry_run, verbose):
    """Add 'staff_leave_management' to STAFF_PORTAL_FEATURE_CHOICES if absent."""
    try:
        content = models_path.read_text(encoding="utf-8")
    except Exception as exc:
        log(f"ERROR reading {models_path}: {exc}")
        return False

    if re.search(r"\(\s*['\"]staff_leave_management['\"]\s*,\s*['\"]Leave Management['\"]\s*\)", content):
        log("OK: STAFF_PORTAL_FEATURE_CHOICES already contains 'staff_leave_management'")
        return True

    match = STAFF_MORE_CHOICE_PATTERN.search(content)
    if not match:
        log(f"WARN: could not find 'staff_more' anchor in {models_path}")
        return False

    anchor = match.group(1)
    replacement = (
        anchor + "\n    ('staff_leave_management', 'Leave Management'),"
    )

    new_content = content.replace(anchor, replacement, 1)

    if new_content.count("'staff_leave_management', 'Leave Management'") != 1:
        log("REFUSE: replacement would create duplicate or zero entries")
        return False

    if dry_run:
        log("DRY-RUN: would add 'staff_leave_management' to STAFF_PORTAL_FEATURE_CHOICES")
        return True

    try:
        models_path.write_text(new_content, encoding="utf-8")
        log("PATCHED: added 'staff_leave_management' to STAFF_PORTAL_FEATURE_CHOICES")
        return True
    except Exception as exc:
        log(f"ERROR writing {models_path}: {exc}")
        return False


# ==================================================================
#  Management command
# ==================================================================

MANAGEMENT_COMMAND = '''"""
Enable Leave Management feature flags for one or more tenants.

Usage:
    python manage.py enable_leave_feature --schema myschool
    python manage.py enable_leave_feature --all
    python manage.py enable_leave_feature --all --dry-run
"""
from django.core.management.base import BaseCommand

from axis_saas.models import SchoolClient


DESKTOP_FLAG = "leave_management"
STAFF_PORTAL_FLAG = "staff_leave_management"


class Command(BaseCommand):
    help = (
        "Enable the Leave Management feature on the given tenant(s) by "
        "adding the flag to enabled_features['desktop'] and adding "
        "staff_leave_management to enabled_features['staff_portal']."
    )

    def add_arguments(self, parser):
        parser.add_argument("--schema", help="Only process this schema")
        parser.add_argument(
            "--all", action="store_true",
            help="Process all non-public tenants",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Preview without writing",
        )

    def handle(self, *args, **options):
        qs = SchoolClient.objects.exclude(schema_name="public")
        if options.get("schema"):
            qs = qs.filter(schema_name=options["schema"])
        elif not options.get("all"):
            self.stdout.write(self.style.WARNING(
                "Nothing to do. Pass --schema <name> or --all."
            ))
            return

        total = 0
        for tenant in qs:
            features = tenant.enabled_features
            # Legacy list format -> upgrade to dict
            if isinstance(features, list):
                features = {
                    "desktop": list(features),
                    "mobile": list(features),
                    "staff_portal": [],
                }
            if not isinstance(features, dict):
                features = {"desktop": [], "mobile": [], "staff_portal": []}

            desktop = list(features.get("desktop") or [])
            staff_portal = list(features.get("staff_portal") or [])

            added_desktop = DESKTOP_FLAG not in desktop
            added_staff = STAFF_PORTAL_FLAG not in staff_portal

            if not (added_desktop or added_staff):
                self.stdout.write(
                    f"SKIP    {tenant.schema_name}: already enabled"
                )
                continue

            if added_desktop:
                desktop.append(DESKTOP_FLAG)
            if added_staff:
                staff_portal.append(STAFF_PORTAL_FLAG)

            features["desktop"] = desktop
            features["staff_portal"] = staff_portal

            if options.get("dry_run"):
                self.stdout.write(
                    f"DRY-RUN {tenant.schema_name}: "
                    f"desktop += [{DESKTOP_FLAG if added_desktop else ''}], "
                    f"staff_portal += [{STAFF_PORTAL_FLAG if added_staff else ''}]"
                )
                continue

            tenant.enabled_features = features
            tenant.save(update_fields=["enabled_features"])
            self.stdout.write(self.style.SUCCESS(
                f"UPDATED {tenant.schema_name}: "
                f"desktop={'yes' if added_desktop else 'no'}, "
                f"staff_portal={'yes' if added_staff else 'no'}"
            ))
            total += 1

        if options.get("dry_run"):
            self.stdout.write(self.style.SUCCESS(
                "Dry-run complete. Re-run without --dry-run to apply."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Done. {total} tenant(s) updated."
            ))
'''


def create_management_command(root, dry_run, verbose):
    cmd_path = (
        root / "axis_saas" / "management" / "commands"
        / "enable_leave_feature.py"
    )
    try:
        cmd_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        log(f"ERROR creating {cmd_path.parent}: {exc}")
        return False

    # Ensure __init__.py exists in both package dirs
    for pkg_dir in (
        root / "axis_saas" / "management",
        cmd_path.parent,
    ):
        init_file = pkg_dir / "__init__.py"
        if not init_file.exists():
            if dry_run:
                log(f"DRY-RUN: would create {init_file}")
            else:
                try:
                    init_file.write_text("", encoding="utf-8")
                    log(f"CREATED: {init_file}")
                except Exception as exc:
                    log(f"ERROR creating {init_file}: {exc}")

    if cmd_path.exists():
        try:
            existing = cmd_path.read_text(encoding="utf-8").strip()
            if existing == MANAGEMENT_COMMAND.strip():
                log("OK: management command already up to date")
                return True
        except Exception:
            pass

    if dry_run:
        log(f"DRY-RUN: would write {cmd_path}")
        return True

    try:
        cmd_path.write_text(MANAGEMENT_COMMAND, encoding="utf-8")
        log(f"WROTE: {cmd_path}")
        return True
    except Exception as exc:
        log(f"ERROR writing {cmd_path}: {exc}")
        return False


# ==================================================================
#  Read-only diagnostics
# ==================================================================

def diagnose(root):
    log("--- Diagnostics (read-only) ---")
    models_path = root / "axis_saas" / "models.py"

    try:
        models_src = models_path.read_text(encoding="utf-8")
    except Exception as exc:
        log(f"  ERROR reading models.py: {exc}")
        return

    # 1. Feature choices
    has_desktop_flag = bool(re.search(
        r"\(\s*['\"]leave_management['\"]\s*,",
        models_src,
    ))
    has_staff_flag = bool(re.search(
        r"\(\s*['\"]staff_leave_management['\"]\s*,",
        models_src,
    ))
    log(f"  SCHOOL_FEATURE_CHOICES has 'leave_management':        "
        f"{'YES' if has_desktop_flag else 'NO'}")

    # 2. Models
    has_policy = "class LeavePolicy(" in models_src
    has_request = "class LeaveRequest(" in models_src
    log(f"  LeavePolicy model present:                            "
        f"{'YES' if has_policy else 'NO'}")
    log(f"  LeaveRequest model present:                           "
        f"{'YES' if has_request else 'NO'}")

    # 3. Sidebar
    try:
        base_src = (root / "templates" / "tenant" / "base.html").read_text(encoding="utf-8")
        has_sidebar = "has_feature:'leave_management'" in base_src
    except Exception:
        has_sidebar = False
    log(f"  Sidebar block present in base.html:                   "
        f"{'YES' if has_sidebar else 'NO'}")

    # 4. URLs
    try:
        pub_src = (root / "axis_saas" / "public_urls.py").read_text(encoding="utf-8")
        has_pub_url = ("name='leave_management'" in pub_src
                       or 'name="leave_management"' in pub_src)
    except Exception:
        has_pub_url = False
    log(f"  Public URL 'leave_management' registered:             "
        f"{'YES' if has_pub_url else 'NO'}")

    try:
        stf_src = (root / "axis_saas" / "staff_urls.py").read_text(encoding="utf-8")
        has_stf_url = ("name='staff_leave_management'" in stf_src
                       or 'name="staff_leave_management"' in stf_src)
    except Exception:
        has_stf_url = False
    log(f"  Staff URL 'staff_leave_management' registered:        "
        f"{'YES' if has_stf_url else 'NO'}")

    # 5. Views file
    has_view_file = (root / "axis_saas" / "views" / "leave_management.py").is_file()
    log(f"  View file leave_management.py present:                "
        f"{'YES' if has_view_file else 'NO'}")


# ==================================================================
#  Main
# ==================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Fix missing Leave Management sidebar entry.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing.")
    parser.add_argument("--verbose", action="store_true",
                        help="Verbose output.")
    parser.add_argument("--target-dir", default=".",
                        help="Project root (default: current dir).")
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not (root / "manage.py").is_file():
        log(f"ERROR: manage.py not found in {root}. Wrong --target-dir?")
        return 1

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")

    models_path = root / "axis_saas" / "models.py"

    log("--- Step 1: SCHOOL_FEATURE_CHOICES ---")
    patch_school_feature_choices(models_path, args.dry_run, args.verbose)

    log("--- Step 2: STAFF_PORTAL_FEATURE_CHOICES ---")
    patch_staff_portal_feature_choices(models_path, args.dry_run, args.verbose)

    log("--- Step 3: Management command ---")
    create_management_command(root, args.dry_run, args.verbose)

    diagnose(root)

    log("--- Next steps ---")
    if args.dry_run:
        log("Re-run without --dry-run to apply file changes.")
    else:
        log("File changes applied.")
    log("")
    log("  1. Dev server ko restart karo (runserver auto-reloads, par safe rahne ke liye Ctrl+C phir runserver).")
    log("  2. Yeh chalao (existing tenants ko DB mein fix karne ke liye):")
    log("       python3 manage.py enable_leave_feature --all")
    log("     Ya sirf ek tenant ke liye:")
    log("       python3 manage.py enable_leave_feature --schema <schema_name>")
    log("")
    log("  3. Browser mein admin panel reload karo. Leave Management sidebar mein nazar aana chahiye.")
    log("")
    log("  Verify karne ke liye (optional):")
    log("       python3 manage.py shell -c \"from axis_saas.models import SchoolClient; print([(t.schema_name, 'leave_management' in (t.enabled_features.get('desktop') if isinstance(t.enabled_features, dict) else t.enabled_features)) for t in SchoolClient.objects.exclude(schema_name='public')])\"")

    return 0


if __name__ == "__main__":
    sys.exit(main())
