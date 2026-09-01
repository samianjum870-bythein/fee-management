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

from .helpers import *

@require_tenant_type(['school'])
@require_school_feature('fee_structure')
def fee_structure(request, schema_name):
    if is_mobile_user_agent(request):
        return redirect('mobile_fee_structure', schema_name=schema_name)
    tenant = get_tenant(request, schema_name)
    edit_grade = request.GET.get('edit', '')
    with schema_context(schema_name):
        if request.method == 'POST':
            grade = request.POST.get('grade')
            monthly_fee = request.POST.get('monthly_fee')
            if grade and monthly_fee:
                obj, created = FeeStructure.objects.update_or_create(grade=grade, defaults={'monthly_fee': monthly_fee})
                Student.objects.filter(grade=grade).update(custom_fee=monthly_fee)
                messages.success(request, f'Fee structure for {grade} saved successfully.')
            else:
                messages.error(request, 'Please provide both grade and monthly fee.')
            return redirect('fee_structure', schema_name=schema_name)
        structures = list(FeeStructure.objects.all().order_by('grade'))
        total_structures = len(structures)
        if structures:
            fees = [fs.monthly_fee for fs in structures]
            avg_fee = sum(fees) / len(fees)
            min_fee = min(fees)
            max_fee = max(fees)
        else:
            avg_fee = min_fee = max_fee = 0
        logger = logging.getLogger(__name__)
        logger.info('Tenant %s fee structures loaded: %s', schema_name, len(structures))
        form = FeeStructureForm()
        if edit_grade:
            try:
                edit_obj = FeeStructure.objects.get(grade=edit_grade)
                form = FeeStructureForm(initial={'grade': edit_obj.grade, 'monthly_fee': edit_obj.monthly_fee})
            except FeeStructure.DoesNotExist:
                pass
    context = {'tenant': tenant, 'form': form, 'fee_structures': structures, 'edit_grade': edit_grade, 'logo_url': tenant.school_logo.url if tenant.school_logo else None, 'debug_count': len(structures), 'total_structures': total_structures, 'avg_fee': avg_fee, 'min_fee': min_fee, 'max_fee': max_fee}
    return render(request, 'tenant/fee_structure.html', context)

@require_tenant_type(['school'])
@require_school_feature('fee_structure')
def mobile_fee_structure(request, schema_name):
    """Mobile version of fee structure page."""
    tenant = get_tenant(request, schema_name)
    edit_grade = request.GET.get('edit', '')
    with schema_context(schema_name):
        if request.method == 'POST':
            grade = request.POST.get('grade')
            monthly_fee = request.POST.get('monthly_fee')
            if grade and monthly_fee:
                obj, created = FeeStructure.objects.update_or_create(grade=grade, defaults={'monthly_fee': monthly_fee})
                Student.objects.filter(grade=grade).update(custom_fee=monthly_fee)
                messages.success(request, f'Fee structure for {grade} saved successfully.')
            else:
                messages.error(request, 'Please provide both grade and monthly fee.')
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
        form = FeeStructureForm()
        if edit_grade:
            try:
                edit_obj = FeeStructure.objects.get(grade=edit_grade)
                form = FeeStructureForm(initial={'grade': edit_obj.grade, 'monthly_fee': edit_obj.monthly_fee})
            except FeeStructure.DoesNotExist:
                pass
    context = {'tenant': tenant, 'form': form, 'fee_structures': structures, 'edit_grade': edit_grade, 'logo_url': tenant.school_logo.url if tenant.school_logo else None, 'debug_count': len(structures), 'total_structures': total_structures, 'avg_fee': avg_fee, 'min_fee': min_fee, 'max_fee': max_fee}
    return render(request, 'mobile/fee_structure.html', context)
