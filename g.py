#!/usr/bin/env python3
"""
axis_patcher.py

Idempotent patcher for axis_saas staff portal WebAuthn second-factor issues.
Applies fixes to:
  - axis_saas/views/staff_portal.py
  - axis_saas/middleware/staff_tenant_middleware.py
"""

import os
import re
import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime

# ----------------------------------------------------------------------
# Logging setup
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('axis_patcher')

# ----------------------------------------------------------------------
# Replacement definitions
# ----------------------------------------------------------------------

# Each replacement is a tuple: (relative_path, pattern, replacement)
# The pattern is a regular expression (with DOTALL) to match the old block.
# The replacement is the new block of code.

REPLACEMENTS = []

# 1. staff_webauthn_authentication_options - improve pending identity handling
OPTIONS_OLD_PATTERN = r'(def staff_webauthn_authentication_options\(request\):.*?)(?=\n@require_http_methods|\ndef )'
OPTIONS_NEW = r'''def staff_webauthn_authentication_options(request):
    data = {}
    try:
        if request.content_type and 'application/json' in request.content_type:
            try:
                data = json.loads(request.body.decode('utf-8') or '{}')
            except json.JSONDecodeError:
                data = {}

        pending_username = request.session.get('pending_username') or request.session.get('staff_username') or ''
        pending_staff_id = request.session.get('pending_staff_id') or request.session.get('staff_id')
        pending_schema_name = request.session.get('pending_schema_name') or request.session.get('staff_schema_name')
        username = (data.get('username') or request.POST.get('username') or '').strip()

        # Log the session state for debugging
        logger.info(
            'Authentication options - pending_staff_id=%s pending_schema=%s pending_username=%s username=%s',
            pending_staff_id, pending_schema_name, pending_username, username
        )

        # If the user already passed the password step, we already know the staff
        # account from session. Do not force a username for that flow.
        if not username:
            username = request.session.get('staff_webauthn_login_username') or request.session.get('staff_username') or request.session.get('pending_username') or ''
        username = username.strip()

        if pending_username:
            if username and username != pending_username:
                return JsonResponse({'error': 'This passkey does not belong to the signed-in account.'}, status=403)
            username = pending_username

        passkeys = []
        with schema_context('public'):
            # If a session-bound account exists, prefer that exact account rather than
            # requiring the browser to send a username. This keeps the password-login
            # verification flow identical to the direct passkey login flow.
            if pending_staff_id and pending_schema_name:
                credential = StaffCredential.objects.filter(
                    staff_id=pending_staff_id,
                    schema_name=pending_schema_name,
                    is_active=True,
                ).first()
                if credential is None:
                    logger.warning('No StaffCredential found for pending_staff_id=%s schema=%s', pending_staff_id, pending_schema_name)
                    return JsonResponse({'error': 'Account not found.'}, status=404)
                username = credential.username
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
                    logger.warning('No valid passkeys for pending_staff_id=%s', pending_staff_id)
                    return JsonResponse({'error': 'No valid passkeys registered for this account.'}, status=403)
                request.session['staff_webauthn_login_username'] = credential.username
                request.session['staff_webauthn_login_staff_id'] = credential.staff_id
                request.session['staff_webauthn_login_schema_name'] = credential.schema_name
                request.session.modified = True
                logger.info('Found credential for pending identity, username=%s, passkeys=%d', credential.username, len(passkeys))
            elif username:
                credential = StaffCredential.objects.filter(username=username, is_active=True).first()
                if credential is None:
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
                    return JsonResponse({'error': 'No valid passkeys registered for this account.'}, status=403)
                request.session['staff_webauthn_login_username'] = credential.username
                request.session['staff_webauthn_login_staff_id'] = credential.staff_id
                request.session['staff_webauthn_login_schema_name'] = credential.schema_name
                request.session.modified = True
                logger.info('Found credential by username, username=%s, passkeys=%d', credential.username, len(passkeys))
            else:
                # Discoverable passkey login: no username is required, and challenge is
                # created without allowCredentials when the browser is already using a
                # platform authenticator tied to the device.
                passkeys = []
                request.session.pop('staff_webauthn_login_username', None)
                request.session.pop('staff_webauthn_login_staff_id', None)
                request.session.pop('staff_webauthn_login_schema_name', None)
                request.session.modified = True
                logger.info('No pending identity or username, attempting discoverable login')

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
        return JsonResponse({'error': 'This passkey challenge could not be prepared for your account. Please try again or sign in with your password again.'}, status=400)'''

REPLACEMENTS.append((
    'axis_saas/views/staff_portal.py',
    OPTIONS_OLD_PATTERN,
    OPTIONS_NEW
))

# 2. staff_webauthn_authentication_verify - promote session after verification
VERIFY_OLD_PATTERN = r'(def staff_webauthn_authentication_verify\(request\):.*?)(?=\n@require_staff_login|\ndef )'
VERIFY_NEW = r'''def staff_webauthn_authentication_verify(request):
    logger.info('Authentication verify session keys=%s', sorted(request.session.keys()))
    expected_challenge = request.session.get('staff_webauthn_auth_challenge')
    login_username = request.session.get('staff_webauthn_login_username') or request.session.get('pending_username') or request.session.get('staff_username')
    login_staff_id = request.session.get('staff_webauthn_login_staff_id') or request.session.get('pending_staff_id') or request.session.get('staff_id')
    login_schema_name = request.session.get('staff_webauthn_login_schema_name') or request.session.get('pending_schema_name') or request.session.get('staff_schema_name')
    staff_id = request.session.get('staff_id')
    schema_name = request.session.get('staff_schema_name')

    if not expected_challenge:
        return JsonResponse({'success': False, 'message': 'Passkey challenge expired. Please sign in again.'}, status=400)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid passkey payload.'}, status=400)

    credential_id = payload.get('id')
    if not credential_id:
        return JsonResponse({'success': False, 'message': 'Passkey identifier missing.'}, status=400)

    try:
        with schema_context('public'):
            webauthn_credential = WebAuthnCredential.objects.filter(credential_id=credential_id, is_active=True).select_related('staff_credential').first()
            logger.info('Authentication credential found=%s id=%s expected_staff=%s expected_schema=%s', bool(webauthn_credential), credential_id, login_staff_id, login_schema_name)
            if webauthn_credential is None:
                return JsonResponse({'success': False, 'message': 'Passkey not recognized.'}, status=404)
            if login_username and webauthn_credential.staff_credential.username != login_username:
                return JsonResponse({'success': False, 'message': 'This passkey does not belong to the provided username.'}, status=403)
            if login_staff_id and str(webauthn_credential.staff_credential.staff_id) != str(login_staff_id):
                return JsonResponse({'success': False, 'message': 'This passkey is not registered for the selected account.'}, status=403)
            if login_schema_name and webauthn_credential.staff_credential.schema_name != login_schema_name:
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
                logger.warning('WebAuthn authentication failed: %s', exc)
                return JsonResponse({'success': False, 'message': 'Passkey verification failed. Please try again.'}, status=400)
            webauthn_credential.sign_count = verification.new_sign_count
            webauthn_credential.last_used = timezone.now()
            webauthn_credential.save(update_fields=['sign_count', 'last_used'])

            target_staff_id = int(webauthn_credential.staff_credential.staff_id)
            target_schema_name = webauthn_credential.staff_credential.schema_name

            # Promote session: set full identity and clear pending flags
            request.session['staff_id'] = target_staff_id
            request.session['staff_schema_name'] = target_schema_name
            request.session['staff_username'] = webauthn_credential.staff_credential.username
            request.session['staff_pending_webauthn'] = False
            request.session['staff_pending_passkey'] = False
            # Clear pending flags if they exist
            request.session.pop('pending_staff_id', None)
            request.session.pop('pending_schema_name', None)
            request.session.pop('pending_username', None)
            # Set session token
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
        logger.info('Authentication success, redirecting to dashboard')
        return JsonResponse({'success': True, 'message': 'Passkey verified successfully.', 'redirect': '/portal/staff/dashboard/'})
    except Exception as exc:
        logger.exception('Staff WebAuthn authentication verification crashed: %s', exc)
        request.session.pop('staff_webauthn_auth_challenge', None)
        request.session.pop('staff_webauthn_login_username', None)
        request.session.pop('staff_webauthn_login_staff_id', None)
        request.session.pop('staff_webauthn_login_schema_name', None)
        request.session.modified = True
        return JsonResponse({'success': False, 'message': 'This passkey could not be verified for your account. Please try again.'}, status=400)'''

REPLACEMENTS.append((
    'axis_saas/views/staff_portal.py',
    VERIFY_OLD_PATTERN,
    VERIFY_NEW
))

# 3. staff_webauthn_registration_verify - ensure credential saved and promote session
REGISTER_VERIFY_OLD_PATTERN = r'(def staff_webauthn_registration_verify\(request\):.*?)(?=\n@require_http_methods|\ndef )'
REGISTER_VERIFY_NEW = r'''def staff_webauthn_registration_verify(request):
    # Bind the new credential to the identity that passed the password step.
    schema_name = request.session.get('pending_schema_name') or request.session.get('staff_schema_name')
    staff_id = request.session.get('pending_staff_id') or request.session.get('staff_id')
    expected_challenge = request.session.get('staff_webauthn_registration_challenge')
    logger.info(
        'Registration verify session keys=%s staff_id=%s schema=%s pending_passkey=%s',
        sorted(request.session.keys()), staff_id, schema_name,
        request.session.get('staff_pending_passkey'),
    )
    if not schema_name or not staff_id or not expected_challenge:
        return JsonResponse({'success': False, 'message': 'Registration session expired.'}, status=400)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid registration payload.'}, status=400)

    with schema_context('public'):
        credential = StaffCredential.objects.filter(staff_id=staff_id, schema_name=schema_name).first()
        logger.info('Registration credential found=%s for staff_id=%s schema=%s', bool(credential), staff_id, schema_name)
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
            # WebAuthnCredential is public-schema data; make its write atomic.
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
            logger.info('Registration credential saved id=%s created=%s active=%s', obj.pk, created, obj.is_active)
        except Exception as e:
            logger.error(f"Failed to save WebAuthn credential: {e}")
            return JsonResponse({'success': False, 'message': f'Database error: {str(e)}'}, status=500)

        # After successful registration, promote the session so the user becomes fully authenticated.
        # This clears the pending flag and sets staff_id and staff_schema_name.
        request.session['staff_id'] = int(staff_id)
        request.session['staff_schema_name'] = schema_name
        request.session['staff_username'] = credential.username
        request.session['staff_pending_passkey'] = False
        request.session.pop('pending_staff_id', None)
        request.session.pop('pending_schema_name', None)
        request.session.pop('pending_username', None)
        # Set session token
        request.session['staff_session_token'] = uuid.uuid4().hex
        request.session.set_expiry(1800)
        request.session.modified = True

        # Update cache
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
        logger.info('Registration success, returning success response')
        return JsonResponse({'success': True, 'message': 'Passkey registered successfully.'})'''

REPLACEMENTS.append((
    'axis_saas/views/staff_portal.py',
    REGISTER_VERIFY_OLD_PATTERN,
    REGISTER_VERIFY_NEW
))

# 4. staff_tenant_middleware.py - adjust passkey required logic and redirect
MIDDLEWARE_OLD_PATTERN = r'(class StaffTenantMiddleware:.*?)(?=\n\nclass|\Z)'
MIDDLEWARE_NEW = r'''class StaffTenantMiddleware:
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

        pending_staff_id = request.session.get('pending_staff_id')
        pending_schema_name = request.session.get('pending_schema_name')
        staff_id = request.session.get('staff_id') or pending_staff_id
        schema_name = request.session.get('staff_schema_name') or pending_schema_name

        is_webauthn_auth = request.path_info in [
            '/portal/staff/security/webauthn/auth/options/',
            '/portal/staff/security/webauthn/auth/verify/',
        ]
        is_webauthn_register = request.path_info in [
            '/portal/staff/security/webauthn/register/options/',
            '/portal/staff/security/webauthn/register/verify/',
        ]
        is_pending_passkey = request.session.get('staff_pending_passkey') is True
        is_verify_passkey_path = request.path_info == '/portal/staff/verify-passkey/'
        allow_pending_verify = is_verify_passkey_path and is_pending_passkey and bool(staff_id and schema_name)

        # Passwordless authentication starts without a tenant identity. Its
        # endpoints query public-schema credentials and establish the identity.
        if is_webauthn_auth and not (staff_id and schema_name):
            connection.set_schema_to_public()
            return self.get_response(request)

        if (not staff_id or not schema_name) and not (is_webauthn_auth or is_webauthn_register or allow_pending_verify):
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

        if token_invalid and not (is_webauthn_auth or is_webauthn_register or allow_pending_verify):
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

        # Determine if passkey is required for the fully authenticated identity.
        # If the user has a staff_id (fully authenticated), check their passkey status.
        # If only pending, we allow the profile and verify pages.
        request.staff_passkey_required = False
        try:
            # Use fully authenticated identity if available; otherwise fallback to pending.
            effective_staff_id = request.session.get('staff_id') or pending_staff_id
            effective_schema_name = request.session.get('staff_schema_name') or pending_schema_name
            if effective_staff_id and effective_schema_name:
                with schema_context('public'):
                    credential = StaffCredential.objects.filter(
                        staff_id=effective_staff_id,
                        schema_name=effective_schema_name,
                    ).first()
                logger.info("Middleware: effective_staff_id=%s effective_schema=%s pending=%s", effective_staff_id, effective_schema_name, is_pending_passkey)
                logger.info("Middleware: credential exists=%s has_passkey=%s", credential is not None, bool(credential and credential.has_passkey))
                if credential and credential.has_passkey:
                    request.staff_passkey_required = False
                else:
                    # No passkey or credential missing
                    request.staff_passkey_required = True
            else:
                # No effective identity; don't enforce passkey
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
            if request.staff_passkey_required and request.path_info not in allowed_paths:
                return redirect('staff_profile_page')
        except Exception as exc:
            logger.exception('Middleware error: %s', exc)
            request.staff_passkey_required = False

        return self.get_response(request)'''

REPLACEMENTS.append((
    'axis_saas/middleware/staff_tenant_middleware.py',
    MIDDLEWARE_OLD_PATTERN,
    MIDDLEWARE_NEW
))

# ----------------------------------------------------------------------
# Patch application logic
# ----------------------------------------------------------------------

def apply_patch(file_path, pattern, replacement, dry_run=False, verbose=False):
    """Apply a single regex replacement to a file, if the pattern matches."""
    if not file_path.exists():
        logger.error('File not found: %s', file_path)
        return False

    content = file_path.read_text(encoding='utf-8')
    compiled = re.compile(pattern, re.DOTALL)
    if not compiled.search(content):
        if verbose:
            logger.info('Pattern not found in %s, skipping.', file_path)
        return False

    new_content = compiled.sub(replacement, content)
    if new_content == content:
        if verbose:
            logger.info('No changes needed for %s.', file_path)
        return False

    if dry_run:
        logger.info('DRY RUN: would modify %s', file_path)
        if verbose:
            # Show diff? We'll just indicate changes.
            pass
        return True

    # Write the new content
    file_path.write_text(new_content, encoding='utf-8')
    logger.info('Patched %s', file_path)
    return True

def main():
    parser = argparse.ArgumentParser(description='Apply patches to axis_saas staff portal.')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without writing.')
    parser.add_argument('--verbose', action='store_true', help='Show detailed output.')
    parser.add_argument('--target-dir', default='.', help='Project root directory (default: current).')
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        logger.error('Target directory does not exist: %s', target_dir)
        sys.exit(1)

    logger.info('Starting patcher with target dir: %s', target_dir)

    any_patched = False
    for rel_path, pattern, replacement in REPLACEMENTS:
        file_path = target_dir / rel_path
        if apply_patch(file_path, pattern, replacement, args.dry_run, args.verbose):
            any_patched = True

    if args.dry_run:
        logger.info('Dry run completed. No files were changed.')
    elif any_patched:
        logger.info('Patcher completed successfully.')
    else:
        logger.info('No patches applied (files were already up-to-date or patterns not found).')

if __name__ == '__main__':
    main()
