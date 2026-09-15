"""Leave Management hardening tests (LEAVE_MANAGEMENT_HARDENING_V3).

Covers:
  * @require_ajax_post CSRF/XHR enforcement.
  * timezone.localdate() via _today().
  * Row-lock prevents double-approve.
  * Auto-suspension returned inline with the reject.
  * Rejections-since-last-suspension counter.
  * Working-day-aware weekly / monthly quotas.
  * Existing suspension rows auto-expire.
  * Suspension-duration upper bound.
  * LeavePolicy singleton + working-days flag.

Run:
    python manage.py test axis_saas.tests.test_leave_management_v2
"""

import json
from datetime import date, timedelta
from unittest import mock

from django.test import RequestFactory, TestCase
from django_tenants.utils import schema_context

from axis_saas.models import (
    LeavePolicy, LeaveRequest, LeaveSuspension, SchoolClient,
    Staff, WeeklyHoliday,
)
from axis_saas.views.leave_management import (
    ADMIN_LEAVES_PAGE_SIZE,
    MAX_SUSPENSION_DAYS,
    _rejections_since_last_suspension,
    _today,
    _working_days_set,
    leave_approve,
    leave_reject,
    leave_policy_save,
    staff_suspend,
)


class BaseLeaveV3(TestCase):

    def setUp(self):
        # LEAVE_MANAGEMENT_HARDENING_V4_1: the working-days cache is
        # backed by Redis keyed on (schema_name, today). Between tests
        # in the same process the schema name and the date don't change,
        # so we must clear the cache or a previous test's WeeklyHoliday
        # configuration would leak into this one.
        from django.core.cache import cache as _test_cache
        _test_cache.clear()

        self.tenant = SchoolClient.objects.create(
            schema_name='leave-v3-test',
            name='Leave V3 Test School',
            admin_username='admin',
            admin_password='admin123',
            enabled_features={
                'desktop': ['leave_management', 'staff_management'],
                'mobile': [],
                'staff_portal': ['staff_leave_management', 'staff_dashboard'],
            },
        )
        with schema_context(self.tenant.schema_name):
            self.staff = Staff.objects.create(
                first_name='Ayesha',
                last_name='Khan',
                email='ayesha@v3.test',
                job_title='Math Teacher',
                department='teaching',
                phone='03001234567',
                role='teacher',
                status='active',
            )
            LeavePolicy.current()

    def _admin_request(self, method='POST', path='/', data=None,
                       xhr=True):
        factory = RequestFactory()
        if method.upper() == 'GET':
            request = factory.get(path)
        else:
            request = factory.post(
                path,
                data=json.dumps(data or {}),
                content_type='application/json',
            )
        if xhr:
            request.META['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        from django.contrib.sessions.backends.db import SessionStore
        request.session = SessionStore()
        request.session['school_admin_authenticated'] = True
        request.session['school_admin_schema'] = self.tenant.schema_name
        request.session['school_admin_username'] = 'admin'
        request.session.save()
        request.tenant = self.tenant
        return request

    def _make_leave(self, start, end, status='pending',
                    title='Test', reason='Reason'):
        with schema_context(self.tenant.schema_name):
            total = (end - start).days + 1
            return LeaveRequest.objects.create(
                staff=self.staff,
                leave_type='casual',
                title=title,
                reason=reason,
                start_date=start,
                end_date=end,
                total_days=total,
                status=status,
            )


class AjaxPostEnforcementTests(BaseLeaveV3):

    def test_approve_rejects_non_xhr_request(self):
        leave = self._make_leave(
            _today() + timedelta(days=5),
            _today() + timedelta(days=5),
        )
        request = self._admin_request(
            data={'remarks': 'ok'}, xhr=False,
        )
        response = leave_approve(
            request, self.tenant.schema_name, leave.id,
        )
        self.assertEqual(response.status_code, 400)

    def test_approve_rejects_get_request(self):
        leave = self._make_leave(
            _today() + timedelta(days=5),
            _today() + timedelta(days=5),
        )
        request = self._admin_request(method='GET')
        response = leave_approve(
            request, self.tenant.schema_name, leave.id,
        )
        self.assertEqual(response.status_code, 405)

    def test_approve_accepts_xhr_post(self):
        leave = self._make_leave(
            _today() + timedelta(days=5),
            _today() + timedelta(days=5),
        )
        request = self._admin_request(data={'remarks': 'ok'})
        response = leave_approve(
            request, self.tenant.schema_name, leave.id,
        )
        self.assertEqual(response.status_code, 200)
        with schema_context(self.tenant.schema_name):
            lv = LeaveRequest.objects.get(id=leave.id)
            self.assertEqual(lv.status, 'approved')


class RowLockTests(BaseLeaveV3):

    def test_second_approve_is_rejected(self):
        leave = self._make_leave(
            _today() + timedelta(days=5),
            _today() + timedelta(days=5),
        )
        r1 = leave_approve(
            self._admin_request(data={'remarks': 'first'}),
            self.tenant.schema_name, leave.id,
        )
        self.assertEqual(r1.status_code, 200)

        r2 = leave_approve(
            self._admin_request(data={'remarks': 'second'}),
            self.tenant.schema_name, leave.id,
        )
        self.assertEqual(r2.status_code, 400)


class RejectionCounterTests(BaseLeaveV3):

    def test_counts_all_rejections_when_no_prior_suspension(self):
        """LEAVE_AUTOSUSPENSION_FIX_V4_2: with no prior suspension,
        count every rejected leave.

        The V3 test (previously named
        ``test_zero_when_no_prior_suspension``) asserted 0 here, which
        made the auto-suspension feature unreachable for a fresh staff
        member: the counter could never reach the threshold because it
        always started at 0 and had no way to increment. The correct
        behaviour — asserted by
        ``test_reject_response_carries_auto_suspension`` — is that the
        very first rejection on a fresh staff member counts towards
        the threshold.
        """
        self._make_leave(
            _today() - timedelta(days=10),
            _today() - timedelta(days=10),
            status='rejected',
        )
        with schema_context(self.tenant.schema_name):
            count = _rejections_since_last_suspension(self.staff)
        self.assertEqual(count, 1)

    def test_counts_rejections_after_latest_suspension(self):
        today = _today()
        with schema_context(self.tenant.schema_name):
            LeaveSuspension.objects.create(
                staff=self.staff,
                start_date=today - timedelta(days=10),
                end_date=today - timedelta(days=3),
                is_active=False,
                created_by='admin',
            )
            old_rejected = LeaveRequest.objects.create(
                staff=self.staff, leave_type='casual', title='old',
                reason='r',
                start_date=today - timedelta(days=30),
                end_date=today - timedelta(days=30),
                total_days=1, status='rejected',
            )
            LeaveRequest.objects.filter(id=old_rejected.id).update(
                reviewed_at=today - timedelta(days=20),
            )
            new_rejected = LeaveRequest.objects.create(
                staff=self.staff, leave_type='casual', title='new',
                reason='r',
                start_date=today - timedelta(days=2),
                end_date=today - timedelta(days=2),
                total_days=1, status='rejected',
            )
            LeaveRequest.objects.filter(id=new_rejected.id).update(
                reviewed_at=today - timedelta(days=1),
            )
            count = _rejections_since_last_suspension(self.staff)
            self.assertEqual(count, 1)

    def test_handles_null_reviewed_at(self):
        today = _today()
        with schema_context(self.tenant.schema_name):
            LeaveSuspension.objects.create(
                staff=self.staff,
                start_date=today - timedelta(days=5),
                end_date=today - timedelta(days=3),
                is_active=False,
                created_by='admin',
            )
            LeaveRequest.objects.create(
                staff=self.staff, leave_type='casual', title='legacy',
                reason='r',
                start_date=today - timedelta(days=1),
                end_date=today - timedelta(days=1),
                total_days=1, status='rejected',
                reviewed_at=None,
            )
            count = _rejections_since_last_suspension(self.staff)
            self.assertEqual(count, 1)


class AutoSuspensionTests(BaseLeaveV3):

    def test_reject_response_carries_auto_suspension(self):
        today = _today()
        with schema_context(self.tenant.schema_name):
            policy = LeavePolicy.current()
            policy.max_rejections_before_suspension = 1
            policy.suspension_days = 5
            policy.save()

        leave = self._make_leave(
            today + timedelta(days=5),
            today + timedelta(days=5),
        )
        response = leave_reject(
            self._admin_request(data={'remarks': 'busy'}),
            self.tenant.schema_name, leave.id,
        )
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertIn('auto_suspension', body)
        self.assertEqual(body['auto_suspension']['staff_id'], self.staff.id)
        self.assertTrue(body['auto_suspension']['currently_active'])


class WorkingDaysTests(BaseLeaveV3):

    def test_working_days_set_defaults_to_all_seven(self):
        with schema_context(self.tenant.schema_name):
            self.assertEqual(set(_working_days_set()), set(range(7)))

    def test_working_days_set_respects_weekly_holiday(self):
        with schema_context(self.tenant.schema_name):
            WeeklyHoliday.objects.create(day_of_week=6, label='Sunday')
            ws = set(_working_days_set())
        self.assertNotIn(6, ws)
        self.assertEqual(len(ws), 6)

    def test_working_days_policy_field_defaults_true(self):
        with schema_context(self.tenant.schema_name):
            policy = LeavePolicy.current()
            self.assertTrue(policy.count_working_days_only)

    def test_working_days_cache_is_tenant_scoped(self):
        """LEAVE_MANAGEMENT_HARDENING_V4_1 regression.

        The V4 `lru_cache(maxsize=1)` leaked the first tenant's working-
        day set to every other tenant on the same worker. This test
        creates two tenants with different WeeklyHoliday configs and
        asserts that each one sees its own set.

        If somebody ever re-introduces a process-global cache keyed
        only on the date, this test will fail immediately.
        """
        from django.core.cache import cache as _test_cache
        _test_cache.clear()

        tenant_a = SchoolClient.objects.create(
            schema_name='leave-v4a-test',
            name='Leave V4A Tenant',
            admin_username='admin',
            admin_password='admin123',
            enabled_features={
                'desktop': ['leave_management'],
                'mobile': [],
                'staff_portal': [],
            },
        )
        tenant_b = SchoolClient.objects.create(
            schema_name='leave-v4b-test',
            name='Leave V4B Tenant',
            admin_username='admin',
            admin_password='admin123',
            enabled_features={
                'desktop': ['leave_management'],
                'mobile': [],
                'staff_portal': [],
            },
        )

        # Tenant A: Sunday (6) off -> {0..5}
        with schema_context(tenant_a.schema_name):
            WeeklyHoliday.objects.create(day_of_week=6, label='Sunday')
            ws_a = set(_working_days_set())

        # Tenant B: Friday (4) off -> {0,1,2,3,5,6}
        with schema_context(tenant_b.schema_name):
            WeeklyHoliday.objects.create(day_of_week=4, label='Friday')
            ws_b = set(_working_days_set())

        self.assertEqual(ws_a, {0, 1, 2, 3, 4, 5})
        self.assertEqual(ws_b, {0, 1, 2, 3, 5, 6})

        # Re-fetch A to prove the cache did NOT hand back B's value.
        with schema_context(tenant_a.schema_name):
            ws_a2 = set(_working_days_set())
        self.assertEqual(ws_a2, ws_a)

        # Re-fetch B likewise.
        with schema_context(tenant_b.schema_name):
            ws_b2 = set(_working_days_set())
        self.assertEqual(ws_b2, ws_b)

    def test_weekly_holiday_edit_invalidates_cache(self):
        """LEAVE_MANAGEMENT_HARDENING_V4_1 regression.

        If an admin adds / removes a WeeklyHoliday mid-day, the cache
        must be invalidated so quota counters reflect the change on the
        very next request. This test drives that via the ORM signal.
        """
        from django.core.cache import cache as _test_cache
        _test_cache.clear()

        with schema_context(self.tenant.schema_name):
            # Initial: no WeeklyHoliday -> all 7 days are working.
            self.assertEqual(set(_working_days_set()), set(range(7)))

            # Admin adds Sunday off. The post_save signal must clear the
            # cache entry for this schema + today.
            wh = WeeklyHoliday.objects.create(day_of_week=6, label='Sunday')
            self.assertEqual(set(_working_days_set()), {0, 1, 2, 3, 4, 5})

            # Admin removes it. The post_delete signal must clear again.
            wh.delete()
            self.assertEqual(set(_working_days_set()), set(range(7)))


class SuspensionBoundTests(BaseLeaveV3):

    def test_suspend_rejects_duration_over_max(self):
        request = self._admin_request(data={
            'reason': 'test',
            'duration_days': MAX_SUSPENSION_DAYS + 50,
        })
        response = staff_suspend(
            request, self.tenant.schema_name, self.staff.id,
        )
        self.assertEqual(response.status_code, 400)

    def test_suspend_accepts_max_duration(self):
        request = self._admin_request(data={
            'reason': 'test',
            'duration_days': MAX_SUSPENSION_DAYS,
        })
        response = staff_suspend(
            request, self.tenant.schema_name, self.staff.id,
        )
        self.assertEqual(response.status_code, 200)

    def test_expired_suspension_auto_deactivates(self):
        today = _today()
        with schema_context(self.tenant.schema_name):
            susp = LeaveSuspension.objects.create(
                staff=self.staff,
                reason='expired',
                start_date=today - timedelta(days=10),
                end_date=today - timedelta(days=5),
                is_active=True,
            )
            self.assertTrue(susp.is_active)
            from axis_saas.views.leave_management import (
                _expire_stale_suspensions,
            )
            _expire_stale_suspensions()
            susp.refresh_from_db()
            self.assertFalse(susp.is_active)
            self.assertEqual(susp.lifted_by, 'system:auto-expired')


class TodayHelperTests(BaseLeaveV3):

    def test_today_uses_timezone_localdate(self):
        with mock.patch(
            'axis_saas.views.leave_management.timezone.localdate',
            return_value=date(2030, 6, 15),
        ):
            self.assertEqual(_today(), date(2030, 6, 15))


class PolicySingletonTests(BaseLeaveV3):

    def test_get_or_create_returns_same_row(self):
        with schema_context(self.tenant.schema_name):
            p1 = LeavePolicy.current()
            p2 = LeavePolicy.current()
        self.assertEqual(p1.pk, p2.pk)

    def test_policy_save_persists_working_days_flag(self):
        request = self._admin_request(data={
            'max_leaves_per_month': 5,
            'max_leaves_per_week': 2,
            'max_consecutive_days': 10,
            'allow_backdated': True,
            'count_approved_only': False,
            'count_working_days_only': False,
            'max_rejections_before_suspension': 4,
            'suspension_days': 14,
        })
        response = leave_policy_save(
            request, self.tenant.schema_name,
        )
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertFalse(body['policy']['count_working_days_only'])
        with schema_context(self.tenant.schema_name):
            p = LeavePolicy.current()
            self.assertFalse(p.count_working_days_only)
            self.assertTrue(p.allow_backdated)


class PaginationConstantTests(BaseLeaveV3):

    def test_page_size_constant_is_set(self):
        # The admin template renders controls based on `pagination_json`;
        # this assertion just pins the size so a future refactor can't
        # silently change it.
        self.assertEqual(ADMIN_LEAVES_PAGE_SIZE, 50)
