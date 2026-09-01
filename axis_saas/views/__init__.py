"""
AXIS views package – re‑export all view functions.
"""

from .dashboard import *
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
