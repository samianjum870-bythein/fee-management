from django.core.management.base import BaseCommand
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
