#!/usr/bin/env python3
"""
Django management command to repair fee data for all tenants.
- Updates student grades to the correct display name from school_class.
- Creates FeeStructure entries for all distinct display grades of active students.
- Sets student.custom_fee from the FeeStructure if not already set.

Usage:
    python manage.py fix_fee_data [--dry-run] [--schema SCHEMA_NAME]
"""

from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context
from axis_saas.models import SchoolClient, Student, FeeStructure, SchoolClass, WingCategory
from axis_saas.views.helpers import get_student_display_grade
from decimal import Decimal

class Command(BaseCommand):
    help = 'Fix student grades and FeeStructure consistency across all tenants.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show changes without applying.')
        parser.add_argument('--schema', type=str, help='Only process this tenant schema.')

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        schema_name = options.get('schema')
        tenants = SchoolClient.objects.filter(is_active=True).exclude(schema_name='public')
        if schema_name:
            tenants = tenants.filter(schema_name=schema_name)

        for tenant in tenants:
            self.stdout.write(f"\nProcessing tenant: {tenant.schema_name} ({tenant.tenant_type})")
            with schema_context(tenant.schema_name):
                # 1. Get all active students
                students = Student.objects.filter(status='active').select_related('school_class__wing_category')
                if not students.exists():
                    self.stdout.write("  No active students.")
                    continue

                # 2. Build grade->class mapping for display names (already in helpers)
                # We'll just use get_student_display_grade for each student.

                # 3. Update student grades
                updated_students = 0
                for student in students:
                    if student.school_class:
                        correct_grade = get_student_display_grade(student)
                        if student.grade != correct_grade:
                            if dry_run:
                                self.stdout.write(f"  Would update {student.name} grade: '{student.grade}' -> '{correct_grade}'")
                            else:
                                student.grade = correct_grade
                                student.save(update_fields=['grade'])
                                updated_students += 1
                    else:
                        self.stdout.write(f"  WARNING: {student.name} has no school_class. Grade='{student.grade}'")

                if updated_students:
                    self.stdout.write(f"  Updated {updated_students} student grades.")

                # 4. Find all distinct display grades among active students (after update)
                distinct_grades = set()
                for student in Student.objects.filter(status='active'):
                    distinct_grades.add(get_student_display_grade(student))
                existing_fee_structures = set(FeeStructure.objects.values_list('grade', flat=True))
                missing_grades = distinct_grades - existing_fee_structures

                if missing_grades:
                    self.stdout.write(f"  Missing FeeStructure for {len(missing_grades)} grades.")
                    for grade in sorted(missing_grades):
                        if dry_run:
                            self.stdout.write(f"    Would create FeeStructure for grade: '{grade}' with fee 0.00")
                        else:
                            FeeStructure.objects.create(grade=grade, monthly_fee=Decimal('0.00'))
                            self.stdout.write(f"    Created FeeStructure for grade: '{grade}'")

                # 5. Set student.custom_fee from FeeStructure if not set
                fee_structs = {fs.grade: fs.monthly_fee for fs in FeeStructure.objects.all()}
                updated_custom_fee = 0
                for student in Student.objects.filter(status='active', custom_fee=0):
                    display_grade = get_student_display_grade(student)
                    if display_grade in fee_structs:
                        if dry_run:
                            self.stdout.write(f"  Would set custom_fee for {student.name} to {fee_structs[display_grade]}")
                        else:
                            student.custom_fee = fee_structs[display_grade]
                            student.save(update_fields=['custom_fee'])
                            updated_custom_fee += 1
                if updated_custom_fee:
                    self.stdout.write(f"  Updated custom_fee for {updated_custom_fee} students.")

                self.stdout.write(f"  Tenant {tenant.schema_name} processed.")

        self.stdout.write(self.style.SUCCESS("\nData repair completed."))
