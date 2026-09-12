"""
AXIS views - class staff management API (CLASS_STAFF_MANAGEMENT_v1).

Endpoints used by the class-detailed page:
    POST /portal/<schema>/my-classes/<class_id>/assign-class-teacher/
    POST /portal/<schema>/my-classes/<class_id>/assign-subject-teacher/
"""
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from ..models import SchoolClass, Staff, Subject, ClassSubject
from .helpers import get_tenant, require_tenant_type, require_school_feature


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('classes_management')
def api_assign_class_teacher(request, schema_name, class_id):
    """Assign / replace / remove the class teacher of a given class.

    Rules:
      * teacher_id empty -> clear class_teacher.
      * teacher_id set   -> must reference an active Staff who is currently
        a subject teacher (of any class). Otherwise 400.
      * The chosen teacher is cleared from any other class where they were
        already class teacher (one-class-per-teacher rule).
    """
    teacher_id = (request.POST.get('teacher_id') or '').strip()

    with schema_context(schema_name):
        school_class = get_object_or_404(SchoolClass, id=class_id, is_active=True)

        if not teacher_id or teacher_id == 'none':
            school_class.class_teacher = None
            school_class.save(update_fields=['class_teacher'])
            return JsonResponse({'success': True, 'message': 'Class teacher removed.'})

        try:
            teacher = Staff.objects.get(id=int(teacher_id), status='active')
        except (Staff.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Invalid teacher.'}, status=400)

        # Eligibility: must teach at least one active subject (any class)
        is_subject_teacher = ClassSubject.objects.filter(
            teacher=teacher, is_active=True
        ).exists()
        if not is_subject_teacher:
            return JsonResponse({
                'success': False,
                'error': 'Only subject teachers (of any class) can become class teacher.'
            }, status=400)

        # One-class-per-teacher: clear other class teacher assignments
        SchoolClass.objects.filter(class_teacher=teacher).exclude(id=school_class.id).update(class_teacher=None)

        school_class.class_teacher = teacher
        school_class.save(update_fields=['class_teacher'])
        return JsonResponse({
            'success': True,
            'message': f'{teacher.full_name} assigned as class teacher.'
        })


@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('classes_management')
def api_assign_subject_teacher(request, schema_name, class_id):
    """Assign / replace / remove the teacher for a subject in a class.

    Body:
      subject_id  (required)
      teacher_id  (empty -> unassign the teacher)
    """
    subject_id = (request.POST.get('subject_id') or '').strip()
    teacher_id = (request.POST.get('teacher_id') or '').strip()

    if not subject_id:
        return JsonResponse({'success': False, 'error': 'Subject is required.'}, status=400)

    with schema_context(schema_name):
        school_class = get_object_or_404(SchoolClass, id=class_id, is_active=True)
        subject = get_object_or_404(Subject, id=subject_id, is_active=True)

        cs, _created = ClassSubject.objects.get_or_create(
            school_class=school_class,
            subject=subject,
            defaults={'is_active': True},
        )

        if not teacher_id or teacher_id == 'none':
            cs.teacher = None
            cs.save(update_fields=['teacher'])
            return JsonResponse({'success': True, 'message': 'Teacher removed from subject.'})

        try:
            teacher = Staff.objects.get(id=int(teacher_id), status='active')
        except (Staff.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Invalid teacher.'}, status=400)

        cs.teacher = teacher
        cs.is_active = True
        cs.save(update_fields=['teacher', 'is_active'])
        return JsonResponse({
            'success': True,
            'message': f'{teacher.full_name} assigned to {subject.name}.'
        })
