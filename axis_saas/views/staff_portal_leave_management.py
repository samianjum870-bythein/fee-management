"""AXIS views — Leave Management (staff portal / mobile side).

LEAVE_MANAGEMENT_HARDENING_V3
-----------------------------
  * Filename typo corrected: staff_portal_leave_managemetn.py ->
    staff_portal_leave_management.py. The old file is kept as a shim
    that re-exports from here.
  * timezone.localdate() everywhere.
  * CSRF enforced (no @csrf_exempt).
  * Shared validation helpers imported from the admin module.
  * Working-day-aware quotas.
  * `min_date` context variable for the backdated-leave UI.
"""
import json
import logging
from calendar import monthrange
from datetime import datetime, timedelta

from django.http import JsonResponse
from django.shortcuts import render, redirect
from django_tenants.utils import schema_context

from ..models import (
    Staff, LeaveRequest, LeavePolicy, ClassSubject, LeaveSuspension,
)
from .staff_portal import require_staff_login, require_staff_feature
from .leave_management import (
    MAX_SUSPENSION_DAYS,  # noqa: F401  (re-exported for parity)
    _active_suspension_for,
    _get_or_create_policy,
    _today,
    _validate_leave_dates,
    _working_days_set,
)

logger = logging.getLogger(__name__)


def _serialize_staff_leave(lv):
    if lv is None:
        return None
    return {
        'id': lv.id,
        'leave_type': lv.leave_type,
        'leave_type_display': lv.get_leave_type_display(),
        'title': lv.title,
        'reason': lv.reason,
        'start_date': lv.start_date.isoformat(),
        'end_date': lv.end_date.isoformat(),
        'total_days': lv.total_days,
        'status': lv.status,
        'status_display': lv.get_status_display(),
        'admin_remarks': lv.admin_remarks,
        'reviewed_by': lv.reviewed_by,
        'reviewed_at': lv.reviewed_at.isoformat() if lv.reviewed_at else '',
        'created_at': lv.created_at.isoformat(),
    }


def _get_active_leave(staff, ref_date=None):
    if staff is None:
        return None
    if ref_date is None:
        ref_date = _today()
    return (
        LeaveRequest.objects
        .filter(
            staff=staff,
            status='approved',
            start_date__lte=ref_date,
            end_date__gte=ref_date,
        )
        .order_by('start_date', 'id')
        .first()
    )


def _leave_quota_queryset(staff, policy=None):
    qs = LeaveRequest.objects.filter(staff=staff)
    if policy is None or getattr(policy, 'count_approved_only', True):
        qs = qs.filter(status='approved')
    else:
        qs = qs.exclude(status__in=['rejected', 'cancelled'])
    return qs


def _count_unique_days(qs, window_start, window_end, working_set):
    used = set()
    for lv in qs:
        lo = max(lv.start_date, window_start)
        hi = min(lv.end_date, window_end)
        d = lo
        while d <= hi:
            if working_set is None or d.weekday() in working_set:
                used.add(d.toordinal())
            d += timedelta(days=1)
    return len(used)


def _month_used_days(staff, ref_date, exclude_id=None, policy=None):
    first_day = ref_date.replace(day=1)
    last_day = ref_date.replace(
        day=monthrange(ref_date.year, ref_date.month)[1],
    )
    qs = _leave_quota_queryset(staff, policy).filter(
        start_date__lte=last_day,
        end_date__gte=first_day,
    )
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    ws = _working_days_set() if (
        policy is not None and getattr(policy, 'count_working_days_only', False)
    ) else None
    return _count_unique_days(qs, first_day, last_day, ws)


def _week_used_days(staff, ref_date, exclude_id=None, policy=None):
    week_start = ref_date - timedelta(days=ref_date.weekday())
    week_end = week_start + timedelta(days=6)
    qs = _leave_quota_queryset(staff, policy).filter(
        start_date__lte=week_end,
        end_date__gte=week_start,
    )
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    ws = _working_days_set() if (
        policy is not None and getattr(policy, 'count_working_days_only', False)
    ) else None
    return _count_unique_days(qs, week_start, week_end, ws)


# --------------------------------------------------------------- views ---

@require_staff_login
@require_staff_feature('staff_leave_management')
def staff_leave_management(request):
    schema_name = request.session['staff_schema_name']
    staff_id = request.session['staff_id']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=staff_id).first()
        if not staff:
            return redirect('staff_login')
        policy = _get_or_create_policy()
        today = _today()
        used_month = _month_used_days(staff, today, policy=policy)
        used_week = _week_used_days(staff, today, policy=policy)
        remaining_month = max(0, policy.max_leaves_per_month - used_month)
        remaining_week = max(0, policy.max_leaves_per_week - used_week)
        active_leave = _get_active_leave(staff, today)
        suspension = _active_suspension_for(staff, today)

        context = {
            'staff': staff,
            'policy': {
                'max_leaves_per_month': policy.max_leaves_per_month,
                'max_leaves_per_week': policy.max_leaves_per_week,
                'max_consecutive_days': policy.max_consecutive_days,
                'allow_backdated': policy.allow_backdated,
                'count_approved_only': policy.count_approved_only,
                'count_working_days_only': policy.count_working_days_only,
            },
            'used_month': used_month,
            'used_week': used_week,
            'remaining_month': remaining_month,
            'remaining_week': remaining_week,
            'active_leave': active_leave,
            'active_suspension': suspension,
            'today': today.isoformat(),
            'leave_type_choices': LeaveRequest.LEAVE_TYPE_CHOICES,
            # (#10) The date inputs use `min_date` when the policy
            # forbids backdated leaves, and skip the `min` attribute
            # entirely when it allows them.
            'min_date': None if policy.allow_backdated else today.isoformat(),
        }
    response = render(request, 'mobile/staff/leave_management.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@require_staff_login
@require_staff_feature('staff_leave_management')
def staff_leave_history_api(request):
    schema_name = request.session['staff_schema_name']
    staff_id = request.session['staff_id']
    with schema_context(schema_name):
        qs = LeaveRequest.objects.filter(staff_id=staff_id).order_by('-created_at')
        data = [_serialize_staff_leave(lv) for lv in qs[:200]]
    return JsonResponse({'ok': True, 'leaves': data})


@require_staff_login
@require_staff_feature('staff_leave_management')
def staff_leave_policy_api(request):
    schema_name = request.session['staff_schema_name']
    staff_id = request.session['staff_id']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=staff_id).first()
        if not staff:
            return JsonResponse(
                {'ok': False, 'error': 'Staff not found'}, status=404,
            )
        policy = _get_or_create_policy()
        today = _today()
        used_month = _month_used_days(staff, today, policy=policy)
        used_week = _week_used_days(staff, today, policy=policy)
        active_leave = _get_active_leave(staff, today)
        suspension = _active_suspension_for(staff, today)

        suspension_payload = None
        if suspension is not None:
            suspension_payload = {
                'id': suspension.id,
                'reason': suspension.reason or '',
                'start_date': suspension.start_date.isoformat() if suspension.start_date else '',
                'end_date': suspension.end_date.isoformat() if suspension.end_date else '',
                'permanent': suspension.end_date is None,
            }
        return JsonResponse({
            'ok': True,
            'max_leaves_per_month': policy.max_leaves_per_month,
            'max_leaves_per_week': policy.max_leaves_per_week,
            'max_consecutive_days': policy.max_consecutive_days,
            'allow_backdated': policy.allow_backdated,
            'count_approved_only': policy.count_approved_only,
            'count_working_days_only': policy.count_working_days_only,
            'used_month': used_month,
            'used_week': used_week,
            'remaining_month': max(0, policy.max_leaves_per_month - used_month),
            'remaining_week': max(0, policy.max_leaves_per_week - used_week),
            'active_leave': _serialize_staff_leave(active_leave),
            'active_suspension': suspension_payload,
        })


@require_staff_login
@require_staff_feature('staff_leave_management')
def staff_leave_apply_api(request):
    if request.method != 'POST':
        return JsonResponse(
            {'ok': False, 'error': 'POST required.'}, status=405,
        )
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse(
            {'ok': False, 'error': 'AJAX request required.'}, status=400,
        )

    schema_name = request.session['staff_schema_name']
    staff_id = request.session['staff_id']
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse(
            {'ok': False, 'error': 'Invalid JSON'}, status=400,
        )

    title = (body.get('title') or '').strip()
    reason = (body.get('reason') or '').strip()
    leave_type = (body.get('leave_type') or 'casual').strip()
    start_str = body.get('start_date') or ''
    end_str = body.get('end_date') or ''

    if not title:
        return JsonResponse(
            {'ok': False, 'error': 'Title is required.'}, status=400,
        )
    if not reason:
        return JsonResponse(
            {'ok': False, 'error': 'Reason is required.'}, status=400,
        )
    if not start_str or not end_str:
        return JsonResponse(
            {'ok': False,
             'error': 'Start and end dates are required.'}, status=400,
        )

    try:
        start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse(
            {'ok': False, 'error': 'Dates must be YYYY-MM-DD.'}, status=400,
        )

    valid_types = {t[0] for t in LeaveRequest.LEAVE_TYPE_CHOICES}
    if leave_type not in valid_types:
        leave_type = 'casual'

    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=staff_id).first()
        if not staff or staff.status != 'active':
            return JsonResponse(
                {'ok': False, 'error': 'Staff account is not active.'},
                status=403,
            )

        policy = _get_or_create_policy()
        today = _today()

        suspension = _active_suspension_for(staff, today)
        if suspension is not None:
            if suspension.end_date:
                span = f"until {suspension.end_date.strftime('%d %b %Y')}"
            else:
                span = "with no end date (contact the admin to lift it)"
            reason_txt = (
                f" Reason: {suspension.reason}" if suspension.reason else ''
            )
            return JsonResponse(
                {'ok': False,
                 'errors': [
                     f"You are suspended from applying for leave {span}."
                     f"{reason_txt}"
                 ]},
                status=400,
            )

        active = _get_active_leave(staff, today)
        if active is not None:
            return JsonResponse(
                {'ok': False,
                 'errors': [
                     f"You are currently on an approved leave from "
                     f"{active.start_date.strftime('%d %b %Y')} to "
                     f"{active.end_date.strftime('%d %b %Y')} "
                     f"({active.total_days} day"
                     f"{'s' if active.total_days > 1 else ''}). You cannot "
                     f"apply for a new leave until this one ends."
                 ]},
                status=400,
            )

        if not policy.allow_backdated and start_date < today:
            return JsonResponse(
                {'ok': False,
                 'errors': ['You cannot apply for a leave starting in the past.']},
                status=400,
            )

        errors = _validate_leave_dates(
            start_date, end_date, policy, staff,
        )
        if errors:
            return JsonResponse(
                {'ok': False, 'errors': errors}, status=400,
            )

        total_days = (end_date - start_date).days + 1
        leave = LeaveRequest.objects.create(
            staff=staff,
            title=title[:200],
            reason=reason,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            total_days=total_days,
            status='pending',
        )
        return JsonResponse({
            'ok': True, 'leave': _serialize_staff_leave(leave),
        })


@require_staff_login
@require_staff_feature('staff_leave_management')
def staff_leave_cancel_api(request, leave_id):
    if request.method != 'POST':
        return JsonResponse(
            {'ok': False, 'error': 'POST required.'}, status=405,
        )
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse(
            {'ok': False, 'error': 'AJAX request required.'}, status=400,
        )
    schema_name = request.session['staff_schema_name']
    staff_id = request.session['staff_id']
    with schema_context(schema_name):
        leave = LeaveRequest.objects.filter(
            id=leave_id, staff_id=staff_id,
        ).first()
        if not leave:
            return JsonResponse(
                {'ok': False, 'error': 'Leave not found.'}, status=404,
            )
        if leave.status not in ('pending', 'approved'):
            return JsonResponse(
                {'ok': False,
                 'error': 'This leave cannot be cancelled.'},
                status=400,
            )
        leave.status = 'cancelled'
        leave.save(update_fields=['status', 'updated_at'])
        return JsonResponse({
            'ok': True, 'leave': _serialize_staff_leave(leave),
        })
