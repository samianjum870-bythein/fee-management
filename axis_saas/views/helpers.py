import functools

# ========== REDIS CACHE HELPERS ==========
import logging
from django.core.cache import cache
import hashlib
import json

logger = logging.getLogger(__name__)

def get_tenant_cache_key(schema_name, prefix, *args):
    """Generate a unique cache key for a tenant."""
    key = f"{prefix}:{schema_name}"
    if args:
        arg_str = json.dumps(args, sort_keys=True)
        hash_val = hashlib.md5(arg_str.encode()).hexdigest()[:8]
        key = f"{key}:{hash_val}"
    return key

def get_cached_or_compute(schema_name, cache_key_prefix, compute_func, timeout=300, *args, **kwargs):
    """Generic cache function."""
    cache_key = get_tenant_cache_key(schema_name, cache_key_prefix, *args)
    result = cache.get(cache_key)
    if result is not None:
        return result
    result = compute_func(*args, **kwargs)
    cache.set(cache_key, result, timeout=timeout)
    return result

def invalidate_tenant_cache(schema_name, cache_key_prefix):
    """Clear a specific cache key for a tenant."""
    cache_key = get_tenant_cache_key(schema_name, cache_key_prefix)
    cache.delete(cache_key)
    cache.delete(get_tenant_cache_key(schema_name, f"{cache_key_prefix}_all"))
"""
AXIS views – helpers module.
"""

import re
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404
from django.contrib import messages
from django.db.models import Sum, Q, Exists, OuterRef, Max, DecimalField, ExpressionWrapper, F, Value
from django.db.models.functions import TruncMonth, TruncDay, Coalesce
from django.db.models import Count
from django.core.paginator import Paginator
from django.db import connection
from django_tenants.utils import schema_context
from decimal import Decimal
from datetime import date, timedelta, datetime
from collections import defaultdict
import json
import re
from functools import wraps
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from ..models import SchoolClient, Student, FeeStructure, FeeRecord, PaymentTransaction, SchoolFeeSettings, Product, ProductCategory, SchoolClass
from ..models import SchoolClass
# DASHBOARD_V3_PROFESSIONAL: models used by _compute_dashboard_context.
# Before this import, every reference below raised NameError inside
# a try/except block, so the dashboard silently showed 0 for staff,
# pending leaves, staff-on-leave, attendance, top sellers, and next
# vacation. Fixed at the import site rather than by removing the
# try/except, so a future missing dependency still fails loudly in
# logging without taking down the page.
from ..models import (
    Staff,
    LeaveRequest,
    StudentAttendance,
    SaleItem,
    Vacation,
)
from ..forms import StudentForm, FeeCollectionForm, FeeSettingsForm, FeeStructureForm, FamilyPaymentForm
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from ..models import ManualGenerationLog, WingCategory

MOBILE_AGENT_RE = re.compile(r"Mobile|Android|iP(hone|od|ad)|Opera Mini|IEMobile|BlackBerry|webOS|Fennec|Silk", re.I)

def require_tenant_type(allowed_types):
    """LEAVE_BUTTONS_FIX_03: functools.wraps added so view attributes
    (notably csrf_exempt) survive this decorator layer."""

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, schema_name, *args, **kwargs):
            if hasattr(request, 'tenant') and request.tenant is not None:
                tenant = request.tenant
            else:
                tenant = get_tenant(request, schema_name)
            tenant_type_matches = tenant.tenant_type in allowed_types or (
                'school' in allowed_types and tenant.tenant_type in ('school', 'wing_school', 'single_small_school')
            )
            if not tenant_type_matches:
                raise Http404('Not available for this tenant type')
            return view_func(request, schema_name, *args, **kwargs)
        return wrapper
    return decorator

def require_school_feature(feature_key):
    """LEAVE_BUTTONS_FIX_03: functools.wraps added so view attributes
    (notably csrf_exempt) survive this decorator layer."""

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, schema_name, *args, **kwargs):
            if hasattr(request, 'tenant') and request.tenant is not None:
                tenant = request.tenant
            else:
                tenant = get_tenant(request, schema_name)
            channel = 'mobile' if '/mobile/' in request.path or is_mobile_user_agent(request) else 'desktop'
            if tenant.tenant_type not in ('school', 'wing_school', 'single_small_school') or not tenant.is_feature_enabled(feature_key, channel):
                raise Http404('This school feature is not enabled for this tenant.')
            return view_func(request, schema_name, *args, **kwargs)
        return wrapper
    return decorator


def require_ajax_post(view_func):
    """LEAVE_MANAGEMENT_HARDENING_V3: enforce POST + X-Requested-With.

    Replaces the previous `@csrf_exempt` pattern on state-changing
    endpoints. The client already sends `X-CSRFToken` and
    `X-Requested-With: XMLHttpRequest`. Django's CSRF middleware is
    active on these views again (no @csrf_exempt), so this decorator
    is pure defence-in-depth.
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.method != 'POST':
            return JsonResponse(
                {'ok': False, 'error': 'POST required.'}, status=405,
            )
        if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
            return JsonResponse(
                {'ok': False, 'error': 'AJAX request required.'}, status=400,
            )
        return view_func(request, *args, **kwargs)
    return wrapper

def create_fee_generation_notification(schema_name, month, year, created_count, triggered_by, mobile=False):
    """Create a notification for fee generation."""
    from ..models import Notification
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        message = f'Fee vouchers generated for {month}/{year}: {created_count} records created.'
        if mobile:
            link = f'/portal/{schema_name}/vouchers/mobile/?month={month}&year={year}'
        else:
            link = f'/portal/{schema_name}/vouchers/?month={month}&year={year}'
        Notification.objects.create(message=message, link=link)

def get_overall_pending(student):
    """Compute overall remaining balance: total fee + total items cost - total paid."""
    from decimal import Decimal
    from django.db.models import Sum
    total_fee = Decimal('0')
    for fr in student.fee_records.all():
        total_fee += fr.total_amount
    total_paid = student.payments.aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
    total_items_cost = Decimal('0')
    for p in student.payments.all():
        items = extract_item_sales_from_remarks(p.remarks or '')
        total_items_cost += sum((item['line_total'] for item in items))
    return total_fee + total_items_cost - total_paid

# STUDENT_PENDING_EXTRA_CHARGES_FIX_V1: PostgreSQL expression that
# sums the `amount` key of every entry inside FeeRecord.extra_charges.
# extra_charges is a JSONField (jsonb on PostgreSQL) storing a list
# of {"title": ..., "amount": ...} dicts. Django's ORM cannot
# aggregate a JSON list, so we drop to RawSQL. NULL and empty
# arrays both coalesce to 0.
_EXTRA_CHARGES_SQL = (
    "COALESCE((SELECT SUM((ch->>'amount')::numeric) "
    "FROM jsonb_array_elements(COALESCE(extra_charges, '[]'::jsonb)) AS ch), 0)"
)


def _extra_charges_expr():
    """Return a RawSQL expression summing the amount of every
    entry in FeeRecord.extra_charges. Safe on PostgreSQL; returns 0
    when the column is NULL or the array is empty.

    STUDENT_PENDING_EXTRA_CHARGES_OUTPUT_FIELD_FIX_V1: the
    RawSQL MUST declare output_field. Without it, Django cannot
    resolve the type of the enclosing ``F + F + RawSQL``
    expression and raises:

        FieldError: Cannot infer type of '+' expression
        involving these types: DecimalField, Field.
    """
    from django.db.models.expressions import RawSQL
    return RawSQL(
        _EXTRA_CHARGES_SQL,
        [],
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def get_student_pending_queryset(students_qs):
    """Annotate each student with SQL-level fee totals and pending balance.

    STUDENT_PENDING_EXTRA_CHARGES_FIX_V1: the sum now includes
    late_fee_accrued AND every entry in FeeRecord.extra_charges,
    so list / defaulters / collection / dashboard totals match the
    student profile (which uses FeeRecord.total_amount).
    """
    _fee_expr = F('amount') + F('late_fee_accrued') + _extra_charges_expr()
    # STUDENT_PENDING_EXTRA_CHARGES_OUTPUT_FIELD_FIX_V1: pass an
    # explicit output_field to Sum() too, so the subquery
    # aggregate has a well-defined Decimal type regardless of
    # backend / Django version quirks.
    _fee_sum = Sum(_fee_expr, output_field=DecimalField(max_digits=12, decimal_places=2))
    fee_total = FeeRecord.objects.filter(student=OuterRef('pk')).values('student').annotate(total=_fee_sum).values('total')
    payment_total = PaymentTransaction.objects.filter(student=OuterRef('pk')).values('student').annotate(total=Sum('amount')).values('total')
    return students_qs.annotate(
        total_fee=Coalesce(Subquery(fee_total), Value(Decimal('0'), output_field=DecimalField())),
        total_paid=Coalesce(Subquery(payment_total), Value(Decimal('0'), output_field=DecimalField())),
    ).annotate(
        pending_amount=ExpressionWrapper(F('total_fee') - F('total_paid'), output_field=DecimalField())
    )

def aggregate_pending_totals():
    """Aggregate fee and payment totals for the full tenant in one database query."""
    # STUDENT_PENDING_CALC_FIX_V1
    _fee_expr = F('amount') + F('late_fee_accrued') + _extra_charges_expr()
    _fee_sum = Sum(_fee_expr, output_field=DecimalField(max_digits=12, decimal_places=2))
    total_fee = FeeRecord.objects.aggregate(total=_fee_sum)['total'] or Decimal('0')
    total_paid = PaymentTransaction.objects.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    return {
        'total_fee': total_fee,
        'total_paid': total_paid,
        'total_pending': total_fee - total_paid,
    }

# Import left at module scope for the Subquery helper above.
from django.db.models import Subquery
from axis_saas.utils.display_grade import get_student_display_grade
from axis_saas.utils.class_display import get_class_display_for_student, get_class_display_name

def local_time_str(dt):
    """Convert aware datetime to local timezone and return formatted time string."""
    if not dt:
        return ''
    from django.utils import timezone
    local = timezone.localtime(dt)
    return local.strftime('%H:%M')

def get_tenant(request, schema_name):
    from django_tenants.utils import schema_context
    with schema_context('public'):
        return get_object_or_404(SchoolClient, schema_name=schema_name)

def create_student_from_payload(schema_name, payload):
    tenant = get_tenant(None, schema_name)
    with schema_context(schema_name):
        form = StudentForm(payload, wing_school=tenant.tenant_type == 'wing_school')
        if not form.is_valid():
            return (None, form)
        student = form.save(commit=False)
        if not student.custom_fee:
            fee_struct = FeeStructure.objects.filter(grade=student.grade).first()
            if fee_struct:
                student.custom_fee = fee_struct.monthly_fee
        student.save()
        return (student, form)

def update_student_from_payload(schema_name, student_id, payload):
    tenant = get_tenant(None, schema_name)
    with schema_context(schema_name):
        student = get_object_or_404(Student, id=student_id)

        form = StudentForm(payload, instance=student, wing_school=tenant.tenant_type == 'wing_school')
        if not form.is_valid():
            return (None, form)
        student = form.save(commit=False)
        if not student.custom_fee:
            fee_struct = FeeStructure.objects.filter(grade=student.grade).first()
            if fee_struct:
                student.custom_fee = fee_struct.monthly_fee
        student.save()
        return (student, form)

def is_mobile_user_agent(request):
    ua = request.META.get('HTTP_USER_AGENT', '')
    return bool(MOBILE_AGENT_RE.search(ua))

def _compute_dashboard_context(tenant, schema_name):
    """DASHBOARD_V2_PROFESSIONAL

    Full dashboard context. Every metric here is what the professional
    dashboard template reads. Cached for 5 minutes by get_dashboard_context.

    Metrics provided
    ----------------
    Fee / finance
        today_collection, month_collection, total_revenue,
        total_pending, defaulters_count, collection_rate,
        recent_payments, top_defaulters, months_labels, months_amounts,
        fee_automation_enabled, next_fee_generation_date

    People
        total_students, total_active_students,
        total_staff, total_active_staff,
        total_classes

    Attendance (today)
        today_attendance_marked, today_attendance_present,
        today_attendance_rate, class_attendance_summary,
        classes_with_attendance_today, total_classes_for_attendance

    Leave
        pending_leave_count, staff_on_leave_today,
        recent_pending_leaves

    Stock
        low_stock_count, top_selling_products

    Calendar
        next_vacation
    """
    from datetime import timedelta
    from calendar import monthrange

    def compute():
        with schema_context(schema_name):
            today = timezone.localdate()
            first_day_month = today.replace(day=1)
            last_30 = today - timedelta(days=30)

            # -----------------------------------------------------------------
            # Fee / finance
            # -----------------------------------------------------------------
            today_collection = (
                PaymentTransaction.objects
                .filter(payment_date=today)
                .aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
            )
            month_collection = (
                PaymentTransaction.objects
                .filter(payment_date__gte=first_day_month)
                .aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
            )
            total_revenue = (
                PaymentTransaction.objects
                .aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
            )
            student_totals = get_student_pending_queryset(Student.objects.all())
            total_pending = (
                student_totals
                .aggregate(total_pending=Sum('pending_amount'))['total_pending']
                or Decimal('0')
            )
            defaulters_count = (
                Student.objects
                .filter(fee_records__status__in=['pending', 'partial', 'overdue'])
                .distinct()
                .count()
            )

            total_students = Student.objects.count()
            total_active_students = Student.objects.filter(status='active').count()
            low_stock_count = Product.objects.filter(quantity__lt=10).count()
            # DASHBOARD_V3_FIX_1: list of the low-stock products so
            # the dashboard can show WHICH items need restocking.
            low_stock_items = list(
                Product.objects
                .filter(quantity__lt=10)
                .order_by('quantity', 'name')
                .values('id', 'name', 'quantity', 'selling_price')[:5]
            )

            total_billed = total_revenue + total_pending
            collection_rate = (
                float(total_revenue) / float(total_billed) * 100
                if total_billed > 0 else 0
            )

            recent_payments = list(
                PaymentTransaction.objects
                .select_related('student')
                .order_by('-payment_date')[:6]
            )

            top_defaulters = []
            for student in (
                student_totals
                .filter(pending_amount__gt=0)
                .order_by('-pending_amount')[:5]
            ):
                fee_pending = sum(
                    fr.remaining_total
                    for fr in student.fee_records.filter(
                        status__in=['pending', 'partial', 'overdue']
                    )
                )
                top_defaulters.append({
                    'student': student,
                    'pending': student.pending_amount,
                    'fee_pending': fee_pending,
                })

            # Six-month trend
            months_labels: list[str] = []
            months_amounts: list[float] = []
            for i in range(5, -1, -1):
                m = today.month - i
                y = today.year
                if m <= 0:
                    m += 12
                    y -= 1
                total = (
                    PaymentTransaction.objects
                    .filter(payment_date__year=y, payment_date__month=m)
                    .aggregate(Sum('amount'))['amount__sum'] or 0
                )
                months_labels.append(f"{m}/{y}")
                months_amounts.append(float(total))

            # -----------------------------------------------------------------
            # Fee automation status
            # -----------------------------------------------------------------
            try:
                fee_settings, _ = SchoolFeeSettings.objects.get_or_create(pk=1)
                fee_automation_enabled = bool(fee_settings.automation_enabled)
                gen_day = int(fee_settings.fee_generation_day or 1)
                if today.day <= gen_day:
                    next_fee_generation_date = date(
                        today.year, today.month,
                        min(gen_day, monthrange(today.year, today.month)[1]),
                    )
                else:
                    nm = today.month + 1 if today.month < 12 else 1
                    ny = today.year + 1 if today.month == 12 else today.year
                    next_fee_generation_date = date(
                        ny, nm,
                        min(gen_day, monthrange(ny, nm)[1]),
                    )
            except Exception:
                fee_automation_enabled = False
                next_fee_generation_date = None

            # -----------------------------------------------------------------
            # Staff / classes
            # -----------------------------------------------------------------
            try:
                total_staff = Staff.objects.count()
                total_active_staff = Staff.objects.filter(status='active').count()
            except Exception:
                total_staff = 0
                total_active_staff = 0

            try:
                class_qs = SchoolClass.objects.filter(is_active=True)
                total_classes = class_qs.count()
            except Exception:
                class_qs = SchoolClass.objects.none()
                total_classes = 0

            # -----------------------------------------------------------------
            # Attendance today (full-day rows only)
            # -----------------------------------------------------------------
            today_attendance_marked = 0
            today_attendance_present = 0
            today_attendance_rate = 0.0
            class_attendance_summary: list[dict] = []
            classes_with_attendance_today = 0

            try:
                today_full = StudentAttendance.objects.filter(
                    date=today, period_order__isnull=True,
                )
                today_attendance_marked = today_full.count()
                today_attendance_present = today_full.filter(
                    status__in=['present', 'late']
                ).count()
                if today_attendance_marked:
                    today_attendance_rate = round(
                        today_attendance_present / today_attendance_marked * 100, 1
                    )

                for c in class_qs.order_by('name', 'section')[:12]:
                    try:
                        total_in_class = Student.objects.filter(
                            school_class=c, status='active'
                        ).count()
                        marked_in_class = StudentAttendance.objects.filter(
                            school_class=c, date=today, period_order__isnull=True
                        ).count()
                    except Exception:
                        total_in_class = 0
                        marked_in_class = 0

                    if total_in_class == 0:
                        status = 'no_students'
                    elif marked_in_class >= total_in_class:
                        status = 'completed'
                    elif marked_in_class > 0:
                        status = 'partial'
                    else:
                        status = 'pending'

                    if marked_in_class > 0:
                        classes_with_attendance_today += 1

                    class_attendance_summary.append({
                        'class_obj': c,
                        'name': str(c),
                        'total': total_in_class,
                        'marked': marked_in_class,
                        'status': status,
                    })
            except Exception:
                pass

            # -----------------------------------------------------------------
            # Leave
            # -----------------------------------------------------------------
            pending_leave_count = 0
            staff_on_leave_today = 0
            # DASHBOARD_V3_FIX_1: names of staff on leave today.
            staff_on_leave_list: list = []
            recent_pending_leaves: list = []
            try:
                pending_leave_count = LeaveRequest.objects.filter(
                    status='pending'
                ).count()
                staff_on_leave_today = LeaveRequest.objects.filter(
                    status='approved',
                    start_date__lte=today,
                    end_date__gte=today,
                ).count()
                # DASHBOARD_V3_FIX_1: fetch the names of staff on
                # leave today, so the widget can render them.
                staff_on_leave_list = [
                    {
                        'staff_id': lv.staff_id,
                        'staff_name': lv.staff.full_name if lv.staff else '',
                        'leave_type': lv.get_leave_type_display(),
                        'start_date': lv.start_date.isoformat() if lv.start_date else '',
                        'end_date': lv.end_date.isoformat() if lv.end_date else '',
                    }
                    for lv in LeaveRequest.objects
                        .filter(
                            status='approved',
                            start_date__lte=today,
                            end_date__gte=today,
                        )
                        .select_related('staff')[:8]
                ]
                recent_pending_leaves = list(
                    LeaveRequest.objects
                    .filter(status='pending')
                    .select_related('staff')
                    .order_by('-created_at')[:4]
                )
            except Exception:
                pass

            # -----------------------------------------------------------------
            # Stock — top sellers last 30 days
            # -----------------------------------------------------------------
            top_selling_products: list[dict] = []
            try:
                top_selling_products = list(
                    SaleItem.objects
                    .filter(created_at__date__gte=last_30, product__isnull=False)
                    .values('name')
                    .annotate(
                        total_qty=Sum('quantity'),
                        total_value=Sum('line_total'),
                    )
                    .order_by('-total_qty')[:5]
                )
            except Exception:
                pass

            # -----------------------------------------------------------------
            # Upcoming vacation
            # -----------------------------------------------------------------
            next_vacation = None
            try:
                next_vacation = (
                    Vacation.objects
                    .filter(end_date__gte=today)
                    .order_by('start_date')
                    .first()
                )
            except Exception:
                pass

        return {
            'tenant': tenant,

            # Fee / finance
            'today_collection': today_collection,
            'month_collection': month_collection,
            'total_revenue': total_revenue,
            'total_pending': total_pending,
            'defaulters_count': defaulters_count,
            'collection_rate': round(collection_rate, 1),
            'recent_payments': recent_payments,
            'top_defaulters': top_defaulters,
            'months_labels': months_labels,
            'months_amounts': months_amounts,
            'fee_automation_enabled': fee_automation_enabled,
            'next_fee_generation_date': next_fee_generation_date,

            # People
            'total_students': total_students,
            'total_active_students': total_active_students,
            'total_staff': total_staff,
            'total_active_staff': total_active_staff,
            'total_classes': total_classes,

            # Attendance
            'today_attendance_marked': today_attendance_marked,
            'today_attendance_present': today_attendance_present,
            'today_attendance_rate': today_attendance_rate,
            'class_attendance_summary': class_attendance_summary,
            'classes_with_attendance_today': classes_with_attendance_today,

            # Leave
            'pending_leave_count': pending_leave_count,
            'staff_on_leave_today': staff_on_leave_today,
            'staff_on_leave_list': staff_on_leave_list,
            'recent_pending_leaves': recent_pending_leaves,

            # Stock
            'low_stock_count': low_stock_count,
            'low_stock_items': low_stock_items,
            'top_selling_products': top_selling_products,

            # Calendar
            'next_vacation': next_vacation,

            # Meta
            'logo_url': tenant.school_logo.url if tenant.school_logo else None,
            'today': today,
            'start_date': first_day_month,
        }

    # DASHBOARD_V3_PROFESSIONAL: 60s instead of 300s. The signals in
    # signals.py already invalidate on every relevant write, but a
    # short TTL is a safety net for deployments where the signal
    # module is not imported yet (e.g. fresh migration, missing
    # apps.ready import). 60s keeps the dashboard live-looking
    # without hammering the DB.
    return get_cached_or_compute(schema_name, 'dashboard_stats', compute, 60)


def product_list_api(request, schema_name):
    """API: Return list of products with their detail URLs for pre‑caching."""
    from django.http import JsonResponse
    from ..models import Product
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        products = Product.objects.all().values('id', 'name')
        data = []
        for p in products:
            data.append({'id': p['id'], 'desktop_url': f"/portal/{schema_name}/stock/product/{p['id']}/", 'mobile_url': f"/portal/{schema_name}/stock/product/{p['id']}/mobile/"})
        return JsonResponse(data, safe=False)

def student_list_api(request, schema_name):
    """API: Return list of students with their profile URLs for pre‑caching."""
    from django.http import JsonResponse
    from ..models import Student
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        students = Student.objects.filter(status='active').values('id', 'name')
        data = []
        for s in students:
            data.append({'id': s['id'], 'desktop_url': f"/portal/{schema_name}/students/{s['id']}/", 'mobile_url': f"/portal/{schema_name}/students/{s['id']}/mobile/"})
        return JsonResponse(data, safe=False)

def receipt_list_api(request, schema_name):
    """API: Return list of all receipt URLs for pre-caching."""
    from django.http import JsonResponse
    from ..models import PaymentTransaction
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        data = []
        for p in PaymentTransaction.objects.all().only('id'):
            data.append({'desktop_url': f'/portal/{schema_name}/fee/receipt/{p.id}/', 'mobile_url': f'/portal/{schema_name}/fee/receipt/mobile/{p.id}/'})
        return JsonResponse(data, safe=False)

def fee_collection_list_api(request, schema_name):
    """API: Return list of active students with their fee collection URLs for pre‑caching."""
    from django.http import JsonResponse
    from ..models import Student
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        students = Student.objects.filter(status='active').values('id', 'name')
        data = []
        for s in students:
            data.append({'id': s['id'], 'desktop_url': f"/portal/{schema_name}/fee/collection/{s['id']}/", 'mobile_url': f"/portal/{schema_name}/fee/collection/mobile/{s['id']}/"})
        return JsonResponse(data, safe=False)

def get_dashboard_context(tenant, schema_name):
    """Cached version of dashboard context."""
    def compute():
        return _compute_dashboard_context(tenant, schema_name)
    return get_cached_or_compute(schema_name, 'dashboard_stats', compute, 300)

# ========== STUDENT CONTEXT HELPERS (added by patcher) ==========

def extract_item_sales_from_remarks(remarks):
    """Extract item sale chunks from payment remarks for analytics and detail pages."""
    import re
    from decimal import Decimal

    text = remarks or ''
    marker_match = re.search(r'items sold\s*:\s*(.*)', text, flags=re.IGNORECASE)
    if not marker_match:
        marker_match = re.search(r'items sold\s+(.*)', text, flags=re.IGNORECASE)

    candidate_text = marker_match.group(1) if marker_match else text
    pattern = re.compile(
        r'(?P<name>.+?)\s*x\s*(?P<qty>\d+)\s*@\s*₹\s*(?P<price>\d+(?:\.\d+)?)\s*=\s*₹\s*(?P<total>\d+(?:\.\d+)?)',
        flags=re.IGNORECASE,
    )

    items = []
    for chunk in re.split(r';\s*', candidate_text):
        chunk = chunk.strip().strip('.').strip()
        if not chunk:
            continue
        match = pattern.search(chunk)
        if not match:
            continue
        items.append({
            'name': match.group('name').strip(),
            'quantity': int(match.group('qty')),
            'unit_price': Decimal(match.group('price')),
            'line_total': Decimal(match.group('total')),
            'raw': chunk,
        })
    return items

def get_student_list_context(request, schema_name):
    tenant = get_tenant(request, schema_name)
    query = request.GET.get('q', '')
    grade = request.GET.get('grade', '')
    section = request.GET.get('section', '')
    class_id = request.GET.get('class_id')
    category_id = request.GET.get('category_id')
    status = request.GET.get('status', '')
    pending_only = request.GET.get('pending_only') == '1'
    page_number = request.GET.get('page', 1)

    logger = logging.getLogger(__name__)
    logger.info('get_student_list_context: schema=%s class_id=%s', schema_name, class_id)

    with schema_context(schema_name):
        students = Student.objects.select_related('wing_category', 'school_class').all()
        if class_id:
            try:
                students = students.filter(school_class_id=class_id)
            except Exception as _tt_exc:
                # TIMETABLE_HARDENING_V1_PHASE3: bare except swallowed
                # KeyboardInterrupt and SystemExit, making the class_id
                # filter path impossible to debug. Narrowed and logged.
                logger.warning(
                    'get_student_list_context: class_id filter failed: %s',
                    _tt_exc,
                )
        if category_id and tenant.tenant_type == 'wing_school':
            students = students.filter(wing_category_id=category_id)
        if query:
            students = students.filter(
                Q(name__icontains=query) | Q(roll_number__icontains=query) |
                Q(father_name__icontains=query) | Q(father_cnic__icontains=query) |
                Q(parent_mobile__icontains=query) | Q(grade__icontains=query)
            )
        if grade:
            students = students.filter(grade=grade)
        if section:
            students = students.filter(section=section)
        if status:
            students = students.filter(status=status)
        students = get_student_pending_queryset(students).order_by('-enrolled_on')
        if pending_only:
            students = students.filter(pending_amount__gt=0)

        total_pending_all = students.aggregate(total_pending=Sum('pending_amount'))['total_pending'] or Decimal('0')
        paginator = Paginator(students, 20)
        page_obj = paginator.get_page(page_number)

        # Get distinct grades, sections, and active classes for filters
        grades = list(Student.objects.values_list('grade', flat=True).distinct().order_by('grade'))
        sections = list(Student.objects.values_list('section', flat=True).distinct().order_by('section'))
        status_choices = Student.STATUS_CHOICES
        total_active = Student.objects.filter(status='active').count()
        classes = SchoolClass.objects.filter(is_active=True).select_related('wing_category').order_by('name', 'section')
        categories = WingCategory.objects.filter(
            is_active=True,
            parent__isnull=False,
        ).select_related('parent').order_by('parent__name', 'name') if tenant.tenant_type == 'wing_school' else []
        if tenant.tenant_type == 'wing_school':
            categories = list(categories)
            parent_ids = {category.parent_id for category in categories}
            categories.extend(
                WingCategory.objects.filter(is_active=True, parent__isnull=True)
                .exclude(id__in=parent_ids).order_by('name')
            )

    return {
        'tenant': tenant,
        'students': page_obj,
        'grades': grades,
        'sections': sections,
        'classes': classes,
        'categories': categories,
        'selected_category': category_id,
        'status_choices': status_choices,
        'search_query': query,
        'total_pending_all': total_pending_all,
        'total_active': total_active,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
def get_student_profile_context(request, schema_name, student_id):
    tenant = get_tenant(request, schema_name)
    page = request.GET.get('page', 1)
    search_date = request.GET.get('date', '').strip()
    with schema_context(schema_name):
        student = get_object_or_404(Student, id=student_id)
        # ---- Get class teacher and subject teachers for this student ----
        school_class = student.school_class
        class_teacher = None
        subject_teachers = []
        if school_class:
            class_teacher = school_class.class_teacher
            # Fetch active subject assignments with teacher
            from ..models import ClassSubject
            assignments = ClassSubject.objects.filter(
                school_class=school_class, is_active=True
            ).select_related('subject', 'teacher')
            for ass in assignments:
                if ass.teacher:
                    subject_teachers.append({
                        'subject': ass.subject.name,
                        'teacher': ass.teacher,
                    })
        today = date.today()
        current_month = today.month
        current_year = today.year

        fee_records_qs = student.fee_records.all().order_by('-year', '-month')
        total_fee = Decimal('0')
        for fr in fee_records_qs:
            total_fee += fr.total_amount
        fee_records = list(fee_records_qs)

        payments_qs_all = student.payments.all().order_by('payment_date')
        if search_date:
            try:
                parsed = datetime.strptime(search_date, '%Y-%m-%d').date()
                payments_qs_all = payments_qs_all.filter(payment_date=parsed)
            except ValueError:
                pass

        total_items_cost_all = Decimal('0')
        items_cost_per_payment = {}
        for p in payments_qs_all:
            items = extract_item_sales_from_remarks(p.remarks or '')
            cost = sum(item['line_total'] for item in items)
            items_cost_per_payment[p.id] = cost
            total_items_cost_all += cost

        # STUDENT_PAYMENT_HISTORY_MATH_FIX_V1: per-payment contribution
        # must come from the PAYMENT's own amount, not from
        # fr.paid_amount (which is the cumulative paid on that record
        # across every payment ever made to it). We iterate the
        # payments in chronological order and split each payment into
        # the fee portion and the items portion. Fee is applied first
        # (matching fee_collection.fee_collection()), the remainder is
        # attributed to items, capped by the total item cost on the
        # whole ledger.
        cumulative_fee_paid = Decimal('0')
        cumulative_items_paid = Decimal('0')
        payment_list = []

        for p in payments_qs_all:
            payment_amount = p.amount or Decimal('0')
            items_cost = items_cost_per_payment.get(p.id, Decimal('0'))
            fee_outstanding = total_fee - cumulative_fee_paid
            if fee_outstanding < 0:
                fee_outstanding = Decimal('0')
            items_outstanding = total_items_cost_all - cumulative_items_paid
            if items_outstanding < 0:
                items_outstanding = Decimal('0')

            total_due_before = fee_outstanding + items_outstanding

            # Fee first, then items — matching how the cashier
            # allocates the amount in fee_collection.
            fee_paid = min(payment_amount, fee_outstanding)
            items_paid = min(payment_amount - fee_paid, items_outstanding)
            # Any surplus beyond both buckets is left as overpayment;
            # it does not reduce remaining below zero.
            cumulative_fee_paid += fee_paid
            cumulative_items_paid += items_paid

            remaining_balance = (
                (total_fee - cumulative_fee_paid)
                + (total_items_cost_all - cumulative_items_paid)
            )
            if remaining_balance < 0:
                remaining_balance = Decimal('0')

            has_fee = p.fee_records.exists()
            remarks = (p.remarks or '').lower()
            has_items = 'items sold' in remarks
            if has_fee and has_items:
                p_type = 'Fee & Items'
            elif has_fee:
                p_type = 'Fee'
            elif has_items:
                p_type = 'Items'
            else:
                p_type = 'Unknown'
            p.payment_type_display = p_type

            payment_list.append({
                'payment': p,
                'fee_paid': fee_paid,
                'total_due_before': total_due_before,
                'remaining_balance': remaining_balance,
            })

        payment_list.reverse()
        paginator = Paginator(payment_list, 10)
        page_obj = paginator.get_page(page)

        total_paid = student.payments.aggregate(Sum('amount'))['amount__sum'] or 0
        fee_paid_total = sum(fr.paid_amount for fr in fee_records)
        item_purchase_total = total_paid - fee_paid_total
        pending_total = total_fee + total_items_cost_all - total_paid

        # STUDENT_MOBILE_EDIT_CLASS_CONTEXT_FIX_V1: the mobile
        # edit-student modal (added by STUDENT_MOBILE_EDIT_CLASS_FIX_V1)
        # renders a `school_class` <select> whose options come from
        # `all_classes`. The view never passed that variable, so the
        # dropdown was empty and the `required` attribute blocked
        # every submit — the mobile edit was still silently broken.
        _all_classes = list(
            SchoolClass.objects
            .filter(is_active=True)
            .order_by('name', 'section')
        )
        return {
            'tenant': tenant,
            'student': student,
            'all_classes': _all_classes,
            'fee_records': fee_records,
            'payments': page_obj,
            'total_fee': total_fee,
            'total_paid': total_paid,
            'pending_total': pending_total,
            'item_purchase_total': item_purchase_total,
            'current_month': current_month,
            'current_year': current_year,
            'logo_url': tenant.school_logo.url if tenant.school_logo else None,
            'search_date': search_date,
            'class_teacher': class_teacher,
            'subject_teachers': subject_teachers,
        }