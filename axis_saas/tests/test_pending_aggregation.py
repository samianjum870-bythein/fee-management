from decimal import Decimal

from django.test import TestCase
from django_tenants.utils import schema_context

from axis_saas.models import FeeRecord, PaymentTransaction, SchoolClient, Student
from axis_saas.views.helpers import aggregate_pending_totals, get_student_pending_queryset


class PendingAggregationTests(TestCase):
    def setUp(self):
        self.tenant = SchoolClient.objects.create(
            schema_name='pending-agg-test',
            name='Pending Aggregation School',
            admin_username='admin',
            admin_password='admin123',
        )

    def test_annotated_student_pending_and_totals_are_aggregated_in_sql(self):
        with schema_context(self.tenant.schema_name):
            student = Student.objects.create(
                name='Ali Khan',
                father_name='Khan',
                father_cnic='35202-1234567-8',
                parent_mobile='03001234567',
                grade='Grade 1',
                section='A',
                custom_fee=Decimal('2000'),
            )
            FeeRecord.objects.create(
                student=student,
                month=9,
                year=2026,
                amount=Decimal('2000.00'),
                paid_amount=Decimal('500.00'),
                due_date='2026-09-15',
                status='partial',
            )
            PaymentTransaction.objects.create(
                student=student,
                amount=Decimal('200.00'),
                payment_mode='cash',
                payment_type='full',
                remarks='Fee payment',
                created_by='admin',
            )

            qs = get_student_pending_queryset(Student.objects.filter(id=student.id))
            pending = qs.get(id=student.id).pending_amount
            totals = aggregate_pending_totals()

            self.assertEqual(pending, Decimal('1300.00'))
            self.assertEqual(totals['total_pending'], Decimal('1300.00'))
