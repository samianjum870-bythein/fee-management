"""
AXIS views – students module.
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
from ..models import SchoolClient, Student, FeeStructure, FeeRecord, PaymentTransaction, SchoolFeeSettings, Product, ProductCategory
from ..forms import StudentForm, FeeCollectionForm, FeeSettingsForm, FeeStructureForm, FamilyPaymentForm
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from ..models import ManualGenerationLog

from .helpers import *
from django.urls import reverse   # ✅ Added for reverse redirects

def student_list(request, schema_name):
    if is_mobile_user_agent(request):
        return redirect('mobile_student_list', schema_name=schema_name)
    context = get_student_list_context(request, schema_name)
    return render(request, 'tenant/student_list.html', context)

@require_tenant_type(['school'])
@require_school_feature('students')
def mobile_student_list(request, schema_name):
    context = get_student_list_context(request, schema_name)
    return render(request, 'mobile/student_list.html', context)

@require_tenant_type(['school'])
@require_school_feature('students')
def student_profile(request, schema_name, student_id):
    if is_mobile_user_agent(request):
        return redirect('mobile_student_profile', schema_name=schema_name, student_id=student_id)
    context = get_student_profile_context(request, schema_name, student_id)
    return render(request, 'tenant/student_profile.html', context)

@require_tenant_type(['school'])
@require_school_feature('students')
def mobile_student_profile(request, schema_name, student_id):
    context = get_student_profile_context(request, schema_name, student_id)
    return render(request, 'mobile/student_profile.html', context)

@require_tenant_type(['school'])
@require_school_feature('students')
def student_search_api(request, schema_name):
    q = request.GET.get('q', '')
    with schema_context(schema_name):
        students = Student.objects.filter(Q(name__icontains=q) | Q(roll_number__icontains=q) | Q(father_name__icontains=q) | Q(father_cnic__icontains=q))[:5]
        data = [{'id': s.id, 'name': s.name, 'roll_no': s.roll_number, 'grade': s.grade} for s in students]
    return JsonResponse(data, safe=False)

@require_tenant_type(['school'])
@require_school_feature('students')
def add_student(request, schema_name):
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        if request.method == 'POST':
            student, form = create_student_from_payload(schema_name, request.POST)
            if student:
                messages.success(request, f'Student {student.name} added successfully. Roll No: {student.roll_number}')
                return redirect('student_list', schema_name=schema_name)
        else:
            form = StudentForm()
        grades = FeeStructure.objects.values_list('grade', flat=True).distinct()
        context = {'tenant': tenant, 'form': form, 'grades': grades, 'classes': SchoolClass.objects.filter(is_active=True).order_by('name', 'section'), 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
    return render(request, 'tenant/student_form.html', context)

@csrf_exempt
@require_http_methods(['POST'])
@require_tenant_type(['school'])
@require_school_feature('students')
def sync_offline_student_api(request, schema_name):
    get_tenant(request, schema_name)
    try:
        if request.content_type and 'application/json' in request.content_type:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        else:
            payload = request.POST
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'errors': ['Invalid JSON payload']}, status=400)
    action = payload.get('action', 'create')
    student = None
    form = None
    if action == 'edit':
        student_id = payload.get('student_id')
        if not student_id:
            return JsonResponse({'ok': False, 'errors': ['Missing student_id for edit action']}, status=400)
        student, form = update_student_from_payload(schema_name, student_id, payload)
    else:
        student, form = create_student_from_payload(schema_name, payload)
    if student:
        return JsonResponse({'ok': True, 'student_id': student.id, 'roll_number': student.roll_number})
    return JsonResponse({'ok': False, 'errors': json.loads(form.errors.as_json())}, status=400)

@require_tenant_type(['school'])
@require_school_feature('students')
def add_student_mobile(request, schema_name):
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        if request.method == 'POST':
            student, form = create_student_from_payload(schema_name, request.POST)
            if student:
                messages.success(request, f'Student {student.name} added successfully. Roll No: {student.roll_number}')
                return redirect('mobile_student_list', schema_name=schema_name)
        else:
            form = StudentForm()
        grades = FeeStructure.objects.values_list('grade', flat=True).distinct()
        context = {'tenant': tenant, 'form': form, 'grades': grades, 'classes': SchoolClass.objects.filter(is_active=True).order_by('name', 'section'), 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
    return render(request, 'mobile/student_form.html', context)

@require_tenant_type(['school'])
@require_school_feature('students')
def edit_student(request, schema_name, student_id):
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        student = get_object_or_404(Student, id=student_id)
        if request.method == 'POST':
            form = StudentForm(request.POST, instance=student)
            if form.is_valid():
                form.save()
                messages.success(request, f'Student {student.name} updated successfully.')
                # ✅ Fixed: redirect to profile with ?updated=1
                if is_mobile_user_agent(request):
                    return redirect(reverse('mobile_student_profile', kwargs={'schema_name': schema_name, 'student_id': student.id}) + '?updated=1')
                return redirect(reverse('student_profile', kwargs={'schema_name': schema_name, 'student_id': student.id}) + '?updated=1')
        else:
            form = StudentForm(instance=student)
        grades = FeeStructure.objects.values_list('grade', flat=True).distinct()
        context = {'tenant': tenant, 'form': form, 'student': student, 'grades': grades, 'classes': SchoolClass.objects.filter(is_active=True).order_by('name', 'section'), 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
    return render(request, 'tenant/student_form.html', context)

def student_fee_records_api(request, schema_name, student_id):
    """API: Return JSON list of fee records for a student."""
    from django.http import JsonResponse
    from ..models import Student
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        try:
            student = Student.objects.get(id=student_id)
        except Student.DoesNotExist:
            return JsonResponse({'error': 'Student not found'}, status=404)
        records = []
        for fr in student.fee_records.all().order_by('-year', '-month'):
            records.append({'id': fr.id, 'month': fr.month, 'year': fr.year, 'amount': float(fr.total_amount), 'paid_amount': float(fr.paid_amount), 'status': fr.get_status_display(), 'due_date': fr.due_date.isoformat(), 'receipts': [{'id': p.id, 'number': p.receipt_number} for p in fr.payments.all()]})
        return JsonResponse(records, safe=False)

@require_tenant_type(['school'])
def student_payments_api(request, schema_name, student_id):
    """API: Return JSON list of payments for a student."""
    from django.http import JsonResponse
    from ..models import Student
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        try:
            student = Student.objects.get(id=student_id)
        except Student.DoesNotExist:
            return JsonResponse({'error': 'Student not found'}, status=404)
        payments = []
        for p in student.payments.all().order_by('-payment_date'):
            payments.append({'id': p.id, 'receipt_number': p.receipt_number, 'amount': float(p.amount), 'date': p.payment_date.isoformat(), 'mode': p.get_payment_mode_display(), 'remarks': p.remarks or '', 'url': f'/portal/{schema_name}/fee/receipt/{p.id}/'})
        return JsonResponse(payments, safe=False)

@require_tenant_type(['school'])
def student_current_fee_status_api(request, schema_name, student_id):
    logger = logging.getLogger(__name__)
    logger.info('student_current_fee_status_api called for student=%s schema=%s', student_id, schema_name)
    "API: Get current month's fee record status for a student."
    from django.http import JsonResponse
    from django.utils import timezone
    from ..models import Student, FeeRecord
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        try:
            student = Student.objects.get(id=student_id)
        except Student.DoesNotExist:
            return JsonResponse({'error': 'Student not found'}, status=404)
        today = timezone.localdate()
        month = today.month
        year = today.year
        try:
            record = FeeRecord.objects.get(student=student, month=month, year=year)
            data = {'exists': True, 'amount': float(record.total_amount), 'paid_amount': float(record.paid_amount), 'status': record.get_status_display(), 'due_date': record.due_date.isoformat(), 'can_edit': record.paid_amount == 0}
        except FeeRecord.DoesNotExist:
            default_fee = float(student.custom_fee) if student.custom_fee > 0 else 0
            if default_fee == 0:
                from ..models import FeeStructure
                fee_struct = FeeStructure.objects.filter(grade=student.grade).first()
                if fee_struct:
                    default_fee = float(fee_struct.monthly_fee)
            data = {'exists': False, 'default_fee': default_fee, 'grade': student.grade}
        return JsonResponse(data)


@require_tenant_type(['school'])
@require_school_feature('students')
def students_by_teacher(request, schema_name, teacher_id):
    """List students of classes where the given teacher is class teacher or subject teacher."""
    from ..models import Staff, Student, SchoolClass
    from django.db.models import Q
    from django.core.paginator import Paginator

    tenant = get_tenant(request, schema_name)
    teacher = get_object_or_404(Staff, id=teacher_id)
    # Get class IDs where teacher is class teacher or subject teacher
    class_ids = list(teacher.class_teacher_of.values_list('id', flat=True))
    class_ids += list(teacher.class_subjects.values_list('school_class_id', flat=True))
    class_ids = list(set(class_ids))

    students = Student.objects.filter(school_class_id__in=class_ids).order_by('name')
    paginator = Paginator(students, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'tenant': tenant,
        'teacher': teacher,
        'students': page_obj,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
    return render(request, 'tenant/students_by_teacher.html', context)

