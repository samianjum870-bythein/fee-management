# axis_saas/management/commands/dedupe_dayschedule.py
#
# Deletes duplicate (academic_calendar_id, label, day_of_week) rows
# from axis_saas_dayschedule across every schema.
#
# Schema names like "3" are quoted safely in Python, so psycopg2 never
# sees stray % characters.

from django.core.management.base import BaseCommand
from django.db import connection


def qident(name: str) -> str:
    """Quote a PostgreSQL identifier safely (works for '3', 'a', 'my schema')."""
    return '"' + name.replace('"', '""') + '"'


class Command(BaseCommand):
    help = ("Delete duplicate (academic_calendar_id, label, day_of_week) rows "
            "from axis_saas_dayschedule in every schema. Keeps the lowest id.")

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT table_schema
                FROM information_schema.tables
                WHERE table_name = 'axis_saas_dayschedule'
                  AND table_schema NOT IN ('pg_catalog','information_schema','pg_toast')
                ORDER BY table_schema;
            """)
            schemas = [row[0] for row in cursor.fetchall()]

        self.stdout.write(f"Found {len(schemas)} schemas: {schemas}")

        total_deleted = 0
        for sch in schemas:
            self.stdout.write(f"--- {sch} ---")
            qsch = qident(sch)

            # 1) Count duplicate groups
            count_sql = f"""
                SELECT COUNT(*) FROM (
                    SELECT 1
                    FROM {qsch}.axis_saas_dayschedule
                    GROUP BY academic_calendar_id, label, day_of_week
                    HAVING COUNT(*) > 1
                ) t;
            """
            with connection.cursor() as cursor:
                cursor.execute(count_sql)
                dup_groups = cursor.fetchone()[0]

            if dup_groups == 0:
                self.stdout.write("  no duplicates")
                continue

            self.stdout.write(f"  {dup_groups} duplicate group(s)")

            # 2) Delete all but the lowest id in each duplicate group
            delete_sql = f"""
                DELETE FROM {qsch}.axis_saas_dayschedule
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY academic_calendar_id, label, day_of_week
                                   ORDER BY id
                               ) AS rn
                        FROM {qsch}.axis_saas_dayschedule
                    ) sub
                    WHERE rn > 1
                );
            """
            with connection.cursor() as cursor:
                cursor.execute(delete_sql)
                deleted = cursor.rowcount
                total_deleted += deleted
                self.stdout.write(f"  deleted {deleted} row(s)")

        self.stdout.write(self.style.SUCCESS(
            f"Dedupe complete. Total rows removed: {total_deleted}"
        ))
