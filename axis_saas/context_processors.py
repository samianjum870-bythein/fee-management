from django_tenants.utils import get_public_schema_name
from django_tenants.utils import schema_context
from .models import SchoolClient, STAFF_PORTAL_FEATURE_CHOICES

class DummyTenant:
    """Fake tenant used when request.tenant is None (public schema)."""
    def __init__(self):
        self.schema_name = get_public_schema_name()
        self.name = 'AXIS Public Portal'


def tenant_processor(request):
    """Ensure request.tenant is never None for public and shared templates."""
    if not hasattr(request, 'tenant') or request.tenant is None:
        request.tenant = DummyTenant()
    return {'tenant': request.tenant}


def staff_portal_features(request):
    schema_name = request.session.get('staff_schema_name')
    enabled = set()
    if schema_name:
        with schema_context('public'):
            tenant = SchoolClient.objects.filter(schema_name=schema_name).first()
        if tenant and tenant.is_channel_enabled('staff_portal'):
            enabled = {
                key for key, _ in STAFF_PORTAL_FEATURE_CHOICES
                if tenant.is_feature_enabled(key, 'staff_portal')
                or (isinstance(tenant.enabled_features, list) and bool(tenant.enabled_features))
            }
    return {'staff_portal_features': enabled}