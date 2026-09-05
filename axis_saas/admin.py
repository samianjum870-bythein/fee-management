import os

from django.conf import settings
from django.contrib import admin
from django_tenants.admin import TenantAdminMixin
from django import forms
from django.utils.safestring import mark_safe
from django.core.exceptions import ValidationError
from .models import (
    SchoolClient,
    SCHOOL_FEATURE_CHOICES,
    STAFF_PORTAL_FEATURE_CHOICES,
    FEATURE_CATEGORY_CHOICES,
    TENANT_TYPE_CHOICES,
)


def get_public_base_url():
    base_url = (
        os.environ.get('PUBLIC_URL')
        or os.environ.get('APP_URL')
        or os.environ.get('SITE_URL')
        or getattr(settings, 'PUBLIC_URL', '')
        or 'http://localhost:8000'
    ).rstrip('/')
    return base_url


class TenantOnlyAdminMixin:
    def has_module_permission(self, request):
        if request.tenant is None:
            return False
        return request.tenant.schema_name != 'public'

    def has_view_permission(self, request, obj=None):
        if request.tenant is None:
            return False
        return request.tenant.schema_name != 'public'

    def has_add_permission(self, request):
        if request.tenant is None:
            return False
        return request.tenant.schema_name != 'public'

    def has_change_permission(self, request, obj=None):
        if request.tenant is None:
            return False
        return request.tenant.schema_name != 'public'

    def has_delete_permission(self, request, obj=None):
        if request.tenant is None:
            return False
        return request.tenant.schema_name != 'public'


class PublicOnlyAdminMixin:
    def has_module_permission(self, request):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_view_permission(self, request, obj=None):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Exclude the 'public' tenant from public list (it's a special internal schema)
        return qs.exclude(schema_name='public')

    def has_add_permission(self, request):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_change_permission(self, request, obj=None):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_delete_permission(self, request, obj=None):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'


class SchoolClientForm(forms.ModelForm):
    feature_categories = forms.MultipleChoiceField(
        choices=FEATURE_CATEGORY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Desktop is always enabled. Select Mobile or Staff Portal to enable those areas.",
    )
    desktop_features = forms.MultipleChoiceField(
        choices=SCHOOL_FEATURE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Desktop modules enabled for this tenant."
    )
    mobile_features = forms.MultipleChoiceField(
        choices=SCHOOL_FEATURE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Mobile modules enabled for this tenant."
    )
    staff_portal_features = forms.MultipleChoiceField(
        choices=STAFF_PORTAL_FEATURE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Staff portal sections enabled for this tenant."
    )

    class Meta:
        model = SchoolClient
        fields = [
            'name', 'schema_name', 'admin_username', 'admin_password', 'is_active',
            'tenant_type', 'feature_categories', 'desktop_features', 'mobile_features', 'staff_portal_features',
        ]
        widgets = {
            'admin_password': forms.PasswordInput(render_value=True),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tenant_type'].choices = TENANT_TYPE_CHOICES
        self._set_feature_initials()

    class Media:
        js = ('js/school_client_features.js',)

    def _set_feature_initials(self):
        features = self.instance.enabled_features if self.instance and self.instance.pk else {}
        if isinstance(features, list):
            features = {'desktop': features, 'mobile': features, 'staff_portal': []}
        self.fields['feature_categories'].initial = [
            'desktop',
            *[category for category in ('mobile', 'staff_portal') if features.get(category)],
        ]
        self.fields['desktop_features'].initial = features.get('desktop', [choice[0] for choice in SCHOOL_FEATURE_CHOICES])
        self.fields['mobile_features'].initial = features.get('mobile', [])
        self.fields['staff_portal_features'].initial = features.get('staff_portal', [])

    def clean(self):
        cleaned_data = super().clean()
        tenant_type = cleaned_data.get('tenant_type') or getattr(self.instance, 'tenant_type', None)
        categories = set(cleaned_data.get('feature_categories') or [])
        categories.add('desktop')
        cleaned_data['feature_categories'] = list(categories)
        if tenant_type not in dict(TENANT_TYPE_CHOICES):
            cleaned_data['feature_categories'] = []
            cleaned_data['desktop_features'] = []
            cleaned_data['mobile_features'] = []
            cleaned_data['staff_portal_features'] = []
        else:
            if not cleaned_data.get('desktop_features'):
                cleaned_data['desktop_features'] = [choice[0] for choice in SCHOOL_FEATURE_CHOICES]
            if 'mobile' in categories and not cleaned_data.get('mobile_features'):
                cleaned_data['mobile_features'] = [choice[0] for choice in SCHOOL_FEATURE_CHOICES]
            if 'staff_portal' in categories and not cleaned_data.get('staff_portal_features'):
                cleaned_data['staff_portal_features'] = [choice[0] for choice in STAFF_PORTAL_FEATURE_CHOICES]
            if 'mobile' not in categories:
                cleaned_data['mobile_features'] = []
            if 'staff_portal' not in categories:
                cleaned_data['staff_portal_features'] = []
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.enabled_features = {
            'desktop': self.cleaned_data.get('desktop_features', []),
            'mobile': self.cleaned_data.get('mobile_features', []),
            'staff_portal': self.cleaned_data.get('staff_portal_features', []),
        }
        raw_password = self.cleaned_data.get("admin_password")
        if raw_password:
            instance._raw_password = raw_password
            instance.set_password(raw_password)
        if commit:
            instance.save()
        return instance

    def clean_schema_name(self):
        schema = self.cleaned_data.get('schema_name').lower().strip()
        if self.instance.pk and self.instance.schema_name == 'public' and schema != 'public':
            raise ValidationError("CRITICAL ERROR: The core public operational schema token cannot be renamed.")
        
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name = %s", [schema])
            exists = cursor.fetchone()
        
        if not self.instance.pk and exists:
            raise ValidationError(f"⚠️ SECURITY BREACH BLOCK: The schema name '{schema}' physically exists in PostgreSQL as an active partition! Choose a unique routing path.")
        return schema


@admin.register(SchoolClient)
class SchoolClientAdmin(TenantAdminMixin, admin.ModelAdmin):
    form = SchoolClientForm
    list_display = ('name', 'schema_name', 'tenant_type', 'admin_username', 'is_active', 'created_on', 'get_admin_url_link')
    readonly_fields = ('school_admin_portal_url',)
    
    fieldsets = (
        ('Master Identity Matrix', {
            'fields': ('name', 'schema_name', 'is_active')
        }),
        ('Dynamic Sub-Tenant Authority Provisioning', {
            'fields': ('admin_username', 'admin_password'),
        }),
        ('Tenant Type', {
            'fields': ('tenant_type',),
        }),
        ('Feature Categories', {
            'fields': ('feature_categories',),
            'description': 'Desktop is always enabled. Select Mobile or Staff Portal to reveal and enable their modules.',
        }),
        ('Desktop Features', {
            'fields': ('desktop_features',),
            'description': 'Select the modules available in the desktop portal.',
        }),
        ('Mobile Features', {
            'fields': ('mobile_features',),
            'description': 'Select the modules available in the mobile portal.',
        }),
        ('Staff Portal Features', {
            'fields': ('staff_portal_features',),
            'description': 'Select the sections available in the staff portal.',
        }),
        ('Generated Access Routes', {
            'fields': ('school_admin_portal_url',),
            'description': 'Once saved, the system automatically builds the exact landing gate link for this school node below.'
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.schema_name == 'public':
            return self.readonly_fields + ('schema_name', 'admin_username', 'is_active')
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        if obj and obj.schema_name == 'public':
            return False
        if request.tenant is None:
            return True   # public schema user can delete tenants
        return request.tenant.schema_name == 'public'

    def school_admin_portal_url(self, obj):
        if obj.pk and obj.schema_name != 'public':
            target_url = f"{get_public_base_url()}/portal/{obj.schema_name}/"
            return mark_safe(f'<a href="{target_url}" target="_blank" style="background: #10b981; color: white; padding: 8px 16px; border-radius: 6px; text-decoration: none; font-weight: bold; display: inline-block;">🚀 Open {obj.name} School Portal</a>')
        return "Link will be generated automatically after you click Save below."
    
    school_admin_portal_url.short_description = "Direct School Portal Gate"
    
    def get_admin_url_link(self, obj):
        if obj.schema_name != 'public':
            target_url = f"{get_public_base_url()}/portal/{obj.schema_name}/"
            return mark_safe(f'<a href="{target_url}" target="_blank" style="color: #38bdf8; font-weight: bold;">Open School Portal</a>')
        return "MASTER NODE"
    get_admin_url_link.short_description = "Quick Portal Link"

    def has_module_permission(self, request):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_view_permission(self, request, obj=None):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.exclude(schema_name='public')


# --- AXIS Student Registry Injection ---
from .models import Student

@admin.register(Student)
class StudentAdmin(TenantOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('roll_number', 'name', 'grade', 'section', 'status', 'enrolled_on')
    list_filter = ('grade', 'section', 'status', 'gender')
    search_fields = ('name', 'roll_number', 'father_name', 'father_cnic')
    ordering = ('-enrolled_on',)
    
    readonly_fields = ('display_student_fee',)

    fieldsets = (
        ('Core Enrollment Records', {
            'fields': ('name', 'roll_number', 'status')
        }),
        ('Academic & Class Placement', {
            'fields': ('grade', 'section', 'admission_date')
        }),
        ('Parental & Verification Matrix', {
            'fields': ('father_name', 'father_cnic', 'parent_mobile')
        }),
        ('Financial Status Matrix', {
            'fields': ('display_student_fee', 'custom_fee'),
            'description': 'Current fee parameters loaded dynamically via matching class standard configurations.',
        }),
    )

    def display_student_fee(self, obj):
        if obj.pk:
            return f"RS {obj.custom_fee}"
        return "Will be computed based on selected class standard fee roster."
    display_student_fee.short_description = "Active Monthly Fee Structure"
    
    def get_readonly_fields(self, request, obj=None):
        base_fields = list(self.readonly_fields)
        if obj:
            base_fields.append('roll_number')
        return tuple(base_fields)


# --- AXIS Fee Structure Registry Injection ---
from .models import FeeStructure


# Staff admin registration
from .models import Staff

@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ('staff_id', 'full_name', 'job_title', 'department', 'status', 'email')
    list_filter = ('department', 'status')
    search_fields = ('staff_id', 'full_name', 'email', 'phone')
    readonly_fields = ('staff_id',)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        generated = getattr(obj, '_generated_password', None)
        username = getattr(obj, '_generated_username', None)
        if generated or username:
            self.message_user(
                request,
                f"Staff portal credentials generated: username={username or obj.email or obj.full_name.lower().replace(' ', '.')} and password={generated or 'stored securely'}",
                level=20,
            )

@admin.register(FeeStructure)
class FeeStructureAdmin(TenantOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('grade', 'monthly_fee', 'updated_at')
    search_fields = ('grade',)


# --- AXIS SECURITY HARDENING: MULTI-TENANT ISOLATION OVERRIDE ---
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin

try:
    admin.site.unregister(User)
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass

@admin.register(User)
class TenantSecuredUserAdmin(BaseUserAdmin):
    def has_module_permission(self, request):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_view_permission(self, request, obj=None):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_add_permission(self, request):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_change_permission(self, request, obj=None):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_delete_permission(self, request, obj=None):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def save_model(self, request, obj, form, change):
        if request.tenant is not None and request.tenant.schema_name != 'public':
            obj.is_superuser = False
        super().save_model(request, obj, form, change)


@admin.register(Group)
class TenantSecuredGroupAdmin(BaseGroupAdmin):
    def has_module_permission(self, request):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_view_permission(self, request, obj=None):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_add_permission(self, request):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_change_permission(self, request, obj=None):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'

    def has_delete_permission(self, request, obj=None):
        if request.tenant is None:
            return True
        return request.tenant.schema_name == 'public'


# Register Fee models
from .models import FeeRecord, PaymentTransaction, SchoolFeeSettings

@admin.register(FeeRecord)
class FeeRecordAdmin(admin.ModelAdmin):
    list_display = ('student', 'month', 'year', 'amount', 'paid_amount', 'status', 'due_date')
    list_filter = ('status', 'month', 'year')
    search_fields = ('student__name', 'student__roll_number')

@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ('receipt_number', 'student', 'amount', 'payment_date', 'payment_type')
    list_filter = ('payment_type', 'payment_mode', 'payment_date')
    search_fields = ('receipt_number', 'student__name', 'student__father_cnic')

@admin.register(SchoolFeeSettings)
class SchoolFeeSettingsAdmin(admin.ModelAdmin):
    list_display = ('fee_generation_day', 'due_date_offset', 'late_fee_penalty', 'updated_at')
# Stock Management admin
from .models import ProductCategory, Product
@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('sku', 'name', 'category', 'selling_price', 'quantity')
    list_filter = ('category',)
    search_fields = ('name', 'sku')