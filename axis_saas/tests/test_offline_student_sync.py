from django.test import TestCase
from django_tenants.utils import schema_context

from axis_saas.models import SchoolClient, Student
from axis_saas.views.helpers import create_student_from_payload


class OfflineStudentSyncTests(TestCase):
    def setUp(self):
        self.tenant = SchoolClient.objects.create(
            schema_name='offline-test',
            name='Offline Test School',
            admin_username='admin',
            admin_password='admin123',
        )

    def test_create_student_from_payload(self):
        payload = {
            'name': 'Ayesha Khan',
            'father_name': 'Naseer Khan',
            'father_cnic': '35202-1234567-8',
            'parent_mobile': '03001234567',
            'grade': 'Grade 1',
            'section': 'A',
            'admission_date': '2024-01-01',
            'status': 'active',
            'gender': 'female',
            'date_of_birth': '2015-01-01',
            'address': 'Test address',
            'notes': 'Created offline',
            'custom_fee': '0',
        }

        with schema_context(self.tenant.schema_name):
            student, errors = create_student_from_payload(self.tenant.schema_name, payload)

        self.assertIsNone(errors)
        self.assertIsNotNone(student)
        self.assertEqual(student.name, 'Ayesha Khan')
        with schema_context(self.tenant.schema_name):
            self.assertEqual(Student.objects.count(), 1)
