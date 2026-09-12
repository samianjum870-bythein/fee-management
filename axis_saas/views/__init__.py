"""
AXIS views package – re‑export all view functions.
"""

from .dashboard import *
from .class_teacher_views import *
from .staff import (
    staff_list, mobile_staff_list, staff_profile, mobile_staff_profile,
    staff_add, staff_add_mobile, staff_edit, staff_search_api
)
from .students import *
from .fee_collection import *
from .reports import *
from .fee_structure import *
from .fee_settings import *
from .stock import *
from .vouchers import *
from .fee_logs import *
from .notifications import *
from .search import *
from .settings import *
from .sell import *

# Also export helpers if needed
from .helpers import *
from .classes import *
from .classes import assign_class_teacher

# --- Classes Management (card view) imports — added by fix_patcher ---
from .wing_classes import *  # noqa: F401,F403
from .wing_classes import wing_classes_view, classes_management_view  # noqa: F401
from .single_classes import *  # noqa: F401,F403
from .single_classes import single_classes_view  # noqa: F401

# --- Class Detailed page views — added by add_class_detailed_page ---
from .wing_class_detailed import *  # noqa: F401,F403
from .wing_class_detailed import wing_class_detailed_view, class_detailed_view  # noqa: F401
from .single_class_detailed import *  # noqa: F401,F403
from .single_class_detailed import single_class_detailed_view  # noqa: F401

# --- Timetable Assignments — added by add_timetable_assignments ---
from .timetable_assignments import *  # noqa: F401,F403
from .timetable_assignments import (  # noqa: F401
    timetable_assignments,
    api_assign_timetable,
    api_unassign_timetable,
)

# --- Class Staff Management — added by CLASS_STAFF_MANAGEMENT_v1 ---
from .class_staff import *  # noqa: F401,F403
from .class_staff import (  # noqa: F401
    api_assign_class_teacher as api_class_assign_class_teacher,
    api_assign_subject_teacher as api_class_assign_subject_teacher,
)

# --- Assign Periods to Teachers — added by ASSIGN_TEACHERS_v1 ---
from .assign_teachers import *  # noqa: F401,F403
from .assign_teachers import (  # noqa: F401
    timetable_assign_teachers,
    api_get_teacher_assignments,
    api_save_teacher_assignments,
)
