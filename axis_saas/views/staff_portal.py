import json
import logging
import secrets
import uuid
from datetime import datetime

from django.conf import settings
from django.contrib.auth.hashers import make_password

from django.core.cache import cache
from django.db import connection
from django.db import transaction
from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from axis_saas.models import Notification, SchoolClass, Staff, StaffCredential, Student, StudentAttendance
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')



@csrf_exempt
def staff_login(request):
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        ip_key = f'staff_login_{get_client_ip(request)}'
        blocked_until = cache.get(f'{ip_key}_blocked_until')
        if blocked_until and blocked_until > timezone.now():
            return render(request, 'mobile/staff/login.html', {'error': 'Too many failed login attempts. Please try again in 15 minutes.'})

        attempts = cache.get(ip_key, 0)
        if attempts >= 10:
            cache.set(f'{ip_key}_blocked_until', timezone.now() + timezone.timedelta(minutes=15), 900)
            return render(request, 'mobile/staff/login.html', {'error': 'Too many failed login attempts. Please try again in 15 minutes.'})

        credential = StaffCredential.objects.filter(username=username).first()
        if credential and credential.is_active and (not credential.locked_until or credential.locked_until <= timezone.now()):
            if credential.check_password(password):
                with schema_context(credential.schema_name):
                    staff = Staff.objects.filter(pk=credential.staff_id).first()
                if staff is None or staff.status != 'active':
                    cache.set(ip_key, attempts + 1, 60)
                    return render(request, 'mobile/staff/login.html', {'error': 'Your staff account is inactive or missing.'})

                cache.delete(ip_key)
                cache.delete(f'{ip_key}_blocked_until')
                credential.reset_failed_attempts()
                credential.last_login = timezone.now()
                credential.save(update_fields=['last_login', 'failed_attempts', 'locked_until'])

                request.session.flush()
                request.session['school_admin_authenticated'] = False
                request.session['school_admin_schema'] = ''
                request.session['staff_id'] = staff.pk
                request.session['staff_schema_name'] = credential.schema_name
                request.session['staff_username'] = credential.username
                request.session['staff_role'] = staff.role
                request.session['staff_name'] = staff.full_name
                request.session['staff_session_token'] = uuid.uuid4().hex
                request.session.set_expiry(1800)
                request.session.modified = True
                session_keys = cache.get(f'staff_session_keys:{credential.schema_name}:{staff.pk}', [])
                if isinstance(session_keys, str):
                    session_keys = [session_keys]
                session_keys = [k for k in list(session_keys) if k]
                if request.session.session_key:
                    session_keys.append(request.session.session_key)
                cache.set(f'staff_session_keys:{credential.schema_name}:{staff.pk}', list(dict.fromkeys(session_keys)), 1800)
                cache.set(f'staff_online:{credential.schema_name}:{staff.pk}', request.session.session_key, 1800)
                cache.set(f'staff_session_token:{credential.schema_name}:{staff.pk}', request.session['staff_session_token'], 1800)
                return redirect('staff_dashboard')

        cache.set(ip_key, attempts + 1, 60)
        if credential:
            credential.increment_failed_attempts()
        return render(request, 'mobile/staff/login.html', {'error': 'Invalid username or password.'})

    return render(request, 'mobile/staff/login.html')


def staff_logout(request):
    staff_id = request.session.get('staff_id')
    schema_name = request.session.get('staff_schema_name')
    if staff_id and schema_name:
        try:
            from django_tenants.utils import schema_context
            with schema_context(schema_name):
                staff = Staff.objects.filter(pk=staff_id).first()
                if staff:
                    staff.logout_session()
        except Exception:
            pass
    request.session.flush()
    request.session.pop('school_admin_authenticated', None)
    request.session.pop('school_admin_schema', None)
    request.session.pop('staff_session_token', None)
    request.session.modified = True
    return redirect('staff_login')


def require_staff_login(view_func):
    def wrapped(request, *args, **kwargs):
        staff_id = request.session.get('staff_id')
        schema_name = request.session.get('staff_schema_name')

        if not staff_id or not schema_name:
            return redirect('staff_login')

        if not settings.DEBUG:
            session_token = request.session.get('staff_session_token')
            cached_token = cache.get(f'staff_session_token:{schema_name}:{staff_id}') if schema_name and staff_id else None
            token_invalid = not session_token or cached_token in ['logged_out'] or cached_token != session_token
        else:
            token_invalid = False

        if token_invalid:
            request.session.flush()
            return redirect('staff_login')
        return view_func(request, *args, **kwargs)
    return wrapped


def staff_accessible_classes(staff, schema_name):
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        classes = SchoolClass.objects.filter(Q(class_teacher=staff) | Q(class_subjects__teacher=staff)).distinct().order_by('name', 'section')
    return classes


@require_staff_login
def staff_dashboard(request):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        classes = SchoolClass.objects.filter(Q(class_teacher=staff) | Q(class_subjects__teacher=staff)).distinct().order_by('name', 'section')
        today = timezone.localdate()
        student_count = Student.objects.filter(school_class__in=classes).count()
        attendance_today = StudentAttendance.objects.filter(date=today, school_class__in=classes).count()
        notifications = Notification.objects.filter(is_read=False).order_by('-created_at')[:5]

    return render(
        request,
        'mobile/staff/dashboard.html',
        {
            'staff': staff,
            'classes': classes,
            'student_count': student_count,
            'attendance_today': attendance_today,
            'notifications': notifications,
            'schema_name': schema_name,
        },
    )


@require_staff_login
def staff_classes(request):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        classes = SchoolClass.objects.filter(Q(class_teacher=staff) | Q(class_subjects__teacher=staff)).distinct().order_by('name', 'section')
        classes = list(classes)
        for school_class in classes:
            school_class.student_count = Student.objects.filter(school_class=school_class).count()
    return render(request, 'mobile/staff/classes.html', {'staff': staff, 'classes': classes})


@require_staff_login
def staff_class_students(request, class_id):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        if not (school_class.class_teacher_id == staff.pk or school_class.class_subjects.filter(teacher=staff).exists()):
            return render(request, 'mobile/staff/403.html', status=403)
        students = Student.objects.filter(school_class=school_class).order_by('roll_number')
        today = timezone.localdate()
        attendance_map = dict(
            StudentAttendance.objects.filter(school_class=school_class, date=today).values_list('student_id', 'status')
        )
        for student in students:
            student.attendance_status = attendance_map.get(student.pk, 'present')
    return render(request, 'mobile/staff/class_students.html', {'staff': staff, 'school_class': school_class, 'students': students, 'attendance_map': attendance_map})


@require_staff_login
def staff_attendance_list(request):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        classes = SchoolClass.objects.filter(Q(class_teacher=staff) | Q(class_subjects__teacher=staff)).distinct().order_by('name', 'section')
    return render(request, 'mobile/staff/attendance.html', {'staff': staff, 'classes': classes})


@require_staff_login
def staff_attendance_mark(request, class_id, attendance_date):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        if not (school_class.class_teacher_id == staff.pk or school_class.class_subjects.filter(teacher=staff).exists()):
            return render(request, 'mobile/staff/403.html', status=403)
        if request.method == 'POST':
            students = Student.objects.filter(school_class=school_class).order_by('roll_number')
            for student in students:
                choice = request.POST.get(f'status_{student.pk}', 'present')
                defaults = {'present': 'present', 'absent': 'absent', 'late': 'late'}
                status = defaults.get(choice, 'present')
                remarks = request.POST.get(f'remarks_{student.pk}', '')
                StudentAttendance.objects.update_or_create(
                    student=student,
                    date=datetime.strptime(attendance_date, '%Y-%m-%d').date(),
                    defaults={'school_class': school_class, 'status': status, 'teacher': staff, 'remarks': remarks},
                )
            return redirect('staff_attendance_list')

        students = Student.objects.filter(school_class=school_class).order_by('roll_number')
        attendance_day = datetime.strptime(attendance_date, '%Y-%m-%d').date()
        marks = dict(StudentAttendance.objects.filter(school_class=school_class, date=attendance_day).values_list('student_id', 'status'))
        for student in students:
            student.attendance_status = marks.get(student.pk, 'present')
    return render(request, 'mobile/staff/attendance_mark.html', {'staff': staff, 'school_class': school_class, 'students': students, 'attendance_date': attendance_date, 'marks': marks})


@require_staff_login
@require_http_methods(['GET'])
def staff_profile(request):
    try:
        schema_name = request.session.get('staff_schema_name')
        staff_id = request.session.get('staff_id')
        logger.info('PROFILE - schema_name=%s staff_id=%s', schema_name, staff_id)
        if not schema_name or not staff_id:
            logger.warning('PROFILE - missing schema or staff_id, flushing session')
            request.session.flush()
            return redirect('staff_login')

        from django_tenants.utils import schema_context
        try:
            with schema_context(schema_name):
                staff = Staff.objects.get(pk=staff_id)
        except Exception as e:
            logger.exception('PROFILE - failed to fetch staff: %s', e)
            request.session.flush()
            return redirect('staff_login')

        with schema_context('public'):
            credential = StaffCredential.objects.filter(staff_id=staff.pk, schema_name=schema_name).first()
        if credential is not None:
            credential.raw_password = None
        try:
            response = render(request, 'mobile/staff/profile.html', {
                'staff': staff,
                'credential': credential,
            })
            logger.info('PROFILE - render successful, returning response')
            return response
        except Exception as e:
            logger.exception('PROFILE - render failed: %s', e)
            return redirect('staff_dashboard')
    except Exception as e:
        logger.exception('PROFILE - unexpected error: %s', e)
        return redirect('staff_login')


@require_staff_login
@require_http_methods(['POST'])
def staff_change_password(request):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    old_password = request.POST.get('old_password')
    new_password = request.POST.get('new_password')
    confirm_password = request.POST.get('confirm_password')
    cnic = (request.POST.get('cnic') or '').strip()
    dob = request.POST.get('date_of_birth')

    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=request.session['staff_id']).first()
        if staff is None:
            return JsonResponse({'success': False, 'message': 'Staff account not found.'}, status=404) if request.headers.get('x-requested-with') == 'XMLHttpRequest' else redirect('staff_profile_page')
        if staff.status != 'active':
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': 'This account is suspended and cannot change password.'}, status=403)
            messages.error(request, 'This account is suspended and cannot change password.')
            return redirect('staff_profile_page')

        if request.POST.get('verify_type') == 'self_reset':
            cnic_ok = bool(staff.cnic) and str(staff.cnic).replace('-', '').replace(' ', '').lower() == str(cnic).replace('-', '').replace(' ', '').lower()
            dob_ok = bool(staff.date_of_birth) and staff.date_of_birth.isoformat() == str(dob)
            if not cnic_ok or not dob_ok:
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'message': 'Your CNIC and date of birth do not match the staff record.'}, status=400)
                messages.error(request, 'Your CNIC and date of birth do not match the staff record.')
                return redirect('staff_profile_page')

    with schema_context('public'):
        credential = StaffCredential.objects.filter(staff_id=request.session['staff_id'], schema_name=schema_name).first()
        if not credential or not credential.check_password(old_password):
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': 'Current password is incorrect.'}, status=400)
            messages.error(request, 'Current password is incorrect.')
            return redirect('staff_profile_page')
        if new_password != confirm_password:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': 'New passwords do not match.'}, status=400)
            messages.error(request, 'New passwords do not match.')
            return redirect('staff_profile_page')
        if len(new_password) < 12 or not any(ch.isupper() for ch in new_password) or not any(ch.isdigit() for ch in new_password) or not any(ch in '!@#$%^&*' for ch in new_password):
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': 'Password must contain at least 12 chars, one uppercase, one digit, and one symbol.'}, status=400)
            messages.error(request, 'Password must contain at least 12 chars, one uppercase, one digit, and one symbol.')
            return redirect('staff_profile_page')
        credential.set_password(new_password)
        credential.save(update_fields=['password', 'visible_password'])
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'message': 'Password updated successfully.'})
        messages.success(request, 'Password updated successfully.')
        return redirect('staff_profile_page')


@require_staff_login
@require_http_methods(['GET'])
def staff_notifications(request):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        notifications = Notification.objects.all().order_by('-created_at')
    return render(request, 'mobile/staff/notifications.html', {'notifications': notifications})


@require_staff_login
@require_http_methods(['GET'])
def staff_more(request):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        staff = Staff.objects.get(pk=request.session['staff_id'])
        notifications_count = Notification.objects.filter(is_read=False).count()
    return render(request, 'mobile/staff/more.html', {
        'staff': staff,
        'notifications_count': notifications_count,
    })


@require_staff_login
@require_http_methods(['POST'])
def staff_mark_notification_read(request, notif_id):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        notification = get_object_or_404(Notification, pk=notif_id)
        notification.is_read = True
        notification.save(update_fields=['is_read'])
    return JsonResponse({'success': True})


@require_staff_login
def staff_student_profile(request, student_id):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        student = get_object_or_404(Student, pk=student_id)
        if student.school_class and not (student.school_class.class_teacher_id == staff.pk or student.school_class.class_subjects.filter(teacher=staff).exists()):
            return render(request, 'mobile/staff/403.html', status=403)
        attendance_summary = StudentAttendance.objects.filter(student=student).order_by('-date')[:10]
    return render(request, 'mobile/staff/student_profile.html', {'staff': staff, 'student': student, 'attendance_summary': attendance_summary})


@require_staff_login
def staff_api_classes(request):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        staff = Staff.objects.get(pk=request.session['staff_id'])
        classes = SchoolClass.objects.filter(Q(class_teacher=staff) | Q(class_subjects__teacher=staff)).distinct().annotate(student_count=Count('students')).order_by('name', 'section')
        payload = [
            {'id': cls.pk, 'name': str(cls), 'student_count': cls.student_count}
            for cls in classes
        ]
    return JsonResponse({'classes': payload})


@require_staff_login
def staff_api_attendance(request, class_id, attendance_date):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        allowed = school_class.class_teacher_id == staff.pk or school_class.class_subjects.filter(teacher=staff).exists()
        if not allowed:
            return JsonResponse({'error': 'Forbidden'}, status=403)
        records = list(StudentAttendance.objects.filter(school_class=school_class, date=attendance_date).values('student_id', 'status', 'remarks'))
    return JsonResponse({'attendance': records})


@require_staff_login
@require_http_methods(['POST'])
def staff_api_attendance_submit(request, class_id, attendance_date):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON payload'}, status=400)

    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        allowed = school_class.class_teacher_id == staff.pk or school_class.class_subjects.filter(teacher=staff).exists()
        if not allowed:
            return JsonResponse({'error': 'Forbidden'}, status=403)
        for record in payload.get('attendance', []):
            student_id = record.get('student_id')
            status = record.get('status', 'present')
            if student_id is None:
                continue
            student = Student.objects.filter(pk=student_id, school_class=school_class).first()
            if student is None:
                continue
            StudentAttendance.objects.update_or_create(
                student=student,
                date=datetime.strptime(attendance_date, '%Y-%m-%d').date(),
                defaults={'school_class': school_class, 'status': status, 'teacher': staff, 'remarks': record.get('remarks', '')},
            )
    return JsonResponse({'success': True})


@require_staff_login
def staff_api_profile(request):
    schema_name = request.session['staff_schema_name']
    from django_tenants.utils import schema_context
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
    return JsonResponse({'id': staff.pk, 'name': staff.full_name, 'role': staff.role, 'email': staff.email, 'phone': staff.phone})
