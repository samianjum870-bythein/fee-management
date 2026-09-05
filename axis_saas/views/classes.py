"""
AXIS views – class & subject management.
"""

import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages
from django.http import JsonResponse, HttpResponseRedirect
from django.db.models import Q
from django.core.paginator import Paginator
from django_tenants.utils import schema_context
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
import json

from ..models import SchoolClass, Subject, ClassSubject, Staff, Student
from ..forms import ClassForm, SubjectForm, ClassSubjectForm
from .helpers import get_tenant, is_mobile_user_agent, require_tenant_type, require_school_feature

def redirect_with_cache_bust(url_name, schema_name, **kwargs):
    """Return a HttpResponseRedirect with cache-control headers and a timestamp."""
    from django.shortcuts import redirect
    from django.urls import reverse
    import time
    url = reverse(url_name, kwargs={'schema_name': schema_name}) + '?updated=' + str(int(time.time()))
    response = redirect(url)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


# ========== CLASS MANAGEMENT ==========

@require_tenant_type(['school'])
@require_school_feature('class_management')
def class_management(request, schema_name):
    """Main page for class & subject management."""
    tenant = get_tenant(request, schema_name)
    if is_mobile_user_agent(request):
        return redirect_with_cache_bust('mobile_class_management', schema_name)

    context = get_class_management_context(request, schema_name)
    template = 'tenant/wing_school_class_management.html' if tenant.tenant_type == 'wing_school' else 'tenant/class_management.html'
    response = render(request, template, context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response

@require_tenant_type(['school'])
@require_school_feature('class_management')
def mobile_class_management(request, schema_name):
    """Mobile version of class management."""
    context = get_class_management_context(request, schema_name)
    template = 'mobile/wing_school_class_management.html' if get_tenant(request, schema_name).tenant_type == 'wing_school' else 'mobile/class_management.html'
    response = render(request, template, context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response

def get_class_management_context(request, schema_name):
    tenant = get_tenant(request, schema_name)
    with schema_context(schema_name):
        # Active classes for display
        classes = SchoolClass.objects.filter(is_active=True).order_by('name', 'section')
        # Compute student strength and class teacher for each class
        for cls in classes:
            cls.student_count = Student.objects.filter(school_class=cls).count() if hasattr(Student, 'school_class') else Student.objects.filter(grade=cls.name, section=cls.section).count()
            cls.class_teacher_name = cls.class_teacher.full_name if cls.class_teacher else None

        # All classes (including inactive) for debug
        all_classes = SchoolClass.objects.all().select_related('wing_category').order_by('name', 'section')
        # Active subjects
        subjects = Subject.objects.filter(is_active=True).order_by('name')
        all_subjects = Subject.objects.all().order_by('name')
        # Active assignments
        assignments = ClassSubject.objects.filter(is_active=True).select_related('school_class', 'subject', 'teacher').order_by('school_class__name', 'subject__name')

        class_subject_map = {}
        for a in assignments:
            key = f"{a.school_class.id}"
            if key not in class_subject_map:
                class_subject_map[key] = []
            class_subject_map[key].append(a)

        class_form = ClassForm(wing_school=tenant.tenant_type == 'wing_school')
        subject_form = SubjectForm()
        assign_form = ClassSubjectForm()
        assign_form.fields['teacher'].queryset = Staff.objects.filter(status='active')

        total_classes = classes.count()
        total_all_classes = all_classes.count()
        total_subjects = subjects.count()
        total_all_subjects = all_subjects.count()
        total_assignments = assignments.count()
        unassigned_subjects = Subject.objects.filter(is_active=True).exclude(id__in=assignments.values_list('subject_id', flat=True)).count()

        logger = logging.getLogger(__name__)
        logger.info('Class summary for schema=%s totals: classes=%s all_classes=%s subjects=%s all_subjects=%s assignments=%s', schema_name, total_classes, total_all_classes, total_subjects, total_all_subjects, total_assignments)

        # Convert querysets to lists for debug display
        debug_all_classes = list(all_classes.values('id', 'name', 'section', 'is_active'))
        debug_all_subjects = list(all_subjects.values('id', 'name', 'is_active'))

        return {
            'tenant': tenant,
            'classes': classes,
            'subjects': subjects,
            'assignments': assignments,
            'class_subject_map': class_subject_map,
            'class_form': class_form,
            'subject_form': subject_form,
            'assign_form': assign_form,
            'total_classes': total_classes,
            'total_subjects': total_subjects,
            'total_assignments': total_assignments,
            'unassigned_subjects': unassigned_subjects,
            'debug_all_classes': debug_all_classes,
            'debug_all_subjects': debug_all_subjects,
            'total_all_classes': total_all_classes,
            'total_all_subjects': total_all_subjects,
            'logo_url': tenant.school_logo.url if tenant.school_logo else None,
        }

# ========== CRUD FOR CLASS ==========

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school'])
@require_school_feature('class_management')
def add_class(request, schema_name):
    """Add a new class."""
    with schema_context(schema_name):
        tenant = get_tenant(request, schema_name)
        form = ClassForm(request.POST, wing_school=tenant.tenant_type == 'wing_school')
        if form.is_valid():
            cls = form.save(commit=False)
            cls.is_active = True
            cls.save()
            messages.success(request, f"Class '{cls}' added successfully.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
            return redirect_with_cache_bust('mobile_class_management', schema_name)
        return redirect_with_cache_bust('class_management', schema_name)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school'])
@require_school_feature('class_management')
def edit_class(request, schema_name, class_id):
    """Edit an existing class."""
    with schema_context(schema_name):
        cls = get_object_or_404(SchoolClass, id=class_id)
        form = ClassForm(request.POST, instance=cls, wing_school=get_tenant(request, schema_name).tenant_type == 'wing_school')
        if form.is_valid():
            form.save()
            messages.success(request, f"Class '{cls}' updated.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
            return redirect_with_cache_bust('mobile_class_management', schema_name)
        return redirect_with_cache_bust('class_management', schema_name)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school'])
@require_school_feature('class_management')
def delete_class(request, schema_name, class_id):
    """Soft delete a class (set is_active=False)."""
    with schema_context(schema_name):
        cls = get_object_or_404(SchoolClass, id=class_id)
        cls.is_active = False
        cls.save()
        messages.success(request, f"Class '{cls}' deactivated.")
    if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
        return redirect_with_cache_bust('mobile_class_management', schema_name)
        return redirect_with_cache_bust('class_management', schema_name)

# ========== CRUD FOR SUBJECT ==========

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school'])
@require_school_feature('class_management')
def add_subject(request, schema_name):
    """Add a new subject."""
    with schema_context(schema_name):
        form = SubjectForm(request.POST)
        if form.is_valid():
            subj = form.save(commit=False)
            subj.is_active = True
            subj.save()
            messages.success(request, f"Subject '{subj}' added successfully.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
            return redirect_with_cache_bust('mobile_class_management', schema_name)
        return redirect_with_cache_bust('class_management', schema_name)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school'])
@require_school_feature('class_management')
def edit_subject(request, schema_name, subject_id):
    """Edit an existing subject."""
    with schema_context(schema_name):
        subj = get_object_or_404(Subject, id=subject_id)
        form = SubjectForm(request.POST, instance=subj)
        if form.is_valid():
            form.save()
            messages.success(request, f"Subject '{subj}' updated.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
            return redirect_with_cache_bust('mobile_class_management', schema_name)
        return redirect_with_cache_bust('class_management', schema_name)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school'])
@require_school_feature('class_management')
def delete_subject(request, schema_name, subject_id):
    """Soft delete a subject (set is_active=False)."""
    with schema_context(schema_name):
        subj = get_object_or_404(Subject, id=subject_id)
        subj.is_active = False
        subj.save()
        messages.success(request, f"Subject '{subj}' deactivated.")
    if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
        return redirect_with_cache_bust('mobile_class_management', schema_name)
        return redirect_with_cache_bust('class_management', schema_name)

# ========== ASSIGNMENT (ClassSubject) ==========

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school'])
@require_school_feature('class_management')
def assign_subject(request, schema_name):
    """Assign a subject to a class with a teacher."""
    with schema_context(schema_name):
        form = ClassSubjectForm(request.POST)
        if form.is_valid():
            assignment = form.save(commit=False)
            assignment.is_active = True
            assignment.save()
            messages.success(request, f"Subject '{assignment.subject}' assigned to class '{assignment.school_class}' with teacher {assignment.teacher.full_name if assignment.teacher else 'None'}.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
            return redirect_with_cache_bust('mobile_class_management', schema_name)
        return redirect_with_cache_bust('class_management', schema_name)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school'])
@require_school_feature('class_management')
def edit_assignment(request, schema_name, assignment_id):
    """Edit an existing assignment (e.g., change teacher)."""
    with schema_context(schema_name):
        assignment = get_object_or_404(ClassSubject, id=assignment_id)
        form = ClassSubjectForm(request.POST, instance=assignment)
        if form.is_valid():
            form.save()
            messages.success(request, f"Assignment updated.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
            return redirect_with_cache_bust('mobile_class_management', schema_name)
        return redirect_with_cache_bust('class_management', schema_name)

@csrf_exempt
@require_http_methods(["POST"])
@require_tenant_type(['school'])
@require_school_feature('class_management')
def delete_assignment(request, schema_name, assignment_id):
    """Soft delete an assignment (set is_active=False)."""
    with schema_context(schema_name):
        assignment = get_object_or_404(ClassSubject, id=assignment_id)
        assignment.is_active = False
        assignment.save()
        messages.success(request, f"Assignment deactivated.")
    if is_mobile_user_agent(request) or request.POST.get('mobile_redirect') == '1':
        return redirect_with_cache_bust('mobile_class_management', schema_name)
        return redirect_with_cache_bust('class_management', schema_name)


@require_tenant_type(['school'])
def class_strength_api(request, schema_name):
    """Return the number of students in a given class."""
    from django.http import JsonResponse
    from ..models import SchoolClass, Student
    class_id = request.GET.get('class_id')
    if not class_id:
        return JsonResponse({'error': 'class_id required'}, status=400)
    try:
        cls = SchoolClass.objects.get(id=class_id)
        count = Student.objects.filter(school_class=cls).count()
        return JsonResponse({'strength': count})
    except SchoolClass.DoesNotExist:
        return JsonResponse({'error': 'Class not found'}, status=404)

