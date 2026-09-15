"""DEPRECATED shim.

LEAVE_MANAGEMENT_HARDENING_V3 renamed this module to
`staff_portal_leave_management` (typo fixed). This file exists only so
any code that still imports the old, misspelled name keeps working.
Do not add new code here.
"""
from .staff_portal_leave_management import (  # noqa: F401
    staff_leave_management,
    staff_leave_apply_api,
    staff_leave_history_api,
    staff_leave_policy_api,
    staff_leave_cancel_api,
    _get_active_leave,
    _month_used_days,
    _week_used_days,
    _active_suspension_for,
)
