from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings as django_settings
from django.conf.urls.static import static
from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404
from django_tenants.utils import schema_context

from .models import SchoolClient

from .views import mobile_fee_structure, add_student, add_student_mobile, dashboard, debug_payments_api, defaulters, edit_student, family_payment, fee_collection, mobile_fee_collection, fee_receipt, mobile_fee_receipt, fee_settings, fee_status_api, fee_structure, manual_generate_api, manual_generate_single_api, reports, settings, student_fee_records_api, student_list, student_payments_api, student_current_fee_status_api, student_profile, student_search_api, stock_management, product_detail, mobile_stock_management, mobile_product_detail, add_category, delete_category, add_product, delete_product, sell_separately, mobile_sell_separately, mobile_dashboard, mobile_more, mobile_student_list, mobile_student_profile, mobile_defaulters, mobile_reports, mobile_fee_settings, mobile_settings, vouchers_list, mobile_vouchers_list, dismiss_notification, notifications_list_api, mark_notification_read_api, mark_all_notifications_read_api, global_search_api, product_list_api, student_list_api, receipt_list_api, fee_collection_list_api, sync_offline_student_api, class_management, mobile_class_management, add_class, edit_class, delete_class, add_subject, edit_subject, delete_subject, assign_subject, edit_assignment, delete_assignment
from .views.staff import staff_list, mobile_staff_list, staff_profile, mobile_staff_profile, staff_add, staff_add_mobile, staff_edit, staff_search_api, staff_toggle_status, staff_force_logout, staff_reset_password
from .views.staff_portal import staff_login, staff_logout
from .views.classes import class_strength_api
from .views.students import students_by_teacher
from .pwa_views import manifest, service_worker
from .views import voucher_status_api, generate_voucher_api, voucher_html_api, global_search_api
from .views import fee_logs, mobile_fee_logs, global_search_api



def saas_homepage(request):
    return HttpResponse('''
    <h1>AXIS School Management System</h1>
    <p>Welcome to Multi-Tenant Platform</p>
    <p>Go to <a href="/admin/">Admin Panel</a> to manage schools</p>
    ''')

def ensure_schoolclient(schema_name):
    """Fetch tenant from public schema; raise 404 if not found."""
    with schema_context('public'):
        try:
            return SchoolClient.objects.get(schema_name=schema_name)
        except SchoolClient.DoesNotExist:
            raise Http404(f"Tenant '{schema_name}' does not exist.")

        if schema_exists:
            # Create the missing SchoolClient row
            tenant, created = SchoolClient.objects.get_or_create(
                schema_name=schema_name,
                defaults={
                    'name': f"{schema_name.title()} School",
                    'admin_username': 's',
                    'admin_password': 'admin123',
                    'is_active': True
                }
            )
            if created:
                print(f"✅ Auto-created SchoolClient for '{schema_name}'")
            return tenant
        else:
            return None

def portal_wrapper(view_func):
    """Wrapper that ensures SchoolClient exists before calling the view."""
    def wrapper(request, schema_name, *args, **kwargs):
        tenant = ensure_schoolclient(schema_name)
        if tenant is None:
            raise Http404(f"Tenant schema '{schema_name}' does not exist.")
        # Store tenant in request for convenience
        request.tenant = tenant
        return view_func(request, schema_name, *args, **kwargs)
    return wrapper

def get_school_default_route(tenant):
    mapping = [
        ('dashboard', 'dashboard'),
        ('students', 'student_list'),
        ('fee_collection', 'fee_collection'),
        ('defaulters', 'defaulters'),
        ('reports', 'reports'),
        ('stock_management', 'stock_management'),
        ('fee_structure', 'fee_structure'),
        ('fee_settings', 'fee_settings'),
        ('family_payment', 'family_payment'),
    ]
    if tenant.tenant_type != 'school':
        return 'dashboard'
    for feature, route_name in mapping:
        if tenant.is_feature_enabled(feature):
            return route_name
    return 'settings'


def school_login(request, schema_name):
    # Ensure tenant exists
    tenant = ensure_schoolclient(schema_name)
    if tenant is None:
        raise Http404(f"Tenant schema '{schema_name}' does not exist.")

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        if username == tenant.admin_username and tenant.check_password(password):
            request.session.flush()
            request.session['school_admin_authenticated'] = True
            request.session['school_admin_schema'] = tenant.schema_name
            request.session['school_admin_username'] = username
            request.session.pop('staff_id', None)
            request.session.pop('staff_schema_name', None)
            request.session.pop('staff_username', None)
            request.session.pop('staff_role', None)
            request.session.pop('staff_name', None)
            request.session.save()
            return redirect(get_school_default_route(tenant), schema_name=tenant.schema_name)
        return render(request, 'tenant/login.html', {'tenant': tenant, 'error': 'Invalid credentials'})
    return render(request, 'tenant/login.html', {'tenant': tenant})

def school_logout(request, schema_name):
    request.session.flush()
    request.session.pop('staff_id', None)
    request.session.pop('staff_schema_name', None)
    request.session.pop('staff_username', None)
    request.session.pop('staff_role', None)
    request.session.pop('staff_name', None)
    return redirect('school_login', schema_name=schema_name)

def login_required_for_schema(view_func):
    def wrapper(request, schema_name, *args, **kwargs):
        if not request.session.get('school_admin_authenticated') or request.session.get('school_admin_schema') != schema_name:
            return redirect('school_login', schema_name=schema_name)
        return view_func(request, schema_name, *args, **kwargs)
    return wrapper

# Wrap all portal views with portal_wrapper to ensure SchoolClient exists
dashboard_view = portal_wrapper(login_required_for_schema(dashboard))
mobile_dashboard_view = portal_wrapper(login_required_for_schema(mobile_dashboard))
mobile_more_view = portal_wrapper(login_required_for_schema(mobile_more))
mobile_student_list_view = portal_wrapper(login_required_for_schema(mobile_student_list))
mobile_student_profile_view = portal_wrapper(login_required_for_schema(mobile_student_profile))
student_list_view = portal_wrapper(login_required_for_schema(student_list))
student_profile_view = portal_wrapper(login_required_for_schema(student_profile))
fee_collection_view = portal_wrapper(login_required_for_schema(fee_collection))
mobile_fee_collection_view = portal_wrapper(login_required_for_schema(mobile_fee_collection))
fee_receipt_view = portal_wrapper(login_required_for_schema(fee_receipt))
mobile_fee_receipt_view = portal_wrapper(login_required_for_schema(mobile_fee_receipt))
defaulters_view = portal_wrapper(login_required_for_schema(defaulters))
reports_view = portal_wrapper(login_required_for_schema(reports))
settings_view = portal_wrapper(login_required_for_schema(settings))
fee_structure_view = portal_wrapper(login_required_for_schema(fee_structure))
mobile_fee_structure_view = portal_wrapper(login_required_for_schema(mobile_fee_structure))
fee_settings_view = portal_wrapper(login_required_for_schema(fee_settings))
family_payment_view = portal_wrapper(login_required_for_schema(family_payment))
student_search_api_view = portal_wrapper(login_required_for_schema(student_search_api))
add_student_view = portal_wrapper(login_required_for_schema(add_student))
add_student_mobile_view = portal_wrapper(login_required_for_schema(add_student_mobile))
edit_student_view = portal_wrapper(login_required_for_schema(edit_student))
student_fee_records_api_view = portal_wrapper(login_required_for_schema(student_fee_records_api))
student_payments_api_view = portal_wrapper(login_required_for_schema(student_payments_api))
student_current_fee_status_api_view = portal_wrapper(login_required_for_schema(student_current_fee_status_api))
mobile_defaulters_view = portal_wrapper(login_required_for_schema(mobile_defaulters))
mobile_reports_view = portal_wrapper(login_required_for_schema(mobile_reports))
mobile_fee_settings_view = portal_wrapper(login_required_for_schema(mobile_fee_settings))
mobile_settings_view = portal_wrapper(login_required_for_schema(mobile_settings))


def tenant_root_redirect(request, schema_name):
    """Redirect to appropriate dashboard based on tenant_type."""
    tenant = ensure_schoolclient(schema_name)
    if tenant is None:
        raise Http404("Tenant not found")
    return redirect(get_school_default_route(tenant), schema_name=schema_name)


urlpatterns = [
    path('portal/staff/', include('axis_saas.staff_urls')),
    path('portal/<slug:schema_name>/students/teacher/<int:teacher_id>/', portal_wrapper(login_required_for_schema(students_by_teacher)), name='students_by_teacher'),
    path('portal/<slug:schema_name>/api/fee-collection/', portal_wrapper(login_required_for_schema(fee_collection_list_api)), name='fee_collection_list_api'),
    path('portal/<slug:schema_name>/api/receipts/', portal_wrapper(login_required_for_schema(receipt_list_api)), name='receipt_list_api'),
    path('portal/<slug:schema_name>/api/students/', portal_wrapper(login_required_for_schema(student_list_api)), name='student_list_api'),
    path('portal/<slug:schema_name>/api/products/', portal_wrapper(login_required_for_schema(product_list_api)), name='product_list_api'),
    path('portal/<slug:schema_name>/api/student/<int:student_id>/current-fee-status/', student_current_fee_status_api_view, name='student_current_fee_status'),
    path('api/debug-payments/', debug_payments_api, name='debug_payments_api'),
    path('', saas_homepage),
    path('admin/', admin.site.urls),
    path('api/fee-status/', fee_status_api, name='fee_status_api'),
    path('api/manual-generate/', manual_generate_api, name='manual_generate_api'),
    path('api/manual-generate-single/', manual_generate_single_api, name='manual_generate_single_api'),
    
    # Auth
    path('portal/<slug:schema_name>/login/', school_login, name='school_login'),
    path('portal/<slug:schema_name>/login/', school_login, name='tenant_login'),
    path('portal/<slug:schema_name>/logout/', school_logout, name='school_logout'),
    path('portal/<slug:schema_name>/logout/', school_logout, name='tenant_logout'),
    
    # Core - using the wrapped views
    
    path('portal/<slug:schema_name>/dashboard/mobile/more/', mobile_more_view, name='mobile_more'),
    path('portal/<slug:schema_name>/dashboard/mobile/', mobile_dashboard_view, name='mobile_dashboard'),
    path('portal/<slug:schema_name>/students/mobile/', mobile_student_list_view, name='mobile_student_list'),
# Staff Management
    path('portal/<slug:schema_name>/staff/', portal_wrapper(login_required_for_schema(staff_list)), name='staff_list'),
    path('portal/<slug:schema_name>/staff/mobile/', portal_wrapper(login_required_for_schema(mobile_staff_list)), name='mobile_staff_list'),
    path('portal/<slug:schema_name>/staff/add/', portal_wrapper(login_required_for_schema(staff_add)), name='staff_add'),
    path('portal/<slug:schema_name>/staff/add/mobile/', portal_wrapper(login_required_for_schema(staff_add_mobile)), name='staff_add_mobile'),
    path('portal/<slug:schema_name>/staff/edit/<int:staff_id>/', portal_wrapper(login_required_for_schema(staff_edit)), name='staff_edit'),
    path('portal/<slug:schema_name>/staff/<int:staff_id>/toggle-status/', portal_wrapper(login_required_for_schema(staff_toggle_status)), name='staff_toggle_status'),
    path('portal/<slug:schema_name>/staff/<int:staff_id>/force-logout/', portal_wrapper(login_required_for_schema(staff_force_logout)), name='staff_force_logout'),
    path('portal/<slug:schema_name>/staff/<int:staff_id>/reset-password/', portal_wrapper(login_required_for_schema(staff_reset_password)), name='staff_reset_password'),
    path('portal/<slug:schema_name>/staff/<int:staff_id>/', portal_wrapper(login_required_for_schema(staff_profile)), name='staff_profile'),
    path('portal/<slug:schema_name>/staff/<int:staff_id>/mobile/', portal_wrapper(login_required_for_schema(mobile_staff_profile)), name='mobile_staff_profile'),
    path('portal/<slug:schema_name>/api/staff-search/', portal_wrapper(login_required_for_schema(staff_search_api)), name='staff_search_api'),
    path('portal/<slug:schema_name>/students/mobile/<int:student_id>/', mobile_student_profile_view, name='mobile_student_profile'),
    path('portal/<slug:schema_name>/fee/collection/mobile/', mobile_fee_collection_view, name='mobile_fee_collection'),
    path('portal/<slug:schema_name>/fee/collection/mobile/<int:student_id>/', mobile_fee_collection_view, name='mobile_fee_collection'),
    path('portal/<slug:schema_name>/fee/receipt/mobile/<int:receipt_id>/', mobile_fee_receipt_view, name='mobile_fee_receipt'),
    path('portal/<slug:schema_name>/dashboard/', dashboard_view, name='dashboard'),
    path('portal/<slug:schema_name>/students/', student_list_view, name='student_list'),
    path('portal/<slug:schema_name>/students/add/', add_student_view, name='add_student'),
    path('portal/<slug:schema_name>/students/add/mobile/', add_student_mobile_view, name='add_student_mobile'),
    path('portal/<slug:schema_name>/students/edit/<int:student_id>/', edit_student_view, name='edit_student'),
    path('portal/<slug:schema_name>/students/<int:student_id>/', student_profile_view, name='student_profile'),
    
    # Fee collection
    # Global Search API
    path('portal/<slug:schema_name>/api/global-search/', portal_wrapper(login_required_for_schema(global_search_api)), name='global_search_api'),
    path('portal/<slug:schema_name>/api/class-strength/', portal_wrapper(login_required_for_schema(class_strength_api)), name='class_strength_api'),
    re_path(r'^portal/(?P<schema_name>[a-zA-Z0-9_-]+)/fee/collection/(?:(?P<student_id>\d+)/)?$', fee_collection_view, name='fee_collection'),
    path('portal/<slug:schema_name>/fee/receipt/<int:receipt_id>/', fee_receipt_view, name='fee_receipt'),
    path('portal/<slug:schema_name>/defaulters/', defaulters_view, name='defaulters'),
    path('portal/<slug:schema_name>/defaulters/mobile/', mobile_defaulters_view, name='mobile_defaulters'),
    path('portal/<slug:schema_name>/reports/', reports_view, name='reports'),
    path('portal/<slug:schema_name>/reports/mobile/', mobile_reports_view, name='mobile_reports'),
    path('portal/<slug:schema_name>/settings/', settings_view, name='settings'),
    path('portal/<slug:schema_name>/fee/structure/', fee_structure_view, name='fee_structure'),
    path('portal/<slug:schema_name>/fee/structure/mobile/', mobile_fee_structure_view, name='mobile_fee_structure'),
    path('portal/<slug:schema_name>/fee/settings/', fee_settings_view, name='fee_settings'),
    path('portal/<slug:schema_name>/fee/settings/mobile/', mobile_fee_settings_view, name='mobile_fee_settings'),
    path('portal/<slug:schema_name>/settings/mobile/', mobile_settings_view, name='mobile_settings'),
    path('portal/<slug:schema_name>/fee/family-payment/', family_payment_view, name='family_payment'),
    path('portal/<slug:schema_name>/api/student-search/', student_search_api_view, name='student_search_api'),
    path('portal/<slug:schema_name>/api/sync-offline-student/', portal_wrapper(login_required_for_schema(sync_offline_student_api)), name='sync_offline_student_api'),
    path('portal/<slug:schema_name>/api/student/<int:student_id>/fee-records/', student_fee_records_api_view, name='student_fee_records_api'),
    path('portal/<slug:schema_name>/api/student/<int:student_id>/payments/', student_payments_api_view, name='student_payments_api'),
    
    path('portal/<slug:schema_name>/', tenant_root_redirect, name='tenant_root'),

    # ===== STOCK MANAGEMENT ROUTES =====
    path('portal/<slug:schema_name>/stock/', portal_wrapper(login_required_for_schema(stock_management)), name='stock_management'),
    path('portal/<slug:schema_name>/stock/product/<int:product_id>/', portal_wrapper(login_required_for_schema(product_detail)), name='product_detail'),
    path('portal/<slug:schema_name>/stock/category/add/', portal_wrapper(login_required_for_schema(add_category)), name='add_category'),
    path('portal/<slug:schema_name>/stock/category/delete/<int:category_id>/', portal_wrapper(login_required_for_schema(delete_category)), name='delete_category'),
    path('portal/<slug:schema_name>/stock/product/add/', portal_wrapper(login_required_for_schema(add_product)), name='add_product'),
    path('portal/<slug:schema_name>/stock/product/delete/<int:product_id>/', portal_wrapper(login_required_for_schema(delete_product)), name='delete_product'),
    # Mobile stock routes
    path('portal/<slug:schema_name>/stock/mobile/', portal_wrapper(login_required_for_schema(mobile_stock_management)), name='mobile_stock_management'),
    path('portal/<slug:schema_name>/stock/product/<int:product_id>/mobile/', portal_wrapper(login_required_for_schema(mobile_product_detail)), name='mobile_product_detail'),

    
    # ===== VOUCHERS (central listing) =====
    path('portal/<slug:schema_name>/vouchers/', portal_wrapper(login_required_for_schema(vouchers_list)), name='vouchers_list'),
    
    path('portal/<slug:schema_name>/fee/logs/', portal_wrapper(login_required_for_schema(fee_logs)), name='fee_logs'),
    path('portal/<slug:schema_name>/fee/logs/mobile/', portal_wrapper(login_required_for_schema(mobile_fee_logs)), name='mobile_fee_logs'),
    path('portal/<slug:schema_name>/api/dismiss-notification/', portal_wrapper(login_required_for_schema(dismiss_notification)), name='dismiss_notification'),
    path('portal/<slug:schema_name>/vouchers/mobile/', portal_wrapper(login_required_for_schema(mobile_vouchers_list)), name='mobile_vouchers_list'),

# ===== SELL SEPARATELY (standalone student search) =====
    path('portal/<slug:schema_name>/sell/', portal_wrapper(login_required_for_schema(sell_separately)), name='sell_separately'),
    path('portal/<slug:schema_name>/sell/mobile/', portal_wrapper(login_required_for_schema(mobile_sell_separately)), name='mobile_sell_separately'),
    path('sw.js', service_worker, name='service_worker'),
    path('portal/<slug:schema_name>/manifest.json', manifest, name='pwa_manifest'),

    # Voucher endpoints
    path('portal/<slug:schema_name>/api/student/<int:student_id>/voucher-status/', voucher_status_api, name='voucher_status_api'),
    path('portal/<slug:schema_name>/api/student/<int:student_id>/generate-voucher/', generate_voucher_api, name='generate_voucher_api'),
    path('portal/<slug:schema_name>/api/student/<int:student_id>/voucher-html/', voucher_html_api, name='voucher_html_api'),

    # Notification API endpoints
    path('portal/<slug:schema_name>/api/notifications/', portal_wrapper(login_required_for_schema(notifications_list_api)), name='notifications_list_api'),
    path('portal/<slug:schema_name>/api/notifications/mark-read/', portal_wrapper(login_required_for_schema(mark_notification_read_api)), name='mark_notification_read_api'),
    path('portal/<slug:schema_name>/api/notifications/mark-all-read/', portal_wrapper(login_required_for_schema(mark_all_notifications_read_api)), name='mark_all_notifications_read_api'),

    # ===== CLASS & SUBJECT MANAGEMENT =====
    path('portal/<slug:schema_name>/classes/', portal_wrapper(login_required_for_schema(class_management)), name='class_management'),
    path('portal/<slug:schema_name>/classes/mobile/', portal_wrapper(login_required_for_schema(mobile_class_management)), name='mobile_class_management'),
    path('portal/<slug:schema_name>/classes/add/', portal_wrapper(login_required_for_schema(add_class)), name='add_class'),
    path('portal/<slug:schema_name>/classes/edit/<int:class_id>/', portal_wrapper(login_required_for_schema(edit_class)), name='edit_class'),
    path('portal/<slug:schema_name>/classes/delete/<int:class_id>/', portal_wrapper(login_required_for_schema(delete_class)), name='delete_class'),
    path('portal/<slug:schema_name>/subjects/add/', portal_wrapper(login_required_for_schema(add_subject)), name='add_subject'),
    path('portal/<slug:schema_name>/subjects/edit/<int:subject_id>/', portal_wrapper(login_required_for_schema(edit_subject)), name='edit_subject'),
    path('portal/<slug:schema_name>/subjects/delete/<int:subject_id>/', portal_wrapper(login_required_for_schema(delete_subject)), name='delete_subject'),
    path('portal/<slug:schema_name>/assignments/add/', portal_wrapper(login_required_for_schema(assign_subject)), name='assign_subject'),
    path('portal/<slug:schema_name>/assignments/edit/<int:assignment_id>/', portal_wrapper(login_required_for_schema(edit_assignment)), name='edit_assignment'),
    path('portal/<slug:schema_name>/assignments/delete/<int:assignment_id>/', portal_wrapper(login_required_for_schema(delete_assignment)), name='delete_assignment'),
]
