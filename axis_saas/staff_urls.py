from django.urls import include, path

from axis_saas.views.staff_portal import (
    staff_api_classes,
    staff_api_profile,
    staff_change_password,
    staff_class_students,
    staff_classes,
    staff_dashboard,
    staff_login,
    staff_logout,
    staff_biometric_setup,
    staff_mark_notification_read,
    staff_more,
    staff_notifications,
    staff_profile,
    staff_student_profile,
)
# LEAVE_MANAGEMENT_HARDENING_V3: import from the correctly-named
# module (typo in the old filename fixed; shim kept for compatibility).
from axis_saas.views.staff_portal_leave_management import (
    staff_leave_management, staff_leave_apply_api,
    staff_leave_history_api, staff_leave_policy_api,
    staff_leave_cancel_api,
)

from axis_saas.views.staff_attendence import (
    staff_attendance_view,
    staff_attendance_students_api,
    staff_attendance_mark_api,
    staff_attendance_records_api,
    staff_attendance_missed_days_api,
    staff_attendance_copy_api,
    staff_attendance_policy_api,
    # STAFF_ATTENDANCE_OVERHAUL_V1_FIX
    staff_attendance_dates_api,
)


urlpatterns = [
    path('', staff_dashboard, name='staff_dashboard_root'),
    path('login/', staff_login, name='staff_login'),
    path('logout/', staff_logout, name='staff_logout'),
    path('biometric/setup/', staff_biometric_setup, name='staff_biometric_setup'),
    path('dashboard/', staff_dashboard, name='staff_dashboard'),
    path('classes/', staff_classes, name='staff_classes'),
    path('classes/<int:class_id>/students/', staff_class_students, name='staff_class_students'),
    path('students/<int:student_id>/', staff_student_profile, name='staff_student_profile'),
    path('profile/', staff_profile, name='staff_profile_page'),
    path('profile/change-password/', staff_change_password, name='staff_change_password'),
    path('notifications/', staff_notifications, name='staff_notifications'),
    path('biometric/', include('axis_saas.biometric_urls')),
    path('more/', staff_more, name='staff_more'),
    path('notifications/<int:notif_id>/mark-read/', staff_mark_notification_read, name='staff_mark_notification_read'),
    path('api/classes/', staff_api_classes, name='staff_api_classes'),
    path('api/profile/', staff_api_profile, name='staff_api_profile'),
    # ===== STAFF LEAVE MANAGEMENT =====
    path('leave/', staff_leave_management, name='staff_leave_management'),
    path('leave/apply/', staff_leave_apply_api, name='staff_leave_apply_api'),
    path('leave/history/', staff_leave_history_api, name='staff_leave_history_api'),
    path('leave/policy/', staff_leave_policy_api, name='staff_leave_policy_api'),
    path('leave/cancel/<int:leave_id>/', staff_leave_cancel_api, name='staff_leave_cancel_api'),
    # ===== ATTENDANCE_SYSTEM_REBUILD_V1 =====
    path('attendance/', staff_attendance_view, name='staff_attendance'),
    path('api/attendance/students/', staff_attendance_students_api, name='staff_attendance_students_api'),
    path('api/attendance/mark/', staff_attendance_mark_api, name='staff_attendance_mark_api'),
    path('api/attendance/records/', staff_attendance_records_api, name='staff_attendance_records_api'),
    path('api/attendance/missed-days/', staff_attendance_missed_days_api, name='staff_attendance_missed_days_api'),
    path('api/attendance/copy/', staff_attendance_copy_api, name='staff_attendance_copy_api'),
    path('api/attendance/policy/', staff_attendance_policy_api, name='staff_attendance_policy_api'),
    # STAFF_ATTENDANCE_OVERHAUL_V1_FIX
    path('api/attendance/dates/', staff_attendance_dates_api, name='staff_attendance_dates_api'),
    # ATTENDANCE_PRODUCTION_V2
]
