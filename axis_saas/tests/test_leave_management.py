"""Leave Management (LEAVE_MANAGEMENT_V1 + V2) test suite.

Covers:
  * Policy defaults and persistence.
  * `_validate_request` rules: date ordering, backdated, consecutive
    cap, monthly cap, weekly cap, overlap with existing pending /
    approved leaves, and the V2 "currently on approved leave" gate.
  * Staff-portal APIs (apply / history / policy / cancel) via direct
    view calls with a RequestFactory.
  * Admin-side APIs (approve / reject / policy_save).

These tests call the view functions directly rather than via the test
client, because the StaffTenantMiddleware forcibly redirects to the
biometric setup page when the staff member has no registered
biometric credential — which would short-circuit every functional
assertion. Calling the view functions with a pre-populated session
exercises the same code paths without the middleware detour.

Run:
    python manage.py test axis_saas.tests.test_leave_management
"""

import json
from datetime import date, timedelta

from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django_tenants.utils import schema_context

from axis_saas.models import (
    LeavePolicy,
    LeaveRequest,
    SchoolClient,
    Staff,
    StaffCredential,
)
from axis_saas.views.staff_portal_leave_managemetn import (
    _get_active_leave,
    _month_used_days,
    _week_used_days,
    _validate_request,
    staff_leave_apply_api,
    staff_leave_cancel_api,
    staff_leave_history_api,
    staff_leave_policy_api,
)


# =====================================================================
# Base class
# =====================================================================
class LeaveTestBase(TestCase):
    """One tenant, one active staff member, both feature flags on."""

    def setUp(self):
        self.tenant = SchoolClient.objects.create(
            schema_name='leave-test',
            name='Leave Test School',
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
                email='ayesha@example.com',
                job_title='Math Teacher',
                department='teaching',
                phone='03001234567',
                role='teacher',
                status='active',
            )
            self.policy = LeavePolicy.objects.create(pk=1)

        # A StaffCredential is created automatically by Staff.save();
        # grab a reference for any test that needs it.
        self.credential = StaffCredential.objects.filter(
            staff_id=self.staff.id,
            schema_name=self.tenant.schema_name,
        ).first()

    # ---------- session helpers ----------

    def _login(self, staff=None):
        staff = staff or self.staff
        token = 'test-token-' + str(staff.id)

        session = self.client.session
        session['staff_id'] = staff.id
        session['staff_schema_name'] = self.tenant.schema_name
        session['staff_username'] = getattr(staff, 'email', '') or 'staff'
        session['staff_role'] = staff.role
        session['staff_name'] = staff.full_name
        session['staff_session_token'] = token
        session.save()

        cache.set(
            f'staff_session_token:{self.tenant.schema_name}:{staff.id}',
            token,
            1800,
        )

    def _make_request(self, method='GET', path='/portal/staff/leave/',
                      data=None, is_json=False):
        """Build a RequestFactory request with a pre-authenticated session."""
        factory = RequestFactory()
        if method.upper() == 'GET':
            request = factory.get(path)
        else:
            if is_json:
                request = factory.post(
                    path,
                    data=json.dumps(data or {}),
                    content_type='application/json',
                )
            else:
                request = factory.post(path, data or {})

        from django.contrib.sessions.backends.db import SessionStore
        request.session = SessionStore()
        token = 'test-token-' + str(self.staff.id)
        request.session['staff_id'] = self.staff.id
        request.session['staff_schema_name'] = self.tenant.schema_name
        request.session['staff_username'] = self.staff.email or 'staff'
        request.session['staff_role'] = self.staff.role
        request.session['staff_name'] = self.staff.full_name
        request.session['staff_session_token'] = token
        request.session.save()

        cache.set(
            f'staff_session_token:{self.tenant.schema_name}:{self.staff.id}',
            token,
            1800,
        )
        return request

    # ---------- data helpers ----------

    def _create_leave(self, start, end, status='pending',
                      leave_type='casual', title='Test leave',
                      reason='Test reason', staff=None):
        staff = staff or self.staff
        with schema_context(self.tenant.schema_name):
            total_days = (end - start).days + 1
            return LeaveRequest.objects.create(
                staff=staff,
                leave_type=leave_type,
                title=title,
                reason=reason,
                start_date=start,
                end_date=end,
                total_days=total_days,
                status=status,
            )

    def _policy(self):
        with schema_context(self.tenant.schema_name):
            return LeavePolicy.objects.get(pk=1)

    def _reload_staff(self):
        with schema_context(self.tenant.schema_name):
            return Staff.objects.get(pk=self.staff.id)


# =====================================================================
# Policy defaults
# =====================================================================
class LeavePolicyDefaultsTests(LeaveTestBase):

    def test_default_policy_values(self):
        policy = self._policy()
        self.assertEqual(policy.max_leaves_per_month, 4)
        self.assertEqual(policy.max_leaves_per_week, 1)
        self.assertEqual(policy.max_consecutive_days, 7)
        self.assertFalse(policy.allow_backdated)


# =====================================================================
# _validate_request
# =====================================================================
class LeaveValidationTests(LeaveTestBase):

    def _validate(self, start_offset, end_offset, staff=None):
        staff = staff or self._reload_staff()
        start = date.today() + timedelta(days=start_offset)
        end = date.today() + timedelta(days=end_offset)
        with schema_context(self.tenant.schema_name):
            policy = LeavePolicy.objects.get(pk=1)
            return _validate_request(staff, start, end, policy)

    def test_valid_request_has_no_errors(self):
        # Start 10 days in the future, single day. Well within limits.
        errors = self._validate(10, 10)
        self.assertEqual(errors, [])

    def test_start_after_end_rejected(self):
        staff = self._reload_staff()
        with schema_context(self.tenant.schema_name):
            policy = LeavePolicy.objects.get(pk=1)
            errors = _validate_request(
                staff,
                date.today() + timedelta(days=10),
                date.today() + timedelta(days=5),
                policy,
            )
        self.assertTrue(any('before or on' in e.lower() for e in errors))

    def test_backdated_blocked_when_disallowed(self):
        errors = self._validate(-5, -3)
        self.assertTrue(any('past' in e.lower() for e in errors))

    def test_backdated_allowed_when_policy_permits(self):
        with schema_context(self.tenant.schema_name):
            p = LeavePolicy.objects.get(pk=1)
            p.allow_backdated = True
            p.save()
        errors = self._validate(-3, -1)
        # No "past" error now. Weekly / monthly limits from today's
        # perspective should not fire because the range is fully in
        # the past, but there might still be an overlap if there are
        # other leaves. In a clean DB this should be empty.
        self.assertFalse(any('past' in e.lower() for e in errors))

    def test_exceeds_consecutive_days(self):
        # Policy default = 7 consecutive days.
        errors = self._validate(10, 20)
        self.assertTrue(any('consecutive' in e.lower() for e in errors))

    def test_overlap_with_pending_leave(self):
        # Occupy days 10..12 with a pending leave, then try to overlap.
        self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=12),
            status='pending',
        )
        errors = self._validate(11, 13)
        self.assertTrue(any('overlap' in e.lower() for e in errors))

    def test_overlap_with_approved_leave(self):
        self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=12),
            status='approved',
        )
        errors = self._validate(11, 13)
        self.assertTrue(any('overlap' in e.lower() for e in errors))

    def test_cancelled_leave_does_not_count(self):
        # Single-day request so the default weekly cap (1 day/week)
        # doesn't fire and mask the real assertion.
        self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            status='cancelled',
        )
        errors = self._validate(10, 10)
        self.assertEqual(errors, [])

    def test_rejected_leave_does_not_count(self):
        # Single-day request so the default weekly cap (1 day/week)
        # doesn't fire and mask the real assertion.
        self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            status='rejected',
        )
        errors = self._validate(10, 10)
        self.assertEqual(errors, [])

    def test_exceeds_monthly_limit(self):
        # Policy: max 4 days per month.
        # Take 3 days now, then request 3 more in the same month.
        today = date.today()
        with schema_context(self.tenant.schema_name):
            p = LeavePolicy.objects.get(pk=1)
            p.max_leaves_per_week = 7  # loosen weekly so monthly is the trigger
            p.max_consecutive_days = 7
            p.save()
        # Use the 1st, 2nd, 3rd of next month to guarantee the same month.
        first_of_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        self._create_leave(
            first_of_next,
            first_of_next + timedelta(days=2),
            status='approved',
        )
        # Now request 2 more days in the same month -> total 5 > 4
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.get(pk=self.staff.id)
            policy = LeavePolicy.objects.get(pk=1)
            errors = _validate_request(
                staff,
                first_of_next + timedelta(days=3),
                first_of_next + timedelta(days=4),
                policy,
            )
        self.assertTrue(any('monthly' in e.lower() for e in errors))

    def test_exceeds_weekly_limit(self):
        # Policy: max 1 day per week.
        today = date.today()
        # Pick Monday of next week so start_date is not in the current week.
        next_monday = today + timedelta(days=(7 - today.weekday()))
        # Use that Monday, then try to add Tuesday of the same week.
        self._create_leave(next_monday, next_monday, status='approved')
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.get(pk=self.staff.id)
            policy = LeavePolicy.objects.get(pk=1)
            errors = _validate_request(
                staff,
                next_monday + timedelta(days=1),
                next_monday + timedelta(days=1),
                policy,
            )
        self.assertTrue(any('weekly' in e.lower() for e in errors))

    # ---------- V2: currently-on-leave gate ----------

    def test_active_leave_blocks_new_request(self):
        """If an approved leave covers today, no new request is allowed."""
        today = date.today()
        self._create_leave(
            today - timedelta(days=1),
            today + timedelta(days=1),
            status='approved',
        )
        errors = self._validate(10, 11)
        self.assertTrue(
            any('currently on' in e.lower() for e in errors),
            f"Expected 'currently on' error, got: {errors}",
        )

    def test_active_leave_only_when_approved(self):
        """A pending leave that covers today does NOT block new requests."""
        today = date.today()
        self._create_leave(
            today - timedelta(days=1),
            today + timedelta(days=1),
            status='pending',
        )
        errors = self._validate(10, 10)
        # No "currently on" error. Might have an overlap error if the
        # new range overlaps the pending leave, but 10 days out does not.
        self.assertFalse(any('currently on' in e.lower() for e in errors))

    def test_active_leave_ends_today_still_blocks(self):
        """A leave whose end_date is today still counts as active."""
        today = date.today()
        self._create_leave(
            today - timedelta(days=2),
            today,
            status='approved',
        )
        errors = self._validate(10, 10)
        self.assertTrue(any('currently on' in e.lower() for e in errors))

    def test_expired_leave_does_not_block(self):
        """A leave that ended yesterday no longer blocks new requests."""
        today = date.today()
        self._create_leave(
            today - timedelta(days=5),
            today - timedelta(days=1),
            status='approved',
        )
        errors = self._validate(10, 10)
        self.assertFalse(any('currently on' in e.lower() for e in errors))

    def test_get_active_leave_returns_correct_row(self):
        today = date.today()
        self._create_leave(
            today - timedelta(days=1),
            today + timedelta(days=1),
            status='approved',
            title='Active',
        )
        self._create_leave(
            today + timedelta(days=20),
            today + timedelta(days=22),
            status='approved',
            title='Future',
        )
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.get(pk=self.staff.id)
            active = _get_active_leave(staff)
        self.assertIsNotNone(active)
        self.assertEqual(active.title, 'Active')

    def test_get_active_leave_returns_none_when_no_approved(self):
        today = date.today()
        self._create_leave(
            today - timedelta(days=1),
            today + timedelta(days=1),
            status='pending',
        )
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.get(pk=self.staff.id)
            active = _get_active_leave(staff)
        self.assertIsNone(active)


# =====================================================================
# Usage counters
# =====================================================================
class LeaveUsageCounterTests(LeaveTestBase):

    def test_month_used_days_counts_unique_days(self):
        today = date.today()
        # Two leaves in the same calendar month, non-overlapping.
        first_of_month = today.replace(day=1)
        self._create_leave(
            first_of_month,
            first_of_month + timedelta(days=1),
            status='approved',
        )
        self._create_leave(
            first_of_month + timedelta(days=5),
            first_of_month + timedelta(days=5),
            status='approved',
        )
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.get(pk=self.staff.id)
            used = _month_used_days(staff, first_of_month)
        self.assertEqual(used, 3)

    def test_month_used_ignores_rejected_and_cancelled(self):
        first_of_month = date.today().replace(day=1)
        self._create_leave(
            first_of_month,
            first_of_month,
            status='rejected',
        )
        self._create_leave(
            first_of_month + timedelta(days=3),
            first_of_month + timedelta(days=3),
            status='cancelled',
        )
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.get(pk=self.staff.id)
            used = _month_used_days(staff, first_of_month)
        self.assertEqual(used, 0)

    def test_week_used_days(self):
        today = date.today()
        # Monday of the current week.
        monday = today - timedelta(days=today.weekday())
        self._create_leave(monday, monday, status='approved')
        with schema_context(self.tenant.schema_name):
            staff = Staff.objects.get(pk=self.staff.id)
            used = _week_used_days(staff, monday)
        self.assertEqual(used, 1)


# =====================================================================
# Staff-portal APIs (direct view calls)
# =====================================================================
class StaffLeaveAPITests(LeaveTestBase):

    def test_apply_creates_pending_leave(self):
        self._login()
        start = (date.today() + timedelta(days=10)).isoformat()
        end = (date.today() + timedelta(days=10)).isoformat()
        request = self._make_request(
            method='POST',
            path='/portal/staff/leave/apply/',
            data={
                'title': 'Doctor visit',
                'reason': 'Routine checkup',
                'leave_type': 'sick',
                'start_date': start,
                'end_date': end,
            },
            is_json=True,
        )
        response = staff_leave_apply_api(request)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body['ok'])
        self.assertEqual(body['leave']['status'], 'pending')
        self.assertEqual(body['leave']['title'], 'Doctor visit')
        with schema_context(self.tenant.schema_name):
            self.assertEqual(LeaveRequest.objects.count(), 1)

    def test_apply_validation_errors_are_returned(self):
        self._login()
        request = self._make_request(
            method='POST',
            path='/portal/staff/leave/apply/',
            data={
                'title': 'X',
                'reason': 'Y',
                'leave_type': 'casual',
                'start_date': (date.today() + timedelta(days=10)).isoformat(),
                'end_date': (date.today() + timedelta(days=40)).isoformat(),  # too long
            },
            is_json=True,
        )
        response = staff_leave_apply_api(request)
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.content)
        self.assertFalse(body['ok'])
        self.assertTrue(any('consecutive' in e.lower() for e in body['errors']))

    def test_apply_blocked_while_on_active_leave(self):
        self._login()
        today = date.today()
        self._create_leave(
            today - timedelta(days=1),
            today + timedelta(days=1),
            status='approved',
        )
        request = self._make_request(
            method='POST',
            path='/portal/staff/leave/apply/',
            data={
                'title': 'Another',
                'reason': 'More',
                'leave_type': 'casual',
                'start_date': (today + timedelta(days=10)).isoformat(),
                'end_date': (today + timedelta(days=10)).isoformat(),
            },
            is_json=True,
        )
        response = staff_leave_apply_api(request)
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.content)
        self.assertFalse(body['ok'])
        self.assertTrue(any('currently on' in e.lower() for e in body['errors']))
        with schema_context(self.tenant.schema_name):
            self.assertEqual(LeaveRequest.objects.count(), 1)

    def test_history_returns_only_own_leaves(self):
        self._login()
        # Leave for current staff
        self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            title='Mine',
        )
        # Leave for a different staff member
        with schema_context(self.tenant.schema_name):
            other = Staff.objects.create(
                first_name='Other',
                last_name='Person',
                email='other@example.com',
                job_title='Teacher',
                department='teaching',
                phone='03009999999',
                role='teacher',
                status='active',
            )
        self._create_leave(
            date.today() + timedelta(days=20),
            date.today() + timedelta(days=20),
            title='Not mine',
            staff=other,
        )
        request = self._make_request(path='/portal/staff/leave/history/')
        response = staff_leave_history_api(request)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body['ok'])
        self.assertEqual(len(body['leaves']), 1)
        self.assertEqual(body['leaves'][0]['title'], 'Mine')

    def test_policy_api_includes_active_leave(self):
        self._login()
        today = date.today()
        self._create_leave(
            today - timedelta(days=1),
            today + timedelta(days=1),
            status='approved',
            title='Ongoing',
        )
        request = self._make_request(path='/portal/staff/leave/policy/')
        response = staff_leave_policy_api(request)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body['ok'])
        self.assertIsNotNone(body['active_leave'])
        self.assertEqual(body['active_leave']['title'], 'Ongoing')
        self.assertEqual(body['active_leave']['status'], 'approved')
        self.assertEqual(body['max_leaves_per_month'], 4)
        self.assertEqual(body['max_leaves_per_week'], 1)

    def test_policy_api_active_leave_is_none_when_not_on_leave(self):
        self._login()
        request = self._make_request(path='/portal/staff/leave/policy/')
        response = staff_leave_policy_api(request)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertIsNone(body['active_leave'])

    def test_policy_api_reports_used_and_remaining(self):
        self._login()
        first_of_month = date.today().replace(day=1)
        self._create_leave(
            first_of_month,
            first_of_month + timedelta(days=1),
            status='approved',
        )
        request = self._make_request(path='/portal/staff/leave/policy/')
        response = staff_leave_policy_api(request)
        body = json.loads(response.content)
        # 2 days used out of 4 -> 2 remaining.
        # (Week counter uses today as the anchor; depending on which
        # day of the week we're in, `used_week` may be 0 or 2. We only
        # strictly assert the month numbers here.)
        self.assertEqual(body['used_month'], 2)
        self.assertEqual(body['remaining_month'], 2)

    def test_cancel_pending_leave(self):
        self._login()
        leave = self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            status='pending',
        )
        request = self._make_request(
            method='POST',
            path=f'/portal/staff/leave/cancel/{leave.id}/',
            is_json=True,
            data={},
        )
        response = staff_leave_cancel_api(request, leave.id)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body['ok'])
        self.assertEqual(body['leave']['status'], 'cancelled')

    def test_cancel_approved_leave(self):
        self._login()
        leave = self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            status='approved',
        )
        request = self._make_request(
            method='POST',
            path=f'/portal/staff/leave/cancel/{leave.id}/',
            is_json=True,
            data={},
        )
        response = staff_leave_cancel_api(request, leave.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['leave']['status'], 'cancelled')

    def test_cancel_rejected_leave_fails(self):
        self._login()
        leave = self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            status='rejected',
        )
        request = self._make_request(
            method='POST',
            path=f'/portal/staff/leave/cancel/{leave.id}/',
            is_json=True,
            data={},
        )
        response = staff_leave_cancel_api(request, leave.id)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(json.loads(response.content)['ok'])

    def test_cannot_cancel_other_staff_leave(self):
        self._login()
        with schema_context(self.tenant.schema_name):
            other = Staff.objects.create(
                first_name='Other',
                last_name='Person',
                email='other-cancel@example.com',
                job_title='Teacher',
                department='teaching',
                phone='03008887777',
                role='teacher',
                status='active',
            )
        leave = self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            status='pending',
            staff=other,
        )
        request = self._make_request(
            method='POST',
            path=f'/portal/staff/leave/cancel/{leave.id}/',
            is_json=True,
            data={},
        )
        response = staff_leave_cancel_api(request, leave.id)
        self.assertEqual(response.status_code, 404)


# =====================================================================
# Admin APIs (approve / reject / policy_save)
# =====================================================================
class AdminLeaveAPITests(LeaveTestBase):

    def _admin_session(self):
        session = self.client.session
        session['school_admin_authenticated'] = True
        session['school_admin_schema'] = self.tenant.schema_name
        session['school_admin_username'] = 'admin'
        session.save()

    def _admin_request(self, method='POST', path='/', data=None):
        factory = RequestFactory()
        if method.upper() == 'GET':
            request = factory.get(path)
        else:
            request = factory.post(
                path,
                data=json.dumps(data or {}),
                content_type='application/json',
            )
        from django.contrib.sessions.backends.db import SessionStore
        request.session = SessionStore()
        request.session['school_admin_authenticated'] = True
        request.session['school_admin_schema'] = self.tenant.schema_name
        request.session['school_admin_username'] = 'admin'
        request.session.save()
        request.tenant = self.tenant
        return request

    def test_admin_approve_marks_status(self):
        from axis_saas.views.leave_management import leave_approve
        leave = self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            status='pending',
        )
        request = self._admin_request(
            data={'remarks': 'OK, approved'},
        )
        response = leave_approve(request, self.tenant.schema_name, leave.id)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body['ok'])
        self.assertEqual(body['leave']['status'], 'approved')
        self.assertEqual(body['leave']['admin_remarks'], 'OK, approved')

    def test_admin_reject_marks_status(self):
        from axis_saas.views.leave_management import leave_reject
        leave = self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            status='pending',
        )
        request = self._admin_request(data={'remarks': 'Busy week'})
        response = leave_reject(request, self.tenant.schema_name, leave.id)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body['ok'])
        self.assertEqual(body['leave']['status'], 'rejected')

    def test_admin_cannot_approve_non_pending(self):
        from axis_saas.views.leave_management import leave_approve
        leave = self._create_leave(
            date.today() + timedelta(days=10),
            date.today() + timedelta(days=10),
            status='approved',
        )
        request = self._admin_request(data={})
        response = leave_approve(request, self.tenant.schema_name, leave.id)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(json.loads(response.content)['ok'])

    def test_admin_policy_save_updates_defaults(self):
        from axis_saas.views.leave_management import leave_policy_save
        request = self._admin_request(data={
            'max_leaves_per_month': 6,
            'max_leaves_per_week': 2,
            'max_consecutive_days': 10,
            'allow_backdated': True,
        })
        response = leave_policy_save(request, self.tenant.schema_name)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body['ok'])
        self.assertEqual(body['policy']['max_leaves_per_month'], 6)
        self.assertEqual(body['policy']['max_leaves_per_week'], 2)
        self.assertEqual(body['policy']['max_consecutive_days'], 10)
        self.assertTrue(body['policy']['allow_backdated'])
        with schema_context(self.tenant.schema_name):
            p = LeavePolicy.objects.get(pk=1)
            self.assertEqual(p.max_leaves_per_month, 6)
            self.assertTrue(p.allow_backdated)

    def test_admin_policy_save_rejects_negative_values(self):
        from axis_saas.views.leave_management import leave_policy_save
        request = self._admin_request(data={
            'max_leaves_per_month': -5,
            'max_leaves_per_week': 0,
            'max_consecutive_days': -1,
        })
        response = leave_policy_save(request, self.tenant.schema_name)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        # Negative / zero values are clamped to at least 1 by _to_int.
        self.assertGreaterEqual(body['policy']['max_leaves_per_month'], 1)
        self.assertGreaterEqual(body['policy']['max_leaves_per_week'], 1)
        self.assertGreaterEqual(body['policy']['max_consecutive_days'], 1)
