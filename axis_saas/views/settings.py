"""
AXIS views – settings module.
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
from ..models import SchoolClient, Student, FeeStructure, FeeRecord, PaymentTransaction, SchoolFeeSettings, Product, ProductCategory, WingCategory
from ..forms import StudentForm, FeeCollectionForm, FeeSettingsForm, FeeStructureForm, FamilyPaymentForm
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from django.db import IntegrityError
from ..models import ManualGenerationLog

from .helpers import *

@require_tenant_type(['school'])
def settings(request, schema_name):
    tenant = get_tenant(request, schema_name)
    if request.method == 'POST':
        if tenant.tenant_type == 'wing_school' and request.POST.get('category_action'):
            name = request.POST.get('category_name', '').strip()
            parent_id = request.POST.get('category_parent') or None
            category_id = request.POST.get('category_id')
            with schema_context(schema_name):
                parent = WingCategory.objects.filter(pk=parent_id, parent__isnull=True, is_active=True).first() if parent_id else None
                if not name:
                    messages.error(request, 'Category name is required.')
                else:
                    try:
                        if request.POST.get('category_action') == 'edit' and category_id:
                            category = get_object_or_404(WingCategory, pk=category_id, is_active=True)
                            if parent and (parent.pk == category.pk or parent.parent_id == category.pk):
                                messages.error(request, 'A category cannot be its own parent or child.')
                            else:
                                category.name = name
                                category.parent = parent
                                category.save(update_fields=['name', 'parent'])
                                messages.success(request, 'Campus / wing category updated successfully.')
                        elif request.POST.get('category_action') == 'add':
                            WingCategory.objects.create(name=name, parent=parent)
                            messages.success(request, 'Campus / wing category added successfully.')
                    except IntegrityError:
                        messages.error(request, 'A category with this name already exists under the selected parent.')
            return redirect('settings', schema_name=schema_name)
        school_name = request.POST.get('school_name', '').strip()
        if school_name:
            tenant.name = school_name
        admin_username = request.POST.get('admin_username', '').strip()
        admin_password = request.POST.get('admin_password', '')
        admin_password_confirm = request.POST.get('admin_password_confirm', '')
        if admin_username:
            tenant.admin_username = admin_username
        if admin_password:
            if admin_password == admin_password_confirm:
                tenant._raw_password = admin_password
                tenant.set_password(admin_password)
            else:
                messages.error(request, 'Passwords do not match.')
                return redirect('settings', schema_name=schema_name)
        if request.FILES.get('school_logo'):
            tenant.school_logo = request.FILES['school_logo']
        tenant.save()
        messages.success(request, 'Settings updated successfully.')
        return redirect('settings', schema_name=schema_name)
    context = {'tenant': tenant, 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
    template = 'tenant/wing_school_settings.html' if tenant.tenant_type == 'wing_school' else 'tenant/settings.html'
    with schema_context(schema_name):
        context['wing_categories'] = WingCategory.objects.filter(is_active=True).select_related('parent') if tenant.tenant_type == 'wing_school' else []
        context['wing_parents'] = WingCategory.objects.filter(is_active=True, parent__isnull=True) if tenant.tenant_type == 'wing_school' else []
    return render(request, template, context)

def mobile_settings(request, schema_name):
    tenant = get_tenant(request, schema_name)
    if request.method == 'POST':
        school_name = request.POST.get('school_name', '').strip()
        if school_name:
            tenant.name = school_name
        admin_username = request.POST.get('admin_username', '').strip()
        admin_password = request.POST.get('admin_password', '')
        admin_password_confirm = request.POST.get('admin_password_confirm', '')
        if admin_username:
            tenant.admin_username = admin_username
        if admin_password:
            if admin_password == admin_password_confirm:
                tenant._raw_password = admin_password
                tenant.set_password(admin_password)
            else:
                messages.error(request, 'Passwords do not match.')
                return redirect('settings', schema_name=schema_name)
        if request.FILES.get('school_logo'):
            tenant.school_logo = request.FILES['school_logo']
        tenant.save()
        messages.success(request, 'Settings updated successfully.')
        return redirect('settings', schema_name=schema_name)
    context = {'tenant': tenant, 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
    template = 'mobile/wing_school_settings.html' if tenant.tenant_type == 'wing_school' else 'mobile/settings.html'
    if tenant.tenant_type == 'wing_school':
        with schema_context(schema_name):
            context['wing_categories'] = WingCategory.objects.filter(is_active=True).select_related('parent')
            context['wing_parents'] = WingCategory.objects.filter(is_active=True, parent__isnull=True)
    return render(request, template, context)
