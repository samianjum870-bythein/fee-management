"""
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
