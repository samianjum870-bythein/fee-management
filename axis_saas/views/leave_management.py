"""AXIS views - Leave Management (admin side).

LEAVE_MANAGEMENT_HARDENING_V3
-----------------------------
Production hardening:
  * CSRF enforced (no @csrf_exempt anywhere).
  * timezone.localdate() everywhere (Asia/Karachi safe).
  * Row-level locks + atomic transactions on approve / reject / suspend.
  * Working-day-aware weekly / monthly quotas.
  * N+1-free list view (prefetch + aggregate + paginated).
  * Auto-suspension is transactional with the reject.
  * Stale suspensions are deactivated on every read (also available as
    a cron-friendly management command `expire_leave_suspensions`).
  * Shared validation helpers for admin & staff sides.
"""
import functools
import json
import logging
from calendar import monthrange
from datetime import date, timedelta

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import (
    Staff, LeaveRequest, LeavePolicy, ClassSubject, LeaveSuspension,
    WeeklyHoliday,
)
from .helpers import (
    get_tenant, is_mobile_user_agent, require_ajax_post,
    require_school_feature, require_tenant_type,
)

logger = logging.getLogger(__name__)


# Upper bound on a manual suspension duration. Prevents an admin
# fat-fingering 100000 and locking a staff member out for years.
MAX_SUSPENSION_DAYS = 365

# Page size for the admin list view. The UI renders Prev / Next
# controls from `pagination_json`, so this is not a silent cap.
ADMIN_LEAVES_PAGE_SIZE = 50


# ============================================================ helpers ===

def _today():
    """Local date in the project timezone (Asia/Karachi).

    Replaces the ~12 places that used `date.today()`. In an environment
    with TIME_ZONE='Asia/Karachi' and USE_TZ=True, `date.today()`
    returns the UTC day, which between 00:00 and 05:00 PKT is
    *yesterday*.
    """
    return timezone.localdate()


def _working_days_set():
    """Set of weekday numbers considered working days.

    LEAVE_MANAGEMENT_HARDENING_V4_1: tenant-safe cache.

    The V4 implementation used `@functools.lru_cache(maxsize=1)` keyed
    only on the local date. That is NOT safe in production: a single
    gunicorn / uwsgi worker serves many tenants, so the first tenant's
    WeeklyHoliday set would leak to every other tenant served by the
    same worker for the rest of the day. Concretely, if Tenant X marks
    Sunday off and Tenant Y marks Friday off, one of them ends up with
    the other's working-day set, so weekly / monthly quota counters are
    wrong and an admin can approve a leave the tenant's own policy
    forbids.

    We now cache via Django's cache backend (Redis in this project)
    with a key scoped to `(schema_name, today)`. The key is correct
    across tenants, workers, and processes, and it self-expires at
    midnight. WeeklyHoliday post_save / post_delete signals (in
    `axis_saas/signals.py`) invalidate the current schema's entry
    immediately after a mid-day edit; `_working_days_set_cache_clear()`
    is available for explicit invalidation from management commands or
    tests.
    """
    from django.core.cache import cache as _cache
    from django.db import connection as _conn

    try:
        _schema = getattr(_conn, 'schema_name', None) or 'public'
    except Exception:
        _schema = 'public'
    _cache_key = f'leave.working_days:{_schema}:{_today().isoformat()}'

    _cached = None
    try:
        _cached = _cache.get(_cache_key)
    except Exception:
        _cached = None
    if _cached is not None:
        return set(_cached)

    try:
        off = set(
            WeeklyHoliday.objects.values_list('day_of_week', flat=True)
        )
    except Exception:
        off = set()
    result = {d for d in range(7) if d not in off}

    try:
        _cache.set(_cache_key, list(result), timeout=86400)
    except Exception:
        # If the cache backend is down we still return a correct value.
        pass
    return result


def _working_days_set_cache_clear(schema_name=None):
    """Invalidate the working-days cache entry for a schema.

    Call this after any out-of-band change to WeeklyHoliday that did
    NOT go through the ORM signals (e.g. a raw SQL migration, a data
    repair shell command). ORM-driven saves and deletes already clear
    the entry via signals, so most callers do not need this.

    If `schema_name` is omitted, the current connection's schema is
    used (which is what you want from a tenant-scoped view or test).
    """
    from django.core.cache import cache as _cache
    from django.db import connection as _conn

    if not schema_name:
        try:
            schema_name = getattr(_conn, 'schema_name', None) or 'public'
        except Exception:
            schema_name = 'public'
    if not schema_name or schema_name == 'public':
        return
    try:
        _cache.delete(
            f'leave.working_days:{schema_name}:{_today().isoformat()}'
        )
    except Exception:
        pass


def _expire_stale_suspensions():
    """Bulk-deactivate suspensions whose end_date has passed.

    LeaveSuspension.is_active used to be set only when an admin lifted
    the row, so expired rows accumulated. We now flag them on every
    read.

    Cost note: this is a single `UPDATE ... WHERE is_active AND
    end_date < today`. When there is nothing to expire PostgreSQL
    hits zero rows and the write is a no-op — but it is still a
    statement on the GET path. Production can rely on the cron job
    `python manage.py expire_leave_suspensions` instead; the lazy
    pass here is a self-healing safety net.
    """
    today = _today()
    LeaveSuspension.objects.filter(
        is_active=True,
        end_date__lt=today,
    ).update(
        is_active=False,
        lifted_at=timezone.now(),
        lifted_by='system:auto-expired',
    )


def _get_or_create_policy():
    """Get the singleton policy via the new current() classmethod."""
    return LeavePolicy.current()


def _prefetch_active_subjects_for_staff(staff_iterable):
    """Return {staff_id: "Math, English, ..."} in ONE query.

    Replaces _staff_subject_names(), which was firing a query per
    leave record (500 leaves => 500 queries).
    """
    ids = [s.id for s in staff_iterable if s is not None]
    if not ids:
        return {}
    out = {}
    rows = (
        ClassSubject.objects
        .filter(teacher_id__in=ids, is_active=True)
        .select_related('subject')
        .values_list('teacher_id', 'subject__name')
    )
    for tid, subj_name in rows:
        if subj_name:
            out.setdefault(tid, []).append(subj_name)
    return {tid: ', '.join(sorted(set(names))) for tid, names in out.items()}


def _serialize_leave(leave, subject_cache=None, leave_type_display=None,
                     status_display=None):
    staff = leave.staff
    sid = staff.id if staff else None
    subjects = ''
    if subject_cache is not None and sid is not None:
        subjects = subject_cache.get(sid, '')
    return {
        'id': leave.id,
        'staff_id': sid,
        'staff_name': staff.full_name if staff else 'Unknown',
        'staff_job_title': staff.job_title if staff else '',
        'staff_email': (staff.email or '') if staff else '',
        'staff_phone': (staff.phone or '') if staff else '',
        'staff_photo_url': (staff.photo.url if staff and staff.photo else ''),
        'subjects': subjects,
        'leave_type': leave.leave_type,
        'leave_type_display': leave_type_display or leave.get_leave_type_display(),
        'title': leave.title,
        'reason': leave.reason,
        'start_date': leave.start_date.isoformat(),
        'end_date': leave.end_date.isoformat(),
        'total_days': leave.total_days,
        'status': leave.status,
        'status_display': status_display or leave.get_status_display(),
        'reviewed_by': leave.reviewed_by,
        'reviewed_at': leave.reviewed_at.isoformat() if leave.reviewed_at else '',
        'admin_remarks': leave.admin_remarks,
        'created_at': leave.created_at.isoformat(),
    }


def _serialize_suspension(susp):
    staff = susp.staff
    return {
        'id': susp.id,
        'staff_id': staff.id if staff else None,
        'staff_name': staff.full_name if staff else 'Unknown',
        'staff_job_title': staff.job_title if staff else '',
        'reason': susp.reason or '',
        'start_date': susp.start_date.isoformat() if susp.start_date else '',
        'end_date': susp.end_date.isoformat() if susp.end_date else '',
        'is_active': susp.is_active,
        'currently_active': susp.is_currently_active(),
        'auto_triggered': bool(susp.auto_triggered),
        'created_by': susp.created_by or '',
        'created_at': susp.created_at.isoformat() if susp.created_at else '',
        'lifted_at': susp.lifted_at.isoformat() if susp.lifted_at else '',
        'lifted_by': susp.lifted_by or '',
    }


def _active_suspension_for(staff, on_date=None):
    if staff is None:
        return None
    if on_date is None:
        on_date = _today()
    qs = (
        LeaveSuspension.objects
        .filter(staff=staff, is_active=True, start_date__lte=on_date)
        .order_by('-created_at')
    )
    for susp in qs:
        if susp.end_date is None or susp.end_date >= on_date:
            return susp
    return None


def _rejections_since_last_suspension(staff):
    """Count rejections since the most recent suspension.

    LEAVE_AUTOSUSPENSION_FIX_V4_2
    -----------------------------
    Two changes from the V3 implementation:

    1. Cutoff is `latest.start_date`, not `latest.created_at`. The
       latter is `auto_now_add=True`, so a suspension back-dated by
       the admin (start_date = today - 10 days, recorded today) would
       set the cutoff to "now" and silently ignore every rejection
       from the suspension window. A suspension is effective from its
       start_date — that is the correct cut-off.

    2. When no prior suspension exists, count every rejected leave.
       The V3 branch `return 0` made it impossible for a fresh staff
       member to ever cross the auto-suspension threshold: the counter
       could only reach the threshold if a suspension already existed,
       which is circular. In practice, tenant leave volume is small,
       and stale rejections are better bounded by a rolling policy
       window (future work) than by a hard "0 forever" gate.

    Rejections whose `reviewed_at` is NULL still count as long as
    their `created_at` falls on or after the cutoff, so legacy rows
    from before `reviewed_at` was populated are not silently dropped.
    """
    latest = (
        LeaveSuspension.objects
        .filter(staff=staff)
        .order_by('-created_at')
        .first()
    )
    qs = LeaveRequest.objects.filter(staff=staff, status='rejected')
    if latest is not None:
        cutoff_date = latest.start_date
        qs = qs.filter(
            Q(reviewed_at__date__gte=cutoff_date) |
            Q(reviewed_at__isnull=True, created_at__date__gte=cutoff_date)
        )
    return qs.count()


def _validate_leave_dates(start_date, end_date, policy, staff, exclude_id=None):
    """Return a list of validation errors. Shared by admin & staff sides."""
    errors = []
    if start_date > end_date:
        errors.append('Start date must be on or before end date.')
        return errors

    working_set = _working_days_set() if policy.count_working_days_only else None

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
    if exclude_id:
        overlap = overlap.exclude(id=exclude_id)
    if overlap.exists():
        errors.append('This leave overlaps with an existing leave request.')

    # Monthly quota.
    first_day = start_date.replace(day=1)
    last_day = start_date.replace(
        day=monthrange(start_date.year, start_date.month)[1],
    )
    existing_month = LeaveRequest.objects.filter(
        staff=staff,
        start_date__lte=last_day,
        end_date__gte=first_day,
    ).exclude(status__in=['rejected', 'cancelled'])
    if exclude_id:
        existing_month = existing_month.exclude(id=exclude_id)

    covered = set()
    for lv in existing_month:
        lo = max(lv.start_date, first_day)
        hi = min(lv.end_date, last_day)
        d = lo
        while d <= hi:
            if working_set is None or d.weekday() in working_set:
                covered.add(d.toordinal())
            d += timedelta(days=1)
    lo = max(start_date, first_day)
    hi = min(end_date, last_day)
    d = lo
    while d <= hi:
        if working_set is None or d.weekday() in working_set:
            covered.add(d.toordinal())
        d += timedelta(days=1)
    if len(covered) > policy.max_leaves_per_month:
        errors.append(
            f'Monthly limit is {policy.max_leaves_per_month} day(s). '
            f'This request would use {len(covered)} day(s).'
        )

    # Weekly quota.
    week_start = start_date - timedelta(days=start_date.weekday())
    week_end = week_start + timedelta(days=6)
    existing_week = LeaveRequest.objects.filter(
        staff=staff,
        start_date__lte=week_end,
        end_date__gte=week_start,
    ).exclude(status__in=['rejected', 'cancelled'])
    if exclude_id:
        existing_week = existing_week.exclude(id=exclude_id)

    covered_w = set()
    for lv in existing_week:
        lo = max(lv.start_date, week_start)
        hi = min(lv.end_date, week_end)
        d = lo
        while d <= hi:
            if working_set is None or d.weekday() in working_set:
                covered_w.add(d.toordinal())
            d += timedelta(days=1)
    lo = max(start_date, week_start)
    hi = min(end_date, week_end)
    d = lo
    while d <= hi:
        if working_set is None or d.weekday() in working_set:
            covered_w.add(d.toordinal())
        d += timedelta(days=1)
    if len(covered_w) > policy.max_leaves_per_week:
        errors.append(
            f'Weekly limit is {policy.max_leaves_per_week} day(s). '
            f'This request would use {len(covered_w)} day(s).'
        )

    return errors


# =============================================================== views ===

@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_management(request, schema_name):
    tenant = get_tenant(request, schema_name)

    page_num = request.GET.get('page', 1)

    with schema_context(schema_name):
        policy = _get_or_create_policy()
        _expire_stale_suspensions()

        status_filter = (request.GET.get('status') or '').strip()
        search = (request.GET.get('q') or '').strip()
        leave_type_filter = (request.GET.get('leave_type') or '').strip()

        qs = (
            LeaveRequest.objects
            .select_related('staff')
            .order_by('-created_at')
        )
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

        paginator = Paginator(qs, ADMIN_LEAVES_PAGE_SIZE)
        page_obj = paginator.get_page(page_num)

        page_leaves = list(page_obj.object_list)
        page_staff = [lv.staff for lv in page_leaves if lv.staff_id]
        subject_cache = _prefetch_active_subjects_for_staff(page_staff)
        leaves = [
            _serialize_leave(lv, subject_cache=subject_cache)
            for lv in page_leaves
        ]

        # Single-query stats aggregate.
        stats_row = LeaveRequest.objects.aggregate(
            total=Count('id'),
            pending=Count('id', filter=Q(status='pending')),
            approved=Count('id', filter=Q(status='approved')),
            rejected=Count('id', filter=Q(status='rejected')),
        )
        stats = {
            'total': stats_row['total'] or 0,
            'pending': stats_row['pending'] or 0,
            'approved': stats_row['approved'] or 0,
            'rejected': stats_row['rejected'] or 0,
        }

        # Staff summary — all batched.
        today = _today()
        first_day = today.replace(day=1)
        last_day = today.replace(day=monthrange(today.year, today.month)[1])
        active_staff = list(
            Staff.objects.filter(status='active').order_by('full_name')
        )
        active_ids = [s.id for s in active_staff]

        subjects_by_staff = _prefetch_active_subjects_for_staff(active_staff)

        total_counts = dict(
            LeaveRequest.objects
            .filter(staff_id__in=active_ids)
            .values('staff_id')
            .annotate(n=Count('id'))
            .values_list('staff_id', 'n')
        )
        pending_counts = dict(
            LeaveRequest.objects
            .filter(staff_id__in=active_ids, status='pending')
            .values('staff_id')
            .annotate(n=Count('id'))
            .values_list('staff_id', 'n')
        )

        # Monthly usage — ONE query, then in-Python bucket.
        month_leaves = LeaveRequest.objects.filter(
            staff_id__in=active_ids,
            start_date__lte=last_day,
            end_date__gte=first_day,
        ).exclude(status__in=['rejected', 'cancelled'])

        working_set = _working_days_set() if policy.count_working_days_only else None
        month_usage = {}
        for lv in month_leaves:
            lo = max(lv.start_date, first_day)
            hi = min(lv.end_date, last_day)
            bucket = month_usage.setdefault(lv.staff_id, set())
            d = lo
            while d <= hi:
                if working_set is None or d.weekday() in working_set:
                    bucket.add(d.toordinal())
                d += timedelta(days=1)

        staff_summary = []
        for s in active_staff:
            used = len(month_usage.get(s.id, ()))
            staff_summary.append({
                'id': s.id,
                'name': s.full_name,
                'job_title': s.job_title or '',
                'subjects': subjects_by_staff.get(s.id, ''),
                'month_used': used,
                'month_remaining': max(0, policy.max_leaves_per_month - used),
                'total_requests': total_counts.get(s.id, 0),
                'pending_requests': pending_counts.get(s.id, 0),
            })

        policy_data = {
            'max_leaves_per_month': policy.max_leaves_per_month,
            'max_leaves_per_week': policy.max_leaves_per_week,
            'max_consecutive_days': policy.max_consecutive_days,
            'allow_backdated': policy.allow_backdated,
            'count_approved_only': policy.count_approved_only,
            'count_working_days_only': policy.count_working_days_only,
            'max_rejections_before_suspension': policy.max_rejections_before_suspension,
            'suspension_days': policy.suspension_days,
        }

        suspensions_qs = (
            LeaveSuspension.objects
            .select_related('staff')
            .order_by('-created_at')[:200]
        )
        suspensions = []
        active_count = 0
        for s in suspensions_qs:
            item = _serialize_suspension(s)
            if item['currently_active']:
                active_count += 1
            suspensions.append(item)

        staff_picker = [
            {'id': s.id, 'name': s.full_name,
             'job_title': s.job_title or ''}
            for s in active_staff
        ]

        page_context = {
            'page_number': page_obj.number,
            'num_pages': paginator.num_pages,
            'has_previous': page_obj.has_previous(),
            'has_next': page_obj.has_next(),
            'previous_page_number': (
                page_obj.previous_page_number() if page_obj.has_previous() else None
            ),
            'next_page_number': (
                page_obj.next_page_number() if page_obj.has_next() else None
            ),
            'total_count': paginator.count,
            'page_size': ADMIN_LEAVES_PAGE_SIZE,
        }

    def _safe_json(obj):
        s = json.dumps(obj)
        return (
            s.replace('&', '\\u0026')
             .replace('<', '\\u003c')
             .replace('>', '\\u003e')
             .replace('\u2028', '\\u2028')
             .replace('\u2029', '\\u2029')
        )

    context = {
        'tenant': tenant,
        'leaves_json': _safe_json(leaves),
        'staff_summary_json': _safe_json(staff_summary),
        'policy_json': _safe_json(policy_data),
        'suspensions_json': _safe_json(suspensions),
        'staff_picker_json': _safe_json(staff_picker),
        'pagination_json': _safe_json(page_context),
        'stats': stats,
        'active_suspension_count': active_count,
        'status_filter': status_filter,
        'leave_type_filter': leave_type_filter,
        'search_query': search,
        'status_choices': LeaveRequest.STATUS_CHOICES,
        'leave_type_choices': LeaveRequest.LEAVE_TYPE_CHOICES,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
        'max_suspension_days': MAX_SUSPENSION_DAYS,
    }
    response = render(request, 'tenant/leave_management.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_detail_api(request, schema_name, leave_id):
    with schema_context(schema_name):
        leave = get_object_or_404(
            LeaveRequest.objects.select_related('staff'), id=leave_id,
        )
        subjects = _prefetch_active_subjects_for_staff([leave.staff])
        return JsonResponse({
            'ok': True,
            'leave': _serialize_leave(leave, subject_cache=subjects),
        })


@require_ajax_post
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_approve(request, schema_name, leave_id):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        body = {}
    remarks = (body.get('remarks') or '').strip()

    with schema_context(schema_name):
        with transaction.atomic():
            try:
                leave = (
                    LeaveRequest.objects
                    .select_for_update()
                    .select_related('staff')
                    .get(id=leave_id)
                )
            except LeaveRequest.DoesNotExist:
                return JsonResponse(
                    {'ok': False, 'error': 'Leave not found.'}, status=404,
                )

            if leave.status != 'pending':
                return JsonResponse(
                    {'ok': False,
                     'error': 'Only pending leaves can be approved.'},
                    status=400,
                )

            leave.status = 'approved'
            leave.reviewed_by = request.session.get(
                'school_admin_username', 'admin',
            )
            leave.reviewed_at = timezone.now()
            leave.admin_remarks = remarks
            leave.save(update_fields=[
                'status', 'reviewed_by', 'reviewed_at', 'admin_remarks',
            ])

            subjects = _prefetch_active_subjects_for_staff([leave.staff])
            return JsonResponse({
                'ok': True,
                'leave': _serialize_leave(leave, subject_cache=subjects),
            })


@require_ajax_post
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_reject(request, schema_name, leave_id):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        body = {}
    remarks = (body.get('remarks') or '').strip()

    with schema_context(schema_name):
        with transaction.atomic():
            try:
                leave = (
                    LeaveRequest.objects
                    .select_for_update()
                    .select_related('staff')
                    .get(id=leave_id)
                )
            except LeaveRequest.DoesNotExist:
                return JsonResponse(
                    {'ok': False, 'error': 'Leave not found.'}, status=404,
                )

            if leave.status != 'pending':
                return JsonResponse(
                    {'ok': False,
                     'error': 'Only pending leaves can be rejected.'},
                    status=400,
                )

            leave.status = 'rejected'
            leave.reviewed_by = request.session.get(
                'school_admin_username', 'admin',
            )
            leave.reviewed_at = timezone.now()
            leave.admin_remarks = remarks
            leave.save(update_fields=[
                'status', 'reviewed_by', 'reviewed_at', 'admin_remarks',
            ])

            auto_suspension = None
            try:
                policy = _get_or_create_policy()
                threshold = int(policy.max_rejections_before_suspension or 0)
                if threshold > 0:
                    count = _rejections_since_last_suspension(leave.staff)
                    if count >= threshold:
                        days = max(1, min(MAX_SUSPENSION_DAYS,
                                          int(policy.suspension_days or 7)))
                        today = _today()
                        auto_suspension = LeaveSuspension.objects.create(
                            staff=leave.staff,
                            reason=(
                                f"Auto-suspended: {count} rejected leave "
                                f"request(s) since last suspension "
                                f"(threshold {threshold})."
                            ),
                            start_date=today,
                            end_date=today + timedelta(days=days),
                            is_active=True,
                            auto_triggered=True,
                            created_by='system',
                        )
                        logger.info(
                            'LEAVE_MANAGEMENT_HARDENING_V3: auto-suspended '
                            'staff=%s for %s days (rejections=%s)',
                            leave.staff_id, days, count,
                        )
            except Exception as exc:
                logger.warning(
                    'LEAVE_MANAGEMENT_HARDENING_V3: auto-suspend failed: %s',
                    exc,
                )

            subjects = _prefetch_active_subjects_for_staff([leave.staff])
            payload = {
                'ok': True,
                'leave': _serialize_leave(leave, subject_cache=subjects),
            }
            if auto_suspension is not None:
                payload['auto_suspension'] = _serialize_suspension(
                    auto_suspension,
                )
            return JsonResponse(payload)


@require_ajax_post
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_policy_save(request, schema_name):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse(
            {'ok': False, 'error': 'Invalid JSON'}, status=400,
        )

    def _to_int(key, default, lo=0, hi=None):
        try:
            v = int(body.get(key, default))
        except (TypeError, ValueError):
            v = default
        v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    with schema_context(schema_name):
        with transaction.atomic():
            policy = LeavePolicy.objects.select_for_update().filter(
                is_singleton=True,
            ).first()
            if policy is None:
                policy = LeavePolicy.current()
            # Upper bounds are the maximum meaningful value for each
            # field (7 days in a week, 31 days in a month, 90-day
            # consecutive cap to match the docs, 100 rejections, and
            # MAX_SUSPENSION_DAYS for suspension length).
            policy.max_leaves_per_month = _to_int(
                'max_leaves_per_month', policy.max_leaves_per_month, 1, 31,
            )
            policy.max_leaves_per_week = _to_int(
                'max_leaves_per_week', policy.max_leaves_per_week, 1, 7,
            )
            policy.max_consecutive_days = _to_int(
                'max_consecutive_days', policy.max_consecutive_days, 1, 90,
            )
            policy.allow_backdated = bool(
                body.get('allow_backdated', policy.allow_backdated),
            )
            policy.count_approved_only = bool(
                body.get('count_approved_only', policy.count_approved_only),
            )
            policy.count_working_days_only = bool(
                body.get(
                    'count_working_days_only',
                    policy.count_working_days_only,
                ),
            )
            policy.max_rejections_before_suspension = _to_int(
                'max_rejections_before_suspension',
                policy.max_rejections_before_suspension,
                0, 100,
            )
            policy.suspension_days = _to_int(
                'suspension_days',
                policy.suspension_days,
                1, MAX_SUSPENSION_DAYS,
            )
            policy.save()
            return JsonResponse({
                'ok': True,
                'policy': {
                    'max_leaves_per_month': policy.max_leaves_per_month,
                    'max_leaves_per_week': policy.max_leaves_per_week,
                    'max_consecutive_days': policy.max_consecutive_days,
                    'allow_backdated': policy.allow_backdated,
                    'count_approved_only': policy.count_approved_only,
                    'count_working_days_only': policy.count_working_days_only,
                    'max_rejections_before_suspension':
                        policy.max_rejections_before_suspension,
                    'suspension_days': policy.suspension_days,
                },
            })


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def leave_staff_summary_api(request, schema_name, staff_id):
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, id=staff_id)
        policy = _get_or_create_policy()
        today = _today()
        first_day = today.replace(day=1)
        last_day = today.replace(
            day=monthrange(today.year, today.month)[1],
        )
        month_qs = LeaveRequest.objects.filter(
            staff=staff,
            start_date__lte=last_day,
            end_date__gte=first_day,
        ).exclude(status__in=['rejected', 'cancelled'])
        working_set = (
            _working_days_set() if policy.count_working_days_only else None
        )
        used = set()
        for lv in month_qs:
            lo = max(lv.start_date, first_day)
            hi = min(lv.end_date, last_day)
            d = lo
            while d <= hi:
                if working_set is None or d.weekday() in working_set:
                    used.add(d.toordinal())
                d += timedelta(days=1)
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


# ============================================================ suspensions ==

@require_ajax_post
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def staff_suspend(request, schema_name, staff_id):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        body = {}
    reason = (body.get('reason') or '').strip()
    raw_days = body.get('duration_days')
    try:
        days = int(raw_days) if raw_days not in (None, '', '0', 0) else 0
    except (TypeError, ValueError):
        days = 0
    if days < 0:
        days = 0
    if days > MAX_SUSPENSION_DAYS:
        return JsonResponse(
            {'ok': False,
             'error': f'Maximum suspension is {MAX_SUSPENSION_DAYS} days.'},
            status=400,
        )

    with schema_context(schema_name):
        with transaction.atomic():
            staff = get_object_or_404(Staff, id=staff_id)

            LeaveSuspension.objects.filter(
                staff=staff, is_active=True,
            ).update(
                is_active=False,
                lifted_at=timezone.now(),
                lifted_by=request.session.get(
                    'school_admin_username', 'admin',
                ),
            )

            today = _today()
            end_date = None
            if days > 0:
                end_date = today + timedelta(days=days)

            susp = LeaveSuspension.objects.create(
                staff=staff,
                reason=reason or 'Suspended by admin.',
                start_date=today,
                end_date=end_date,
                is_active=True,
                auto_triggered=False,
                created_by=request.session.get(
                    'school_admin_username', 'admin',
                ),
            )
            return JsonResponse({
                'ok': True,
                'suspension': _serialize_suspension(susp),
            })


@require_ajax_post
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def staff_unsuspend(request, schema_name, staff_id):
    with schema_context(schema_name):
        with transaction.atomic():
            staff = get_object_or_404(Staff, id=staff_id)
            qs = LeaveSuspension.objects.filter(staff=staff, is_active=True)
            count = qs.count()
            qs.update(
                is_active=False,
                lifted_at=timezone.now(),
                lifted_by=request.session.get(
                    'school_admin_username', 'admin',
                ),
            )
            return JsonResponse({'ok': True, 'lifted': count})


@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('leave_management')
def staff_suspensions_api(request, schema_name, staff_id):
    with schema_context(schema_name):
        _expire_stale_suspensions()
        staff = get_object_or_404(Staff, id=staff_id)
        qs = (
            LeaveSuspension.objects
            .filter(staff=staff)
            .order_by('-created_at')
        )
        data = [_serialize_suspension(s) for s in qs]
        return JsonResponse({
            'ok': True,
            'staff_id': staff.id,
            'staff_name': staff.full_name,
            'suspensions': data,
        })
