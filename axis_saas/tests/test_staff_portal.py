from django.test import TestCase
from django_tenants.utils import schema_context

from axis_saas.models import SchoolClient, Staff, StaffBiometricCredential, StaffCredential


class StaffPortalTests(TestCase):
    def setUp(self):
        self.tenant = SchoolClient.objects.create(
            schema_name='staffportal-test',
            name='Staff Portal Test School',
            admin_username='admin',
            admin_password='Admin@123',
        )

    def test_staff_credentials_are_generated_and_login_works(self):
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.create(
                first_name='Ayesha',
                last_name='Khan',
                email='ayesha@example.com',
                job_title='Mathematics Teacher',
                department='teaching',
                phone='03001234567',
                role='teacher',
            )

        credential = StaffCredential.objects.get(staff_id=staff.id, schema_name=self.tenant.schema_name)
        self.assertTrue(credential.username.startswith('ayesha.khan'))
        self.assertTrue(credential.is_active)
        self.assertTrue(credential.check_password(credential.raw_password))

        response = self.client.post(
            '/portal/staff/login/',
            {'username': credential.username, 'password': credential.raw_password},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session.get('staff_id'), staff.id)
        self.assertEqual(self.client.session.get('staff_schema_name'), self.tenant.schema_name)

    def test_password_login_does_not_require_passkey(self):
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.create(
                first_name='Sana',
                last_name='Ali',
                email='sana@example.com',
                job_title='Math Teacher',
                department='teaching',
                phone='03001112233',
                role='teacher',
            )

        credential = StaffCredential.objects.get(staff_id=staff.id, schema_name=self.tenant.schema_name)
        response = self.client.post(
            '/portal/staff/login/',
            {'username': credential.username, 'password': credential.raw_password},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session.get('staff_id'), staff.id)
        self.assertEqual(self.client.session.get('staff_schema_name'), self.tenant.schema_name)
        self.assertNotIn('staff_pending_passkey', self.client.session)

    def test_password_login_is_blocked_when_biometric_is_enabled(self):
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.create(
                first_name='Omar',
                last_name='Rashid',
                email='omar@example.com',
                job_title='English Teacher',
                department='teaching',
                phone='03005556666',
                role='teacher',
            )

        credential = StaffCredential.objects.get(staff_id=staff.id, schema_name=self.tenant.schema_name)
        StaffBiometricCredential.objects.create(
            staff_id=staff.id,
            schema_name=self.tenant.schema_name,
            credential_id='registered-device-credential',
            public_key='registered-device-key',
        )

        response = self.client.post(
            '/portal/staff/login/',
            {'username': credential.username, 'password': credential.raw_password},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Biometric verification is required')
        self.assertNotEqual(self.client.session.get('staff_id'), staff.id)

    def test_staff_profile_exposes_biometric_status(self):
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.create(
                first_name='Bilal',
                last_name='Hassan',
                email='bilal@example.com',
                job_title='Computer Teacher',
                department='teaching',
                phone='03009999888',
                role='teacher',
            )

        credential = StaffCredential.objects.get(staff_id=staff.id, schema_name=self.tenant.schema_name)
        self.client.post(
            '/portal/staff/login/',
            {'username': credential.username, 'password': credential.raw_password},
            follow=True,
        )

        response = self.client.get('/portal/staff/profile/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Biometric')

    def test_staff_biometric_status_endpoint_is_available(self):
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.create(
                first_name='Nimra',
                last_name='Awan',
                email='nimra@example.com',
                job_title='Biology Teacher',
                department='teaching',
                phone='03001113333',
                role='teacher',
            )

        self.client.post(
            '/portal/staff/login/',
            {'username': StaffCredential.objects.get(staff_id=staff.id, schema_name=self.tenant.schema_name).username,
             'password': StaffCredential.objects.get(staff_id=staff.id, schema_name=self.tenant.schema_name).raw_password},
            follow=True,
        )
        response = self.client.get('/portal/staff/biometric/status/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['enabled'])

    def test_staff_biometric_registration_options_are_serialized(self):
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.create(
                first_name='Hina',
                last_name='Raza',
                email='hina@example.com',
                job_title='Science Teacher',
                department='teaching',
                phone='03002224444',
                role='teacher',
            )

        credential = StaffCredential.objects.get(staff_id=staff.id, schema_name=self.tenant.schema_name)
        self.client.post(
            '/portal/staff/login/',
            {'username': credential.username, 'password': credential.raw_password},
            follow=True,
        )

        response = self.client.post('/portal/staff/biometric/registration-options/', '{}', content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(response.json()['options']['attestation'], 'none')

    def test_staff_credential_does_not_store_visible_password(self):
        self.assertFalse(StaffCredential._meta.has_field('visible_password'))
