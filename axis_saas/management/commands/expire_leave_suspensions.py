"""Deactivate expired LeaveSuspension rows across every tenant.

LEAVE_MANAGEMENT_HARDENING_V3: the admin list view also runs a lazy
expiry pass on every read (cheap — it's a single UPDATE ... WHERE that
hits no rows most of the time). This command exists so production can
schedule the expiry as a cron job instead of relying on GET side
effects:

    # crontab
    5 0 * * * cd /srv/app && python manage.py expire_leave_suspensions

Idempotent, safe to run as often as you like.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import schema_context

from axis_saas.models import LeaveSuspension, SchoolClient


class Command(BaseCommand):
    help = (
        "Deactivate LeaveSuspension rows whose end_date has passed in "
        "every tenant schema. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--schema',
            help='Only process this schema (default: all non-public)',
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        qs = SchoolClient.objects.exclude(schema_name='public')
        if options.get('schema'):
            qs = qs.filter(schema_name=options['schema'])

        grand_total = 0
        for tenant in qs:
            with schema_context(tenant.schema_name):
                with transaction.atomic():
                    count = LeaveSuspension.objects.filter(
                        is_active=True,
                        end_date__lt=today,
                    ).update(
                        is_active=False,
                        lifted_at=timezone.now(),
                        lifted_by='system:auto-expired',
                    )
            if count:
                self.stdout.write(
                    f"  {tenant.schema_name}: expired {count} suspension(s)"
                )
            grand_total += count

        if grand_total == 0:
            self.stdout.write(self.style.SUCCESS('Nothing to expire.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Done. Total suspensions expired: {grand_total}'
            ))
