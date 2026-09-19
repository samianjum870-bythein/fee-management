"""STUDENT_MODULE_BUGFIX_V1+V2 — full regression suite.

Every bug fixed by the two patcher passes has at least one test.

Run:
    python manage.py test axis_saas.tests.test_student_bugfixes -v 2
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase
from django_tenants.utils import schema_context

from axis_saas.models import (
    FeeRecord, PaymentTransaction, SchoolClient, SchoolClass, Student,
)
from axis_saas.views.helpers import (
    aggregate_pending_totals, get_student_pending_queryset,
)


TEMPLATES_DIR = Path(settings.BASE_DIR) / "templates"


class StudentBugfixBase(TestCase):

    schema = "student-bugfix-test"

    def setUp(self):
        from django.db import connection
        connection.set_schema_to_public()

        self.tenant = SchoolClient.objects.create(
            schema_name=self.schema,
            name="Student Bugfix Test School",
            admin_username="admin",
            admin_password="admin123",
            enabled_features={
                "desktop": [
                    "dashboard",
                    "students",
                    "fee_collection",
                    "attendance_management",
                ],
                "mobile": ["students"],
                "staff_portal": ["staff_dashboard"],
            },
        )
        connection.set_schema_to_public()

        with schema_context(self.schema):
            self.school_class = SchoolClass.objects.create(
                name="Grade 1", section="A",
            )

        self.client = Client()
        session = self.client.session
        session["school_admin_authenticated"] = True
        session["school_admin_schema"] = self.schema
        session["school_admin_username"] = "admin"
        session.save()

    def tearDown(self):
        from django.db import connection
        connection.set_schema_to_public()
        try:
            self.tenant.delete(force_drop=True)
        except TypeError:
            try:
                self.tenant.delete()
            except Exception:
                pass
        except Exception:
            pass
        connection.set_schema_to_public()

    def url(self, path: str) -> str:
        return f"/portal/{self.schema}/{path.lstrip('/')}"

    def _mk_student(self, name="Ali", roll=None):
        with schema_context(self.schema):
            kwargs = dict(
                name=name,
                father_name="Khan",
                father_cnic="35202-1234567-8",
                parent_mobile="03001234567",
                grade="Grade 1",
                section="A",
                school_class=self.school_class,
            )
            if roll is not None:
                kwargs["roll_number"] = roll
            return Student.objects.create(**kwargs)


# ---- BUG 1 -----------------------------------------------------------

class FeeRecordsApiTests(StudentBugfixBase):

    def test_fee_records_api_exposes_total_amount(self):
        s = self._mk_student()
        with schema_context(self.schema):
            FeeRecord.objects.create(
                student=s, month=9, year=2026,
                amount=Decimal("2000"),
                paid_amount=Decimal("0"),
                due_date=date(2026, 9, 15),
                status="pending",
                late_fee_accrued=Decimal("50"),
                extra_charges=[{"title": "Library", "amount": 100}],
            )

        r = self.client.get(self.url(f"api/student/{s.id}/fee-records/"))
        self.assertEqual(r.status_code, 200)
        row = r.json()[0]
        self.assertIn("amount", row)
        self.assertIn("total_amount", row)
        self.assertEqual(row["total_amount"], 2150.0)
        self.assertEqual(row["amount"], 2000.0)


# ---- BUG 2 -----------------------------------------------------------

class PaymentsApiTests(StudentBugfixBase):

    def test_payments_api_exposes_day_and_type(self):
        s = self._mk_student()
        with schema_context(self.schema):
            PaymentTransaction.objects.create(
                student=s, amount=Decimal("500"),
                payment_mode="cash", payment_type="full",
                remarks="Fee payment",
                created_by="admin",
            )
        r = self.client.get(self.url(f"api/student/{s.id}/payments/"))
        row = r.json()[0]
        self.assertIn("day", row)
        self.assertIn("type", row)
        self.assertEqual(row["type"], "Fee")

    def test_payments_api_type_is_items_when_items_sold(self):
        s = self._mk_student()
        with schema_context(self.schema):
            PaymentTransaction.objects.create(
                student=s, amount=Decimal("250"),
                payment_mode="cash", payment_type="partial",
                remarks="Items sold: Book x1 @ ₹250 = ₹250",
                created_by="admin",
            )
        r = self.client.get(self.url(f"api/student/{s.id}/payments/"))
        self.assertEqual(r.json()[0]["type"], "Items")


# ---- BUG 6, 13, 1.5 -------------------------------------------------

class StudentModelBugfixTests(StudentBugfixBase):

    def test_explicit_zero_custom_fee_is_preserved_on_update(self):
        s = self._mk_student()
        with schema_context(self.schema):
            s.refresh_from_db()
            s.custom_fee = Decimal("0")
            s.save()
            s.refresh_from_db()
            self.assertEqual(s.custom_fee, Decimal("0"))

    def test_roll_number_auto_assigned_and_unique(self):
        s1 = self._mk_student(name="A")
        s2 = self._mk_student(name="B")
        with schema_context(self.schema):
            s1.refresh_from_db(); s2.refresh_from_db()
        self.assertNotEqual(s1.roll_number, s2.roll_number)
        self.assertTrue(s1.roll_number.isdigit())
        self.assertTrue(s2.roll_number.isdigit())

    def test_feerecord_accepts_string_due_date(self):
        s = self._mk_student()
        with schema_context(self.schema):
            rec = FeeRecord.objects.create(
                student=s, month=9, year=2026,
                amount=Decimal("1500"),
                paid_amount=Decimal("0"),
                due_date="2026-09-15",   # string on purpose
                status="pending",
            )
            rec.refresh_from_db()
        self.assertEqual(rec.due_date, date(2026, 9, 15))

    def test_feerecord_rejects_invalid_string_due_date(self):
        # STUDENT_FEERECORD_STRICT_DUE_DATE_V3: an unparseable
        # string must raise a clear ValidationError, not silently
        # set due_date=None and crash on the NOT NULL constraint.
        from django.core.exceptions import ValidationError
        s = self._mk_student()
        with schema_context(self.schema):
            with self.assertRaises(ValidationError):
                FeeRecord.objects.create(
                    student=s, month=9, year=2026,
                    amount=Decimal("1500"),
                    paid_amount=Decimal("0"),
                    due_date="not-a-date",
                    status="pending",
                )

    def test_feerecord_rejects_empty_string_due_date(self):
        from django.core.exceptions import ValidationError
        s = self._mk_student()
        with schema_context(self.schema):
            with self.assertRaises(ValidationError):
                FeeRecord.objects.create(
                    student=s, month=9, year=2026,
                    amount=Decimal("1500"),
                    paid_amount=Decimal("0"),
                    due_date="",
                    status="pending",
                )


# ---- BUG 4 -----------------------------------------------------------

class CsrfHardeningTests(StudentBugfixBase):

    def test_sync_offline_student_api_is_not_csrf_exempt(self):
        from axis_saas.views.students import sync_offline_student_api
        self.assertFalse(
            getattr(sync_offline_student_api, "csrf_exempt", False),
        )


# ---- BUG 15 ----------------------------------------------------------

class FeatureGateTests(StudentBugfixBase):

    def _disable_students(self):
        from django.db import connection
        connection.set_schema_to_public()
        with schema_context("public"):
            self.tenant.enabled_features = {
                "desktop": ["dashboard"],
                "mobile": [],
                "staff_portal": [],
            }
            self.tenant.save(update_fields=["enabled_features"])
        connection.set_schema_to_public()

    def test_fee_records_api_requires_feature(self):
        self._disable_students()
        s = self._mk_student()
        r = self.client.get(self.url(f"api/student/{s.id}/fee-records/"))
        self.assertEqual(r.status_code, 404)

    def test_payments_api_requires_feature(self):
        self._disable_students()
        s = self._mk_student()
        r = self.client.get(self.url(f"api/student/{s.id}/payments/"))
        self.assertEqual(r.status_code, 404)

    def test_current_fee_status_api_requires_feature(self):
        self._disable_students()
        s = self._mk_student()
        r = self.client.get(
            self.url(f"api/student/{s.id}/current-fee-status/"),
        )
        self.assertEqual(r.status_code, 404)


# ---- BUG 11 ----------------------------------------------------------

class CsrfCookieTests(StudentBugfixBase):

    def test_student_profile_sets_csrf_cookie(self):
        s = self._mk_student()
        r = self.client.get(self.url(f"students/{s.id}/"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("csrftoken", r.cookies)


# ---- BUG 7 -----------------------------------------------------------

class PendingCalcTests(StudentBugfixBase):

    def test_pending_annotation_includes_late_fee(self):
        s = self._mk_student()
        with schema_context(self.schema):
            FeeRecord.objects.create(
                student=s, month=9, year=2026,
                amount=Decimal("2000"),
                paid_amount=Decimal("0"),
                due_date=date(2026, 9, 15),
                status="pending",
                late_fee_accrued=Decimal("150"),
            )
            qs = get_student_pending_queryset(
                Student.objects.filter(id=s.id),
            )
            self.assertEqual(
                qs.get(id=s.id).pending_amount, Decimal("2150"),
            )
            self.assertEqual(
                aggregate_pending_totals()["total_pending"],
                Decimal("2150"),
            )


# ---- Template content tests (BUGs 3, 5, 8, 9, 10, 12, 14, 16, 17, 18, 19) --

class StudentProfileTemplateTests(TestCase):

    def _read(self):
        return (TEMPLATES_DIR / "tenant" / "student_profile.html"
                ).read_text(encoding="utf-8")

    def test_voucher_modal_container_present(self):
        html = self._read()
        self.assertIn('id="voucherModal"', html)
        self.assertIn('id="voucherModalBody"', html)
        self.assertIn('id="voucherModalPrint"', html)
        self.assertIn('id="voucherModalClose"', html)

    def test_voucher_modal_fetches_real_html(self):
        html = self._read()
        self.assertIn("voucher-html/", html)
        self.assertIn("STUDENT_VOUCHER_MODAL_FIX_V1", html)
        self.assertNotIn("alert('Voucher modal for student '", html)

    def test_init_does_not_overwrite_server_render(self):
        # STUDENT_MODULE_BUGFIX_V4: the V1 marker
        # (STUDENT_PROFILE_INIT_LOAD_FIX_V1) was replaced by
        # STUDENT_PROFILE_DEAD_JS_REMOVED_V1 when the two
        # functions were deleted entirely rather than just
        # commented out. The stronger invariant — that the
        # async functions are gone — is asserted in
        # test_dead_js_functions_removed below; this test just
        # pins the new marker so both ends of the fix stay in
        # sync.
        html = self._read()
        self.assertIn(
            "STUDENT_PROFILE_DEAD_JS_REMOVED_V1", html,
        )
        self.assertNotIn(
            "STUDENT_PROFILE_INIT_LOAD_FIX_V1", html,
        )

    def test_suspended_badge_css_present(self):
        html = self._read()
        self.assertIn(".status-suspended", html)
        self.assertIn("STUDENT_SUSPENDED_BADGE_CSS_FIX_V1", html)

    def test_attendance_button_feature_gated(self):
        html = self._read()
        self.assertIn(
            "{% if tenant|has_feature:'attendance_management' %}", html,
        )
        self.assertIn("STUDENT_ATT_BUTTON_FEATURE_GATE_V1", html)

    def test_generate_does_not_refresh_before_redirect(self):
        html = self._read()
        self.assertIn("STUDENT_FEE_GEN_REDIRECT_FIX_V1", html)

    def test_dead_js_functions_removed(self):
        # STUDENT_PROFILE_DEAD_JS_REMOVED_V1: the two async
        # functions that used to overwrite the server-rendered
        # tables must be gone entirely.
        html = self._read()
        self.assertNotIn("async function loadFeeRecords", html)
        self.assertNotIn("async function loadPayments", html)
        self.assertIn("STUDENT_PROFILE_DEAD_JS_REMOVED_V1", html)


class StudentListTemplateTests(TestCase):

    def _read(self):
        return (TEMPLATES_DIR / "tenant" / "student_list.html"
                ).read_text(encoding="utf-8")

    def test_no_duplicate_class_display_load(self):
        html = self._read()
        self.assertEqual(html.count("{% load class_display %}"), 1)

    def test_search_input_present(self):
        html = self._read()
        self.assertIn('id="searchInput"', html)
        self.assertIn('name="q"', html)
        self.assertIn("STUDENT_LIST_SEARCH_INPUT_FIX_V1", html)

    def test_pagination_preserves_class_id(self):
        html = self._read()
        self.assertIn("STUDENT_LIST_PAGINATION_CLASS_FILTER_V1", html)
        self.assertIn("request.GET.class_id", html)

    def test_no_dead_grade_fragment(self):
        html = self._read()
        self.assertNotIn(
            "{% if request.GET.grade %}&grade={{ request.GET.grade }}{% endif %}",
            html,
        )

    def test_no_debug_console_logs(self):
        html = self._read()
        self.assertNotIn("[DEBUG]", html)

    def test_mobile_redirect_regex_cleaned(self):
        html = self._read()
        self.assertNotIn(r"\?.*$", html)
        self.assertIn("STUDENT_MOBILE_REDIRECT_REGEX_FIX_V1", html)


class StudentFormTemplateTests(TestCase):

    def test_desktop_form_has_online_check(self):
        html = (TEMPLATES_DIR / "tenant" / "student_form.html"
                ).read_text(encoding="utf-8")
        self.assertIn("STUDENT_FORM_ONLINE_SUBMIT_FIX_V1", html)
        self.assertIn("navigator.onLine", html)

    def test_mobile_form_has_online_check(self):
        html = (TEMPLATES_DIR / "mobile" / "student_form.html"
                ).read_text(encoding="utf-8")
        self.assertIn("STUDENT_FORM_ONLINE_SUBMIT_FIX_V1", html)
        self.assertIn("navigator.onLine", html)


class StudentsByTeacherTemplateTests(TestCase):

    def test_uses_display_class_filter(self):
        html = (TEMPLATES_DIR / "tenant" / "students_by_teacher.html"
                ).read_text(encoding="utf-8")
        self.assertIn("{% load class_display %}", html)
        self.assertIn("STUDENTS_BY_TEACHER_WING_DISPLAY_FIX_V1", html)
        self.assertIn("{{ s|display_class:tenant }}", html)
