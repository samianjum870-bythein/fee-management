
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
from ..forms import StudentForm, FeeCollectionForm, FeeSettingsForm, FeeStructureForm, FamilyPaymentForm
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from ..models import ManualGenerationLog, WingCategory

MOBILE_AGENT_RE = re.compile(r"Mobile|Android|iP(hone|od|ad)|Opera Mini|IEMobile|BlackBerry|webOS|Fennec|Silk", re.I)

def require_tenant_type(allowed_types):

    def decorator(view_func):

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

    def decorator(view_func):

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


def get_student_pending_queryset(students_qs):
    """Annotate each student with SQL-level fee totals and pending balance."""
    fee_total = FeeRecord.objects.filter(student=OuterRef('pk')).values('student').annotate(total=Sum('amount')).values('total')
    payment_total = PaymentTransaction.objects.filter(student=OuterRef('pk')).values('student').annotate(total=Sum('amount')).values('total')
    return students_qs.annotate(
        total_fee=Coalesce(Subquery(fee_total), Value(Decimal('0'), output_field=DecimalField())),
        total_paid=Coalesce(Subquery(payment_total), Value(Decimal('0'), output_field=DecimalField())),
    ).annotate(
        pending_amount=ExpressionWrapper(F('total_fee') - F('total_paid'), output_field=DecimalField())
    )


def aggregate_pending_totals():
    """Aggregate fee and payment totals for the full tenant in one database query."""
    total_fee = FeeRecord.objects.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_paid = PaymentTransaction.objects.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    return {
        'total_fee': total_fee,
        'total_paid': total_paid,
        'total_pending': total_fee - total_paid,
    }


# Import left at module scope for the Subquery helper above.
from django.db.models import Subquery

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
    # Use Redis caching (5 minutes)
    def compute():
        with schema_context(schema_name):
            today = timezone.localdate()
            first_day_month = today.replace(day=1)
            today_collection = PaymentTransaction.objects.filter(payment_date=today).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
            month_collection = PaymentTransaction.objects.filter(payment_date__gte=first_day_month).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
            total_revenue = PaymentTransaction.objects.aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
            student_totals = get_student_pending_queryset(Student.objects.all())
            total_pending = student_totals.aggregate(total_pending=Sum('pending_amount'))['total_pending'] or Decimal('0')
            defaulters_count = Student.objects.filter(fee_records__status__in=['pending', 'partial', 'overdue']).distinct().count()
            total_students = Student.objects.count()
            low_stock_count = Product.objects.filter(quantity__lt=10).count()
            total_billed = total_revenue + total_pending
            collection_rate = float(total_revenue) / float(total_billed) * 100 if total_billed > 0 else 0
            recent_payments = list(PaymentTransaction.objects.select_related('student').order_by('-payment_date')[:5])
            top_defaulters = []
            for student in student_totals.filter(pending_amount__gt=0).order_by('-pending_amount')[:5]:
                fee_pending = sum(fr.remaining_total for fr in student.fee_records.filter(status__in=['pending', 'partial', 'overdue']))
                top_defaulters.append({'student': student, 'pending': student.pending_amount, 'fee_pending': fee_pending})
            months_labels = []
            months_amounts = []
            for i in range(5, -1, -1):
                m = today.month - i
                y = today.year
                if m <= 0:
                    m += 12
                    y -= 1
                total = PaymentTransaction.objects.filter(payment_date__year=y, payment_date__month=m).aggregate(Sum('amount'))['amount__sum'] or 0
                months_labels.append(f"{m}/{y}")
                months_amounts.append(float(total))
        return {
            'tenant': tenant,
            'today_collection': today_collection,
            'month_collection': month_collection,
            'total_revenue': total_revenue,
            'total_pending': total_pending,
            'defaulters_count': defaulters_count,
            'total_students': total_students,
            'low_stock_count': low_stock_count,
            'collection_rate': round(collection_rate, 1),
            'recent_payments': recent_payments,
            'top_defaulters': top_defaulters,
            'months_labels': months_labels,
            'months_amounts': months_amounts,
            'logo_url': tenant.school_logo.url if tenant.school_logo else None,
            'today': today,
            'start_date': first_day_month,
        }

    return get_cached_or_compute(schema_name, 'dashboard_stats', compute, 300)



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
            except:
                pass
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

        cumulative_fee_paid = Decimal('0')
        cumulative_items_paid = Decimal('0')
        payment_list = []

        for p in payments_qs_all:
            fee_paid = sum(fr.paid_amount for fr in p.fee_records.all())
            items_cost = items_cost_per_payment.get(p.id, Decimal('0'))
            total_due_before = (total_fee - cumulative_fee_paid) + (total_items_cost_all - cumulative_items_paid)

            cumulative_fee_paid += fee_paid
            cumulative_items_paid += (p.amount - fee_paid)

            remaining_balance = (total_fee - cumulative_fee_paid) + (total_items_cost_all - cumulative_items_paid)
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

        return {
            'tenant': tenant,
            'student': student,
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
        }
