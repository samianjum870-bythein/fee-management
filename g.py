#!/usr/bin/env python3
"""
axis_patcher.py
===============

STAFF_BIOMETRIC_ADMIN_CONTROL_V1
--------------------------------

Gives the **school admin** the power to enable / disable biometric login
for any individual staff member from that staff member's profile page.

Behaviour:
  * Admin enables  -> identical to today: staff MUST register & use
                      biometric (fingerprint / Face ID / passkey) to log
                      in.
  * Admin disables -> staff biometric is bypassed end-to-end:
                      staff signs in with username + password only.
                      No forced redirect to the biometric setup page.
                      No "Biometric verification is required" error.
                      No biometric WebAuthn prompt on the login screen.

Implementation summary
-----------------------
1. Adds `Staff.biometric_login_enabled` (BooleanField, default=True).
2. New migration `0028_staff_biometric_login_enabled.py`.
3. `staff_login` view respects the flag.
4. `staff_biometric_prepare_login` view respects the flag.
5. `StaffTenantMiddleware` skips the biometric-setup redirect when the
   flag is False.
6. `staff_biometric_setup` view redirects to dashboard when flag False.
7. New admin endpoint:
       POST /portal/<schema>/staff/<staff_id>/toggle-biometric/
   exposed as `staff_toggle_biometric` in `axis_saas.views.staff`.
8. New URL route + views-`__init__` export.
9. Adds a toggle card in `templates/tenant/staff_profile.html`.
10. Extends `get_staff_profile_context` to expose the current state +
    registered device count.

Idempotent, safe to re-run.

Usage:
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py --target-dir /path/to/project
    python3 axis_patcher.py                       # apply in place
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


MARKER = "STAFF_BIOMETRIC_ADMIN_CONTROL_V1"


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def _replace_once(content, old, new, label, verbose, marker=None):
    """Replace first literal occurrence.

    If `marker` is supplied and already present anywhere in `content`,
    the edit is considered already applied and skipped.
    """
    if marker and marker in content:
        if verbose:
            log(f"  SKIP (already patched): {label}")
        return content, False
    if old not in content:
        log(f"  WARN: {label} — anchor not found; leaving it alone.")
        return content, False
    content = content.replace(old, new, 1)
    if verbose:
        log(f"  patched: {label}")
    return content, True


def _write(path, content, dry_run, verbose, label):
    if dry_run:
        log(f"  DRY-RUN: would write {path} ({label})")
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as exc:
        log(f"  ERROR writing {path}: {exc}")
        return False


def _read(path):
    return path.read_text(encoding="utf-8")


# =====================================================================
# 1) models.py — add Staff.biometric_login_enabled
# =====================================================================
MODELS_REL = Path("axis_saas") / "models.py"

MODELS_ANCHOR = """    photo = models.ImageField(upload_to='staff_photos/', blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_on = models.DateTimeField(auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_on']
"""

MODELS_NEW = """    photo = models.ImageField(upload_to='staff_photos/', blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    # ========== STAFF_BIOMETRIC_ADMIN_CONTROL_V1 ==========
    # Per-staff switch that only the school admin can flip.
    # True  -> the staff member must register & use biometric to sign in.
    # False -> biometric is bypassed; username + password login only.
    biometric_login_enabled = models.BooleanField(
        default=True,
        help_text=(
            "If True (default), this staff member must register and use "
            "biometric (fingerprint / Face ID / passkey) to sign in. "
            "If False, biometric is bypassed and the staff member signs "
            "in with username and password only. Managed by the school "
            "admin from the staff profile page."
        ),
    )
    # =====================================================
    created_on = models.DateTimeField(auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_on']
"""


def patch_models(root, dry_run, verbose):
    path = root / MODELS_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = _read(path)

    if "biometric_login_enabled" in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, changed = _replace_once(
        content, MODELS_ANCHOR, MODELS_NEW,
        "models.py: Staff.biometric_login_enabled", verbose,
    )
    if not changed:
        return False
    return _write(path, content, dry_run, verbose, "Staff field")


# =====================================================================
# 2) migration 0028
# =====================================================================
MIGRATION_REL = (
    Path("axis_saas") / "migrations" / "0028_staff_biometric_login_enabled.py"
)

MIGRATION_CONTENT = '''# Generated by axis_patcher — STAFF_BIOMETRIC_ADMIN_CONTROL_V1
#
# Adds Staff.biometric_login_enabled. When False, biometric is bypassed
# for that staff member and username+password login works normally.
# Only the school admin can toggle this from the staff profile page.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('axis_saas', '0027_substitute_assignment'),
    ]

    operations = [
        migrations.AddField(
            model_name='staff',
            name='biometric_login_enabled',
            field=models.BooleanField(
                default=True,
                help_text=(
                    "If True (default), this staff member must register and "
                    "use biometric (fingerprint / Face ID / passkey) to sign "
                    "in. If False, biometric is bypassed and the staff member "
                    "signs in with username and password only. Managed by "
                    "the school admin from the staff profile page."
                ),
            ),
        ),
    ]
'''


def patch_migration(root, dry_run, verbose):
    path = root / MIGRATION_REL
    if path.is_file():
        log(f"SKIP (already exists): {path}")
        return True
    return _write(path, MIGRATION_CONTENT, dry_run, verbose, "new migration")


# =====================================================================
# 3) staff_portal.py — respect the flag in staff_login
# =====================================================================
STAFF_PORTAL_REL = Path("axis_saas") / "views" / "staff_portal.py"

STAFF_PORTAL_LOGIN_OLD = """                with schema_context('public'):
                    biometric_enabled = StaffBiometricCredential.objects.filter(
                        staff_id=staff.pk,
                        schema_name=credential.schema_name,
                        enabled=True,
                    ).exists()
                if biometric_enabled:
                    return render(request, 'mobile/staff/login.html', {
                        'error': 'Biometric verification is required for this account. Please use a registered device.',
                        'biometric_available': True,
                    })
"""

STAFF_PORTAL_LOGIN_NEW = """                with schema_context('public'):
                    biometric_enabled = StaffBiometricCredential.objects.filter(
                        staff_id=staff.pk,
                        schema_name=credential.schema_name,
                        enabled=True,
                    ).exists()
                # STAFF_BIOMETRIC_ADMIN_CONTROL_V1: only force biometric when
                # the school admin has left biometric enabled for this
                # specific staff member. If disabled, allow password login
                # to succeed exactly as if no biometric credential existed.
                _staff_biometric_allowed = getattr(
                    staff, 'biometric_login_enabled', True,
                )
                if biometric_enabled and _staff_biometric_allowed:
                    return render(request, 'mobile/staff/login.html', {
                        'error': 'Biometric verification is required for this account. Please use a registered device.',
                        'biometric_available': True,
                    })
"""

STAFF_PORTAL_SETUP_OLD = """@require_staff_login
@require_http_methods(['GET'])
@require_staff_feature('staff_profile')
def staff_biometric_setup(request):
    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
    return render(request, 'mobile/staff/biometric_setup.html', {'staff': staff})
"""

STAFF_PORTAL_SETUP_NEW = """@require_staff_login
@require_http_methods(['GET'])
@require_staff_feature('staff_profile')
def staff_biometric_setup(request):
    schema_name = request.session['staff_schema_name']
    with schema_context(schema_name):
        staff = get_object_or_404(Staff, pk=request.session['staff_id'])
    # STAFF_BIOMETRIC_ADMIN_CONTROL_V1: if the school admin disabled
    # biometric for this staff member, do not force the setup page on
    # them — send them straight to the dashboard.
    if not getattr(staff, 'biometric_login_enabled', True):
        return redirect('staff_dashboard')
    return render(request, 'mobile/staff/biometric_setup.html', {'staff': staff})
"""


def patch_staff_portal(root, dry_run, verbose):
    path = root / STAFF_PORTAL_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = _read(path)
    any_change = False

    content, c1 = _replace_once(
        content,
        STAFF_PORTAL_LOGIN_OLD,
        STAFF_PORTAL_LOGIN_NEW,
        "staff_portal.py: staff_login biometric gate",
        verbose,
        marker="STAFF_BIOMETRIC_ADMIN_CONTROL_V1: only force biometric when",
    )
    any_change = any_change or c1

    content, c2 = _replace_once(
        content,
        STAFF_PORTAL_SETUP_OLD,
        STAFF_PORTAL_SETUP_NEW,
        "staff_portal.py: staff_biometric_setup redirect",
        verbose,
        marker="do not force the setup page on",
    )
    any_change = any_change or c2

    if not any_change:
        return True
    return _write(path, content, dry_run, verbose, "staff_portal.py")


# =====================================================================
# 4) staff_biometric.py — respect the flag in prepare_login
# =====================================================================
STAFF_BIOMETRIC_REL = Path("axis_saas") / "views" / "staff_biometric.py"

STAFF_BIOMETRIC_PREP_OLD = """    with schema_context('public'):
        biometrics = list(StaffBiometricCredential.objects.filter(
            staff_id=staff.pk,
            schema_name=credential.schema_name,
            enabled=True,
        ))
    if not biometrics:
        return JsonResponse({'ok': True, 'biometric_enabled': False, 'message': 'No biometric credential found.'})
"""

STAFF_BIOMETRIC_PREP_NEW = """    # STAFF_BIOMETRIC_ADMIN_CONTROL_V1: if the school admin disabled
    # biometric for this staff member, immediately tell the client that
    # no biometric step is needed and let the normal password login
    # proceed on the server side.
    if not getattr(staff, 'biometric_login_enabled', True):
        return JsonResponse({
            'ok': True,
            'biometric_enabled': False,
            'message': 'Biometric is disabled for this account by the school admin.',
        })

    with schema_context('public'):
        biometrics = list(StaffBiometricCredential.objects.filter(
            staff_id=staff.pk,
            schema_name=credential.schema_name,
            enabled=True,
        ))
    if not biometrics:
        return JsonResponse({'ok': True, 'biometric_enabled': False, 'message': 'No biometric credential found.'})
"""


def patch_staff_biometric(root, dry_run, verbose):
    path = root / STAFF_BIOMETRIC_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = _read(path)

    if "Biometric is disabled for this account by the school admin" in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, changed = _replace_once(
        content,
        STAFF_BIOMETRIC_PREP_OLD,
        STAFF_BIOMETRIC_PREP_NEW,
        "staff_biometric.py: prepare_login gate",
        verbose,
    )
    if not changed:
        return False
    return _write(path, content, dry_run, verbose, "staff_biometric.py")


# =====================================================================
# 5) middleware — skip forced setup when flag is False
# =====================================================================
MIDDLEWARE_REL = Path("axis_saas") / "middleware" / "staff_tenant_middleware.py"

MIDDLEWARE_OLD = """        with schema_context('public'):
            biometric_enabled = StaffBiometricCredential.objects.filter(
                staff_id=staff_id,
                schema_name=schema_name,
                enabled=True,
            ).exists()
        if not biometric_enabled and request.path_info != '/portal/staff/biometric/setup/':
            return redirect('staff_biometric_setup')
"""

MIDDLEWARE_NEW = """        with schema_context('public'):
            biometric_enabled = StaffBiometricCredential.objects.filter(
                staff_id=staff_id,
                schema_name=schema_name,
                enabled=True,
            ).exists()

        # STAFF_BIOMETRIC_ADMIN_CONTROL_V1: read the per-staff admin switch.
        # When False, biometric is bypassed entirely — do NOT redirect the
        # staff member to the setup page even if they have never
        # registered a credential.
        try:
            with schema_context(schema_name):
                _staff_biometric_allowed = (
                    Staff.objects
                    .filter(pk=staff_id)
                    .values_list('biometric_login_enabled', flat=True)
                    .first()
                )
        except Exception:
            _staff_biometric_allowed = None
        if _staff_biometric_allowed is None:
            _staff_biometric_allowed = True

        if (
            _staff_biometric_allowed
            and not biometric_enabled
            and request.path_info != '/portal/staff/biometric/setup/'
        ):
            return redirect('staff_biometric_setup')
"""


def patch_middleware(root, dry_run, verbose):
    path = root / MIDDLEWARE_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = _read(path)

    if "STAFF_BIOMETRIC_ADMIN_CONTROL_V1" in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, changed = _replace_once(
        content,
        MIDDLEWARE_OLD,
        MIDDLEWARE_NEW,
        "staff_tenant_middleware.py: admin flag",
        verbose,
    )
    if not changed:
        return False
    return _write(path, content, dry_run, verbose, "middleware")


# =====================================================================
# 6) staff.py views — new toggle endpoint + enriched profile context
# =====================================================================
STAFF_VIEWS_REL = Path("axis_saas") / "views" / "staff.py"

STAFF_VIEWS_IMPORT_OLD = (
    "from ..models import SchoolClient, Staff, StaffCredential, SchoolClass, ClassSubject"
)

STAFF_VIEWS_IMPORT_NEW = (
    "from ..models import (\n"
    "    SchoolClient, Staff, StaffCredential, StaffBiometricCredential,\n"
    "    SchoolClass, ClassSubject,\n"
    ")"
)

STAFF_VIEWS_CONTEXT_OLD = """    with schema_context('public'):
        credential = StaffCredential.objects.filter(staff_id=staff.id, schema_name=schema_name).first()
    if credential is not None:
"""

STAFF_VIEWS_CONTEXT_NEW = """    with schema_context('public'):
        credential = StaffCredential.objects.filter(staff_id=staff.id, schema_name=schema_name).first()
        # STAFF_BIOMETRIC_ADMIN_CONTROL_V1: count devices so the admin
        # can see whether any biometric credential is currently
        # registered for this staff member.
        biometric_device_count = StaffBiometricCredential.objects.filter(
            staff_id=staff.id,
            schema_name=schema_name,
            enabled=True,
        ).count()
    if credential is not None:
"""

STAFF_VIEWS_RETURN_OLD = """    return {
        'tenant': tenant,
        'classes': classes,
        'sections': sections,
        'selected_class_id': class_id,
        'selected_section': section,
        'staff': staff,
        'credential': credential,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
        'assigned_classes': assigned_classes,
        'class_teacher_classes': class_teacher_classes,
    }
"""

STAFF_VIEWS_RETURN_NEW = """    return {
        'tenant': tenant,
        'classes': classes,
        'sections': sections,
        'selected_class_id': class_id,
        'selected_section': section,
        'staff': staff,
        'credential': credential,
        'logo_url': tenant.school_logo.url if tenant.school_logo else None,
        'assigned_classes': assigned_classes,
        'class_teacher_classes': class_teacher_classes,
        # STAFF_BIOMETRIC_ADMIN_CONTROL_V1
        'biometric_login_enabled': getattr(staff, 'biometric_login_enabled', True),
        'biometric_device_count': biometric_device_count,
    }
"""


STAFF_VIEWS_TOGGLE = '''
# =====================================================================
# STAFF_BIOMETRIC_ADMIN_CONTROL_V1
# ---------------------------------------------------------------------
# Admin-only endpoint: flip Staff.biometric_login_enabled for a single
# staff member from their profile page.
# =====================================================================

@require_tenant_type(['school'])
@require_school_feature('staff_management')
@require_http_methods(['POST'])
def staff_toggle_biometric(request, schema_name, staff_id):
    """Enable / disable biometric login for a specific staff member.

    POST param:
        enabled : "true"/"1"/"yes"/"on"  -> enable
                  anything else           -> disable
    """
    raw = request.POST.get('enabled', '')
    enabled = str(raw).strip().lower() in ('true', '1', 'yes', 'on')

    with schema_context(schema_name):
        staff = get_object_or_404(Staff, id=staff_id)
        staff.biometric_login_enabled = enabled
        staff.save(update_fields=['biometric_login_enabled'])

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'enabled': enabled,
            'message': (
                'Biometric login enabled for this staff member.'
                if enabled else
                'Biometric login disabled — staff can now sign in with '
                'username and password only.'
            ),
        })

    messages.success(
        request,
        'Biometric login updated successfully.' if enabled
        else 'Biometric login disabled for this staff member.'
    )
    return redirect('staff_profile', schema_name=schema_name, staff_id=staff_id)
'''


def patch_staff_views(root, dry_run, verbose):
    path = root / STAFF_VIEWS_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = _read(path)
    any_change = False

    # import
    if 'StaffBiometricCredential' not in content:
        content, c = _replace_once(
            content,
            STAFF_VIEWS_IMPORT_OLD,
            STAFF_VIEWS_IMPORT_NEW,
            "staff.py: import StaffBiometricCredential",
            verbose,
        )
        any_change = any_change or c

    # context enrichment
    if 'biometric_device_count' not in content:
        content, c = _replace_once(
            content,
            STAFF_VIEWS_CONTEXT_OLD,
            STAFF_VIEWS_CONTEXT_NEW,
            "staff.py: get_staff_profile_context biometric count",
            verbose,
        )
        any_change = any_change or c

        content, c = _replace_once(
            content,
            STAFF_VIEWS_RETURN_OLD,
            STAFF_VIEWS_RETURN_NEW,
            "staff.py: get_staff_profile_context return biometric keys",
            verbose,
        )
        any_change = any_change or c

    # new toggle view
    if "def staff_toggle_biometric(" not in content:
        content = content.rstrip() + "\n\n" + STAFF_VIEWS_TOGGLE
        if verbose:
            log("  patched: staff.py: staff_toggle_biometric appended")
        any_change = True

    if not any_change:
        log(f"SKIP (already patched): {path}")
        return True
    return _write(path, content, dry_run, verbose, "staff.py")


# =====================================================================
# 7) views/__init__.py — export new view
# =====================================================================
VIEWS_INIT_REL = Path("axis_saas") / "views" / "__init__.py"

VIEWS_INIT_OLD = """from .staff import (
    staff_list, mobile_staff_list, staff_profile, mobile_staff_profile,
    staff_add, staff_add_mobile, staff_edit, staff_search_api
)"""

VIEWS_INIT_NEW = """from .staff import (
    staff_list, mobile_staff_list, staff_profile, mobile_staff_profile,
    staff_add, staff_add_mobile, staff_edit, staff_search_api,
    staff_toggle_biometric,
)"""


def patch_views_init(root, dry_run, verbose):
    path = root / VIEWS_INIT_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = _read(path)

    if 'staff_toggle_biometric' in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, changed = _replace_once(
        content, VIEWS_INIT_OLD, VIEWS_INIT_NEW,
        "views/__init__.py: staff_toggle_biometric export", verbose,
    )
    if not changed:
        # Fallback: append an explicit import at the end.
        content = content.rstrip() + (
            "\n\nfrom .staff import staff_toggle_biometric  # noqa: F401\n"
        )
    return _write(path, content, dry_run, verbose, "views __init__")


# =====================================================================
# 8) public_urls.py — import + register new route
# =====================================================================
URLS_REL = Path("axis_saas") / "public_urls.py"

URLS_IMPORT_OLD = (
    "from .views.staff import staff_list, mobile_staff_list, staff_profile, "
    "mobile_staff_profile, staff_add, staff_add_mobile, staff_edit, "
    "staff_search_api, staff_toggle_status, staff_force_logout, "
    "staff_reset_password"
)

URLS_IMPORT_NEW = (
    "from .views.staff import staff_list, mobile_staff_list, staff_profile, "
    "mobile_staff_profile, staff_add, staff_add_mobile, staff_edit, "
    "staff_search_api, staff_toggle_status, staff_force_logout, "
    "staff_reset_password, staff_toggle_biometric"
)

URLS_ROUTE_ANCHOR = (
    "    path('portal/<slug:schema_name>/staff/<int:staff_id>/reset-password/', "
    "portal_wrapper(login_required_for_schema(staff_reset_password)), "
    "name='staff_reset_password'),\n"
)

URLS_ROUTE_NEW = (
    "    path('portal/<slug:schema_name>/staff/<int:staff_id>/reset-password/', "
    "portal_wrapper(login_required_for_schema(staff_reset_password)), "
    "name='staff_reset_password'),\n"
    "    # ===== STAFF_BIOMETRIC_ADMIN_CONTROL_V1 =====\n"
    "    path('portal/<slug:schema_name>/staff/<int:staff_id>/toggle-biometric/', "
    "portal_wrapper(login_required_for_schema(staff_toggle_biometric)), "
    "name='staff_toggle_biometric'),\n"
)


def patch_urls(root, dry_run, verbose):
    path = root / URLS_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = _read(path)

    if 'staff_toggle_biometric' in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, c1 = _replace_once(
        content, URLS_IMPORT_OLD, URLS_IMPORT_NEW,
        "public_urls.py: import staff_toggle_biometric", verbose,
    )
    content, c2 = _replace_once(
        content, URLS_ROUTE_ANCHOR, URLS_ROUTE_NEW,
        "public_urls.py: route staff_toggle_biometric", verbose,
    )
    if not (c1 and c2):
        log("ERROR: public_urls.py anchors not all found — leaving file alone.")
        return False
    return _write(path, content, dry_run, verbose, "public_urls.py")


# =====================================================================
# 9) templates/tenant/staff_profile.html — admin toggle card
# =====================================================================
TEMPLATE_REL = Path("templates") / "tenant" / "staff_profile.html"

TEMPLATE_ANCHOR = """<div class="action-panel" style="margin-top:1.5rem;">
    <div class="info-card">
        <h3>Account Control</h3>"""

TEMPLATE_NEW = """<!-- ===== STAFF_BIOMETRIC_ADMIN_CONTROL_V1 ===== -->
<div class="info-card" style="margin-top:1.5rem;" id="biometricControlCard">
    <h3>Staff Portal Biometric Login</h3>
    <p style="color:var(--muted); font-size:0.85rem; margin:0.5rem 0 0.75rem 0; line-height:1.5;">
        When <strong>enabled</strong>, this staff member must register and use
        biometric (fingerprint / Face ID / passkey) to sign in.<br>
        When <strong>disabled</strong>, biometric is bypassed and the staff member
        signs in with username and password only.
    </p>
    <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
        <span class="status-badge" style="background:{% if biometric_login_enabled %}#22C55E{% else %}#94A3B8{% endif %}; color:white; padding:0.3rem 0.85rem; border-radius:1rem; font-size:0.8rem; font-weight:600; letter-spacing:0.02em;">
            {% if biometric_login_enabled %}Enabled{% else %}Disabled{% endif %}
        </span>
        <span style="color:var(--muted); font-size:0.78rem;">
            Registered device(s): <strong>{{ biometric_device_count }}</strong>
        </span>
        <form method="post"
              action="{% url 'staff_toggle_biometric' schema_name=tenant.schema_name staff_id=staff.id %}"
              style="margin-left:auto; display:inline;">
            {% csrf_token %}
            <input type="hidden" name="enabled" value="{% if biometric_login_enabled %}false{% else %}true{% endif %}">
            {% if biometric_login_enabled %}
                <button type="submit" class="btn-secondary"
                        onclick="return confirm('Disable biometric login for {{ staff.full_name|escapejs }}? They will be able to sign in with username and password only.');">
                    Disable Biometric
                </button>
            {% else %}
                <button type="submit" class="btn-primary">
                    Enable Biometric
                </button>
            {% endif %}
        </form>
    </div>
</div>
<!-- ===== END STAFF_BIOMETRIC_ADMIN_CONTROL_V1 ===== -->

<div class="action-panel" style="margin-top:1.5rem;">
    <div class="info-card">
        <h3>Account Control</h3>"""


def patch_template(root, dry_run, verbose):
    path = root / TEMPLATE_REL
    if not path.is_file():
        log(f"ERROR: {path} not found")
        return False
    content = _read(path)

    if MARKER in content:
        log(f"SKIP (already patched): {path}")
        return True

    content, changed = _replace_once(
        content, TEMPLATE_ANCHOR, TEMPLATE_NEW,
        "staff_profile.html: biometric control card", verbose,
    )
    if not changed:
        return False
    return _write(path, content, dry_run, verbose, "staff_profile.html")


# =====================================================================
# main
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "STAFF_BIOMETRIC_ADMIN_CONTROL_V1 — let the school admin "
            "enable/disable biometric login per staff member from the "
            "staff profile page."
        )
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing.")
    parser.add_argument("--verbose", action="store_true",
                        help="Verbose output.")
    parser.add_argument("--target-dir", default=".",
                        help="Project root (default: current dir).")
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not (root / "manage.py").is_file():
        log(f"ERROR: manage.py not found in {root}. Wrong --target-dir?")
        return 1

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")

    ok = True

    log("--- 1/9: axis_saas/models.py (Staff.biometric_login_enabled) ---")
    ok &= patch_models(root, args.dry_run, args.verbose)

    log("--- 2/9: migrations/0028_staff_biometric_login_enabled.py ---")
    ok &= patch_migration(root, args.dry_run, args.verbose)

    log("--- 3/9: views/staff_portal.py (login + setup gates) ---")
    ok &= patch_staff_portal(root, args.dry_run, args.verbose)

    log("--- 4/9: views/staff_biometric.py (prepare_login gate) ---")
    ok &= patch_staff_biometric(root, args.dry_run, args.verbose)

    log("--- 5/9: middleware/staff_tenant_middleware.py ---")
    ok &= patch_middleware(root, args.dry_run, args.verbose)

    log("--- 6/9: views/staff.py (toggle view + context) ---")
    ok &= patch_staff_views(root, args.dry_run, args.verbose)

    log("--- 7/9: views/__init__.py (export) ---")
    ok &= patch_views_init(root, args.dry_run, args.verbose)

    log("--- 8/9: public_urls.py (route) ---")
    ok &= patch_urls(root, args.dry_run, args.verbose)

    log("--- 9/9: templates/tenant/staff_profile.html (toggle card) ---")
    ok &= patch_template(root, args.dry_run, args.verbose)

    if not ok:
        log("One or more steps failed. See messages above.")
        return 2

    log("Done.")
    if args.dry_run:
        log("Re-run without --dry-run to apply.")
    else:
        log("Next steps:")
        log("  1. python manage.py migrate_schemas --shared")
        log("  2. python manage.py migrate_schemas          # tenant schemas")
        log("  3. Restart the dev server.")
        log("  4. Open  /portal/<schema>/staff/<id>/  as the school admin")
        log("     — the new 'Staff Portal Biometric Login' card lets you")
        log("       enable/disable biometric per staff member.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
