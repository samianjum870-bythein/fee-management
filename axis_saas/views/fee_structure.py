"""
AXIS views – fee_structure module.
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
from ..models import SchoolClass, WingCategory  # added by patcher

from .helpers import *

@require_tenant_type(['school'])
@require_school_feature('fee_structure')
def fee_structure(request, schema_name):
    if is_mobile_user_agent(request):
        return redirect('mobile_fee_structure', schema_name=schema_name)
    tenant = get_tenant(request, schema_name)
    edit_param = request.GET.get('edit', '')
    with schema_context(schema_name):
        # Fetch all active classes and wing categories
        classes = SchoolClass.objects.filter(is_active=True).select_related('wing_category').order_by('name', 'section')
        wing_categories = WingCategory.objects.filter(is_active=True, parent__isnull=False).select_related('parent').order_by('parent__name', 'name') if tenant.tenant_type == 'wing_school' else []

        if request.method == 'POST':
            class_id = request.POST.get('class_id')
            monthly_fee = request.POST.get('monthly_fee')
            if class_id and monthly_fee:
                try:
                    school_class = SchoolClass.objects.get(id=class_id)
                    # Build grade string based on tenant type
                    if tenant.tenant_type == 'wing_school' and school_class.wing_category:
                        main = school_class.wing_category.parent
                        sub = school_class.wing_category
                        if school_class.section:
                            grade = f"{main.name} ({sub.name}) - {school_class.name} - {school_class.section}"
                        else:
                            grade = f"{main.name} ({sub.name}) - {school_class.name}"
                    else:
                        if school_class.section:
                            grade = f"{school_class.name} - {school_class.section}"
                        else:
                            grade = school_class.name

                    obj, created = FeeStructure.objects.update_or_create(grade=grade, defaults={'monthly_fee': monthly_fee})
                    Student.objects.filter(grade=grade).update(custom_fee=monthly_fee)
                    messages.success(request, f'Fee structure for {grade} saved successfully.')
                except SchoolClass.DoesNotExist:
                    messages.error(request, 'Invalid class selected.')
            else:
                messages.error(request, 'Please select a class and enter a monthly fee.')
            return redirect('fee_structure', schema_name=schema_name)

        # Get existing fee structures
        structures = list(FeeStructure.objects.all().order_by('grade'))
        total_structures = len(structures)
        if structures:
            fees = [fs.monthly_fee for fs in structures]
            avg_fee = sum(fees) / len(fees)
            min_fee = min(fees)
            max_fee = max(fees)
        else:
            avg_fee = min_fee = max_fee = 0

        # Determine selected class for editing
        selected_class = None
        edit_class_id = None
        if edit_param:
            if edit_param.isdigit():
                try:
                    selected_class = SchoolClass.objects.get(id=edit_param)
                    edit_class_id = edit_param
                except SchoolClass.DoesNotExist:
                    pass
            else:
                for cls in classes:
                    if tenant.tenant_type == 'wing_school' and cls.wing_category:
                        main = cls.wing_category.parent
                        sub = cls.wing_category
                        if cls.section:
                            grade_str = f"{main.name} ({sub.name}) - {cls.name} - {cls.section}"
                        else:
                            grade_str = f"{main.name} ({sub.name}) - {cls.name}"
                    else:
                        if cls.section:
                            grade_str = f"{cls.name} - {cls.section}"
                        else:
                            grade_str = cls.name
                    if grade_str == edit_param:
                        selected_class = cls
                        edit_class_id = cls.id
                        break

        form = FeeStructureForm()
        if selected_class:
            if tenant.tenant_type == 'wing_school' and selected_class.wing_category:
                main = selected_class.wing_category.parent
                sub = selected_class.wing_category
                if selected_class.section:
                    grade_str = f"{main.name} ({sub.name}) - {selected_class.name} - {selected_class.section}"
                else:
                    grade_str = f"{main.name} ({sub.name}) - {selected_class.name}"
            else:
                if selected_class.section:
                    grade_str = f"{selected_class.name} - {selected_class.section}"
                else:
                    grade_str = selected_class.name
            try:
                fee_obj = FeeStructure.objects.get(grade=grade_str)
                form = FeeStructureForm(initial={'grade': grade_str, 'monthly_fee': fee_obj.monthly_fee})
            except FeeStructure.DoesNotExist:
                form = FeeStructureForm(initial={'grade': grade_str, 'monthly_fee': 0.00})

        grade_to_class_id = {}
        for cls in classes:
            if tenant.tenant_type == 'wing_school' and cls.wing_category:
                main = cls.wing_category.parent
                sub = cls.wing_category
                if cls.section:
                    grade_str = f"{main.name} ({sub.name}) - {cls.name} - {cls.section}"
                else:
                    grade_str = f"{main.name} ({sub.name}) - {cls.name}"
            else:
                if cls.section:
                    grade_str = f"{cls.name} - {cls.section}"
                else:
                    grade_str = cls.name
            grade_to_class_id[grade_str] = cls.id

        context = {
            'tenant': tenant,
            'form': form,
            'fee_structures': structures,
            'edit_class_id': edit_class_id,
            'selected_class': selected_class,
            'classes': classes,
            'wing_categories': wing_categories,
            'grade_to_class_id': grade_to_class_id,
            'logo_url': tenant.school_logo.url if tenant.school_logo else None,
            'debug_count': len(structures),
            'total_structures': total_structures,
            'avg_fee': avg_fee,
            'min_fee': min_fee,
            'max_fee': max_fee,
        }
        return render(request, 'tenant/fee_structure.html', context)

@require_tenant_type(['school'])
@require_school_feature('fee_structure')
def mobile_fee_structure(request, schema_name):
    """Mobile version of fee structure page."""
    tenant = get_tenant(request, schema_name)
    edit_param = request.GET.get('edit', '')
    with schema_context(schema_name):
        classes = SchoolClass.objects.filter(is_active=True).select_related('wing_category').order_by('name', 'section')
        wing_categories = WingCategory.objects.filter(is_active=True, parent__isnull=False).select_related('parent').order_by('parent__name', 'name') if tenant.tenant_type == 'wing_school' else []

        if request.method == 'POST':
            class_id = request.POST.get('class_id')
            monthly_fee = request.POST.get('monthly_fee')
            if class_id and monthly_fee:
                try:
                    school_class = SchoolClass.objects.get(id=class_id)
                    if tenant.tenant_type == 'wing_school' and school_class.wing_category:
                        main = school_class.wing_category.parent
                        sub = school_class.wing_category
                        if school_class.section:
                            grade = f"{main.name} ({sub.name}) - {school_class.name} - {school_class.section}"
                        else:
                            grade = f"{main.name} ({sub.name}) - {school_class.name}"
                    else:
                        if school_class.section:
                            grade = f"{school_class.name} - {school_class.section}"
                        else:
                            grade = school_class.name

                    obj, created = FeeStructure.objects.update_or_create(grade=grade, defaults={'monthly_fee': monthly_fee})
                    Student.objects.filter(grade=grade).update(custom_fee=monthly_fee)
                    messages.success(request, f'Fee structure for {grade} saved successfully.')
                except SchoolClass.DoesNotExist:
                    messages.error(request, 'Invalid class selected.')
            else:
                messages.error(request, 'Please select a class and enter a monthly fee.')
            return redirect('mobile_fee_structure', schema_name=schema_name)

        structures = list(FeeStructure.objects.all().order_by('grade'))
        total_structures = len(structures)
        if structures:
            fees = [fs.monthly_fee for fs in structures]
            avg_fee = sum(fees) / len(fees)
            min_fee = min(fees)
            max_fee = max(fees)
        else:
            avg_fee = min_fee = max_fee = 0

        selected_class = None
        edit_class_id = None
        if edit_param:
            if edit_param.isdigit():
                try:
                    selected_class = SchoolClass.objects.get(id=edit_param)
                    edit_class_id = edit_param
                except SchoolClass.DoesNotExist:
                    pass
            else:
                for cls in classes:
                    if tenant.tenant_type == 'wing_school' and cls.wing_category:
                        main = cls.wing_category.parent
                        sub = cls.wing_category
                        if cls.section:
                            grade_str = f"{main.name} ({sub.name}) - {cls.name} - {cls.section}"
                        else:
                            grade_str = f"{main.name} ({sub.name}) - {cls.name}"
                    else:
                        if cls.section:
                            grade_str = f"{cls.name} - {cls.section}"
                        else:
                            grade_str = cls.name
                    if grade_str == edit_param:
                        selected_class = cls
                        edit_class_id = cls.id
                        break

        form = FeeStructureForm()
        if selected_class:
            if tenant.tenant_type == 'wing_school' and selected_class.wing_category:
                main = selected_class.wing_category.parent
                sub = selected_class.wing_category
                if selected_class.section:
                    grade_str = f"{main.name} ({sub.name}) - {selected_class.name} - {selected_class.section}"
                else:
                    grade_str = f"{main.name} ({sub.name}) - {selected_class.name}"
            else:
                if selected_class.section:
                    grade_str = f"{selected_class.name} - {selected_class.section}"
                else:
                    grade_str = selected_class.name
            try:
                fee_obj = FeeStructure.objects.get(grade=grade_str)
                form = FeeStructureForm(initial={'grade': grade_str, 'monthly_fee': fee_obj.monthly_fee})
            except FeeStructure.DoesNotExist:
                form = FeeStructureForm(initial={'grade': grade_str, 'monthly_fee': 0.00})

        grade_to_class_id = {}
        for cls in classes:
            if tenant.tenant_type == 'wing_school' and cls.wing_category:
                main = cls.wing_category.parent
                sub = cls.wing_category
                if cls.section:
                    grade_str = f"{main.name} ({sub.name}) - {cls.name} - {cls.section}"
                else:
                    grade_str = f"{main.name} ({sub.name}) - {cls.name}"
            else:
                if cls.section:
                    grade_str = f"{cls.name} - {cls.section}"
                else:
                    grade_str = cls.name
            grade_to_class_id[grade_str] = cls.id

        context = {
            'tenant': tenant,
            'form': form,
            'fee_structures': structures,
            'edit_class_id': edit_class_id,
            'selected_class': selected_class,
            'classes': classes,
            'wing_categories': wing_categories,
            'grade_to_class_id': grade_to_class_id,
            'logo_url': tenant.school_logo.url if tenant.school_logo else None,
            'debug_count': len(structures),
            'total_structures': total_structures,
            'avg_fee': avg_fee,
            'min_fee': min_fee,
            'max_fee': max_fee,
        }
        return render(request, 'mobile/fee_structure.html', context)
