"""Deploy-time schema sanity check.

ASSIGN_TEACHERS_HARDENING_V3 ships a new migration that adds
``created_by`` / ``updated_by`` to ``axis_saas_periodteacherassignment``.
If an operator forgets to run ``migrate_schemas`` on a tenant, the
assign-teachers endpoints will 500 with ``column does not exist``.

Run this command in the deploy pipeline (after migrations) to fail
fast when a tenant is behind:

    python manage.py check_schema_columns

Exits non-zero if any active tenant is missing the columns.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django_tenants.utils import schema_context

from axis_saas.models import SchoolClient


REQUIRED_COLUMNS = {
    'axis_saas_periodteacherassignment': ('created_by', 'updated_by'),
}


class Command(BaseCommand):
    help = (
        "Verify that every active tenant schema has the columns that "
        "ASSIGN_TEACHERS_HARDENING_V3 requires. Fails (exit 1) if any "
        "tenant is missing them."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--schema',
            help='Only check this tenant schema (default: all active)',
        )

    def handle(self, *args, **options):
        qs = (
            SchoolClient.objects
            .filter(is_active=True)
            .exclude(schema_name='public')
        )
        if options.get('schema'):
            qs = qs.filter(schema_name=options['schema'])

        failures = []
        for tenant in qs:
            with schema_context(tenant.schema_name):
                with connection.cursor() as cursor:
                    for table, cols in REQUIRED_COLUMNS.items():
                        for col in cols:
                            cursor.execute(
                                "SELECT 1 FROM information_schema.columns "
                                "WHERE table_schema = current_schema() "
                                "  AND table_name = %s "
                                "  AND column_name = %s",
                                [table, col],
                            )
                            if cursor.fetchone() is None:
                                failures.append(
                                    f"{tenant.schema_name}: missing "
                                    f"{table}.{col}"
                                )

        if failures:
            for f in failures:
                self.stderr.write(self.style.ERROR(f"  ✗ {f}"))
            raise CommandError(
                "One or more tenants are missing required columns. "
                "Run `python manage.py migrate_schemas`."
            )

        self.stdout.write(self.style.SUCCESS(
            f"All {qs.count()} tenant schema(s) OK."
        ))
