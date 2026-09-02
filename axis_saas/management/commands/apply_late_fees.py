from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context
from axis_saas.models import SchoolClient, FeeRecord
from datetime import date
from decimal import Decimal

class Command(BaseCommand):
    help = 'Apply late fees to overdue fee records'

    def handle(self, *args, **options):
        tenants = SchoolClient.objects.filter(is_active=True).exclude(schema_name='public')
        today = date.today()
        total_updated = 0

        for tenant in tenants:
            with schema_context(tenant.schema_name):
                overdue_records = list(
                    FeeRecord.objects.filter(
                        due_date__lt=today,
                        status__in=['pending', 'partial', 'overdue']
                    ).order_by('id')
                )
                updates = []
                for record in overdue_records:
                    days = (today - record.due_date).days
                    if days <= 0:
                        continue
                    record.late_fee_accrued = Decimal(days) * record.late_fee_per_day
                    updates.append(record)

                if updates:
                    FeeRecord.objects.bulk_update(updates, ['late_fee_accrued'])
                    total_updated += len(updates)

        self.stdout.write(self.style.SUCCESS(f"Applied late fees to {total_updated} records."))
