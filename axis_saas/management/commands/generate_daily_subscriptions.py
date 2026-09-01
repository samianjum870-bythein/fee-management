from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context
from axis_saas.models import SchoolClient, GymCustomer, GymSubscription
from datetime import date
from calendar import monthrange


class Command(BaseCommand):
    help = 'Generate subscriptions for customers whose membership_start day is today'

    def handle(self, *args, **options):
        today = date.today()
        day = today.day
        tenants = SchoolClient.objects.filter(is_active=True, tenant_type='gym').exclude(schema_name='public')
        total_generated = 0

        for tenant in tenants:
            with schema_context(tenant.schema_name):
                customers = GymCustomer.objects.filter(
                    status='active',
                    membership_start__day=day
                ).exclude(membership_end__lt=today)
                generated = 0
                for cust in customers:
                    if cust.subscriptions.filter(month=today.month, year=today.year).exists():
                        continue
                    due_day = cust.membership_start.day
                    try:
                        due_date = date(today.year, today.month, due_day)
                    except ValueError:
                        last_day = monthrange(today.year, today.month)[1]
                        due_date = date(today.year, today.month, last_day)
                    cust.generate_subscription_for_month(today.year, today.month, due_date)
                    generated += 1
                total_generated += generated
                if generated:
                    self.stdout.write(f"{tenant.schema_name}: generated {generated} subscriptions")

        self.stdout.write(self.style.SUCCESS(f"Total subscriptions generated: {total_generated}"))
        self.stdout.write(self.style.WARNING("⚠️ Auto-generation is DISABLED by administrator. Subscriptions will NOT be created automatically."))
        self.stdout.write("   Please create subscriptions manually from customer profile.")