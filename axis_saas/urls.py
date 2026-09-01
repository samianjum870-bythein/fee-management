urlpatterns = [
    path('admin/', admin.site.urls),
    path('voucher/html/<int:student_id>/', voucher_html_api, name='voucher_html'),
]