"""
AXIS views — single school class detail page.

Opens from the "View" button on a class card in /my-classes/.
Displays analytics + class teacher + subject teachers + students
(active & suspended only) for a single class.
"""
import logging

from django.shortcuts import render, get_object_or_404
from django_tenants.utils import schema_context

from ..models import SchoolClass, Student, ClassSubject, ClassTimetableAssignment, PeriodsTimetable, PeriodTeacherAssignment
import json
from .helpers import (
    get_tenant, require_tenant_type, require_school_feature, is_mobile_user_agent,
)
from axis_saas.utils.class_display import get_class_display_name


logger = logging.getLogger(__name__)


def _build_context(schema_name, tenant, class_id):
    with schema_context(schema_name):
        school_class = get_object_or_404(
            SchoolClass.objects.select_related('class_teacher'),
            id=class_id,
            is_active=True,
        )

        students_qs = Student.objects.filter(school_class=school_class)

        analytics = {
            'total_active':    students_qs.filter(status='active').count(),
            'total_suspended': students_qs.filter(status='suspended').count(),
            'males':           students_qs.filter(gender='male').count(),
            'females':         students_qs.filter(gender='female').count(),
        }
        analytics['visible'] = analytics['total_active'] + analytics['total_suspended']

        students = list(
            students_qs
            .filter(status__in=['active', 'suspended'])
            .order_by('status', 'roll_number', 'name')
        )

        class_teacher = school_class.class_teacher

        subject_assignments = list(
            ClassSubject.objects
            .filter(school_class=school_class, is_active=True)
            .select_related('subject', 'teacher')
            .order_by('subject__name')
        )

        display_name = get_class_display_name(school_class, tenant.tenant_type)

        # MANAGE_PERIODS_TIMETABLE_v1: assigned periods timetable
        try:
            _tt_assignment = ClassTimetableAssignment.objects.select_related('timetable').filter(school_class=school_class).first()
        except Exception:
            _tt_assignment = None
        assigned_timetable = None
        if _tt_assignment and _tt_assignment.timetable:
            _tt = _tt_assignment.timetable
            assigned_timetable = {
                'id': _tt.id,
                'title': _tt.title,
                'label': _tt.label or '',
                'break_duration': _tt.break_duration or 0,
                'days': _tt.days or [],
            }

        # ===== ASSIGN_TEACHERS_v1: period -> subject/teacher map =====
        try:
            _pta_qs = PeriodTeacherAssignment.objects.filter(school_class=school_class).select_related('subject', 'teacher')
        except Exception:
            _pta_qs = []
        _period_teacher_map = {}
        for _pta in _pta_qs:
            _k = f"{_pta.day_of_week}|{_pta.period_order}"
            _period_teacher_map[_k] = {
                'subject': _pta.subject.name if _pta.subject else '',
                'teacher': _pta.teacher.full_name if _pta.teacher else '',
            }
        period_teacher_map_json = json.dumps(_period_teacher_map)
        # ===== END ASSIGN_TEACHERS_v1 =====

        # ===== CLASS_STAFF_MANAGEMENT_v1 =====
        from ..models import Staff as _Staff, Subject as _Subject

        # --- Current class teacher details ---
        class_teacher_info = None
        _ct = school_class.class_teacher
        if _ct:
            _ct_subject_row = (
                ClassSubject.objects
                .filter(school_class=school_class, teacher=_ct, is_active=True)
                .select_related('subject')
                .first()
            )
            class_teacher_info = {
                'id': _ct.id,
                'full_name': _ct.full_name,
                'job_title': _ct.job_title or '',
                'cnic': _ct.cnic or '',
                'phone': _ct.phone or '',
                'email': _ct.email or '',
                'subject_in_class': _ct_subject_row.subject.name if _ct_subject_row else '',
            }

        # --- Subject teacher rows ---
        subject_teacher_rows = []
        for _cs in (
            ClassSubject.objects
            .filter(school_class=school_class, is_active=True)
            .select_related('subject', 'teacher')
            .order_by('subject__name')
        ):
            subject_teacher_rows.append({
                'subject_id': _cs.subject_id,
                'subject_name': _cs.subject.name,
                'teacher_id': _cs.teacher_id,
                'teacher_name': _cs.teacher.full_name if _cs.teacher else '',
                'is_class_teacher': bool(
                    _cs.teacher_id and school_class.class_teacher_id == _cs.teacher_id
                ),
            })

        # --- Eligible class teacher candidates (subject teachers anywhere) ---
        _teaching_staff_ids = list(
            ClassSubject.objects
            .filter(is_active=True, teacher__isnull=False)
            .values_list('teacher_id', flat=True)
            .distinct()
        )
        eligible_ct_candidates = []
        for _s in _Staff.objects.filter(id__in=_teaching_staff_ids, status='active').order_by('full_name'):
            _first_teach = (
                ClassSubject.objects
                .filter(teacher=_s, is_active=True)
                .select_related('subject', 'school_class')
                .first()
            )
            _teaches = ''
            if _first_teach:
                _cls_label = str(_first_teach.school_class)
                _teaches = f"{_first_teach.subject.name} ({_cls_label})"
            _ct_of = list(
                SchoolClass.objects
                .filter(class_teacher=_s, is_active=True)
                .exclude(id=school_class.id)
                .values_list('name', 'section')
            )
            _ct_of_labels = [
                (f"{n} - {sec}" if sec else n) for n, sec in _ct_of
            ]
            eligible_ct_candidates.append({
                'id': _s.id,
                'full_name': _s.full_name,
                'job_title': _s.job_title or '',
                'teaches': _teaches,
                'ct_of': _ct_of_labels,
            })

        # --- All active teachers (for subject-teacher picker) ---
        all_active_teachers = [
            {'id': _s.id, 'full_name': _s.full_name, 'job_title': _s.job_title or ''}
            for _s in _Staff.objects.filter(status='active').order_by('full_name')
        ]

        # --- All active subjects ---
        all_active_subjects = [
            {'id': _sub.id, 'name': _sub.name}
            for _sub in _Subject.objects.filter(is_active=True).order_by('name')
        ]

        import json as _json
        class_teacher_info_json = _json.dumps(class_teacher_info or {})
        subject_teacher_rows_json = _json.dumps(subject_teacher_rows)
        eligible_ct_candidates_json = _json.dumps(eligible_ct_candidates)
        all_active_teachers_json = _json.dumps(all_active_teachers)
        all_active_subjects_json = _json.dumps(all_active_subjects)
        # ===== END CLASS_STAFF_MANAGEMENT_v1 =====

        # ===== EDIT_PERIODS_TIMETABLE_v1: slots + all timetables for edit form =====
        from ..models import DaySchedule as _DaySchedule, ScheduleLabel as _ScheduleLabel
        _edit_labels = list(_ScheduleLabel.objects.all().order_by('name'))
        _edit_slots_by_label = {}
        for _lbl in _edit_labels:
            _scheds = _DaySchedule.objects.filter(label=_lbl.name).order_by('day_of_week', 'order')
            _edit_slots_by_label[_lbl.name] = [
                {
                    'id': _ds.id,
                    'day': _ds.day_of_week,
                    'day_label': _ds.get_day_of_week_display(),
                    'start': _ds.start_time.strftime('%H:%M'),
                    'end': _ds.end_time.strftime('%H:%M'),
                    'periods': _ds.periods,
                    'duration': _ds.duration,
                }
                for _ds in _scheds
            ]
        _edit_timetables = []
        for _tt in PeriodsTimetable.objects.order_by('id'):
            _edit_timetables.append({
                'id': _tt.id,
                'title': _tt.title,
                'label': _tt.label or '',
                'break_duration': _tt.break_duration or 0,
                'days': _tt.days or [],
            })
        # ===== END EDIT_PERIODS_TIMETABLE_v1 =====

    return {
        'tenant': tenant,
        'class_obj': school_class,
        'class_display_name': display_name,
        'assigned_timetable': assigned_timetable,
        'assigned_timetable_json': json.dumps(assigned_timetable or {}),
        'period_teacher_map_json': period_teacher_map_json,
        'class_teacher_info_json': class_teacher_info_json,
        'subject_teacher_rows_json': subject_teacher_rows_json,
        'eligible_ct_candidates_json': eligible_ct_candidates_json,
        'all_active_teachers_json': all_active_teachers_json,
        'all_active_subjects_json': all_active_subjects_json,
        'labels': _edit_labels,
        'slots_by_label_json': json.dumps(_edit_slots_by_label),
        'timetables_json': json.dumps(_edit_timetables),
        'students': students,
        'analytics': analytics,
        'class_teacher': class_teacher,
        'subject_assignments': subject_assignments,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
    }


@require_tenant_type(['single_small_school', 'school'])
@require_school_feature('classes_management')
def single_class_detailed_view(request, schema_name, class_id):
    tenant = get_tenant(request, schema_name)
    context = _build_context(schema_name, tenant, class_id)
    response = render(request, 'tenant/single_class_detailed.html', context)
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response
