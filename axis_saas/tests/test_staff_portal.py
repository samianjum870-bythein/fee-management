import base64
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase, TestCase
from django_tenants.utils import schema_context

from axis_saas.middleware.staff_tenant_middleware import StaffTenantMiddleware
from axis_saas.models import SchoolClient, Staff, StaffCredential, WebAuthnCredential


class StaffMiddlewarePasskeyAccessTests(SimpleTestCase):
    def test_missing_passkey_does_not_block_dashboard_access(self):
        factory = RequestFactory()
        request = factory.get('/portal/staff/dashboard/')
        request.path_info = '/portal/staff/dashboard/'
        request.session = MagicMock()
        request.session.get.side_effect = lambda key, default=None: {
            'staff_id': 7,
            'staff_schema_name': 'tenant_demo',
            'staff_session_token': 'abc123',
            'pending_staff_id': None,
            'pending_schema_name': None,
            'staff_pending_passkey': False,
        }.get(key, default)
        request.session.flush = MagicMock()

        dummy_tenant = MagicMock()
        dummy_tenant.schema_name = 'tenant_demo'

        dummy_staff = MagicMock()
        dummy_staff.pk = 7
        dummy_staff.status = 'active'

        dummy_credential = MagicMock()
        dummy_credential.has_passkey = False

        response = object()
        middleware = StaffTenantMiddleware(lambda req: response)

        with patch('axis_saas.middleware.staff_tenant_middleware.get_tenant_model') as mock_tenant_model, \
             patch('axis_saas.middleware.staff_tenant_middleware.connection.set_tenant') as mock_set_tenant, \
             patch('axis_saas.middleware.staff_tenant_middleware.Staff.objects.filter') as mock_staff_filter, \
             patch('axis_saas.middleware.staff_tenant_middleware.StaffCredential.objects.filter') as mock_credential_filter, \
             patch('django.core.cache.cache.get', return_value='abc123'):
            mock_tenant_model.objects.get.return_value = dummy_tenant
            mock_staff_filter.return_value.first.return_value = dummy_staff
            mock_credential_filter.return_value.first.return_value = dummy_credential

            result = middleware(request)

        self.assertIs(result, response)
        self.assertTrue(request.staff_passkey_required)
        mock_set_tenant.assert_called_once_with(dummy_tenant)


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

    def test_webauthn_registration_options_require_platform_authenticator(self):
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.create(
                first_name='Bilal',
                last_name='Ahmad',
                email='bilal@example.com',
                job_title='Accountant',
                department='support',
                phone='03009876543',
                role='class_teacher',
            )

        credential = StaffCredential.objects.get(staff_id=staff.id, schema_name=self.tenant.schema_name)
        session = self.client.session
        session['staff_id'] = staff.id
        session['staff_schema_name'] = self.tenant.schema_name
        session.save()

        response = self.client.post('/portal/staff/security/webauthn/register/options/', secure=True)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['authenticatorSelection']['authenticatorAttachment'], 'platform')
        self.assertEqual(data['authenticatorSelection']['userVerification'], 'required')
        self.assertIn('rpId', data)

    def test_webauthn_authentication_options_accept_username_for_passwordless_login(self):
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.create(
                first_name='Hina',
                last_name='Javed',
                email='hina@example.com',
                job_title='Science Teacher',
                department='teaching',
                phone='03005556677',
                role='teacher',
            )
            cred = StaffCredential.objects.get(staff_id=staff.id, schema_name=self.tenant.schema_name)
            cred.set_password('StrongPass@123')
            cred.save(update_fields=['password'])
            WebAuthnCredential.objects.create(
                staff_credential=cred,
                credential_id=base64.urlsafe_b64encode(b'passkey-credential').decode().rstrip('='),
                public_key='test-public-key',
                is_active=True,
            )

        response = self.client.post(
            '/portal/staff/security/webauthn/auth/options/',
            data={'username': cred.username},
            content_type='application/json',
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('challenge', payload)
        self.assertIn('allowCredentials', payload)

    def test_webauthn_authentication_options_uses_pending_identity_for_verify_flow(self):
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.create(
                first_name='Sara',
                last_name='Iqbal',
                email='sara@example.com',
                job_title='English Teacher',
                department='teaching',
                phone='03003334445',
                role='teacher',
            )
            cred = StaffCredential.objects.get(staff_id=staff.id, schema_name=self.tenant.schema_name)
            cred.set_password('StrongPass@123')
            cred.save(update_fields=['password'])
            WebAuthnCredential.objects.create(
                staff_credential=cred,
                credential_id=base64.urlsafe_b64encode(b'pending-passkey').decode().rstrip('='),
                public_key='pending-public-key',
                is_active=True,
            )

        session = self.client.session
        session['pending_staff_id'] = staff.id
        session['pending_schema_name'] = self.tenant.schema_name
        session['pending_username'] = cred.username
        session['staff_pending_passkey'] = True
        session.save()

        response = self.client.post(
            '/portal/staff/security/webauthn/auth/options/',
            data={},
            content_type='application/json',
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('challenge', payload)
        self.assertEqual(self.client.session['staff_webauthn_login_staff_id'], staff.id)
        self.assertEqual(self.client.session['staff_webauthn_login_schema_name'], self.tenant.schema_name)
