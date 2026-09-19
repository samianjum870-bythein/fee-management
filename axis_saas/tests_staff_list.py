"""STAFF_LIST_MEGA_V1 tests."""

import json

from django.test import override_settings
from django.urls import reverse
from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient

from axis_saas.models import (
    Staff, SchoolClass, Subject, ClassSubject, StaffCredential,
)


class StaffListBase(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.tenant.tenant_type = 'single_small_school'
        self.tenant.enabled_features = [
            'staff_management', 'students', 'classes_management',
            'timetable_management', 'dashboard',
        ]
        self.tenant.save()
        self.client = TenantClient(self.tenant)
        self._login()

    def _login(self):
        session = self.client.session
        session['school_admin_authenticated'] = True
        session['school_admin_schema'] = self.tenant.schema_name
        session['school_admin_username'] = 'admin'
        session.save()

    def _make_staff(self, **kw):
        defaults = dict(
            first_name='Alice', last_name='Anderson',
            job_title='Teacher', department='teaching', status='active',
        )
        defaults.update(kw)
        return Staff.objects.create(**defaults)

    def _make_class(self, name='Grade 1', section='A'):
        return SchoolClass.objects.create(name=name, section=section, is_active=True)

    def _make_subject(self, name='Math'):
        return Subject.objects.create(name=name)


class StaffListRenderTests(StaffListBase):
    def test_page_renders(self):
        self._make_staff()
        url = reverse('staff_list', kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Staff Management', resp.content.decode('utf-8'))

    def test_pagination_size_is_50(self):
        for i in range(60):
            self._make_staff(
                first_name=f'S{i}',
                last_name=f'L{i}',
                email=f's{i}@example.com',
            )
        url = reverse('staff_list', kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        page_obj = resp.context['staff']
        self.assertEqual(page_obj.paginator.per_page, 50)
        self.assertEqual(len(page_obj.object_list), 50)

    def test_search_filter(self):
        self._make_staff(first_name='Bob', last_name='Marley', email='bob@x.com')
        self._make_staff(first_name='Cara', last_name='Dune', email='cara@x.com')
        url = reverse('staff_list', kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url, {'q': 'Bob'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['staff']), 1)

    def test_department_filter(self):
        self._make_staff(first_name='T1', last_name='T1', department='teaching',
                         email='t1@x.com')
        self._make_staff(first_name='A1', last_name='A1', department='admin',
                         email='a1@x.com')
        url = reverse('staff_list', kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url, {'department': 'admin'})
        self.assertEqual(len(resp.context['staff']), 1)

    def test_status_filter(self):
        self._make_staff(first_name='A', last_name='A', email='a@x.com', status='active')
        self._make_staff(first_name='B', last_name='B', email='b@x.com', status='inactive')
        url = reverse('staff_list', kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url, {'status': 'inactive'})
        self.assertEqual(len(resp.context['staff']), 1)

    def test_analytics_context(self):
        self._make_staff(first_name='A', last_name='A', email='a@x.com')
        self._make_staff(first_name='B', last_name='B', email='b@x.com', status='inactive')
        url = reverse('staff_list', kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url)
        self.assertEqual(resp.context['analytics']['total'], 2)
        self.assertEqual(resp.context['analytics']['active'], 1)
        self.assertEqual(resp.context['analytics']['inactive'], 1)

    def test_feature_gate_404_when_disabled(self):
        self.tenant.enabled_features = ['dashboard']
        self.tenant.save()
        url = reverse('staff_list', kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)


class SubjectAssignmentsApiTests(StaffListBase):
    def test_requires_login(self):
        self.client.session.flush()
        url = reverse('staff_subject_assignments_api',
                      kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url)
        self.assertIn(resp.status_code, (301, 302, 403, 404))

    def test_list_returns_classes_subjects_teachers(self):
        c = self._make_class()
        s = self._make_subject()
        t = self._make_staff()
        ClassSubject.objects.create(
            school_class=c, subject=s, teacher=t, is_active=True,
        )
        url = reverse('staff_subject_assignments_api',
                      kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data['ok'])
        self.assertEqual(len(data['rows']), 1)
        self.assertEqual(len(data['classes']), 1)
        self.assertEqual(len(data['subjects']), 1)
        self.assertEqual(len(data['teachers']), 1)

    def test_assign_creates_assignment(self):
        c = self._make_class()
        s = self._make_subject()
        t = self._make_staff()
        url = reverse('staff_assign_subject_teacher_api',
                      kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.post(
            url,
            data=json.dumps({'class_id': c.id, 'subject_id': s.id,
                             'teacher_id': t.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertTrue(body['ok'])
        self.assertEqual(body['row']['teacher_id'], t.id)

    def test_assign_invalid_teacher(self):
        c = self._make_class()
        s = self._make_subject()
        url = reverse('staff_assign_subject_teacher_api',
                      kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.post(
            url,
            data=json.dumps({'class_id': c.id, 'subject_id': s.id,
                             'teacher_id': 99999}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_assign_missing_class(self):
        s = self._make_subject()
        url = reverse('staff_assign_subject_teacher_api',
                      kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.post(
            url,
            data=json.dumps({'subject_id': s.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_unassign(self):
        c = self._make_class()
        s = self._make_subject()
        t = self._make_staff()
        cs = ClassSubject.objects.create(
            school_class=c, subject=s, teacher=t, is_active=True,
        )
        url = reverse('staff_unassign_subject_teacher_api',
                      kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.post(
            url,
            data=json.dumps({'assignment_id': cs.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        cs.refresh_from_db()
        self.assertIsNone(cs.teacher)
        self.assertFalse(cs.is_active)


class ClassTeacherApiTests(StaffListBase):
    def test_list_returns_classes_and_candidates(self):
        self._make_class(name='Grade 1', section='A')
        self._make_staff()
        url = reverse('staff_class_teacher_management_api',
                      kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data['ok'])
        self.assertEqual(len(data['rows']), 1)
        self.assertEqual(len(data['candidates']), 1)

    def test_assign_class_teacher(self):
        c = self._make_class()
        t = self._make_staff()
        url = reverse('staff_assign_class_teacher_api',
                      kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.post(
            url,
            data=json.dumps({'class_id': c.id, 'teacher_id': t.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.class_teacher_id, t.id)

    def test_one_class_per_teacher_rule(self):
        c1 = self._make_class(name='Grade 1', section='A')
        c2 = self._make_class(name='Grade 2', section='A')
        t = self._make_staff()
        c1.class_teacher = t
        c1.save()
        url = reverse('staff_assign_class_teacher_api',
                      kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.post(
            url,
            data=json.dumps({'class_id': c2.id, 'teacher_id': t.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        c1.refresh_from_db()
        c2.refresh_from_db()
        self.assertIsNone(c1.class_teacher_id)
        self.assertEqual(c2.class_teacher_id, t.id)

    def test_unassign_class_teacher(self):
        c = self._make_class()
        t = self._make_staff()
        c.class_teacher = t
        c.save()
        url = reverse('staff_assign_class_teacher_api',
                      kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.post(
            url,
            data=json.dumps({'class_id': c.id, 'teacher_id': None}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        c.refresh_from_db()
        self.assertIsNone(c.class_teacher_id)


class StaffQuickStatsApiTests(StaffListBase):
    def test_stats_returns_expected_keys(self):
        self._make_staff()
        url = reverse('staff_quick_stats_api',
                      kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        for key in ('ok', 'total', 'active', 'inactive', 'class_teachers',
                    'subject_assignments', 'with_credentials'):
            self.assertIn(key, data)


class AuthTests(StaffListBase):
    def test_unauthenticated_redirects(self):
        self.client.session.flush()
        url = reverse('staff_list', kwargs={'schema_name': self.tenant.schema_name})
        resp = self.client.get(url)
        self.assertIn(resp.status_code, (301, 302))
