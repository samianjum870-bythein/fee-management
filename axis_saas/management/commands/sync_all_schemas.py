from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django_tenants.utils import schema_context
from django.conf import settings
from axis_saas.models import SchoolClient
from django.apps import apps

class Command(BaseCommand):
    help = 'Sync all tenant schemas: create missing tables and add missing columns'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('sync_all_schemas is disabled outside DEBUG mode. Use migrate_schemas for production-safe tenant migration.')
        tenants = SchoolClient.objects.exclude(schema_name='public')
        self.stdout.write(f"Found {tenants.count()} tenant schemas")
        for tenant in tenants:
            self.stdout.write(f"Syncing schema: {tenant.schema_name}")
            with schema_context(tenant.schema_name):
                # Ensure all tables exist by running migrations (safe)
                from django.core.management import call_command
                call_command('migrate', verbosity=0, interactive=False)
                
                # Additional manual fixes for known missing columns
                with connection.cursor() as cursor:
                    # Fix PaymentTransaction table
                    cursor.execute("""
                        SELECT column_name FROM information_schema.columns 
                        WHERE table_name='axis_saas_paymenttransaction'
                    """)
                    cols = [row[0] for row in cursor.fetchall()]
                    if 'payment_type' not in cols:
                        cursor.execute("ALTER TABLE axis_saas_paymenttransaction ADD COLUMN payment_type varchar(20) DEFAULT 'full'")
                        self.stdout.write("  Added payment_type column")
                    if 'remarks' not in cols:
                        cursor.execute("ALTER TABLE axis_saas_paymenttransaction ADD COLUMN remarks text")
                        self.stdout.write("  Added remarks column")
                    if 'created_by' not in cols:
                        cursor.execute("ALTER TABLE axis_saas_paymenttransaction ADD COLUMN created_by varchar(150)")
                        self.stdout.write("  Added created_by column")
                    # Ensure payment_date is NOT NULL and has default
                    cursor.execute("ALTER TABLE axis_saas_paymenttransaction ALTER COLUMN payment_date SET NOT NULL")
                    cursor.execute("ALTER TABLE axis_saas_paymenttransaction ALTER COLUMN payment_date SET DEFAULT CURRENT_DATE")
                    
                    # Fix Student table
                    cursor.execute("""
                        SELECT column_name FROM information_schema.columns 
                        WHERE table_name='axis_saas_student'
                    """)
                    student_cols = [row[0] for row in cursor.fetchall()]
                    if 'father_name' not in student_cols:
                        cursor.execute("ALTER TABLE axis_saas_student ADD COLUMN father_name varchar(150) DEFAULT ''")
                        self.stdout.write("  Added father_name column")
                    if 'custom_fee' not in student_cols:
                        cursor.execute("ALTER TABLE axis_saas_student ADD COLUMN custom_fee numeric(10,2) DEFAULT 0")
                        self.stdout.write("  Added custom_fee column")
                    if 'roll_number' not in student_cols:
                        cursor.execute("ALTER TABLE axis_saas_student ADD COLUMN roll_number varchar(50) UNIQUE")
                        self.stdout.write("  Added roll_number column")
                    if 'enrolled_on' not in student_cols:
                        cursor.execute("ALTER TABLE axis_saas_student ADD COLUMN enrolled_on timestamp with time zone DEFAULT now()")
                        self.stdout.write("  Added enrolled_on column")
        self.stdout.write(self.style.SUCCESS("All schemas synced successfully."))
