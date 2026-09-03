from django.urls import path

from axis_saas.views.staff_biometric import (
    staff_biometric_complete_login,
    staff_biometric_prepare_login,
    staff_biometric_register,
    staff_biometric_registration_options,
    staff_biometric_status,
    staff_biometric_disable,
)

urlpatterns = [
    path('status/', staff_biometric_status, name='staff_biometric_status'),
    path('registration-options/', staff_biometric_registration_options, name='staff_biometric_registration_options'),
    path('register/', staff_biometric_register, name='staff_biometric_register'),
    path('prepare-login/', staff_biometric_prepare_login, name='staff_biometric_prepare_login'),
    path('complete-login/', staff_biometric_complete_login, name='staff_biometric_complete_login'),
    path('disable/', staff_biometric_disable, name='staff_biometric_disable'),
]
