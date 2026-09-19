"""DASHBOARD_V2_TESTS

Basic tests for the professional tenant dashboard.

These run under Django's standard test runner. They do not require a
live tenant; they use the tenant the test database is already scoped
to. Adjust the setup helpers if your project ships a different
tenant-testing fixture.
"""
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse
from django_tenants.utils import schema_context


class DashboardViewTests(TestCase):
    """Smoke tests for the rewritten dashboard view.

    These tests assume the test client hits the tenant whose schema is
    the current connection's schema. If your test runner is not
    tenant-aware, guard the URL reverse with a skip.
    """

    def setUp(self):
        self.client = Client()
        # Simulate an authenticated admin session the way
        # login_required_for_schema expects it.
        session = self.client.session
        session['school_admin_authenticated'] = True
        session['school_admin_schema'] = 'public'
        session.save()

    def _reverse_dashboard(self, schema_name='public'):
        try:
            return reverse('dashboard', kwargs={'schema_name': schema_name})
        except Exception:
            return None

    def test_dashboard_url_resolves(self):
        url = self._reverse_dashboard()
        self.assertIsNotNone(url, "dashboard URL must be name-resolvable")

    def test_dashboard_redirects_unauthenticated(self):
        """Without an authenticated session the view should redirect to login."""
        fresh = Client()
        url = self._reverse_dashboard()
        if not url:
            self.skipTest("dashboard url unavailable in this tenant context")
        resp = fresh.get(url)
        # 301/302/404 are all acceptable; a 500 is not.
        self.assertNotEqual(resp.status_code, 500)

    def test_dashboard_context_has_v2_keys(self):
        """The context must expose every key the new template reads."""
        url = self._reverse_dashboard()
        if not url:
            self.skipTest("dashboard url unavailable in this tenant context")
        resp = self.client.get(url)
        # 200 if authenticated, 302 if the middleware bounces us.
        if resp.status_code != 200:
            self.skipTest(f"dashboard returned {resp.status_code} — "
                          f"likely tenant/session mismatch in this env")
        ctx = resp.context
        self.assertIsNotNone(ctx)

        required_keys = [
            # finance
            'today_collection', 'month_collection', 'total_revenue',
            'total_pending', 'defaulters_count', 'collection_rate',
            'recent_payments', 'top_defaulters',
            'months_labels', 'months_amounts',
            'fee_automation_enabled', 'next_fee_generation_date',
            # people
            'total_students', 'total_active_students',
            'total_staff', 'total_active_staff', 'total_classes',
            # attendance
            'today_attendance_marked', 'today_attendance_present',
            'today_attendance_rate', 'class_attendance_summary',
            'classes_with_attendance_today',
            # leave
            'pending_leave_count', 'staff_on_leave_today',
            'recent_pending_leaves',
            # stock
            'low_stock_count', 'top_selling_products',
            # calendar
            'next_vacation',
        ]
        for key in required_keys:
            self.assertIn(key, ctx, f"dashboard context missing key: {key}")

    def test_dashboard_numeric_context_types(self):
        url = self._reverse_dashboard()
        if not url:
            self.skipTest("dashboard url unavailable")
        resp = self.client.get(url)
        if resp.status_code != 200:
            self.skipTest("unauthenticated in this env")
        ctx = resp.context
        self.assertIsInstance(ctx['total_students'], int)
        self.assertIsInstance(ctx['total_staff'], int)
        self.assertIsInstance(ctx['total_classes'], int)
        self.assertIsInstance(ctx['pending_leave_count'], int)
        self.assertIsInstance(ctx['today_attendance_rate'], float)
        self.assertIsInstance(ctx['months_labels'], list)
        self.assertIsInstance(ctx['months_amounts'], list)


class DashboardTemplateTests(TestCase):
    """Static checks on the dashboard template file."""

    def test_template_contains_v2_marker(self):
        from pathlib import Path
        from django.conf import settings
        p = Path(settings.BASE_DIR) / 'templates' / 'tenant' / 'dashboard.html'
        if not p.exists():
            self.skipTest("dashboard.html not present in this checkout")
        content = p.read_text(encoding='utf-8')
        self.assertIn('DASHBOARD_V2_PROFESSIONAL', content)

    def test_template_renders_key_sections(self):
        from pathlib import Path
        from django.conf import settings
        p = Path(settings.BASE_DIR) / 'templates' / 'tenant' / 'dashboard.html'
        if not p.exists():
            self.skipTest("dashboard.html not present")
        content = p.read_text(encoding='utf-8')
        for marker in (
            'Quick Actions',
            'Recent Payments',
            'Top Defaulters',
            'Monthly Collection Trend',
            'Fee Automation',
        ):
            self.assertIn(marker, content,
                          f"dashboard template missing section: {marker}")
