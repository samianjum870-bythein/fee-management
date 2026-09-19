"""DASHBOARD_V3_PROFESSIONAL — regression tests.

These tests guard against the specific root cause of the "every widget
shows 0" bug that shipped in the previous dashboard:

    axis_saas/views/helpers.py did not import Staff, LeaveRequest,
    StudentAttendance, SaleItem, or Vacation. The dashboard compute()
    function referenced all of them inside `try / except Exception:
    pass` blocks, so every NameError was silently swallowed and every
    metric fell back to 0. The admin saw "0 pending leaves" even when
    ten were waiting for review.

If a future refactor removes one of those imports, these tests fail
loudly BEFORE the dashboard silently breaks again.

The template tests check the structural contract of the redesign —
blink classes, attention strip, and the absence of the old duplicate
"Quick Actions" grid.

DASHBOARD_V3_FIX_2
------------------
`test_quick_actions_grid_removed` used to fail because the template's
own documentation comment mentions the phrase "Quick Actions" while
explaining why the grid was removed. The test now strips template
comments before checking, so the assertion fires on real UI content
only — not on documentation.
"""

import re

from django.test import SimpleTestCase
from django.template.loader import get_template


# ---------------------------------------------------------------------
# Root-cause regression: helpers must import what the dashboard uses.
# ---------------------------------------------------------------------

class DashboardHelpersImportTests(SimpleTestCase):
    """The dashboard helper module must import every model it uses.

    Before DASHBOARD_V3 these five imports were missing, so every
    metric that depends on them silently rendered as 0.
    """

    REQUIRED_MODELS = (
        'Staff',
        'LeaveRequest',
        'StudentAttendance',
        'SaleItem',
        'Vacation',
    )

    def test_helpers_module_exists(self):
        import axis_saas.views.helpers  # noqa: F401

    def test_helpers_imports_required_models(self):
        import axis_saas.views.helpers as helpers
        missing = [
            name for name in self.REQUIRED_MODELS
            if not hasattr(helpers, name)
        ]
        self.assertEqual(
            missing, [],
            msg=(
                "axis_saas.views.helpers must import the following "
                "models from ..models — the dashboard depends on them "
                "and their absence silently forced every metric to 0: "
                f"{missing}"
            ),
        )


# ---------------------------------------------------------------------
# Template contract: structure of the redesign.
# ---------------------------------------------------------------------

class DashboardTemplateTests(SimpleTestCase):
    """The dashboard template must compile and carry the markers that
    make the attention-first redesign work."""

    def _source(self) -> str:
        tpl = get_template('tenant/dashboard.html')
        # Django exposes .template.source on every backend.
        return tpl.template.source

    @staticmethod
    def _strip_comments(src: str) -> str:
        """Remove template comments before structural assertions.

        We want to check for user-visible markup, not documentation.
        Two comment syntaxes must be handled:

            {# ... #}
            {% comment %} ... {% endcomment %}

        `{# ... #}` in Django is terminated by the first `#}` on the
        same line — a plain non-greedy `.*?` with re.DOTALL is a
        stricter superset that also handles multi-line `{#` blocks
        (Django itself does not allow those, but being permissive here
        is harmless).
        """
        src = re.sub(r'\{#.*?#\}', '', src, flags=re.DOTALL)
        src = re.sub(
            r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}',
            '', src, flags=re.DOTALL,
        )
        return src

    def test_template_compiles(self):
        # get_template() raises TemplateSyntaxError if the template is
        # broken — this test is a compile check.
        self.assertIsNotNone(get_template('tenant/dashboard.html'))

    def test_has_alert_strip(self):
        self.assertIn('dv3-alerts', self._source())

    def test_has_blink_classes(self):
        src = self._source()
        # Both blink variants must exist in the stylesheet…
        self.assertIn('.dv3-blink-danger', src)
        self.assertIn('.dv3-blink-warn', src)
        # …and must be conditionally applied to the alert cards.
        self.assertIn("{% if pending_leave_count %}", src)
        self.assertIn("{% if low_stock_count %}", src)
        self.assertIn("{% if staff_on_leave_today %}", src)
        self.assertIn("{% if defaulters_count %}", src)

    def test_has_kpi_row_and_main_layout(self):
        src = self._source()
        self.assertIn('dv3-kpi-row', src)
        self.assertIn('dv3-layout', src)

    def test_has_low_stock_items_table(self):
        # DASHBOARD_V3 renders WHICH items are low stock, not just a
        # count. This assertion guards that the table does not get
        # accidentally removed.
        self.assertIn('low_stock_items', self._source())

    def test_quick_actions_grid_removed(self):
        """The old dashboard had a "Quick Actions" grid that duplicated
        every sidebar link. Removing it was deliberate — it added no
        navigation value and pushed every actionable widget off the
        fold.

        DASHBOARD_V3_FIX_2: comments are stripped before checking, so
        the template's documentation comment that mentions the phrase
        "Quick Actions" does not produce a false positive. We still
        assert on both the visible heading text and the CSS class.
        """
        src = self._strip_comments(self._source())
        self.assertNotIn('Quick Actions', src)
        self.assertNotIn('dv2-actions', src)

    def test_no_old_v2_markers(self):
        """Confirm we didn't just append to the old template."""
        src = self._source()
        self.assertNotIn('DASHBOARD_V2_PROFESSIONAL', src)
        self.assertNotIn('dv2-hero', src)
