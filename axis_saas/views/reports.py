"""
AXIS views – reports module.
"""

import re
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404
from django.contrib import messages
from django.db.models import Sum, Q, Exists, OuterRef, Max, F
from django.db.models.functions import TruncMonth, TruncDay
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
from ..models import SchoolClient, Student, FeeStructure, FeeRecord, PaymentTransaction, SchoolFeeSettings, Product, ProductCategory
from ..forms import StudentForm, FeeCollectionForm, FeeSettingsForm, FeeStructureForm, FamilyPaymentForm
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from ..models import ManualGenerationLog

from django.views.decorators.cache import cache_page

from .helpers import *

@cache_page(60)
@require_tenant_type(['school'])
@require_school_feature('defaulters')
def defaulters(request, schema_name, force_mobile=False):
    """Defaulters list with search, filters, pagination, and analytics KPIs.
       Now includes students with overall pending (fee + items) even if no pending fee record.
    """
    tenant = get_tenant(request, schema_name)
    q = request.GET.get('q', '').strip()
    grade = request.GET.get('grade', '')
    section = request.GET.get('section', '')
    days = request.GET.get('days', '0')
    sort_by = request.GET.get('sort_by', 'overdue')
    page_number = request.GET.get('page', 1)
    try:
        days = int(days)
    except:
        days = 0
    if days < 0:
        days = 0
    with schema_context(schema_name):
        today = timezone.localdate()
        cutoff = today - timedelta(days=days) if days > 0 else None
        students_qs = Student.objects.all()
        if q:
            students_qs = students_qs.filter(Q(name__icontains=q) | Q(roll_number__icontains=q) | Q(father_name__icontains=q) | Q(father_cnic__icontains=q) | Q(parent_mobile__icontains=q))
        if grade:
            students_qs = students_qs.filter(grade=grade)
        if section:
            students_qs = students_qs.filter(section=section)

        students_qs = get_student_pending_queryset(students_qs)
        if show_only_pending := request.GET.get('pending_only') == '1':
            students_qs = students_qs.filter(pending_amount__gt=0)
        else:
            students_qs = students_qs.filter(pending_amount__gt=0)

        students_qs = students_qs.annotate(
            fee_pending=Coalesce(
                Sum('fee_records__remaining_total', distinct=True),
                Value(Decimal('0')),
            )
        )
        if cutoff:
            students_qs = students_qs.filter(fee_records__due_date__lt=cutoff)
        students_qs = students_qs.distinct()

        if sort_by == 'pending':
            students_qs = students_qs.order_by('-pending_amount', 'name')
        elif sort_by == 'name':
            students_qs = students_qs.order_by('name')
        else:
            students_qs = students_qs.order_by('-pending_amount', 'name')

        page_obj = Paginator(students_qs, 15).get_page(page_number)
        result = list(page_obj.object_list)
        total_defaulters = students_qs.count()
        total_pending_all = students_qs.aggregate(total_pending=Sum('pending_amount'))['total_pending'] or Decimal('0')
        avg_overdue = 0
        max_overdue = 0
        for student in result:
            oldest_due = student.fee_records.filter(status__in=['pending', 'partial', 'overdue']).order_by('due_date').first()
            days_overdue = (today - oldest_due.due_date).days if oldest_due and oldest_due.due_date < today else 0
            student.days_overdue = days_overdue
            avg_overdue += days_overdue
            max_overdue = max(max_overdue, days_overdue)
        avg_overdue = (avg_overdue / total_defaulters) if total_defaulters else 0
        grades = list(Student.objects.values_list('grade', flat=True).distinct().order_by('grade'))
        sections = list(Student.objects.values_list('section', flat=True).distinct().order_by('section'))
    context = {'tenant': tenant, 'defaulters': page_obj, 'total_defaulters': total_defaulters, 'total_pending_all': total_pending_all, 'avg_overdue': round(avg_overdue, 1), 'max_overdue': max_overdue, 'days': days, 'search_query': q, 'grade_filter': grade, 'section_filter': section, 'sort_by': sort_by, 'grades': grades, 'sections': sections, 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
    template = 'mobile/defaulters.html' if force_mobile else 'tenant/defaulters.html'
    return render(request, template, context)

@cache_page(60)
@require_tenant_type(['school'])
@require_school_feature('reports')
def reports(request, schema_name, force_mobile=False):
    tenant = get_tenant(request, schema_name)
    report_type = request.GET.get('type', 'collection')
    today = timezone.localdate()
    quick_filter = request.GET.get('quick_filter')
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    search_q = request.GET.get('search', '').strip()
    page_num = request.GET.get('page', 1)
    if quick_filter == 'today':
        start_date = end_date = today
    elif quick_filter == 'week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif quick_filter == 'month':
        start_date = today.replace(day=1)
        end_date = today
    elif quick_filter == 'year':
        start_date = today.replace(month=1, day=1)
        end_date = today
    elif quick_filter == 'all':
        start_date = date(2000, 1, 1)
        end_date = today
    elif quick_filter == 'last6months':
        start_date = today - timedelta(days=180)
        end_date = today
    elif start_date_str and end_date_str:
        try:
            start_date = date.fromisoformat(start_date_str)
            end_date = date.fromisoformat(end_date_str)
            if start_date > end_date:
                start_date, end_date = (end_date, start_date)
            quick_filter = 'custom'
        except:
            start_date = today - timedelta(days=180)
            end_date = today
            quick_filter = 'last6months'
    else:
        start_date = date(2000, 1, 1)
        end_date = today
        quick_filter = 'all'
    with schema_context(schema_name):
        payments_qs = PaymentTransaction.objects.filter(payment_date__gte=start_date, payment_date__lte=end_date)
        if search_q:
            payments_qs = payments_qs.filter(Q(receipt_number__icontains=search_q) | Q(student__name__icontains=search_q) | Q(student__roll_number__icontains=search_q))
        paginator = Paginator(payments_qs.order_by('-payment_date'), 15)
        payments_page = paginator.get_page(page_num)
        total_collection = payments_qs.aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
        payment_count = payments_qs.count()
        total_pending = FeeRecord.objects.aggregate(total=Sum(F('amount') - F('paid_amount')))['total'] or Decimal('0')
        total_collection_all = PaymentTransaction.objects.aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
        total_billed = total_collection_all + total_pending
        collection_rate = float(total_collection_all) / float(total_billed) * 100 if total_billed > 0 else 0
        defaulters_count = Student.objects.filter(fee_records__status__in=['pending', 'partial', 'overdue']).distinct().count()
        monthly_data = []
        for i in range(5, -1, -1):
            m = today.month - i
            y = today.year
            if m <= 0:
                m += 12
                y -= 1
            total = PaymentTransaction.objects.filter(payment_date__year=y, payment_date__month=m).aggregate(Sum('amount'))['amount__sum'] or 0
            monthly_data.append({'month': f'{m}/{y}', 'amount': float(total)})
        mode_totals = {}
        for mode_code, mode_name in PaymentTransaction.PAYMENT_MODE_CHOICES:
            total = payments_qs.filter(payment_mode=mode_code).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
            if total > 0:
                mode_totals[mode_name] = float(total)
        mode_distribution = [{'name': k, 'amount': v} for k, v in mode_totals.items()]
        class_pending = []
        grades = Student.objects.values_list('grade', flat=True).distinct().order_by('grade')
        grades = list(grades)
        for grade in grades:
            pending = get_student_pending_queryset(Student.objects.filter(grade=grade)).aggregate(total_pending=Sum('pending_amount'))['total_pending'] or Decimal('0')
            if pending > 0:
                class_pending.append({'grade': grade, 'pending': float(pending)})
        class_pending.sort(key=lambda x: x['pending'], reverse=True)
        top_defaulters = []
        for student in get_student_pending_queryset(Student.objects.all()).filter(pending_amount__gt=0).order_by('-pending_amount')[:5]:
            top_defaulters.append({'student': student, 'pending': float(student.pending_amount)})
        defaulters_list = Student.objects.filter(fee_records__status__in=['pending', 'partial', 'overdue']).distinct()
        defaulters_data = []
        for student in get_student_pending_queryset(defaulters_list).prefetch_related('fee_records'):
            pending = student.pending_amount
            oldest_due = student.fee_records.filter(status__in=['pending', 'partial', 'overdue']).order_by('due_date').first()
            days_overdue = (timezone.localdate() - oldest_due.due_date).days if oldest_due and oldest_due.due_date < timezone.localdate() else 0
            defaulters_data.append({'student': student, 'pending_amount': pending, 'days_overdue': days_overdue})
        defaulters_data.sort(key=lambda x: x['days_overdue'], reverse=True)
        context = {'tenant': tenant, 'report_type': report_type, 'start_date': start_date, 'end_date': end_date, 'quick_filter': quick_filter, 'search_query': search_q, 'total_collection': total_collection, 'total_pending': total_pending, 'collection_rate': round(collection_rate, 1), 'defaulters_count': defaulters_count, 'monthly_data': monthly_data, 'mode_distribution': mode_distribution, 'class_pending': class_pending, 'top_defaulters': top_defaulters, 'defaulters_data': defaulters_data, 'payments': payments_page, 'total': total_collection, 'payment_count': payment_count, 'logo_url': tenant.school_logo.url if tenant.school_logo else None, 'total_collection_all': total_collection_all}
        template = 'mobile/reports.html' if force_mobile else 'tenant/reports.html'
    return render(request, template, context)

@require_tenant_type(['school'])
@require_school_feature('defaulters')
def mobile_defaulters(request, schema_name):
    """Mobile version of defaulters page."""
    return defaulters(request, schema_name, force_mobile=True)

@require_tenant_type(['school'])
@require_school_feature('reports')
def mobile_reports(request, schema_name):
    """Mobile version of reports page."""
    return reports(request, schema_name, force_mobile=True)
