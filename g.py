#!/usr/bin/env python3
"""
axis_patcher.py - Fix Staff Passkey second-factor and persistence issues.

This script applies changes to:
- axis_saas/views/staff_portal.py
- axis_saas/middleware/staff_tenant_middleware.py

It removes the pending_* session keys and introduces a staff_2fa_pending flag,
ensuring the password + passkey flow works correctly and users with existing
passkeys are not redirected to the profile page.

Usage:
    python axis_patcher.py [--dry-run] [--verbose] [--target-dir PATH]
"""

import os
import re
import sys
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple

# -----------------------------------------------------------------------------
# Logging setup
# -----------------------------------------------------------------------------
logger = logging.getLogger("axis_patcher")
logger.setLevel(logging.INFO)
console = logging.StreamHandler()
console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(console)

# -----------------------------------------------------------------------------
# New code blocks
# -----------------------------------------------------------------------------

# ---- staff_login replacement (full session, staff_2fa_pending) ----
STAFF_LOGIN_NEW = """@csrf_exempt
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

                has_passkey = bool(WebAuthnCredential.objects.filter(staff_credential=credential, is_active=True).exists())
                cache.delete(ip_key)
                cache.delete(f'{ip_key}_blocked_until')
                credential.reset_failed_attempts()
                credential.last_login = timezone.now()
                credential.save(update_fields=['last_login', 'failed_attempts', 'locked_until'])

                request.session.flush()
                request.session['school_admin_authenticated'] = False
                request.session['school_admin_schema'] = ''
                # Set actual identity immediately
                request.session['staff_id'] = staff.pk
                request.session['staff_schema_name'] = credential.schema_name
                request.session['staff_username'] = credential.username
                request.session['staff_role'] = staff.role
                request.session['staff_name'] = staff.full_name
                request.session['staff_session_token'] = uuid.uuid4().hex
                # Indicate that 2FA is required if passkey exists
                request.session['staff_2fa_pending'] = has_passkey
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

                if has_passkey:
                    return redirect('staff_verify_passkey')
                # No passkey, go directly to profile to register (or dashboard if desired)
                return redirect('staff_profile_page')

        cache.set(ip_key, attempts + 1, 60)
        if credential:
            credential.increment_failed_attempts()
        return render(request, 'mobile/staff/login.html', {'error': 'Invalid username or password.'})

    return render(request, 'mobile/staff/login.html')
"""

# ---- staff_verify_passkey replacement (use full session) ----
STAFF_VERIFY_PASSKEY_NEW = """@require_http_methods(['GET'])
def staff_verify_passkey(request):
    # Always use the session identity (no pending keys)
    staff_id = request.session.get('staff_id')
    schema_name = request.session.get('staff_schema_name')
    username = request.session.get('staff_username', '')
    logger.info('Rendering passkey verification for staff_id=%s username=%s schema=%s', staff_id, username, schema_name)
    if not staff_id or not schema_name:
        return redirect('staff_login')
    # If 2FA is not pending, redirect to dashboard
    if not request.session.get('staff_2fa_pending', False):
        return redirect('staff_dashboard')
    return render(request, 'mobile/staff/verify_passkey.html', {
        'staff_id': staff_id,
        'schema_name': schema_name,
        'username': username,
    })
"""

# ---- staff_webauthn_authentication_options replacement ----
STAFF_WEBAUTHN_AUTH_OPTIONS_NEW = """def staff_webauthn_authentication_options(request):
    data = {}
    try:
        if request.content_type and 'application/json' in request.content_type:
            try:
                data = json.loads(request.body.decode('utf-8') or '{}')
            except json.JSONDecodeError:
                data = {}

        # Use session identity directly
        staff_id = request.session.get('staff_id')
        schema_name = request.session.get('staff_schema_name')
        username = request.session.get('staff_username', '')
        logger.info('AUTH OPTIONS - staff_id=%s schema=%s username=%s', staff_id, schema_name, username)

        if not staff_id or not schema_name:
            logger.warning('AUTH OPTIONS - Missing staff_id or schema_name in session')
            return JsonResponse({'error': 'Authentication required.'}, status=401)

        with schema_context('public'):
            credential = StaffCredential.objects.filter(
                staff_id=staff_id,
                schema_name=schema_name,
                is_active=True,
            ).first()
            if credential is None:
                logger.warning('AUTH OPTIONS - No StaffCredential found for staff_id=%s schema=%s', staff_id, schema_name)
                return JsonResponse({'error': 'Account not found.'}, status=404)

            passkeys = list(WebAuthnCredential.objects.filter(staff_credential=credential, is_active=True))
            valid_passkeys = []
            for item in passkeys:
                try:
                    base64url_to_bytes(item.credential_id)
                    valid_passkeys.append(item)
                except Exception:
                    logger.warning('Skipping malformed WebAuthn credential id for staff credential %s', credential.pk)
            passkeys = valid_passkeys

            if not passkeys:
                logger.warning('AUTH OPTIONS - No valid passkeys for staff_id=%s', staff_id)
                return JsonResponse({'error': 'No valid passkeys registered for this account.'}, status=403)

            # Store for verification
            request.session['staff_webauthn_login_username'] = credential.username
            request.session['staff_webauthn_login_staff_id'] = credential.staff_id
            request.session['staff_webauthn_login_schema_name'] = credential.schema_name
            request.session.modified = True
            logger.info('AUTH OPTIONS - Found credential, username=%s, passkeys=%d', credential.username, len(passkeys))

        challenge = secrets.token_bytes(32)
        request.session['staff_webauthn_auth_challenge'] = bytes_to_base64url(challenge)
        allow_credentials = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id), type='public-key')
            for item in passkeys
        ] or None
        try:
            options = generate_authentication_options(
                rp_id=_staff_compute_rp_id(request),
                challenge=challenge,
                timeout=60000,
                allow_credentials=allow_credentials,
                user_verification=UserVerificationRequirement.REQUIRED,
            )
        except Exception as exc:
            logger.exception('generate_authentication_options failed: %s', exc)
            return JsonResponse({'error': 'This passkey challenge could not be prepared for your account. Please try again or sign in with your password again.'}, status=400)
        return JsonResponse(json.loads(options_to_json(options)))
    except Exception as exc:
        logger.exception('Staff WebAuthn authentication options failed: %s', exc)
        request.session.pop('staff_webauthn_auth_challenge', None)
        request.session.pop('staff_webauthn_login_username', None)
        request.session.pop('staff_webauthn_login_staff_id', None)
        request.session.pop('staff_webauthn_login_schema_name', None)
        request.session.modified = True
        return JsonResponse({'error': 'This passkey challenge could not be prepared for your account. Please try again or sign in with your password again.'}, status=400)
"""

# ---- staff_webauthn_authentication_verify replacement ----
STAFF_WEBAUTHN_AUTH_VERIFY_NEW = """@require_http_methods(['POST'])
def staff_webauthn_authentication_verify(request):
    logger.info('AUTH VERIFY - session keys before: %s', sorted(request.session.keys()))
    expected_challenge = request.session.get('staff_webauthn_auth_challenge')
    login_username = request.session.get('staff_webauthn_login_username') or request.session.get('staff_username')
    login_staff_id = request.session.get('staff_webauthn_login_staff_id') or request.session.get('staff_id')
    login_schema_name = request.session.get('staff_webauthn_login_schema_name') or request.session.get('staff_schema_name')

    if not expected_challenge:
        logger.warning('AUTH VERIFY - No expected challenge in session')
        return JsonResponse({'success': False, 'message': 'Passkey challenge expired. Please sign in again.'}, status=400)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        logger.warning('AUTH VERIFY - Invalid JSON payload')
        return JsonResponse({'success': False, 'message': 'Invalid passkey payload.'}, status=400)

    credential_id = payload.get('id')
    if not credential_id:
        logger.warning('AUTH VERIFY - Missing credential id')
        return JsonResponse({'success': False, 'message': 'Passkey identifier missing.'}, status=400)

    try:
        with schema_context('public'):
            webauthn_credential = WebAuthnCredential.objects.filter(credential_id=credential_id, is_active=True).select_related('staff_credential').first()
            logger.info('AUTH VERIFY - credential found=%s, expected_staff=%s, expected_schema=%s', bool(webauthn_credential), login_staff_id, login_schema_name)
            if webauthn_credential is None:
                return JsonResponse({'success': False, 'message': 'Passkey not recognized.'}, status=404)
            if login_username and webauthn_credential.staff_credential.username != login_username:
                logger.warning('AUTH VERIFY - username mismatch: provided=%s, credential=%s', login_username, webauthn_credential.staff_credential.username)
                return JsonResponse({'success': False, 'message': 'This passkey does not belong to the provided username.'}, status=403)
            if login_staff_id and str(webauthn_credential.staff_credential.staff_id) != str(login_staff_id):
                logger.warning('AUTH VERIFY - staff_id mismatch: provided=%s, credential=%s', login_staff_id, webauthn_credential.staff_credential.staff_id)
                return JsonResponse({'success': False, 'message': 'This passkey is not registered for the selected account.'}, status=403)
            if login_schema_name and webauthn_credential.staff_credential.schema_name != login_schema_name:
                logger.warning('AUTH VERIFY - schema mismatch: provided=%s, credential=%s', login_schema_name, webauthn_credential.staff_credential.schema_name)
                return JsonResponse({'success': False, 'message': 'This passkey belongs to a different tenant.'}, status=403)
            try:
                verification = verify_authentication_response(
                    credential=payload,
                    expected_challenge=base64url_to_bytes(expected_challenge),
                    expected_rp_id=_staff_compute_rp_id(request),
                    expected_origin=_staff_expected_origins(request),
                    credential_public_key=base64url_to_bytes(webauthn_credential.public_key),
                    credential_current_sign_count=webauthn_credential.sign_count,
                    require_user_verification=True,
                )
            except Exception as exc:
                logger.warning('WebAuthn authentication verification failed: %s', exc)
                return JsonResponse({'success': False, 'message': 'Passkey verification failed. Please try again.'}, status=400)
            webauthn_credential.sign_count = verification.new_sign_count
            webauthn_credential.last_used = timezone.now()
            webauthn_credential.save(update_fields=['sign_count', 'last_used'])

            # Ensure session is fully authenticated and clear 2FA pending
            target_staff_id = int(webauthn_credential.staff_credential.staff_id)
            target_schema_name = webauthn_credential.staff_credential.schema_name
            request.session['staff_id'] = target_staff_id
            request.session['staff_schema_name'] = target_schema_name
            request.session['staff_username'] = webauthn_credential.staff_credential.username
            request.session['staff_2fa_pending'] = False
            request.session['staff_session_token'] = uuid.uuid4().hex
            request.session.set_expiry(1800)
            request.session.modified = True

            with schema_context(target_schema_name):
                staff = Staff.objects.filter(pk=target_staff_id).first()
                if staff:
                    request.session['staff_role'] = staff.role
                    request.session['staff_name'] = staff.full_name

            token = request.session['staff_session_token']
            cache.set(f'staff_session_token:{target_schema_name}:{target_staff_id}', token, 1800)
            session_keys = cache.get(f'staff_session_keys:{target_schema_name}:{target_staff_id}', [])
            if isinstance(session_keys, str):
                session_keys = [session_keys]
            session_keys = [k for k in list(session_keys) if k]
            if request.session.session_key:
                session_keys.append(request.session.session_key)
            cache.set(f'staff_session_keys:{target_schema_name}:{target_staff_id}', list(dict.fromkeys(session_keys)), 1800)
            cache.set(f'staff_online:{target_schema_name}:{target_staff_id}', request.session.session_key, 1800)

        request.session.pop('staff_webauthn_auth_challenge', None)
        request.session.pop('staff_webauthn_login_username', None)
        request.session.pop('staff_webauthn_login_staff_id', None)
        request.session.pop('staff_webauthn_login_schema_name', None)
        request.session.modified = True
        logger.info('AUTH VERIFY - success, redirecting to dashboard')
        return JsonResponse({'success': True, 'message': 'Passkey verified successfully.', 'redirect': '/portal/staff/dashboard/'})
    except Exception as exc:
        logger.exception('Staff WebAuthn authentication verification crashed: %s', exc)
        request.session.pop('staff_webauthn_auth_challenge', None)
        request.session.pop('staff_webauthn_login_username', None)
        request.session.pop('staff_webauthn_login_staff_id', None)
        request.session.pop('staff_webauthn_login_schema_name', None)
        request.session.modified = True
        return JsonResponse({'success': False, 'message': 'This passkey could not be verified for your account. Please try again.'}, status=400)
"""

# ---- staff_webauthn_registration_verify replacement ----
STAFF_WEBAUTHN_REG_VERIFY_NEW = """@require_http_methods(['POST'])
def staff_webauthn_registration_verify(request):
    # Use session identity directly
    staff_id = request.session.get('staff_id')
    schema_name = request.session.get('staff_schema_name')
    expected_challenge = request.session.get('staff_webauthn_registration_challenge')
    logger.info(
        'REG VERIFY - staff_id=%s schema=%s challenge_present=%s 2fa_pending=%s',
        staff_id, schema_name, bool(expected_challenge), request.session.get('staff_2fa_pending', False)
    )
    if not staff_id or not schema_name or not expected_challenge:
        logger.warning('REG VERIFY - missing session data: staff_id=%s, schema=%s, challenge=%s', staff_id, schema_name, expected_challenge)
        return JsonResponse({'success': False, 'message': 'Registration session expired.'}, status=400)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        logger.warning('REG VERIFY - invalid JSON')
        return JsonResponse({'success': False, 'message': 'Invalid registration payload.'}, status=400)

    with schema_context('public'):
        credential = StaffCredential.objects.filter(staff_id=staff_id, schema_name=schema_name).first()
        logger.info('REG VERIFY - credential found=%s', bool(credential))
        if credential is None:
            return JsonResponse({'success': False, 'message': 'Credential not found.'}, status=404)

        try:
            verification = verify_registration_response(
                credential=payload,
                expected_challenge=base64url_to_bytes(expected_challenge),
                expected_rp_id=_staff_compute_rp_id(request),
                expected_origin=_staff_expected_origins(request),
                require_user_verification=True,
            )
        except Exception as e:
            logger.error(f"WebAuthn registration verification failed: {e}")
            return JsonResponse({'success': False, 'message': f'Verification error: {str(e)}'}, status=400)

        logger.info(f"WebAuthn registration verification succeeded for credential_id={bytes_to_base64url(verification.credential_id)}")

        try:
            with transaction.atomic():
                obj, created = WebAuthnCredential.objects.update_or_create(
                    credential_id=bytes_to_base64url(verification.credential_id),
                    defaults={
                        'staff_credential': credential,
                        'public_key': bytes_to_base64url(verification.credential_public_key),
                        'sign_count': verification.sign_count,
                        'device_name': payload.get('deviceName', 'Unknown Device'),
                        'is_active': True,
                    },
                )
            if not created and obj.is_active is False:
                obj.is_active = True
                obj.save(update_fields=['is_active'])
            logger.info('REG VERIFY - credential saved id=%s created=%s active=%s', obj.pk, created, obj.is_active)
        except Exception as e:
            logger.error(f"Failed to save WebAuthn credential: {e}")
            return JsonResponse({'success': False, 'message': f'Database error: {str(e)}'}, status=500)

        # Promote session: clear 2FA pending flag
        request.session['staff_id'] = staff_id
        request.session['staff_schema_name'] = schema_name
        request.session['staff_username'] = credential.username
        request.session['staff_2fa_pending'] = False
        request.session['staff_session_token'] = uuid.uuid4().hex
        request.session.set_expiry(1800)
        request.session.modified = True

        cache.set(f'staff_session_token:{schema_name}:{staff_id}', request.session['staff_session_token'], 1800)
        session_keys = cache.get(f'staff_session_keys:{schema_name}:{staff_id}', [])
        if isinstance(session_keys, str):
            session_keys = [session_keys]
        session_keys = [k for k in list(session_keys) if k]
        if request.session.session_key:
            session_keys.append(request.session.session_key)
        cache.set(f'staff_session_keys:{schema_name}:{staff_id}', list(dict.fromkeys(session_keys)), 1800)
        cache.set(f'staff_online:{schema_name}:{staff_id}', request.session.session_key, 1800)

        request.session.pop('staff_webauthn_registration_challenge', None)
        request.session.modified = True
        logger.info('REG VERIFY - success, session promoted')
        return JsonResponse({'success': True, 'message': 'Passkey registered successfully.'})
"""

# ---- staff_profile replacement ----
STAFF_PROFILE_NEW = """@require_staff_login
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
            passkeys = list(WebAuthnCredential.objects.filter(staff_credential=credential, is_active=True).order_by('-last_used', '-created_at')) if credential else []
        logger.info('PROFILE - passkeys count=%d, 2fa_pending=%s', len(passkeys), request.session.get('staff_2fa_pending'))
        if request.session.get('staff_2fa_pending') and passkeys:
            logger.info('PROFILE - redirecting to verify-passkey because 2fa_pending is True and passkeys exist')
            return redirect('staff_verify_passkey')
        if credential is not None:
            credential.raw_password = None
        try:
            response = render(request, 'mobile/staff/profile.html', {
                'staff': staff,
                'credential': credential,
                'passkeys': passkeys,
                'has_passkey': bool(passkeys),
                'staff_passkey_required': getattr(request, 'staff_passkey_required', False),
            })
            logger.info('PROFILE - render successful')
            return response
        except Exception as e:
            logger.exception('PROFILE - render failed: %s', e)
            return redirect('staff_dashboard')
    except Exception as e:
        logger.exception('PROFILE - unexpected error: %s', e)
        return redirect('staff_login')
"""

# ---- staff_logout addition: clear staff_2fa_pending ----
STAFF_LOGOUT_NEW = """def staff_logout(request):
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
    request.session.pop('staff_2fa_pending', None)  # ensure cleared
    request.session.modified = True
    return redirect('staff_login')
"""

# ---- require_staff_login update: allow access when 2fa_pending ----
REQUIRE_STAFF_LOGIN_NEW = """def require_staff_login(view_func):
    def wrapped(request, *args, **kwargs):
        staff_id = request.session.get('staff_id')
        schema_name = request.session.get('staff_schema_name')
        # Allow profile and verify pages even if 2fa_pending
        allowed_2fa_paths = {
            '/portal/staff/profile/',
            '/portal/staff/verify-passkey/',
            '/portal/staff/security/webauthn/register/options/',
            '/portal/staff/security/webauthn/register/verify/',
            '/portal/staff/security/webauthn/auth/options/',
            '/portal/staff/security/webauthn/auth/verify/',
        }
        if not staff_id or not schema_name:
            return redirect('staff_login')

        if not settings.DEBUG:
            session_token = request.session.get('staff_session_token')
            cached_token = cache.get(f'staff_session_token:{schema_name}:{staff_id}') if schema_name and staff_id else None
            token_invalid = not session_token or cached_token in ['logged_out'] or cached_token != session_token
        else:
            token_invalid = False

        is_webauthn_auth = request.path_info in [
            '/portal/staff/security/webauthn/auth/options/',
            '/portal/staff/security/webauthn/auth/verify/',
        ]
        is_pending_webauthn = request.session.get('staff_pending_webauthn') is True

        if token_invalid and not (is_webauthn_auth and is_pending_webauthn):
            request.session.flush()
            return redirect('staff_login')

        # If 2fa_pending, only allow the allowed paths; else block.
        if request.session.get('staff_2fa_pending', False) and request.path_info not in allowed_2fa_paths:
            # Redirect to verify-passkey if they have passkey, else to profile to register
            with schema_context('public'):
                credential = StaffCredential.objects.filter(staff_id=staff_id, schema_name=schema_name).first()
                has_passkey = credential and credential.has_passkey
            if has_passkey:
                return redirect('staff_verify_passkey')
            else:
                return redirect('staff_profile_page')

        return view_func(request, *args, **kwargs)
    return wrapped
"""

# ---- Middleware: replace entire class with new version using staff_2fa_pending ----
MIDDLEWARE_NEW = """class StaffTenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path_info.startswith('/portal/staff/'):
            return self.get_response(request)

        request.tenant = None
        request.staff = None

        if request.path_info in ['/portal/staff/login/', '/portal/staff/logout/']:
            connection.set_schema_to_public()
            return self.get_response(request)

        staff_id = request.session.get('staff_id')
        schema_name = request.session.get('staff_schema_name')

        is_webauthn_auth = request.path_info in [
            '/portal/staff/security/webauthn/auth/options/',
            '/portal/staff/security/webauthn/auth/verify/',
        ]
        is_webauthn_register = request.path_info in [
            '/portal/staff/security/webauthn/register/options/',
            '/portal/staff/security/webauthn/register/verify/',
        ]
        is_pending_2fa = request.session.get('staff_2fa_pending') is True
        is_verify_passkey_path = request.path_info == '/portal/staff/verify-passkey/'
        allow_pending_2fa = is_verify_passkey_path and is_pending_2fa and bool(staff_id and schema_name)

        # Passwordless authentication starts without a tenant identity. Its
        # endpoints query public-schema credentials and establish the identity.
        if is_webauthn_auth and not (staff_id and schema_name):
            connection.set_schema_to_public()
            return self.get_response(request)

        if (not staff_id or not schema_name) and not (is_webauthn_auth or is_webauthn_register or allow_pending_2fa):
            request.session.flush()
            return redirect('staff_login')

        cached_token = None
        try:
            from django.core.cache import cache
            cached_token = cache.get(f'staff_session_token:{schema_name}:{staff_id}')
        except Exception:
            cached_token = None

        session_token = request.session.get('staff_session_token')
        if not settings.DEBUG:
            token_invalid = not session_token or cached_token in ['logged_out'] or cached_token != session_token
        else:
            token_invalid = False

        if token_invalid and not (is_webauthn_auth or is_webauthn_register or allow_pending_2fa):
            request.session.flush()
            return redirect('staff_login')

        TenantModel = get_tenant_model()
        try:
            tenant = TenantModel.objects.get(schema_name=schema_name)
        except TenantModel.DoesNotExist:
            request.session.flush()
            return redirect('staff_login')

        request.tenant = tenant
        connection.set_tenant(tenant)

        if not settings.DEBUG:
            try:
                with schema_context(schema_name):
                    request.staff = Staff.objects.filter(pk=staff_id, status='active').first()
            except Exception:
                request.staff = None

            if request.staff is None:
                request.session.flush()
                return redirect('staff_login')
        else:
            try:
                request.staff = Staff.objects.get(pk=staff_id)
            except Staff.DoesNotExist:
                request.staff = Staff(pk=staff_id, full_name='Developer', status='active')

        # Determine if passkey is required. We only block if 2fa_pending is True and the user has a passkey.
        request.staff_passkey_required = False
        try:
            logger.info("Middleware: staff_id=%s schema=%s pending_2fa=%s", staff_id, schema_name, is_pending_2fa)
            if staff_id and schema_name:
                with schema_context('public'):
                    credential = StaffCredential.objects.filter(
                        staff_id=staff_id,
                        schema_name=schema_name,
                    ).first()
                logger.info("Middleware: credential exists=%s has_passkey=%s", credential is not None, bool(credential and credential.has_passkey))
                if credential and credential.has_passkey:
                    # If passkey exists and 2fa is pending, we need to enforce verification.
                    # Otherwise, if already verified (2fa_pending False), no block.
                    if is_pending_2fa:
                        request.staff_passkey_required = True
                    else:
                        request.staff_passkey_required = False
                else:
                    # No passkey, no requirement (they need to register)
                    request.staff_passkey_required = False
            else:
                request.staff_passkey_required = False

            allowed_paths = [
                '/portal/staff/profile/',
                '/portal/staff/verify-passkey/',
                '/portal/staff/security/webauthn/register/options/',
                '/portal/staff/security/webauthn/register/verify/',
                '/portal/staff/security/webauthn/auth/options/',
                '/portal/staff/security/webauthn/auth/verify/',
                '/portal/staff/logout/',
                '/portal/staff/login/',
            ]
            logger.info("Middleware: staff_passkey_required=%s, path=%s", request.staff_passkey_required, request.path_info)
            if request.staff_passkey_required and request.path_info not in allowed_paths:
                logger.info("Middleware: redirecting to verify-passkey because passkey required")
                return redirect('staff_verify_passkey')
        except Exception as exc:
            logger.exception('Middleware error: %s', exc)
            request.staff_passkey_required = False
        return self.get_response(request)
"""

# -----------------------------------------------------------------------------
# Patcher logic
# -----------------------------------------------------------------------------

def find_and_replace(file_path: Path, pattern: str, replacement: str, dry_run: bool, verbose: bool) -> bool:
    """Find the first occurrence of pattern (regex) in file and replace with replacement."""
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        return False

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content, count = re.subn(pattern, replacement, content, count=1)
    if count == 0:
        logger.warning(f"Pattern not found in {file_path}")
        return False

    if dry_run:
        logger.info(f"Dry-run: would modify {file_path} (pattern matched)")
        if verbose:
            # Show diff-like output? For simplicity, just log.
            pass
    else:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        logger.info(f"Modified {file_path}")

    return True

def patch_staff_portal(target_dir: Path, dry_run: bool, verbose: bool):
    """Apply patches to staff_portal.py."""
    file_path = target_dir / "axis_saas" / "views" / "staff_portal.py"
    if not file_path.exists():
        logger.error(f"staff_portal.py not found at {file_path}")
        return

    def make_func_pattern(func_name: str) -> str:
        return rf'^def {func_name}\s*\([^)]*\)\s*:.*?(?=^def\s|\Z)'

    replacements = [
        (make_func_pattern('staff_login'), STAFF_LOGIN_NEW),
        (make_func_pattern('staff_verify_passkey'), STAFF_VERIFY_PASSKEY_NEW),
        (make_func_pattern('staff_webauthn_authentication_options'), STAFF_WEBAUTHN_AUTH_OPTIONS_NEW),
        (make_func_pattern('staff_webauthn_authentication_verify'), STAFF_WEBAUTHN_AUTH_VERIFY_NEW),
        (make_func_pattern('staff_webauthn_registration_verify'), STAFF_WEBAUTHN_REG_VERIFY_NEW),
        (make_func_pattern('staff_profile'), STAFF_PROFILE_NEW),
        (make_func_pattern('staff_logout'), STAFF_LOGOUT_NEW),
        (make_func_pattern('require_staff_login'), REQUIRE_STAFF_LOGIN_NEW),
    ]

    for pattern, repl in replacements:
        if not find_and_replace(file_path, pattern, repl, dry_run, verbose):
            if verbose:
                logger.warning(f"Failed to apply replacement for pattern: {pattern[:50]}...")

def patch_middleware(target_dir: Path, dry_run: bool, verbose: bool):
    """Apply patches to staff_tenant_middleware.py."""
    file_path = target_dir / "axis_saas" / "middleware" / "staff_tenant_middleware.py"
    if not file_path.exists():
        logger.error(f"staff_tenant_middleware.py not found at {file_path}")
        return

    pattern = r'^class StaffTenantMiddleware\s*:.*?(?=^class\s|\Z)'
    if not find_and_replace(file_path, pattern, MIDDLEWARE_NEW, dry_run, verbose):
        logger.error(f"Failed to replace StaffTenantMiddleware in {file_path}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Apply staff passkey 2FA fixes.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying.")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output.")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current directory).")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.is_dir():
        logger.error(f"Target directory does not exist: {target_dir}")
        sys.exit(1)

    logger.info(f"Starting axis_patcher in {'dry-run' if args.dry_run else 'live'} mode on {target_dir}")

    patch_staff_portal(target_dir, args.dry_run, args.verbose)
    patch_middleware(target_dir, args.dry_run, args.verbose)

    logger.info("Patcher finished.")

if __name__ == "__main__":
    main()
