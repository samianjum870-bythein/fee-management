"""AXIS views - Leave Management (staff portal / mobile side).

Filename intentionally kept as specified by the project owner
("leave_managemetn"), do not rename.
"""
import json
import logging
from calendar import monthrange
from datetime import date, datetime, timedelta

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import Staff, LeaveRequest, LeavePolicy, ClassSubject
from .staff_portal import require_staff_login, require_staff_feature

logger = logging.getLogger(__name__)


# ----------------------------------------------------------- helpers ----
def _get_or_create_policy():
    policy, _ = LeavePolicy.objects.get_or_create(pk=1)
    return policy


def _serialize_staff_leave(lv):
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


def _month_used_days(staff, ref_date, exclude_id=None):
    first_day = ref_date.replace(day=1)
    last_day = ref_date.replace(day=monthrange(ref_date.year, ref_date.month)[1])
    qs = LeaveRequest.objects.filter(
        staff=staff,
        start_date__lte=last_day,
        end_date__gte=first_day,
    ).exclude(status__in=['rejected', 'cancelled'])
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    used = set()
    for lv in qs:
        lo = max(lv.start_date, first_day)
        hi = min(lv.end_date, last_day)
        for n in range(lo.toordinal(), hi.toordinal() + 1):
            used.add(n)
    return len(used)


def _week_used_days(staff, ref_date, exclude_id=None):
    week_start = ref_date - timedelta(days=ref_date.weekday())
    week_end = week_start + timedelta(days=6)
    qs = LeaveRequest.objects.filter(
        staff=staff,
        start_date__lte=week_end,
        end_date__gte=week_start,
    ).exclude(status__in=['rejected', 'cancelled'])
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    used = set()
    for lv in qs:
        lo = max(lv.start_date, week_start)
        hi = min(lv.end_date, week_end)
        for n in range(lo.toordinal(), hi.toordinal() + 1):
            used.add(n)
    return len(used)


def _validate_request(staff, start_date, end_date, policy):
    errors = []
    if start_date > end_date:
        errors.append('Start date must be before or on end date.')
        return errors

    if not policy.allow_backdated and start_date < date.today():
        errors.append('You cannot apply for a leave starting in the past.')

    total_days = (end_date - start_date).days + 1
    if total_days > policy.max_consecutive_days:
        errors.append(
            f'Maximum consecutive days allowed is '
            f'{policy.max_consecutive_days}.'
        )

    overlap = LeaveRequest.objects.filter(
        staff=staff,
        start_date__lte=end_date,
        end_date__gte=start_date,
    ).exclude(status__in=['rejected', 'cancelled'])
    if overlap.exists():
        errors.append('This leave overlaps with an existing leave request.')

    used_month = _month_used_days(staff, start_date)
    first_day = start_date.replace(day=1)
    last_day = start_date.replace(day=monthrange(start_date.year, start_date.month)[1])
    new_month_days = set()
    lo = max(start_date, first_day)
    hi = min(end_date, last_day)
    for n in range(lo.toordinal(), hi.toordinal() + 1):
        new_month_days.add(n)
    if used_month + len(new_month_days) > policy.max_leaves_per_month:
        errors.append(
            f'Monthly limit is {policy.max_leaves_per_month} day(s). '
            f'You have used {used_month} this month.'
        )

    used_week = _week_used_days(staff, start_date)
    week_start = start_date - timedelta(days=start_date.weekday())
    week_end = week_start + timedelta(days=6)
    new_week_days = set()
    lo = max(start_date, week_start)
    hi = min(end_date, week_end)
    for n in range(lo.toordinal(), hi.toordinal() + 1):
        new_week_days.add(n)
    if used_week + len(new_week_days) > policy.max_leaves_per_week:
        errors.append(
            f'Weekly limit is {policy.max_leaves_per_week} day(s). '
            f'You have used {used_week} this week.'
        )

    return errors


# ------------------------------------------------------------- views ----
@require_staff_login
@require_staff_feature('staff_leave_management')
def staff_leave_management(request):
    schema_name = request.session['staff_schema_name']
    staff_id = request.session['staff_id']
    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=staff_id).first()
        if not staff:
            from django.shortcuts import redirect
            return redirect('staff_login')
        policy = _get_or_create_policy()
        today = date.today()
        used_month = _month_used_days(staff, today)
        used_week = _week_used_days(staff, today)
        remaining_month = max(0, policy.max_leaves_per_month - used_month)
        remaining_week = max(0, policy.max_leaves_per_week - used_week)

        context = {
            'staff': staff,
            'policy': {
                'max_leaves_per_month': policy.max_leaves_per_month,
                'max_leaves_per_week': policy.max_leaves_per_week,
                'max_consecutive_days': policy.max_consecutive_days,
                'allow_backdated': policy.allow_backdated,
            },
            'used_month': used_month,
            'used_week': used_week,
            'remaining_month': remaining_month,
            'remaining_week': remaining_week,
            'today': today.isoformat(),
            'leave_type_choices': LeaveRequest.LEAVE_TYPE_CHOICES,
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
            return JsonResponse({'ok': False, 'error': 'Staff not found'}, status=404)
        policy = _get_or_create_policy()
        today = date.today()
        used_month = _month_used_days(staff, today)
        used_week = _week_used_days(staff, today)
        return JsonResponse({
            'ok': True,
            'max_leaves_per_month': policy.max_leaves_per_month,
            'max_leaves_per_week': policy.max_leaves_per_week,
            'max_consecutive_days': policy.max_consecutive_days,
            'allow_backdated': policy.allow_backdated,
            'used_month': used_month,
            'used_week': used_week,
            'remaining_month': max(0, policy.max_leaves_per_month - used_month),
            'remaining_week': max(0, policy.max_leaves_per_week - used_week),
        })


@csrf_exempt
@require_http_methods(['POST'])
@require_staff_login
@require_staff_feature('staff_leave_management')
def staff_leave_apply_api(request):
    schema_name = request.session['staff_schema_name']
    staff_id = request.session['staff_id']
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    title = (body.get('title') or '').strip()
    reason = (body.get('reason') or '').strip()
    leave_type = (body.get('leave_type') or 'casual').strip()
    start_str = body.get('start_date') or ''
    end_str = body.get('end_date') or ''

    if not title:
        return JsonResponse({'ok': False, 'error': 'Title is required.'}, status=400)
    if not reason:
        return JsonResponse({'ok': False, 'error': 'Reason is required.'}, status=400)
    if not start_str or not end_str:
        return JsonResponse({'ok': False, 'error': 'Start and end dates are required.'}, status=400)

    try:
        start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Dates must be YYYY-MM-DD.'}, status=400)

    valid_types = {t[0] for t in LeaveRequest.LEAVE_TYPE_CHOICES}
    if leave_type not in valid_types:
        leave_type = 'casual'

    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=staff_id).first()
        if not staff or staff.status != 'active':
            return JsonResponse({'ok': False, 'error': 'Staff account is not active.'}, status=403)

        policy = _get_or_create_policy()
        errors = _validate_request(staff, start_date, end_date, policy)
        if errors:
            return JsonResponse({'ok': False, 'errors': errors}, status=400)

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
        return JsonResponse({'ok': True, 'leave': _serialize_staff_leave(leave)})


@csrf_exempt
@require_http_methods(['POST'])
@require_staff_login
@require_staff_feature('staff_leave_management')
def staff_leave_cancel_api(request, leave_id):
    schema_name = request.session['staff_schema_name']
    staff_id = request.session['staff_id']
    with schema_context(schema_name):
        leave = LeaveRequest.objects.filter(id=leave_id, staff_id=staff_id).first()
        if not leave:
            return JsonResponse({'ok': False, 'error': 'Leave not found.'}, status=404)
        if leave.status not in ('pending', 'approved'):
            return JsonResponse({'ok': False, 'error': 'This leave cannot be cancelled.'}, status=400)
        leave.status = 'cancelled'
        leave.save(update_fields=['status'])
        return JsonResponse({'ok': True, 'leave': _serialize_staff_leave(leave)})
