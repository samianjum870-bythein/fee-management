import logging

from django.dispatch import receiver
from django_tenants.signals import post_schema_sync
from django_tenants.utils import schema_context
from django.contrib.auth import get_user_model
from axis_saas.models import SchoolClient
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)

@receiver(post_schema_sync)
def provision_secure_tenant_admin(sender, tenant, **kwargs):
    if tenant.schema_name == 'public':
        return

    User = get_user_model()
    
    u_name = tenant.admin_username
    u_pass = tenant.admin_password
    u_email = f"{u_name}@{tenant.schema_name}.com"
    
    if not u_name or not u_pass:
        return

    raw_pw = getattr(tenant, '_raw_password', None)
    if not raw_pw:
        logger.warning('Raw password not available for %s; cannot provision superuser.', tenant.schema_name)
        return

    with schema_context(tenant.schema_name):
        if not User.objects.filter(username=u_name).exists():
            User.objects.create_superuser(
                username=u_name,
                email=u_email,
                password=raw_pw
            )
            logger.info("Operational superuser '%s' provisioned into tenant schema '%s'.", u_name, tenant.schema_name)
@receiver(post_save, sender=SchoolClient)
def sync_tenant_admin_password(sender, instance, created, **kwargs):
    if instance.schema_name == 'public' or created:
        return
        
    u_name = instance.admin_username
    raw_pw = getattr(instance, '_raw_password', None)
    if u_name and raw_pw:
        with schema_context(instance.schema_name):
            User = get_user_model()
            user = User.objects.filter(username=u_name).first()
            if user:
                user.set_password(raw_pw)
                user.save()
                logger.info("Password synchronized for '%s' in schema '%s'.", u_name, instance.schema_name)
    elif u_name and not raw_pw:
        logger.warning('Raw password not available for %s; cannot sync password.', instance.schema_name)

# ========== CACHE INVALIDATION SIGNALS ==========
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Student, PaymentTransaction, FeeRecord
from .views.helpers import invalidate_tenant_cache
from django.db import connection

@receiver(post_save, sender=PaymentTransaction)
@receiver(post_delete, sender=PaymentTransaction)
def clear_cache_on_payment(sender, instance, **kwargs):
    schema_name = connection.schema_name
    if schema_name != 'public':
        invalidate_tenant_cache(schema_name, 'dashboard_stats')
        invalidate_tenant_cache(schema_name, 'defaulters_stats')

@receiver(post_save, sender=Student)
@receiver(post_delete, sender=Student)
def clear_cache_on_student(sender, instance, **kwargs):
    schema_name = connection.schema_name
    if schema_name != 'public':
        invalidate_tenant_cache(schema_name, 'dashboard_stats')
        invalidate_tenant_cache(schema_name, 'student_list_stats')

@receiver(post_save, sender=FeeRecord)
@receiver(post_delete, sender=FeeRecord)
def clear_cache_on_feerecord(sender, instance, **kwargs):
    schema_name = connection.schema_name
    if schema_name != 'public':
        invalidate_tenant_cache(schema_name, 'dashboard_stats')
        invalidate_tenant_cache(schema_name, 'defaulters_stats')
        invalidate_tenant_cache(schema_name, 'vouchers_stats')
