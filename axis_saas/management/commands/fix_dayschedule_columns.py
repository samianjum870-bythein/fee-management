# axis_saas/management/commands/fix_dayschedule_columns.py
from django.core.management.base import BaseCommand
from django.db import connection


SQL_BLOCK = """
DO $$
DECLARE
    sch text;
BEGIN
    FOR sch IN
        SELECT schema_name FROM information_schema.schemata
        WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast')
    LOOP
        -- ===== axis_saas_dayschedule =====
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = sch AND table_name = 'axis_saas_dayschedule'
        ) THEN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema=sch AND table_name='axis_saas_dayschedule' AND column_name='label')
            THEN
                EXECUTE format('ALTER TABLE %I.axis_saas_dayschedule ADD COLUMN label varchar(50) NOT NULL DEFAULT %L', sch, '');
                RAISE NOTICE 'added label          -> %', sch;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema=sch AND table_name='axis_saas_dayschedule' AND column_name='order')
            THEN
                EXECUTE format('ALTER TABLE %I.axis_saas_dayschedule ADD COLUMN "order" integer NOT NULL DEFAULT 0', sch);
                RAISE NOTICE 'added order          -> %', sch;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema=sch AND table_name='axis_saas_dayschedule' AND column_name='break_after')
            THEN
                EXECUTE format('ALTER TABLE %I.axis_saas_dayschedule ADD COLUMN break_after integer NULL', sch);
                RAISE NOTICE 'added break_after    -> %', sch;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema=sch AND table_name='axis_saas_dayschedule' AND column_name='break_duration')
            THEN
                EXECUTE format('ALTER TABLE %I.axis_saas_dayschedule ADD COLUMN break_duration integer NOT NULL DEFAULT 0', sch);
                RAISE NOTICE 'added break_duration -> %', sch;
            END IF;
        END IF;

        -- ===== axis_saas_schedulelabel =====
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = sch AND table_name = 'axis_saas_schedulelabel'
        ) THEN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema=sch AND table_name='axis_saas_schedulelabel' AND column_name='description')
            THEN
                EXECUTE format('ALTER TABLE %I.axis_saas_schedulelabel ADD COLUMN description varchar(150) NOT NULL DEFAULT %L', sch, '');
                RAISE NOTICE 'added description    -> %', sch;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema=sch AND table_name='axis_saas_schedulelabel' AND column_name='created_at')
            THEN
                EXECUTE format('ALTER TABLE %I.axis_saas_schedulelabel ADD COLUMN created_at timestamptz NOT NULL DEFAULT NOW()', sch);
                RAISE NOTICE 'added created_at     -> %', sch;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema=sch AND table_name='axis_saas_schedulelabel' AND column_name='updated_at')
            THEN
                EXECUTE format('ALTER TABLE %I.axis_saas_schedulelabel ADD COLUMN updated_at timestamptz NOT NULL DEFAULT NOW()', sch);
                RAISE NOTICE 'added updated_at     -> %', sch;
            END IF;
        END IF;
    END LOOP;
END $$;
"""


class Command(BaseCommand):
    help = (
        "Ensure required columns exist on axis_saas_dayschedule and "
        "axis_saas_schedulelabel across every schema."
    )

    def handle(self, *args, **options):
        self.stdout.write("Fixing columns across all schemas...")
        with connection.cursor() as cursor:
            cursor.execute(SQL_BLOCK)
            # NOTICE messages come through as warnings; print them all
            for msg in cursor.connection.notices:
                self.stdout.write(msg.rstrip())
        self.stdout.write(self.style.SUCCESS("Done. Verifying..."))

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT table_schema, column_name
                FROM information_schema.columns
                WHERE table_name='axis_saas_dayschedule'
                  AND column_name IN ('label','order','break_after','break_duration')
                ORDER BY table_schema, column_name;
            """)
            rows = cursor.fetchall()
            self.stdout.write("Dayschedule columns found:")
            for sch, col in rows:
                self.stdout.write(f"  {sch:20s} {col}")

            cursor.execute("""
                SELECT table_schema, column_name
                FROM information_schema.columns
                WHERE table_name='axis_saas_schedulelabel'
                ORDER BY table_schema, column_name;
            """)
            rows = cursor.fetchall()
            self.stdout.write("ScheduleLabel columns found:")
            for sch, col in rows:
                self.stdout.write(f"  {sch:20s} {col}")

        self.stdout.write(self.style.SUCCESS("Column fix complete."))
