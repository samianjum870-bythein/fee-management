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

        if (not staff_id or not schema_name):
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

        if token_invalid:
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

        return self.get_response(request)
