#!/usr/bin/env python3
"""
axis_patcher.py – Fix missing timetable tables by adding a migration‑generating command.

This patcher creates a management command `fix_timetable_tables` that:
1. Runs `makemigrations axis_saas` (if needed) to generate a migration for the new models.
2. Runs `migrate` for every active tenant schema to apply the migration.

Run it once after applying the timetable feature.

Usage:
    python axis_patcher.py [--dry-run] [--verbose] [--target-dir /path/to/project]
"""

import os
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

def create_file(filepath, content, dry_run=False, verbose=False, force=False):
    """Create a new file with the given content. If force=False, skip if exists."""
    if os.path.exists(filepath) and not force:
        log(f"File already exists, skipping: {filepath}", verbose)
        return False
    if dry_run:
        log(f"DRY RUN: Would create {filepath}", verbose, always=True)
        return True
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    log(f"Created {filepath}", verbose, always=True)
    return True

# -----------------------------------------------------------------------------
# Main patcher
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fix missing timetable tables")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output.")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current).")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.is_dir():
        log(f"Target directory does not exist: {target_dir}", always=True)
        sys.exit(1)

    log(f"Starting patcher in {target_dir} (dry-run={args.dry_run})", args.verbose, always=True)

    # Define the new management command file
    cmd_dir = target_dir / "axis_saas" / "management" / "commands"
    cmd_file = cmd_dir / "fix_timetable_tables.py"

    # Content of the new command
    command_content = '''from django.core.management.base import BaseCommand
from django.core.management import call_command
from django_tenants.utils import schema_context
from axis_saas.models import SchoolClient

class Command(BaseCommand):
    help = "Generate migrations and create missing timetable tables for all tenant schemas."

    def handle(self, *args, **options):
        self.stdout.write("Generating migrations for axis_saas...")
        call_command('makemigrations', 'axis_saas', verbosity=1, interactive=False)
        self.stdout.write("Migrations generated (if any).")

        tenants = SchoolClient.objects.filter(is_active=True).exclude(schema_name='public')
        if not tenants.exists():
            self.stdout.write(self.style.WARNING("No active tenants found."))
            return

        self.stdout.write(f"Found {tenants.count()} tenant(s). Running migrations...")
        for tenant in tenants:
            self.stdout.write(f"  Migrating schema: {tenant.schema_name}")
            with schema_context(tenant.schema_name):
                call_command('migrate', verbosity=0, interactive=False)
        self.stdout.write(self.style.SUCCESS("All tenant schemas migrated successfully."))
'''

    # Create the command file
    create_file(cmd_file, command_content, args.dry_run, args.verbose)

    # Provide instructions
    log("\n✅ Command added: fix_timetable_tables", always=True)
    log("To create the missing tables, run:\n    python manage.py fix_timetable_tables", always=True)
    log("This will generate a migration (if needed) and apply it to all tenants.", always=True)

    log("Patcher finished.", args.verbose, always=True)

if __name__ == "__main__":
    main()
