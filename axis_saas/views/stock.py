"""
AXIS views – stock module.
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
from ..models import SchoolClient, Student, FeeStructure, FeeRecord, PaymentTransaction, SchoolFeeSettings, Product, ProductCategory, SaleItem
from ..forms import StudentForm, FeeCollectionForm, FeeSettingsForm, FeeStructureForm, FamilyPaymentForm
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from ..models import ManualGenerationLog

from .helpers import *
from django.urls import reverse   # ✅ Added for reverse redirects

@require_tenant_type(['school'])
def stock_management(request, schema_name, force_mobile=False):
    """Main stock management page: list categories and products (RAW SQL)."""
    from django.shortcuts import render
    from django.db import connection
    from ..models import ProductCategory, Product
    from django_tenants.utils import schema_context
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        with connection.cursor() as cursor:
            cursor.execute('SELECT id, name, description FROM axis_saas_productcategory ORDER BY name')
            raw_cats = cursor.fetchall()
        with connection.cursor() as cursor:
            cursor.execute('''
                SELECT p.id, p.name, p.sku, p.selling_price, p.quantity, p.notes,
                       c.id as category_id, c.name as category_name
                FROM axis_saas_product p
                JOIN axis_saas_productcategory c ON p.category_id = c.id
                ORDER BY c.name, p.name
            ''')
            raw_products = cursor.fetchall()
        products_qs = Product.objects.select_related('category').all().order_by('category__name', 'name')
        total_products = products_qs.count()
        total_categories = ProductCategory.objects.count()
        total_stock_value = sum((p.selling_price * p.quantity) for p in products_qs)
        low_stock_count = products_qs.filter(quantity__lt=10).count()
        item_sales = []
        total_units_sold = 0
        total_sales_value = Decimal('0.00')
        product_sales = defaultdict(lambda: {'units': 0, 'value': Decimal('0.00'), 'last_sale': None, 'id': None})
        product_id_cache = {}
        try:
            recent_sales = SaleItem.objects.select_related('payment__student', 'product').order_by('-created_at')[:100]
            for sale in recent_sales:
                total_units_sold += sale.quantity
                total_sales_value += sale.line_total
                item_sales.append({'payment': sale.payment, 'item': {'name': sale.name, 'quantity': sale.quantity, 'line_total': sale.line_total}, 'student': sale.payment.student})
                name = sale.name.strip().lower()
                entry = product_sales[name]
                entry['units'] += sale.quantity
                entry['value'] += sale.line_total
                if entry['last_sale'] is None or sale.payment.payment_date > entry['last_sale']:
                    entry['last_sale'] = sale.payment.payment_date
                if sale.product_id is not None and entry['id'] is None:
                    entry['id'] = sale.product_id
        except Exception:
            recent_sales = []
            item_sales = []
            product_sales = defaultdict(lambda: {'units': 0, 'value': Decimal('0.00'), 'last_sale': None, 'id': None})
        top_items = []
        for name, values in product_sales.items():
            top_items.append({'name': name.title(), 'units': values['units'], 'value': values['value'], 'last_sale': values['last_sale'], 'id': values['id']})
        context = {'tenant': tenant, 'raw_cats': raw_cats, 'raw_products': raw_products, 'analytics': {'total_products': total_products, 'total_categories': total_categories, 'total_stock_value': total_stock_value, 'low_stock_count': low_stock_count, 'total_units_sold': total_units_sold, 'total_sales_value': total_sales_value}, 'recent_sales': item_sales[:10], 'top_items': top_items, 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
        template = 'mobile/stock_management.html' if is_mobile_user_agent(request) or force_mobile else 'tenant/stock_management.html'
    return render(request, template, context)

@require_tenant_type(['school'])
@require_school_feature('stock_management')
def product_detail(request, schema_name, product_id, force_mobile=False):
    """Detailed product analytics page with sales history and recent receipts."""
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        product = get_object_or_404(Product.objects.select_related('category'), id=product_id)
        sales_events = []
        total_units_sold = 0
        total_sales_value = Decimal('0.00')
        last_sale_date = None
        buyer_info = {}
        for sale in SaleItem.objects.filter(product=product).select_related('payment__student').order_by('-created_at'):
            total_units_sold += sale.quantity
            total_sales_value += sale.line_total
            sales_events.append({'payment': sale.payment, 'item': {'name': sale.name, 'quantity': sale.quantity, 'line_total': sale.line_total}, 'student': sale.payment.student})
            if sale.payment.student:
                buyer_info[sale.payment.student.id] = sale.payment.student.name
            if last_sale_date is None or sale.payment.payment_date > last_sale_date:
                last_sale_date = sale.payment.payment_date
        context = {'tenant': tenant, 'product': product, 'sales_events': sales_events[:50], 'analytics': {'total_units_sold': total_units_sold, 'total_sales_value': total_sales_value, 'stock_value': product.selling_price * product.quantity, 'low_stock': product.quantity < 10, 'last_sale_date': last_sale_date, 'average_sale_value': total_sales_value / total_units_sold if total_units_sold else Decimal('0.00')}, 'recent_buyers': [{'id': sid, 'name': name} for sid, name in buyer_info.items()][:8], 'logo_url': tenant.school_logo.url if tenant.school_logo else None}
        template = 'mobile/product_detail.html' if is_mobile_user_agent(request) or force_mobile else 'tenant/product_detail.html'
    return render(request, template, context)

@require_tenant_type(['school'])
def mobile_stock_management(request, schema_name):
    """Mobile-only stock management view."""
    return stock_management(request, schema_name, force_mobile=True)

@require_tenant_type(['school'])
def mobile_product_detail(request, schema_name, product_id):
    """Mobile-only product detail view."""
    return product_detail(request, schema_name, product_id, force_mobile=True)

def add_category(request, schema_name):
    """Add or edit a product category."""
    from django.shortcuts import redirect, get_object_or_404
    from django.contrib import messages
    from ..models import ProductCategory
    from django_tenants.utils import schema_context
    if request.method == 'POST':
        cat_id = request.POST.get('category_id')
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        if not name:
            messages.error(request, 'Category name is required')
            if is_mobile_user_agent(request):
                return redirect('mobile_stock_management', schema_name=schema_name)
            if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
                return redirect('mobile_stock_management', schema_name=schema_name)
            return redirect('stock_management', schema_name=schema_name)
        with schema_context(schema_name):
            if cat_id:
                category = get_object_or_404(ProductCategory, id=cat_id)
                category.name = name
                category.description = description
                category.save()
                messages.success(request, f"Category '{name}' updated.")
            elif ProductCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Category with this name already exists.')
            else:
                ProductCategory.objects.create(name=name, description=description)
                messages.success(request, f"Category '{name}' added.")
    if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
        return redirect('mobile_stock_management', schema_name=schema_name)
    return redirect('stock_management', schema_name=schema_name)

@require_tenant_type(['school'])
@require_school_feature('stock_management')
def delete_category(request, schema_name, category_id):
    """Delete a category (only if no products linked)."""
    from django.shortcuts import get_object_or_404, redirect
    from django.contrib import messages
    from ..models import ProductCategory
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        category = get_object_or_404(ProductCategory, id=category_id)
        if category.products.exists():
            messages.error(request, f"Cannot delete '{category.name}' because it has products. Remove products first.")
        else:
            category.delete()
            messages.success(request, f"Category '{category.name}' deleted.")
    if is_mobile_user_agent(request):
        return redirect('mobile_stock_management', schema_name=schema_name)
    if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
        return redirect('mobile_stock_management', schema_name=schema_name)
    return redirect('stock_management', schema_name=schema_name)

@require_tenant_type(['school'])
@require_school_feature('stock_management')
def add_product(request, schema_name):
    """Add or edit a product."""
    from django.shortcuts import redirect, get_object_or_404
    from django.contrib import messages
    from decimal import Decimal
    from ..models import ProductCategory, Product
    from django_tenants.utils import schema_context

    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        category_id = request.POST.get('category')
        name = request.POST.get('name', '').strip()
        selling_price = request.POST.get('selling_price')
        quantity = request.POST.get('quantity')
        notes = request.POST.get('notes', '')

        if not name or not category_id or (not selling_price):
            messages.error(request, 'Category, Name, and Selling Price are required.')
            if is_mobile_user_agent(request):
                return redirect('mobile_stock_management', schema_name=schema_name)
            if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
                return redirect('mobile_stock_management', schema_name=schema_name)
            return redirect('stock_management', schema_name=schema_name)

        try:
            price = Decimal(selling_price)
            qty = int(quantity) if quantity else 0
        except:
            messages.error(request, 'Invalid price or quantity.')
            if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
                return redirect('mobile_stock_management', schema_name=schema_name)
            return redirect('stock_management', schema_name=schema_name)

        with schema_context(schema_name):
            category = get_object_or_404(ProductCategory, id=category_id)
            if product_id:
                product = get_object_or_404(Product, id=product_id)
                product.category = category
                product.name = name
                product.selling_price = price
                product.quantity = qty
                product.notes = notes
                product.save()
                messages.success(request, f"Product '{name}' updated.")
            else:
                product = Product.objects.create(
                    category=category,
                    name=name,
                    selling_price=price,
                    quantity=qty,
                    notes=notes
                )
                messages.success(request, f"Product '{name}' added. SKU: {product.sku}")

        # ✅ Fixed: redirect to product detail with ?updated=1 (only after successful POST)
        if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
            return redirect(reverse('mobile_product_detail', kwargs={'schema_name': schema_name, 'product_id': product.id}) + '?updated=1')
        return redirect(reverse('product_detail', kwargs={'schema_name': schema_name, 'product_id': product.id}) + '?updated=1')

    # For GET requests, redirect to stock management (since form is in modal)
    return redirect('stock_management', schema_name=schema_name)

@require_tenant_type(['school'])
@require_school_feature('stock_management')
def delete_product(request, schema_name, product_id):
    """Delete a product."""
    from django.shortcuts import get_object_or_404, redirect
    from django.contrib import messages
    from ..models import Product
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        product = get_object_or_404(Product, id=product_id)
        product.delete()
        messages.success(request, f"Product '{product.name}' deleted.")
    if is_mobile_user_agent(request):
        return redirect('mobile_stock_management', schema_name=schema_name)
    if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
        return redirect('mobile_stock_management', schema_name=schema_name)
    return redirect('stock_management', schema_name=schema_name)
