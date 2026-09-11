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
