"""AXIS views - Leave Management (admin side)."""
import json
import logging
from calendar import monthrange
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import Staff, LeaveRequest, LeavePolicy, ClassSubject
from .helpers import (
    get_tenant, is_mobile_user_agent, require_school_feature, require_tenant_type,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------- helpers ----
def _get_or_create_policy():
    policy, _ = LeavePolicy.objects.get_or_create(pk=1)
    return policy


def _staff_subject_names(staff):
    if not staff:
        return ''
    names = list(
        ClassSubject.objects
        .filter(teacher=staff, is_active=True)
        .values_list('subject__name', flat=True)
        .distinct()
    )
    return ', '.join(names) if names else ''


def _serialize_leave(leave):
    staff = leave.staff
    return {
        'id': leave.id,
        'staff_id': staff.id if staff else None,
        'staff_name': staff.full_name if staff else 'Unknown',
        'staff_job_title': staff.job_title if staff else '',
        'staff_email': (staff.email or '') if staff else '',
        'staff_phone': (staff.phone or '') if staff else '',
        'staff_photo_url': (staff.photo.url if staff and staff.photo else ''),
        'subjects': _staff_subject_names(staff),
        'leave_type': leave.leave_type,
        'leave_type_display': leave.get_leave_type_display(),
        'title': leave.title,
        'reason': leave.reason,
        'start_date': leave.start_date.isoformat(),
        'end_date': leave.end_date.isoformat(),
        'total_days': leave.total_days,
        'status': leave.status,
        'status_display': leave.get_status_display(),
        'reviewed_by': leave.reviewed_by,
        'reviewed_at': leave.reviewed_at.isoformat() if leave.reviewed_at else '',
        'admin_remarks': leave.admin_remarks,
        'created_at': leave.created_at.isoformat(),
    }


def _validate_leave_dates(start_date, end_date, policy, staff, exclude_id=None):
    """Return list of validation error strings (empty if valid)."""
    errors = []
    if start_date > end_date:
        errors.append('Start date must be on or before end date.')
        return errors

    total_days = (end_date - start_date).days + 1
    if total_days > policy.max_consecutive_days:
        errors.append(
            f'Maximum consecutive days allowed is '
            f'{policy.max_consecutive_days}.'
        )

    # Overlap with existing non-rejected leaves
    overlap = LeaveRequest.objects.filter(
        staff=staff,
        start_date__lte=end_date,
        end_date__gte=start_date,
    ).exclude(status__in=['rejected', 'cancelled'])
    if exclude_id:
        overlap = overlap.exclude(id=exclude_id)
    if overlap.exists():
        errors.append('This leave overlaps with an existing leave request.')

    # Monthly quota (unique days in that month)
    first_day = start_date.replace(day=1)
    last_day = start_date.replace(day=monthrange(start_date.year, start_date.month)[1])
    existing_month = LeaveRequest.objects.filter(
        staff=staff,
        start_date__lte=last_day,
        end_date__gte=first_day,
    ).exclude(status__in=['rejected', 'cancelled'])
    if exclude_id:
        existing_month = existing_month.exclude(id=exclude_id)

    days_covered = set()
    for lv in existing_month:
        lo = max(lv.start_date, first_day)
        hi = min(lv.end_date, last_day)
        for n in range(lo.toordinal(), hi.toordinal() + 1):
            days_covered.add(n)
    lo = max(start_date, first_day)
    hi = min(end_date, last_day)
    for n in range(lo.toordinal(), hi.toordinal() + 1):
        days_covered.add(n)
    if len(days_covered) > policy.max_leaves_per_month:
        errors.append(
            f'Monthly limit is {policy.max_leaves_per_month} day(s). '
            f'This request would use {len(days_covered)} day(s).'
        )

    # Weekly quota
    week_start = start_date - timedelta(days=start_date.weekday())
    week_end = week_start + timedelta(days=6)
    existing_week = LeaveRequest.objects.filter(
        staff=staff,
        start_date__lte=week_end,
        end_date__gte=week_start,
    ).exclude(status__in=['rejected', 'cancelled'])
    if exclude_id:
        existing_week = existing_week.exclude(id=exclude_id)

    days_covered_w = set()
    for lv in existing_week:
        lo = max(lv.start_date, week_start)
        hi = min(lv.end_date, week_end)
        for n in range(lo.toordinal(), hi.toordinal() + 1):
            days_covered_w.add(n)
    lo = max(start_date, week_start)
    hi = min(end_date, week_end)
    for n in range(lo.toordinal(), hi.toordinal() + 1):
        days_covered_w.add(n)
    if len(days_covered_w) > policy.max_leaves_per_week:
        errors.append(
            f'Weekly limit is {policy.max_leaves_per_week} day(s). '
            f'This request would use {len(days_covered_w)} day(s).'
        )

    return errors


# ------------------------------------------------------------- views ----
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_management(request, schema_name):
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        policy = _get_or_create_policy()

        status_filter = (request.GET.get('status') or '').strip()
        search = (request.GET.get('q') or '').strip()
        leave_type_filter = (request.GET.get('leave_type') or '').strip()

        qs = LeaveRequest.objects.select_related('staff').order_by('-created_at')
        if status_filter:
            qs = qs.filter(status=status_filter)
        if leave_type_filter:
            qs = qs.filter(leave_type=leave_type_filter)
        if search:
            qs = qs.filter(
                Q(staff__full_name__icontains=search)
                | Q(title__icontains=search)
                | Q(reason__icontains=search)
            )

        leaves = [_serialize_leave(lv) for lv in qs[:500]]

        stats = {
            'total': LeaveRequest.objects.count(),
            'pending': LeaveRequest.objects.filter(status='pending').count(),
            'approved': LeaveRequest.objects.filter(status='approved').count(),
            'rejected': LeaveRequest.objects.filter(status='rejected').count(),
        }

        today = date.today()
        first_day = today.replace(day=1)
        last_day = today.replace(day=monthrange(today.year, today.month)[1])
        staff_summary = []
        for s in Staff.objects.filter(status='active').order_by('full_name'):
            month_qs = LeaveRequest.objects.filter(
                staff=s,
                start_date__lte=last_day,
                end_date__gte=first_day,
            ).exclude(status__in=['rejected', 'cancelled'])
            used_days = set()
            for lv in month_qs:
                lo = max(lv.start_date, first_day)
                hi = min(lv.end_date, last_day)
                for n in range(lo.toordinal(), hi.toordinal() + 1):
                    used_days.add(n)
            used = len(used_days)
            staff_summary.append({
                'id': s.id,
                'name': s.full_name,
                'job_title': s.job_title or '',
                'subjects': _staff_subject_names(s),
                'month_used': used,
                'month_remaining': max(0, policy.max_leaves_per_month - used),
                'total_requests': LeaveRequest.objects.filter(staff=s).count(),
                'pending_requests': LeaveRequest.objects.filter(
                    staff=s, status='pending'
                ).count(),
            })

        policy_data = {
            'max_leaves_per_month': policy.max_leaves_per_month,
            'max_leaves_per_week': policy.max_leaves_per_week,
            'max_consecutive_days': policy.max_consecutive_days,
            'allow_backdated': policy.allow_backdated,
        }

    context = {
        'tenant': tenant,
        'leaves_json': json.dumps(leaves),
        'staff_summary_json': json.dumps(staff_summary),
        'policy_json': json.dumps(policy_data),
        'stats': stats,
        'status_filter': status_filter,
        'leave_type_filter': leave_type_filter,
        'search_query': search,
        'status_choices': LeaveRequest.STATUS_CHOICES,
        'leave_type_choices': LeaveRequest.LEAVE_TYPE_CHOICES,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }
    response = render(request, 'tenant/leave_management.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_detail_api(request, schema_name, leave_id):
    with schema_context(schema_name):
        leave = get_object_or_404(LeaveRequest.objects.select_related('staff'), id=leave_id)
        return JsonResponse({'ok': True, 'leave': _serialize_leave(leave)})


@csrf_exempt
@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_approve(request, schema_name, leave_id):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        body = {}
    remarks = (body.get('remarks') or '').strip()
    with schema_context(schema_name):
        leave = get_object_or_404(LeaveRequest, id=leave_id)
        if leave.status != 'pending':
            return JsonResponse(
                {'ok': False, 'error': 'Only pending leaves can be approved.'},
                status=400,
            )
        leave.status = 'approved'
        leave.reviewed_by = request.session.get('school_admin_username', 'admin')
        leave.reviewed_at = timezone.now()
        leave.admin_remarks = remarks
        leave.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_remarks'])
        return JsonResponse({'ok': True, 'leave': _serialize_leave(leave)})


@csrf_exempt
@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_reject(request, schema_name, leave_id):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        body = {}
    remarks = (body.get('remarks') or '').strip()
    with schema_context(schema_name):
        leave = get_object_or_404(LeaveRequest, id=leave_id)
        if leave.status != 'pending':
            return JsonResponse(
                {'ok': False, 'error': 'Only pending leaves can be rejected.'},
                status=400,
            )
        leave.status = 'rejected'
        leave.reviewed_by = request.session.get('school_admin_username', 'admin')
        leave.reviewed_at = timezone.now()
        leave.admin_remarks = remarks
        leave.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_remarks'])
        return JsonResponse({'ok': True, 'leave': _serialize_leave(leave)})


@csrf_exempt
@require_http_methods(['POST'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_policy_save(request, schema_name):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    def _to_int(key, default, lo=0):
        try:
            v = int(body.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, v)

    with schema_context(schema_name):
        policy = _get_or_create_policy()
        policy.max_leaves_per_month = _to_int('max_leaves_per_month', policy.max_leaves_per_month, 1)
        policy.max_leaves_per_week = _to_int('max_leaves_per_week', policy.max_leaves_per_week, 1)
        policy.max_consecutive_days = _to_int('max_consecutive_days', policy.max_consecutive_days, 1)
        policy.allow_backdated = bool(body.get('allow_backdated', policy.allow_backdated))
        policy.save()
        return JsonResponse({
            'ok': True,
            'policy': {
                'max_leaves_per_month': policy.max_leaves_per_month,
                'max_leaves_per_week': policy.max_leaves_per_week,
                'max_consecutive_days': policy.max_consecutive_days,
                'allow_backdated': policy.allow_backdated,
            },
        })


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_staff_summary_api(request, schema_name, staff_id):
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, id=staff_id)
        policy = _get_or_create_policy()
        today = date.today()
        first_day = today.replace(day=1)
        last_day = today.replace(day=monthrange(today.year, today.month)[1])
        month_qs = LeaveRequest.objects.filter(
            staff=staff,
            start_date__lte=last_day,
            end_date__gte=first_day,
        ).exclude(status__in=['rejected', 'cancelled'])
        used = set()
        for lv in month_qs:
            lo = max(lv.start_date, first_day)
            hi = min(lv.end_date, last_day)
            for n in range(lo.toordinal(), hi.toordinal() + 1):
                used.add(n)
        return JsonResponse({
            'ok': True,
            'staff_id': staff.id,
            'staff_name': staff.full_name,
            'month_used': len(used),
            'month_limit': policy.max_leaves_per_month,
            'month_remaining': max(0, policy.max_leaves_per_month - len(used)),
            'week_limit': policy.max_leaves_per_week,
            'max_consecutive_days': policy.max_consecutive_days,
        })
