from django.db import connection

from django_tenants.utils import get_tenant_model, schema_context

from axis_saas.models import Staff, StaffCredential
from django.shortcuts import redirect
import logging
from django.conf import settings
logger = logging.getLogger(__name__)


class StaffTenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path_info.startswith('/portal/staff/'):
            return self.get_response(request)

        request.tenant = None
        request.staff = None

        if request.path_info in ['/portal/staff/login/', '/portal/staff/logout/']:
            connection.set_schema_to_public()
            return self.get_response(request)

        pending_staff_id = request.session.get('pending_staff_id')
        pending_schema_name = request.session.get('pending_schema_name')
        staff_id = request.session.get('staff_id') or pending_staff_id
        schema_name = request.session.get('staff_schema_name') or pending_schema_name

        is_webauthn_auth = request.path_info in [
            '/portal/staff/security/webauthn/auth/options/',
            '/portal/staff/security/webauthn/auth/verify/',
        ]
        is_webauthn_register = request.path_info in [
            '/portal/staff/security/webauthn/register/options/',
            '/portal/staff/security/webauthn/register/verify/',
        ]
        is_pending_passkey = request.session.get('staff_pending_passkey') is True
        is_verify_passkey_path = request.path_info == '/portal/staff/verify-passkey/'
        allow_pending_verify = is_verify_passkey_path and is_pending_passkey and bool(staff_id and schema_name)

        # Passwordless authentication starts without a tenant identity. Its
        # endpoints query public-schema credentials and establish the identity.
        if is_webauthn_auth and not (staff_id and schema_name):
            connection.set_schema_to_public()
            return self.get_response(request)

        if (not staff_id or not schema_name) and not (is_webauthn_auth or is_webauthn_register or allow_pending_verify):
            request.session.flush()
            return redirect('staff_login')

        cached_token = None
        try:
            from django.core.cache import cache
            cached_token = cache.get(f'staff_session_token:{schema_name}:{staff_id}')
        except Exception:
            cached_token = None

        session_token = request.session.get('staff_session_token')
        if not settings.DEBUG:
            token_invalid = not session_token or cached_token in ['logged_out'] or cached_token != session_token
        else:
            token_invalid = False

        if token_invalid and not (is_webauthn_auth or is_webauthn_register or allow_pending_verify):
            request.session.flush()
            return redirect('staff_login')

        TenantModel = get_tenant_model()
        try:
            tenant = TenantModel.objects.get(schema_name=schema_name)
        except TenantModel.DoesNotExist:
            request.session.flush()
            return redirect('staff_login')

        request.tenant = tenant
        connection.set_tenant(tenant)

        if not settings.DEBUG:
            try:
                with schema_context(schema_name):
                    request.staff = Staff.objects.filter(pk=staff_id, status='active').first()
            except Exception:
                request.staff = None

            if request.staff is None:
                request.session.flush()
                return redirect('staff_login')
        else:
            try:
                request.staff = Staff.objects.get(pk=staff_id)
            except Staff.DoesNotExist:
                request.staff = Staff(pk=staff_id, full_name='Developer', status='active')

        # Passkeys are required for staff portal access. If a user has no active
        # passkey, they are forced back to the profile page until they register one.
        request.staff_passkey_required = False
        try:
            effective_staff_id = request.session.get('staff_id') or pending_staff_id
            effective_schema_name = request.session.get('staff_schema_name') or pending_schema_name
            logger.info("Middleware: effective_staff_id=%s effective_schema=%s pending_passkey=%s", effective_staff_id, effective_schema_name, is_pending_passkey)
            if effective_staff_id and effective_schema_name:
                with schema_context('public'):
                    credential = StaffCredential.objects.filter(
                        staff_id=effective_staff_id,
                        schema_name=effective_schema_name,
                    ).first()
                    has_passkey = bool(credential and credential.webauthn_credentials.filter(is_active=True).exists())
                logger.info("Middleware: credential exists=%s has_passkey=%s", credential is not None, has_passkey)
                request.staff_passkey_required = not has_passkey
            else:
                request.staff_passkey_required = False

            allowed_paths = [
                '/portal/staff/profile/',
                '/portal/staff/verify-passkey/',
                '/portal/staff/security/webauthn/register/options/',
                '/portal/staff/security/webauthn/register/verify/',
                '/portal/staff/security/webauthn/auth/options/',
                '/portal/staff/security/webauthn/auth/verify/',
                '/portal/staff/logout/',
                '/portal/staff/login/',
            ]
            logger.info("Middleware: staff_passkey_required=%s, path=%s", request.staff_passkey_required, request.path_info)
            if request.staff_passkey_required and request.path_info not in allowed_paths:
                logger.info("Middleware: redirecting to profile because passkey required")
                return redirect('staff_profile_page')
        except Exception as exc:
            logger.exception('Middleware error: %s', exc)
            request.staff_passkey_required = False
        return self.get_response(request)
