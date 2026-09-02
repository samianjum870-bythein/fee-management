"""
AXIS views – fee_collection module.
"""

import logging
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
from ..models import SchoolClient, Student, FeeStructure, FeeRecord, PaymentTransaction, SchoolFeeSettings, Product, ProductCategory, SaleItem
from ..forms import StudentForm, FeeCollectionForm, FeeSettingsForm, FeeStructureForm, FamilyPaymentForm
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from ..models import ManualGenerationLog

from .helpers import *

@require_tenant_type(['school'])
@require_school_feature('fee_collection')
def mobile_fee_collection(request, schema_name, student_id=None):
    return fee_collection(request, schema_name, student_id, force_mobile=True)

@require_tenant_type(['school'])
@require_school_feature('fee_collection')
def mobile_fee_receipt(request, schema_name, receipt_id):
    return fee_receipt(request, schema_name, receipt_id, force_mobile=True)

@require_tenant_type(['school'])
def fee_receipt(request, schema_name, receipt_id, force_mobile=False):
    if is_mobile_user_agent(request) and (not force_mobile):
        return redirect('mobile_fee_receipt', schema_name=schema_name, receipt_id=receipt_id)
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        payment = get_object_or_404(PaymentTransaction.objects.select_related('student'), id=receipt_id)
        fee_records = list(payment.fee_records.all())
        item_details = extract_item_sales_from_remarks(payment.remarks or '')
        total_pending_before = sum((fr.amount for fr in fee_records))
        total_items_cost = sum((item['line_total'] for item in item_details)) if item_details else Decimal('0')
        total_paid = payment.amount
        remaining = total_pending_before + total_items_cost - total_paid
        if remaining < 0:
            remaining = Decimal('0')
        context = {'tenant': tenant, 'payment': payment, 'fee_records': fee_records, 'item_details': item_details, 'has_fee': bool(fee_records), 'has_items': bool(item_details), 'logo_url': tenant.school_logo.url if tenant.school_logo else None, 'total_fee_paid': total_pending_before, 'total_items_cost': total_items_cost, 'total_paid': total_paid, 'total_pending_before': total_pending_before, 'remaining': remaining, 'payment_mode_display': payment.get_payment_mode_display(), 'payment_type_display': payment.payment_type}
    template = 'mobile/receipt.html' if is_mobile_user_agent(request) else 'tenant/receipt.html'
    return render(request, template, context)

@require_tenant_type(['school'])
@require_school_feature('fee_collection')
def fee_collection(request, schema_name, student_id=None, force_mobile=False):
    if is_mobile_user_agent(request) and (not force_mobile):
        if student_id is not None:
            return redirect('mobile_fee_collection', schema_name=schema_name, student_id=student_id)
        return redirect('mobile_fee_collection', schema_name=schema_name)
    tenant = get_tenant(request, schema_name)
    mobile_mode = force_mobile or is_mobile_user_agent(request)
    with schema_context(schema_name):
        if request.method == 'POST':
            student_id_post = request.POST.get('student_id')
            amount_raw = request.POST.get('amount')
            payment_mode = request.POST.get('payment_mode')
            remarks = request.POST.get('remarks', '')
            product_items_raw = request.POST.get('product_items_json', '[]')
            try:
                product_items = json.loads(product_items_raw or '[]')
            except Exception:
                product_items = []
            if student_id_post and amount_raw:
                try:
                    student = Student.objects.get(id=student_id_post)
                    amount = Decimal(amount_raw)
                    product_total = Decimal('0.00')
                    item_breakdown = []
                    for item in product_items:
                        try:
                            product_id = int(item.get('product_id'))
                            qty = int(item.get('quantity', 0))
                        except (TypeError, ValueError):
                            continue
                        if qty <= 0:
                            continue
                        product = Product.objects.filter(id=product_id).first()
                        if not product:
                            raise ValueError(f'Product {product_id} not found')
                        if product.quantity < qty:
                            raise ValueError(f'Only {product.quantity} units available for {product.name}')
                        line_total = product.selling_price * qty
                        product_total += line_total
                        item_breakdown.append((product, qty, line_total))
                    pending_records = student.fee_records.filter(status__in=['pending', 'partial', 'overdue']).order_by('due_date')
                    fee_pending = sum((r.remaining_total for r in pending_records))
                    total_due = fee_pending + product_total
                    amount_received = amount
                    fee_to_apply = min(amount_received, Decimal(fee_pending)) if fee_pending else Decimal('0.00')
                    amount_left = amount_received - fee_to_apply
                    item_to_apply = min(amount_left, product_total) if product_total else Decimal('0.00')
                    remaining = fee_to_apply
                    paid_records = []
                    for record in pending_records:
                        if remaining <= 0:
                            break
                        due = record.remaining_total
                        apply_now = min(due, remaining)
                        record.paid_amount += apply_now
                        remaining -= apply_now
                        record.save()
                        paid_records.append(record)
                    item_details = '; '.join([f'{product.name} x{qty} @ ₹{product.selling_price} = ₹{line_total}' for product, qty, line_total in item_breakdown]) if item_breakdown else ''
                    combined_remarks = (remarks or '').strip()
                    if fee_to_apply > 0 and item_breakdown:
                        combined_remarks = (combined_remarks + '\n' if combined_remarks else '') + f'Fee payment applied: ₹{fee_to_apply:.2f}. Items sold: {item_details}'
                    elif fee_to_apply > 0:
                        combined_remarks = combined_remarks or 'Fee payment'
                    elif item_breakdown:
                        combined_remarks = (combined_remarks + '\n' if combined_remarks else '') + ('Items sold: ' + item_details)
                    payment_record = None
                    if amount_received > 0:
                        payment_record = PaymentTransaction.objects.create(student=student, amount=amount_received, payment_mode=payment_mode, payment_type='full' if amount_received >= total_due else 'partial', remarks=combined_remarks or 'Fee and item payment', created_by=request.session.get('school_admin_username', 'admin'))
                        if paid_records:
                            payment_record.fee_records.set(paid_records)
                        if item_breakdown:
                            for product, qty, _ in item_breakdown:
                                product.quantity -= qty
                                product.save(update_fields=['quantity'])
                    if amount_received > total_due:
                        messages.info(request, f'Payment received exceeds total due by ₹{amount_received - total_due:.2f}.')
                    elif amount_received < total_due:
                        messages.info(request, f'Amount received covers pending fee and selected items partially. Remaining balance: ₹{total_due - amount_received:.2f}.')
                    if payment_record:
                        for product, qty, line_total in item_breakdown:
                            SaleItem.objects.create(
                                payment=payment_record,
                                product=product,
                                name=product.name,
                                quantity=qty,
                                unit_price=product.selling_price,
                                line_total=line_total,
                            )
                        messages.success(request, f'Payment recorded successfully. Receipt: {payment_record.receipt_number}')
                        return redirect('fee_receipt', schema_name=schema_name, receipt_id=payment_record.id)
                    messages.success(request, 'Payment recorded.')
                    return redirect('fee_collection', schema_name=schema_name, student_id=student.id)
                except Student.DoesNotExist:
                    messages.error(request, 'Student not found')
                except Exception as e:
                    messages.error(request, f'Error processing payment: {str(e)}')
            else:
                messages.error(request, 'Invalid payment data')
            return redirect('fee_collection', schema_name=schema_name)
        if student_id is not None:
            try:
                student = Student.objects.get(id=student_id)
                pending_records = student.fee_records.filter(status__in=['pending', 'partial', 'overdue']).order_by('due_date')
                total_pending = get_overall_pending(student)
                products = list(Product.objects.select_related('category').filter(quantity__gt=0).order_by('category__name', 'name'))
                categories = list(ProductCategory.objects.all().order_by('name'))
                context = {'tenant': tenant, 'student': student, 'pending_records': pending_records, 'total_pending': total_pending, 'products': products, 'categories': categories, 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
                template_name = 'mobile/collect_fee.html' if mobile_mode else 'tenant/collect_fee.html'
                return render(request, template_name, context)
            except Student.DoesNotExist:
                messages.error(request, 'Student not found')
                return redirect('fee_collection', schema_name=schema_name)
        search_filter = request.GET.get('pending_search', '')
        grade_filter = request.GET.get('pending_grade', '')
        section_filter = request.GET.get('pending_section', '')
        page_number = request.GET.get('page', 1)
        students_qs = Student.objects.all()
        if search_filter:
            students_qs = students_qs.filter(Q(name__icontains=search_filter) | Q(roll_number__icontains=search_filter) | Q(father_name__icontains=search_filter) | Q(father_cnic__icontains=search_filter))
        if grade_filter:
            students_qs = students_qs.filter(grade=grade_filter)
        if section_filter:
            students_qs = students_qs.filter(section=section_filter)
        students_qs = get_student_pending_queryset(students_qs).prefetch_related('fee_records')
        pending_students = []
        for s in students_qs:
            pending = s.pending_amount
            if pending > 0:
                s.pending_total = pending
                pending_students.append(s)
        pending_students.sort(key=lambda x: x.pending_total, reverse=True)
        paginator = Paginator(pending_students, 20)
        pending_page = paginator.get_page(page_number)
        total_pending_all = get_student_pending_queryset(Student.objects.all()).aggregate(total_pending=Sum('pending_amount'))['total_pending'] or Decimal('0')
        total_payments_count = PaymentTransaction.objects.count()
        today = date.today()
        today_collection = PaymentTransaction.objects.filter(payment_date=today).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
        recent_payments = list(PaymentTransaction.objects.select_related('student').order_by('-payment_date')[:5])
        grades = list(Student.objects.values_list('grade', flat=True).distinct().order_by('grade'))
        sections = list(Student.objects.values_list('section', flat=True).distinct().order_by('section'))
        products = list(Product.objects.select_related('category').filter(quantity__gt=0).order_by('category__name', 'name'))
        categories = list(ProductCategory.objects.all().order_by('name'))
        context = {'tenant': tenant, 'pending_students': pending_page, 'recent_payments': recent_payments, 'total_pending_all': total_pending_all, 'total_payments_count': total_payments_count, 'today_collection': today_collection, 'grades': grades, 'sections': sections, 'products': products, 'categories': categories, 'search_filter': search_filter, 'grade_filter': grade_filter, 'section_filter': section_filter, 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
        template_name = 'mobile/fee_collection.html' if mobile_mode else 'tenant/fee_collection.html'
        return render(request, template_name, context)

@csrf_exempt
@require_http_methods(['GET'])
def debug_payments_api(request):
    if not request.session.get('school_admin_authenticated'):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    schema_name = request.session.get('school_admin_schema')
    if not schema_name:
        return JsonResponse({'error': 'No tenant schema'}, status=400)
    try:
        tenant = SchoolClient.objects.get(schema_name=schema_name)
    except SchoolClient.DoesNotExist:
        return JsonResponse({'error': 'Tenant not found'}, status=404)
    with schema_context(schema_name):
        payments = PaymentTransaction.objects.all().order_by('-payment_date')[:10]
        data = [{'id': p.id, 'receipt_number': p.receipt_number, 'student_id': p.student_id, 'amount': float(p.amount), 'date': p.payment_date.strftime('%Y-%m-%d')} for p in payments]
        return JsonResponse({'payments': data, 'total': PaymentTransaction.objects.count()})

def family_payment(request, schema_name):
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        if request.method == 'POST':
            form = FamilyPaymentForm(request.POST)
            if form.is_valid():
                cnic = form.cleaned_data['father_cnic']
                amount = form.cleaned_data['amount'] or None
                mode = form.cleaned_data['payment_mode']
                remarks = form.cleaned_data['remarks']
                students = Student.objects.filter(father_cnic=cnic, status='active')
                if not students.exists():
                    messages.error(request, 'No student found with this CNIC.')
                    return redirect('family_payment', schema_name=schema_name)
                total_pending = 0
                all_pending_records = []
                for s in students:
                    records = s.fee_records.filter(status__in=['pending', 'partial', 'overdue']).order_by('due_date')
                    total_pending += sum((r.remaining_total for r in records))
                    all_pending_records.extend(records)
                if amount is None:
                    amount = total_pending
                if amount > total_pending:
                    messages.error(request, f'Amount exceeds total pending ({total_pending})')
                    return redirect('family_payment', schema_name=schema_name)
                remaining = amount
                paid_records = []
                for record in all_pending_records:
                    if remaining <= 0:
                        break
                    due = record.remaining_total
                    if remaining >= due:
                        record.paid_amount = record.total_amount
                        remaining -= due
                    else:
                        record.paid_amount += remaining
                        remaining = 0
                    record.save()
                    paid_records.append(record)
                for student in students:
                    student_paid = [r for r in paid_records if r.student == student]
                    if student_paid:
                        payment = PaymentTransaction.objects.create(student=student, amount=sum((r.paid_amount for r in student_paid)), payment_mode=mode, payment_type='full' if remaining == 0 else 'partial', remarks=remarks, created_by=request.session.get('school_admin_username', 'admin'))
                        payment.fee_records.set(student_paid)
                messages.success(request, f'Family payment of ₹{amount} processed for CNIC {cnic}')
                return redirect('reports', schema_name=schema_name)
        else:
            form = FamilyPaymentForm()
    context = {'tenant': tenant, 'form': form, 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
    return render(request, 'tenant/family_payment.html', context)

@csrf_exempt
@require_http_methods(['GET'])
def fee_status_api(request):
    if not request.session.get('school_admin_authenticated'):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    schema_name = request.session.get('school_admin_schema')
    if not schema_name:
        return JsonResponse({'error': 'No tenant schema'}, status=400)
    try:
        tenant = SchoolClient.objects.get(schema_name=schema_name)
    except SchoolClient.DoesNotExist:
        return JsonResponse({'error': 'Tenant not found'}, status=404)
    with schema_context(schema_name):
        settings, _ = SchoolFeeSettings.objects.get_or_create(pk=1)
        last_record = FeeRecord.objects.order_by('-year', '-month').first()
        last_gen = f'{last_record.month}/{last_record.year}' if last_record else 'Never'
        today = timezone.localdate()
        gen_day = settings.fee_generation_day
        from calendar import monthrange
        if today.day <= gen_day:
            next_date = date(today.year, today.month, min(gen_day, monthrange(today.year, today.month)[1]))
        else:
            next_month = today.month + 1 if today.month < 12 else 1
            next_year = today.year + 1 if today.month == 12 else today.year
            next_date = date(next_year, next_month, min(gen_day, monthrange(next_year, next_month)[1]))
        return JsonResponse({'last_generation': last_gen, 'next_generation': next_date.strftime('%Y-%m-%d'), 'status': 'success'})

@csrf_exempt
@require_http_methods(['POST'])
def manual_generate_api(request):
    """Generate fee records for all active students with extra charges."""
    if not request.session.get('school_admin_authenticated'):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    schema_name = request.session.get('school_admin_schema')
    if not schema_name:
        return JsonResponse({'error': 'No tenant schema'}, status=400)
    try:
        tenant = SchoolClient.objects.get(schema_name=schema_name)
    except SchoolClient.DoesNotExist:
        return JsonResponse({'error': 'Tenant not found'}, status=404)
    with schema_context(schema_name):
        settings, _ = SchoolFeeSettings.objects.get_or_create(pk=1)
        today = timezone.localdate()
        month = today.month
        year = today.year
        students = Student.objects.filter(status='active')
        if not students.exists():
            return JsonResponse({'message': 'No active students found.'})
        due_date = today + timedelta(days=settings.due_date_offset)
        extra_charges = settings.default_extra_charges or []
        total_extra = sum((ch.get('amount', 0) for ch in extra_charges), 0)
        created = 0
        skipped_existing = 0
        skipped_no_fee = 0
        fee_records_to_create = []
        for student in students:
            existing = FeeRecord.objects.filter(student=student, month=month, year=year).first()
            if existing:
                skipped_existing += 1
                continue
            fee_struct = FeeStructure.objects.filter(grade=student.grade).first()
            if fee_struct:
                base_fee = fee_struct.monthly_fee
                if student.custom_fee != base_fee:
                    student.custom_fee = base_fee
                    student.save(update_fields=['custom_fee'])
            else:
                base_fee = student.custom_fee if student.custom_fee > 0 else 0
            if base_fee > 0:
                fee_records_to_create.append(
                    FeeRecord(
                        student=student,
                        month=month,
                        year=year,
                        amount=base_fee,
                        due_date=due_date,
                        status='pending',
                        extra_charges=extra_charges,
                    )
                )
                created += 1
            else:
                skipped_no_fee += 1
        if fee_records_to_create:
            FeeRecord.objects.bulk_create(fee_records_to_create)
        ManualGenerationLog.objects.create(month=month, year=year, created_count=created, skipped_existing=skipped_existing, skipped_no_fee=skipped_no_fee, triggered_by=request.session.get('school_admin_username', 'admin'), log_type='manual')
        create_fee_generation_notification(schema_name, month, year, created, request.session.get('school_admin_username', 'admin'), mobile=is_mobile_user_agent(request))
        message = f'Generated {created} fee records for {month}/{year}.'
        if skipped_existing > 0:
            message += f' Skipped {skipped_existing} students because they already have a fee record.'
        if skipped_no_fee > 0:
            message += f' Skipped {skipped_no_fee} students because no fee structure defined for their grade.'
        return JsonResponse({'message': message, 'created': created, 'skipped_existing': skipped_existing, 'skipped_no_fee': skipped_no_fee})

def manual_generate_single_api(request):
    if not request.session.get('school_admin_authenticated'):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    schema_name = request.session.get('school_admin_schema')
    if not schema_name:
        return JsonResponse({'error': 'No tenant schema'}, status=400)
    student_id = request.GET.get('student_id') or request.POST.get('student_id')
    custom_amount = request.POST.get('custom_amount') or request.GET.get('custom_amount')
    if not student_id:
        return JsonResponse({'error': 'Student ID required'}, status=400)
    logger = logging.getLogger(__name__)
    logger.info('manual_generate_single_api called for student=%s custom_amount=%s', student_id, custom_amount)
    try:
        tenant = SchoolClient.objects.get(schema_name=schema_name)
    except SchoolClient.DoesNotExist:
        return JsonResponse({'error': 'Tenant not found'}, status=404)
    with schema_context(schema_name):
        try:
            student = Student.objects.get(id=student_id)
        except Student.DoesNotExist:
            return JsonResponse({'error': 'Student not found'}, status=404)
        settings, _ = SchoolFeeSettings.objects.get_or_create(pk=1)
        today = timezone.localdate()
        month = today.month
        year = today.year
        existing_record = FeeRecord.objects.filter(student=student, month=month, year=year).first()
        if existing_record and existing_record.paid_amount > 0:
            return JsonResponse({'error': f'Fee already exists for {month}/{year} with paid amount ₹{existing_record.paid_amount}. Cannot modify.'}, status=400)
        if custom_amount:
            try:
                base_fee = Decimal(custom_amount)
                if base_fee <= 0:
                    raise ValueError
            except:
                return JsonResponse({'error': 'Invalid custom amount'}, status=400)
            extra_charges = []
            total_extra = Decimal('0')
            final_amount = base_fee
        else:
            base_fee = student.custom_fee if student.custom_fee > 0 else Decimal('0')
            if base_fee == 0:
                fee_struct = FeeStructure.objects.filter(grade=student.grade).first()
                if fee_struct:
                    base_fee = fee_struct.monthly_fee
                    student.custom_fee = base_fee
                    student.save(update_fields=['custom_fee'])
            if base_fee <= 0:
                return JsonResponse({'error': 'No fee structure defined for this grade and no valid custom amount provided.'}, status=400)
            extra_charges = settings.default_extra_charges or []
            final_amount = base_fee
        due_date = today + timedelta(days=settings.due_date_offset)
        if existing_record:
            existing_record.amount = final_amount
            existing_record.due_date = due_date
            existing_record.extra_charges = extra_charges
            existing_record.due_date_offset = settings.due_date_offset
            existing_record.late_fee_per_day = settings.late_fee_penalty
            existing_record.save()
            logger.info('Updated fee for %s to ₹%s (base: %s, extras: %s)', student.name, final_amount, base_fee, total_extra)
            return JsonResponse({'message': f'Fee amount updated for {student.name} for {month}/{year} to ₹{final_amount} (including extras).'})
        else:
            FeeRecord.objects.create(student=student, month=month, year=year, amount=final_amount, due_date=due_date, status='pending', extra_charges=extra_charges, due_date_offset=settings.due_date_offset, late_fee_per_day=settings.late_fee_penalty)
            logger.info('Created fee for %s with amount ₹%s (base: %s, extras: %s)', student.name, final_amount, base_fee, total_extra)
            return JsonResponse({'message': f'Fee record created for {student.name} for {month}/{year} with amount ₹{final_amount} (including extras).'})
