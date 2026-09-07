"""
AXIS views – vouchers module.
"""

import re
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404
from django.contrib import messages
from django.db.models import Sum, Q, Exists, OuterRef, Max
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

from .helpers import *

from axis_saas.utils.display_grade import get_student_display_grade

def voucher_status_api(request, schema_name, student_id):
    """API: Get current month fee status, default fee, charges, pending totals."""
    from django.http import JsonResponse
    from django.utils import timezone
    from decimal import Decimal
    from ..models import Student, FeeRecord, FeeStructure, SchoolFeeSettings
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        try:
            student = Student.objects.get(id=student_id)
        except Student.DoesNotExist:
            return JsonResponse({'error': 'Student not found'}, status=404)
        today = timezone.localdate()
        month, year = (today.month, today.year)
        fee_record = None
        try:
            fee_record = FeeRecord.objects.get(student=student, month=month, year=year)
        except FeeRecord.DoesNotExist:
            pass
        default_fee = Decimal('0')
        display_grade = get_student_display_grade(student)
        fee_struct = FeeStructure.objects.filter(grade=display_grade).first()
        if fee_struct:
            default_fee = fee_struct.monthly_fee
        if student.custom_fee > 0:
            default_fee = student.custom_fee
        total_fee = student.fee_records.aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
        total_paid = student.payments.aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
        pending = total_fee - total_paid
        if pending < 0:
            pending = Decimal('0')
        settings, _ = SchoolFeeSettings.objects.get_or_create(pk=1)
        default_charges = settings.default_extra_charges or student.default_extra_charges or []
        response = {'exists': fee_record is not None, 'fee_record': {'id': fee_record.id if fee_record else None, 'amount': float(fee_record.amount) if fee_record else 0, 'paid_amount': float(fee_record.paid_amount) if fee_record else 0, 'extra_charges': fee_record.extra_charges or [] if fee_record else [], 'status': fee_record.status if fee_record else None, 'can_edit': fee_record and fee_record.paid_amount == 0, 'due_date_offset': fee_record.due_date_offset if fee_record else settings.due_date_offset, 'late_fee_per_day': float(fee_record.late_fee_per_day) if fee_record and fee_record.late_fee_per_day is not None else float(settings.late_fee_penalty)} if fee_record else None, 'default_fee': float(default_fee), 'default_charges': default_charges, 'settings': {'due_date_offset': fee_record.due_date_offset if fee_record else settings.due_date_offset, 'late_fee_penalty': float(fee_record.late_fee_per_day) if fee_record and fee_record.late_fee_per_day is not None else float(settings.late_fee_penalty)}, 'student_name': student.name, 'student_roll': student.roll_number, 'grade': student.grade, 'section': student.section, 'total_pending': float(pending)}
        return JsonResponse(response)

@csrf_exempt
def generate_voucher_api(request, schema_name, student_id):
    """API: Create or update fee record for current month with custom amount and charges."""
    from django.http import JsonResponse
    from django.utils import timezone
    from decimal import Decimal
    from ..models import Student, FeeRecord, FeeStructure, SchoolFeeSettings
    from django_tenants.utils import schema_context
    import json
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    custom_amount = data.get('custom_amount')
    charges = data.get('charges', [])
    save_default = data.get('save_default_charges', False)
    due_date_offset = data.get('due_date_offset')
    late_fee_penalty = data.get('late_fee_penalty')
    with schema_context(schema_name):
        try:
            student = Student.objects.get(id=student_id)
        except Student.DoesNotExist:
            return JsonResponse({'error': 'Student not found'}, status=404)
        settings, _ = SchoolFeeSettings.objects.get_or_create(pk=1)
        today = timezone.localdate()
        month, year = (today.month, today.year)
        if custom_amount is not None:
            try:
                amount = Decimal(str(custom_amount))
                if amount <= 0:
                    raise ValueError
            except Exception:
                return JsonResponse({'error': 'Invalid custom amount'}, status=400)
        else:
            amount = student.custom_fee if student.custom_fee > 0 else Decimal('0')
            if amount == 0:
                display_grade = get_student_display_grade(student)
                fee_struct = FeeStructure.objects.filter(grade=display_grade).first()
                if fee_struct:
                    amount = fee_struct.monthly_fee
            if amount <= 0:
                return JsonResponse({'error': 'No fee structure defined and no custom amount provided.'}, status=400)
        validated_charges = []
        for ch in charges:
            if isinstance(ch, dict) and 'title' in ch and ('amount' in ch):
                try:
                    ch_amount = Decimal(str(ch['amount']))
                    if ch_amount < 0:
                        continue
                    validated_charges.append({'title': str(ch['title']).strip() or 'Unnamed', 'amount': float(ch_amount)})
                except Exception:
                    pass
        effective_charges = validated_charges if validated_charges else settings.default_extra_charges or []
        try:
            offset_value = int(due_date_offset) if due_date_offset is not None else settings.due_date_offset
            if offset_value < 1:
                offset_value = settings.due_date_offset
        except (TypeError, ValueError):
            offset_value = settings.due_date_offset
        try:
            penalty_value = Decimal(str(late_fee_penalty)) if late_fee_penalty is not None else settings.late_fee_penalty
        except Exception:
            penalty_value = settings.late_fee_penalty
        try:
            fee_record, created = FeeRecord.objects.get_or_create(student=student, month=month, year=year, defaults={'amount': amount, 'due_date': today + timedelta(days=offset_value), 'due_date_offset': offset_value, 'late_fee_per_day': penalty_value, 'status': 'pending'})
        except Exception as exc:
            if 'due_date' in str(exc) or 'due_date_offset' in str(exc) or 'NOT NULL' in str(exc):
                fee_record = FeeRecord.objects.filter(student=student, month=month, year=year).first()
                if not fee_record:
                    fee_record = FeeRecord(student=student, month=month, year=year, amount=amount, due_date=today + timedelta(days=offset_value), due_date_offset=offset_value, late_fee_per_day=penalty_value, status='pending')
                    fee_record.save(force_insert=True)
                    created = True
                else:
                    created = False
            else:
                raise
        if not created:
            if fee_record.paid_amount > 0:
                return JsonResponse({'error': 'Fee already paid, cannot modify.'}, status=400)
            fee_record.extra_charges = effective_charges
            fee_record.due_date_offset = offset_value
            fee_record.late_fee_per_day = penalty_value
            if fee_record.due_date is None:
                fee_record.due_date = today + timedelta(days=offset_value)
            else:
                fee_record.due_date = fee_record.due_date
            if fee_record.paid_amount == 0 and fee_record.due_date < today and (fee_record.status in ['pending', 'overdue']):
                fee_record.amount = max(amount + penalty_value, amount)
            else:
                fee_record.amount = amount
            fee_record.save()
        else:
            fee_record.extra_charges = effective_charges
            fee_record.due_date = today + timedelta(days=offset_value)
            fee_record.due_date_offset = offset_value
            fee_record.late_fee_per_day = penalty_value
            if fee_record.paid_amount == 0 and fee_record.due_date < today and (fee_record.status in ['pending', 'overdue']):
                fee_record.amount = max(amount + penalty_value, amount)
            else:
                fee_record.amount = amount
            fee_record.save()
        if save_default:
            settings.default_extra_charges = effective_charges
            settings.due_date_offset = offset_value
            settings.late_fee_penalty = penalty_value
            settings.save(update_fields=['default_extra_charges', 'due_date_offset', 'late_fee_penalty'])
        voucher = {'receipt_number': f'VOUCHER-{student.id}-{year}{month:02d}', 'student_name': student.name, 'student_roll': student.roll_number, 'grade': student.grade, 'section': student.section, 'fee_amount': float(amount), 'charges': effective_charges, 'month': month, 'year': year, 'due_date': fee_record.due_date.strftime('%Y-%m-%d'), 'due_date_offset': offset_value, 'late_fee_penalty': float(penalty_value), 'total_pending': float(student.fee_records.aggregate(Sum('amount'))['amount__sum'] or 0) - float(student.payments.aggregate(Sum('amount'))['amount__sum'] or 0), 'generated_on': timezone.now().strftime('%Y-%m-%d %H:%M')}
        voucher['total'] = voucher['fee_amount'] + sum((ch['amount'] for ch in voucher['charges']))
        return JsonResponse({'success': True, 'voucher': voucher, 'created': created})

def voucher_html_api(request, schema_name, student_id):
    """API: Return HTML of the voucher for the current month (or latest if not exists)."""
    from django.http import HttpResponse
    from django.utils import timezone
    from django.template.loader import render_to_string
    from ..models import Student, FeeRecord, SchoolClient, SchoolFeeSettings
    from django_tenants.utils import schema_context
    from decimal import Decimal
    with schema_context(schema_name):
        try:
            student = Student.objects.get(id=student_id)
        except Student.DoesNotExist:
            return HttpResponse('Student not found', status=404)
        today = timezone.localdate()
        month, year = (today.month, today.year)
        fee_record = FeeRecord.objects.filter(student=student, month=month, year=year).first()
        if not fee_record:
            return HttpResponse('No fee record for current month', status=404)
        tenant = None
        try:
            with schema_context('public'):
                tenant = SchoolClient.objects.get(schema_name=schema_name)
        except SchoolClient.DoesNotExist:
            pass
        settings, _ = SchoolFeeSettings.objects.get_or_create(pk=1)
        charges = fee_record.extra_charges or []
        total = fee_record.amount + sum((Decimal(str(ch['amount'])) for ch in charges))
        voucher_data = {'student': student, 'fee_record': fee_record, 'charges': charges, 'total': total, 'tenant': tenant, 'settings': settings}
        html = render_to_string('tenant/voucher_snippet.html', voucher_data)
        return HttpResponse(html)

@require_tenant_type(['school'])
def vouchers_list(request, schema_name):
    """List all fee vouchers (fee records) with filters, grouped by status."""
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        today = timezone.localdate()
        current_month = today.month
        current_year = today.year
        month = request.GET.get('month')
        year = request.GET.get('year')
        search = request.GET.get('search', '').strip()
        if month:
            try:
                month = int(month)
                if month < 1 or month > 12:
                    month = current_month
            except ValueError:
                month = current_month
        else:
            month = current_month
        if year:
            try:
                year = int(year)
            except ValueError:
                year = current_year
        else:
            year = current_year
        from calendar import monthrange
        last_day = date(year, month, monthrange(year, month)[1])
        fee_records_qs = FeeRecord.objects.filter(month=month, year=year).select_related('student').order_by('student__name')
        if search:
            fee_records_qs = fee_records_qs.filter(Q(student__name__icontains=search) | Q(student__roll_number__icontains=search) | Q(student__father_name__icontains=search) | Q(student__grade__icontains=search) | Q(student__section__icontains=search))
        existing_student_ids = fee_records_qs.values_list('student_id', flat=True)
        missing_students = Student.objects.filter(status='active', enrolled_on__lte=last_day).exclude(id__in=existing_student_ids)
        pending_items = []
        partial_items = []
        paid_items = []
        missing_items = []
        for fr in fee_records_qs:
            item = {'type': 'record', 'record': fr, 'student': fr.student, 'status': fr.status, 'amount': fr.amount, 'paid': fr.paid_amount, 'due_date': fr.due_date, 'month': fr.month, 'year': fr.year, 'total_amount': fr.total_amount, 'remaining': fr.remaining_total}
            if fr.status == 'pending':
                pending_items.append(item)
            elif fr.status == 'partial':
                partial_items.append(item)
            elif fr.status == 'paid':
                paid_items.append(item)
            else:
                pending_items.append(item)
        for student in missing_students:
            reason = 'No fee structure'
            if student.custom_fee > 0:
                reason = 'Custom fee not set'
            else:
                display_grade = get_student_display_grade(student)
                fee_struct = FeeStructure.objects.filter(grade=display_grade).first()
                if fee_struct:
                    reason = 'Not generated'
            missing_items.append({'type': 'missing', 'student': student, 'reason': reason, 'status': 'missing', 'amount': 0, 'paid': 0, 'due_date': None, 'month': month, 'year': year})
        per_page = 10
        pending_page = Paginator(pending_items, per_page).get_page(request.GET.get('pending_page', 1))
        partial_page = Paginator(partial_items, per_page).get_page(request.GET.get('partial_page', 1))
        paid_page = Paginator(paid_items, per_page).get_page(request.GET.get('paid_page', 1))
        missing_page = Paginator(missing_items, per_page).get_page(request.GET.get('missing_page', 1))
        context = {'tenant': tenant, 'pending_page': pending_page, 'partial_page': partial_page, 'paid_page': paid_page, 'missing_page': missing_page, 'month': month, 'year': year, 'months': list(range(1, 13)), 'years': list(range(current_year - 5, current_year + 2)), 'search_query': search, 'is_current_month': month == current_month and year == current_year, 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
        return render(request, 'tenant/vouchers.html', context)

@require_tenant_type(['school'])
def mobile_vouchers_list(request, schema_name):
    """Mobile version of vouchers list grouped by status."""
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        today = timezone.localdate()
        current_month = today.month
        current_year = today.year
        month = request.GET.get('month')
        year = request.GET.get('year')
        search = request.GET.get('search', '').strip()
        if month:
            try:
                month = int(month)
                if month < 1 or month > 12:
                    month = current_month
            except ValueError:
                month = current_month
        else:
            month = current_month
        if year:
            try:
                year = int(year)
            except ValueError:
                year = current_year
        else:
            year = current_year
        from calendar import monthrange
        last_day = date(year, month, monthrange(year, month)[1])
        fee_records_qs = FeeRecord.objects.filter(month=month, year=year).select_related('student').order_by('student__name')
        if search:
            fee_records_qs = fee_records_qs.filter(Q(student__name__icontains=search) | Q(student__roll_number__icontains=search) | Q(student__father_name__icontains=search) | Q(student__grade__icontains=search) | Q(student__section__icontains=search))
        existing_student_ids = fee_records_qs.values_list('student_id', flat=True)
        missing_students = Student.objects.filter(status='active', enrolled_on__lte=last_day).exclude(id__in=existing_student_ids)
        pending_items = []
        partial_items = []
        paid_items = []
        missing_items = []
        for fr in fee_records_qs:
            item = {'type': 'record', 'record': fr, 'student': fr.student, 'status': fr.status, 'amount': fr.amount, 'paid': fr.paid_amount, 'due_date': fr.due_date, 'month': fr.month, 'year': fr.year, 'total_amount': fr.total_amount, 'remaining': fr.remaining_total}
            if fr.status == 'pending':
                pending_items.append(item)
            elif fr.status == 'partial':
                partial_items.append(item)
            elif fr.status == 'paid':
                paid_items.append(item)
            else:
                pending_items.append(item)
        for student in missing_students:
            reason = 'No fee structure'
            if student.custom_fee > 0:
                reason = 'Custom fee not set'
            else:
                display_grade = get_student_display_grade(student)
                fee_struct = FeeStructure.objects.filter(grade=display_grade).first()
                if fee_struct:
                    reason = 'Not generated'
            missing_items.append({'type': 'missing', 'student': student, 'reason': reason, 'status': 'missing', 'amount': 0, 'paid': 0, 'due_date': None, 'month': month, 'year': year})
        per_page = 10
        pending_page = Paginator(pending_items, per_page).get_page(request.GET.get('pending_page', 1))
        partial_page = Paginator(partial_items, per_page).get_page(request.GET.get('partial_page', 1))
        paid_page = Paginator(paid_items, per_page).get_page(request.GET.get('paid_page', 1))
        missing_page = Paginator(missing_items, per_page).get_page(request.GET.get('missing_page', 1))
        context = {'tenant': tenant, 'pending_page': pending_page, 'partial_page': partial_page, 'paid_page': paid_page, 'missing_page': missing_page, 'month': month, 'year': year, 'months': list(range(1, 13)), 'years': list(range(current_year - 5, current_year + 2)), 'search_query': search, 'is_current_month': month == current_month and year == current_year, 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
        return render(request, 'mobile/vouchers.html', context)