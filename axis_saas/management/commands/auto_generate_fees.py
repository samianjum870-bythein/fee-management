from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context
from axis_saas.views import create_fee_generation_notification
from axis_saas.models import SchoolClient, SchoolFeeSettings, Student, FeeRecord, FeeStructure, ManualGenerationLog
from datetime import date, timedelta
from decimal import Decimal

class Command(BaseCommand):
    help = 'Automatically generate monthly fees for tenants with automation enabled'

    def handle(self, *args, **options):
        tenants = SchoolClient.objects.filter(is_active=True).exclude(schema_name='public')
        today = date.today()
        generated_total = 0

        for tenant in tenants:
            with schema_context(tenant.schema_name):
                settings, _ = SchoolFeeSettings.objects.get_or_create(pk=1)

                if not settings.automation_enabled:
                    self.stdout.write(self.style.WARNING(f"{tenant.schema_name}: automation disabled, skipping"))
                    continue

                if today.day != settings.fee_generation_day:
                    self.stdout.write(self.style.WARNING(f"{tenant.schema_name}: today {today.day} != generation day {settings.fee_generation_day}, skipping"))
                    continue

                month, year = today.month, today.year
                due_date = today + timedelta(days=settings.due_date_offset)
                students = Student.objects.filter(status='active')
                created = 0
                skipped_existing = 0
                skipped_no_fee = 0

                # Pre-fetch fee structures for efficiency
                fee_structs = {fs.grade: fs.monthly_fee for fs in FeeStructure.objects.all()}

                extra_charges = settings.default_extra_charges or []
                total_extra = sum(Decimal(str(ch.get('amount', 0))) for ch in extra_charges)

                fee_records_to_create = []
                for student in students:
                    if FeeRecord.objects.filter(student=student, month=month, year=year).exists():
                        skipped_existing += 1
                        continue

                    base_fee = student.custom_fee if student.custom_fee > 0 else 0
                    if base_fee == 0:
                        base_fee = fee_structs.get(student.grade, 0)

                    if base_fee > 0:
                        fee_records_to_create.append(
                            FeeRecord(
                                student=student,
                                month=month,
                                year=year,
                                amount=base_fee,
                                due_date=due_date,
                                status='pending',
                                extra_charges=extra_charges,
                                due_date_offset=settings.due_date_offset,
                                late_fee_per_day=settings.late_fee_penalty,
                            )
                        )
                        created += 1
                    else:
                        skipped_no_fee += 1

                if fee_records_to_create:
                    FeeRecord.objects.bulk_create(fee_records_to_create)

                if created > 0 or skipped_existing > 0 or skipped_no_fee > 0:
                    ManualGenerationLog.objects.create(
                        month=month,
                        year=year,
                        created_count=created,
                        skipped_existing=skipped_existing,
                        skipped_no_fee=skipped_no_fee,
                        triggered_by='system',
                        log_type='auto'
                    )
                    # Create notification (only if fees were created)
                    if created > 0:
                        create_fee_generation_notification(tenant.schema_name, month, year, created, 'system', mobile=False)

                    self.stdout.write(
                        f"{tenant.schema_name}: generated {created}, "
                        f"already had fee: {skipped_existing}, "
                        f"skipped (no fee structure): {skipped_no_fee} for {month}/{year}"
                    )
                    generated_total += created

        self.stdout.write(self.style.SUCCESS(f"Total fees generated: {generated_total}"))
