import logging

from django.dispatch import receiver
from django_tenants.signals import post_schema_sync
from django_tenants.utils import schema_context
from django.contrib.auth import get_user_model
from axis_saas.models import SchoolClient
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)

@receiver(post_schema_sync)
def provision_secure_tenant_admin(sender, tenant, **kwargs):
    if tenant.schema_name == 'public':
        return

    User = get_user_model()
    
    u_name = tenant.admin_username
    u_pass = tenant.admin_password
    u_email = f"{u_name}@{tenant.schema_name}.com"
    
    if not u_name or not u_pass:
        return

    raw_pw = getattr(tenant, '_raw_password', None)
    if not raw_pw:
        logger.warning('Raw password not available for %s; cannot provision superuser.', tenant.schema_name)
        return

    with schema_context(tenant.schema_name):
        if not User.objects.filter(username=u_name).exists():
            User.objects.create_superuser(
                username=u_name,
                email=u_email,
                password=raw_pw
            )
            logger.info("Operational superuser '%s' provisioned into tenant schema '%s'.", u_name, tenant.schema_name)
@receiver(post_save, sender=SchoolClient)
def sync_tenant_admin_password(sender, instance, created, **kwargs):
    if instance.schema_name == 'public' or created:
        return
        
    u_name = instance.admin_username
    raw_pw = getattr(instance, '_raw_password', None)
    if u_name and raw_pw:
        with schema_context(instance.schema_name):
            User = get_user_model()
            user = User.objects.filter(username=u_name).first()
            if user:
                user.set_password(raw_pw)
                user.save()
                logger.info("Password synchronized for '%s' in schema '%s'.", u_name, instance.schema_name)
    elif u_name and not raw_pw:
        logger.warning('Raw password not available for %s; cannot sync password.', instance.schema_name)

# ========== CACHE INVALIDATION SIGNALS ==========
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Student, PaymentTransaction, FeeRecord
from .views.helpers import invalidate_tenant_cache
from django.db import connection

@receiver(post_save, sender=PaymentTransaction)
@receiver(post_delete, sender=PaymentTransaction)
def clear_cache_on_payment(sender, instance, **kwargs):
    schema_name = connection.schema_name
    if schema_name != 'public':
        invalidate_tenant_cache(schema_name, 'dashboard_stats')
        invalidate_tenant_cache(schema_name, 'defaulters_stats')

@receiver(post_save, sender=Student)
@receiver(post_delete, sender=Student)
def clear_cache_on_student(sender, instance, **kwargs):
    schema_name = connection.schema_name
    if schema_name != 'public':
        invalidate_tenant_cache(schema_name, 'dashboard_stats')
        invalidate_tenant_cache(schema_name, 'student_list_stats')

@receiver(post_save, sender=FeeRecord)
@receiver(post_delete, sender=FeeRecord)
def clear_cache_on_feerecord(sender, instance, **kwargs):
    schema_name = connection.schema_name
    if schema_name != 'public':
        invalidate_tenant_cache(schema_name, 'dashboard_stats')
        invalidate_tenant_cache(schema_name, 'defaulters_stats')
        invalidate_tenant_cache(schema_name, 'vouchers_stats')


# ========== TIMETABLE_OPTIMISTIC_LOCK_V1: signal-driven reconcile ==========
#
# Previously _reconcile_timetables() ran synchronously on every GET of
# /timetable/periods/, which is wasteful when nothing has changed.
#
# We now fire it on the commit of any transaction that saves or deletes
# a DaySchedule row. To keep a single view (which may save N rows) from
# triggering N reconciles, the callback is debounced per-request using
# thread-local state. Since the process is synchronous, one thread ==
# one request, so the set is safe.
import threading as _tt_lock_threading

_tt_lock_state = _tt_lock_threading.local()


def _tt_lock_schedule_reconcile(schema_name):
    if not schema_name or schema_name == 'public':
        return
    pending = getattr(_tt_lock_state, 'schemas', None)
    if pending is None:
        pending = set()
        _tt_lock_state.schemas = pending
    if schema_name in pending:
        return
    pending.add(schema_name)

    def _run():
        # Clear the debounce flag first, then run.
        pending.discard(schema_name)
        try:
            from axis_saas.views.periods import _reconcile_timetables
            _reconcile_timetables(schema_name)
        except Exception as exc:
            logger.warning(
                'TIMETABLE_OPTIMISTIC_LOCK_V1: reconcile failed for '
                'schema %s: %s', schema_name, exc,
            )
        # ASSIGN_TEACHERS_HARDENING_V3: after the timetable days are
        # recomputed, also clean up orphan PeriodTeacherAssignment
        # rows. V2 only triggered this from periods.api_add_bunch, so
        # an admin editing the calendar via api_save_day_schedules
        # (which changes DaySchedule.periods from 8 to 6, say) left
        # rows for periods 7 and 8 stranded. Those then showed up as
        # false conflicts in the busy map and as phantom absent
        # periods in api_get_todays_leave.
        try:
            from axis_saas.views.assign_teachers import (
                _reconcile_period_teacher_assignments,
            )
            _deleted = _reconcile_period_teacher_assignments(schema_name)
            if _deleted:
                logger.info(
                    'ASSIGN_TEACHERS_HARDENING_V3: reconcile removed '
                    '%s orphan PeriodTeacherAssignment row(s) in '
                    'schema %s', _deleted, schema_name,
                )
        except Exception as exc:
            logger.warning(
                'ASSIGN_TEACHERS_HARDENING_V3: PeriodTeacherAssignment '
                'reconcile failed for schema %s: %s', schema_name, exc,
            )

    from django.db import transaction as _tt_lock_tx
    _tt_lock_tx.on_commit(_run)


from axis_saas.models import DaySchedule as _TT_LOCK_DaySchedule


@receiver(post_save, sender=_TT_LOCK_DaySchedule)
@receiver(post_delete, sender=_TT_LOCK_DaySchedule)
def _tt_lock_on_dayschedule_change(sender, instance, **kwargs):
    _tt_lock_schedule_reconcile(connection.schema_name)


# ========== TIMETABLE_FK_REFACTOR_V1: label-change signal removed =====
# The rename no longer needs a cascade: DaySchedule.label and
# PeriodsTimetable.label are now ForeignKeys, so reads follow
# ScheduleLabel.name automatically. ScheduleLabel deletion is guarded
# by on_delete=PROTECT, so no post_delete hook is needed either.


# ========== LEAVE_MANAGEMENT_HARDENING_V4_1 ===========================
# _working_days_set() in views/leave_management.py caches the tenant's
# WeeklyHoliday set in Redis, keyed by (schema_name, today). If an
# admin edits WeeklyHoliday mid-day, that cache entry must be
# invalidated, or the quota counters keep using yesterday's working-
# day set until the key self-expires at midnight.
#
# Doing the invalidation via signals means we do NOT have to sprinkle
# cache-clear calls into the timetable views (api_add_holiday,
# api_update_holiday, api_delete_holiday). Any ORM save or delete of
# a WeeklyHoliday row — from a view, from the admin, from a shell, or
# from a test — triggers the invalidation.
from axis_saas.models import WeeklyHoliday as _LMV41_WeeklyHoliday


def _lmv41_clear_working_days_cache(schema_name):
    if not schema_name or schema_name == 'public':
        return
    try:
        from django.core.cache import cache as _lmv41_cache
        from django.utils import timezone as _lmv41_tz
        _lmv41_cache.delete(
            'leave.working_days:'
            + schema_name + ':'
            + _lmv41_tz.localdate().isoformat()
        )
    except Exception:
        # Cache backend may be down; the key self-expires at midnight
        # anyway, so a failed delete is not fatal.
        pass


@receiver(post_save, sender=_LMV41_WeeklyHoliday)
@receiver(post_delete, sender=_LMV41_WeeklyHoliday)
def _lmv41_on_weekly_holiday_change(sender, instance, **kwargs):
    _lmv41_clear_working_days_cache(connection.schema_name)
# ===== END LEAVE_MANAGEMENT_HARDENING_V4_1 ===========================


# ===== ATTENDANCE_PRODUCTION_V2 SIGNALS =============================
# Auto-mark 'excused' attendance for approved StudentLeave rows, and
# keep StaffAttendance in sync when a staff LeaveRequest is approved.
# ====================================================================

from axis_saas.models import StudentLeave as _AP2_StudentLeave
from axis_saas.models import StudentAttendance as _AP2_StudentAttendance
from axis_saas.models import AttendanceAuditLog as _AP2_AuditLog


def _ap2_backfill_student_leave_dates(leave):
    """For each date covered by an approved StudentLeave, upsert an
    'excused' full-day StudentAttendance row (period_order=NULL)."""
    if leave.status != 'approved':
        return
    if not leave.student_id or not leave.start_date or not leave.end_date:
        return
    try:
        from datetime import timedelta as _td
        day = leave.start_date
        while day <= leave.end_date:
            # Skip weekly Sunday? Leave that decision to admin — we mark
            # excused every covered date so teachers don't have to think.
            exists = _AP2_StudentAttendance.objects.filter(
                student_id=leave.student_id, date=day, period_order__isnull=True,
            ).first()
            if exists:
                if exists.status != 'excused':
                    old = exists.status
                    exists.status = 'excused'
                    exists.source = 'auto_leave'
                    exists.remarks = (exists.remarks or '') + ' [auto: student leave]'
                    exists.save(update_fields=['status', 'source', 'remarks', 'updated_at'])
                    try:
                        _AP2_AuditLog.objects.create(
                            attendance=exists,
                            student_id_snapshot=exists.student_id,
                            date_snapshot=exists.date,
                            action='update',
                            old_status=old, new_status='excused',
                            changed_by_name='system:auto_leave',
                            reason='student leave approved',
                        )
                    except Exception:
                        pass
            else:
                try:
                    _cls = None
                    from axis_saas.models import Student as _AP2_Student
                    _s = _AP2_Student.objects.filter(id=leave.student_id).first()
                    if _s and _s.school_class_id:
                        _cls = _s.school_class
                    if _cls is None:
                        day += _td(days=1); continue
                    _row = _AP2_StudentAttendance.objects.create(
                        student_id=leave.student_id,
                        school_class=_cls,
                        date=day,
                        period_order=None,
                        status='excused',
                        source='auto_leave',
                        remarks='auto: student leave',
                    )
                    try:
                        _AP2_AuditLog.objects.create(
                            attendance=_row,
                            student_id_snapshot=_row.student_id,
                            date_snapshot=_row.date,
                            action='create',
                            new_status='excused',
                            changed_by_name='system:auto_leave',
                            reason='student leave approved',
                        )
                    except Exception:
                        pass
                except Exception:
                    pass
            day += _td(days=1)
    except Exception:
        pass


@receiver(post_save, sender=_AP2_StudentLeave)
def _ap2_on_student_leave_save(sender, instance, **kwargs):
    _ap2_backfill_student_leave_dates(instance)


# Staff LeaveRequest approval -> StaffAttendance on_leave
from axis_saas.models import LeaveRequest as _AP2_LeaveRequest
from axis_saas.models import StaffAttendance as _AP2_StaffAttendance


def _ap2_sync_staff_attendance_from_leave(leave):
    if leave.status != 'approved' or not leave.staff_id:
        return
    try:
        from datetime import timedelta as _td
        day = leave.start_date
        while day <= leave.end_date:
            _AP2_StaffAttendance.objects.update_or_create(
                staff_id=leave.staff_id,
                date=day,
                defaults={
                    'status': 'on_leave',
                    'source': 'auto_leave',
                    'remarks': (leave.title or '')[:200],
                },
            )
            day += _td(days=1)
    except Exception:
        pass


@receiver(post_save, sender=_AP2_LeaveRequest)
def _ap2_on_leave_request_save(sender, instance, **kwargs):
    _ap2_sync_staff_attendance_from_leave(instance)


# ===== END ATTENDANCE_PRODUCTION_V2 SIGNALS =========================

