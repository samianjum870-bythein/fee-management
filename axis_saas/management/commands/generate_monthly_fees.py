from django.core.management.base import BaseCommand
from django.db import connection
from axis_saas.models import SchoolClient, SchoolFeeSettings, Student, FeeRecord, FeeStructure
from datetime import date, timedelta
from decimal import Decimal
from django_tenants.utils import schema_context

class Command(BaseCommand):
    help = 'Generate monthly fee records for all tenants based on their fee_generation_day'

    def handle(self, *args, **options):
        tenants = SchoolClient.objects.filter(is_active=True).exclude(schema_name='public')
        today = date.today()
        generated_count = 0
        
        for tenant in tenants:
            with schema_context(tenant.schema_name):
                settings, created = SchoolFeeSettings.objects.get_or_create(pk=1)
                generation_day = settings.fee_generation_day
                
                if today.day == generation_day:
                    month = today.month
                    year = today.year
                    
                    if FeeRecord.objects.filter(month=month, year=year).exists():
                        self.stdout.write(f"Skipping {tenant.schema_name} - fees already generated")
                        continue
                    
                    students = Student.objects.filter(status='active')
                    due_date = today + timedelta(days=settings.due_date_offset)
                    created_records = 0
                    
                    records_to_create = []
                    for student in students:
                        base_fee = student.custom_fee if student.custom_fee > 0 else 0
                        if base_fee == 0:
                            fee_struct = FeeStructure.objects.filter(grade=student.grade).first()
                            if fee_struct:
                                base_fee = fee_struct.monthly_fee

                        if base_fee > 0:
                            records_to_create.append(
                                FeeRecord(
                                    student=student,
                                    month=month,
                                    year=year,
                                    amount=base_fee,
                                    due_date=due_date,
                                    status='pending',
                                )
                            )
                            created_records += 1

                    if records_to_create:
                        FeeRecord.objects.bulk_create(records_to_create)
                    
                    generated_count += created_records
                    self.stdout.write(f"Generated {created_records} fee records for {tenant.schema_name}")
        
        self.stdout.write(self.style.SUCCESS(f"Total: {generated_count} records"))
