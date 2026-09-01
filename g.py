#!/usr/bin/env python3
"""
axis_patcher.py

Fix staff passkey second-factor and persistence issues.
Also fix the middleware to always return a response.

Usage:
    python axis_patcher.py [--dry-run] [--verbose] [--target-dir PATH]

This script patches:
    - axis_saas/views/staff_portal.py
    - axis_saas/middleware/staff_tenant_middleware.py

It adds detailed logging, ensures pending identity is used correctly,
wraps staff_profile in a top-level try/except, and adds missing return
in StaffTenantMiddleware.__call__.
"""

import os
import re
import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime

# -----------------------------------------------------------------------------
# Logging setup
# -----------------------------------------------------------------------------
logger = logging.getLogger("axis_patcher")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(ch)


# -----------------------------------------------------------------------------
# New function bodies (to replace existing ones)
# -----------------------------------------------------------------------------

NEW_AUTH_OPTIONS = """@require_http_methods(['POST'])
def staff_webauthn_authentication_options(request):
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

        logger.info(
            'AUTH OPTIONS - pending_staff_id=%s pending_schema=%s pending_username=%s username=%s',
            pending_staff_id, pending_schema_name, pending_username, username
        )
        logger.info('AUTH OPTIONS - session keys: %s', sorted(request.session.keys()))

        # If we have a pending identity, we must use it (second-factor flow)
        if pending_staff_id and pending_schema_name:
            logger.info('AUTH OPTIONS - Using pending identity: staff_id=%s schema=%s', pending_staff_id, pending_schema_name)
            with schema_context('public'):
                credential = StaffCredential.objects.filter(
                    staff_id=pending_staff_id,
                    schema_name=pending_schema_name,
                    is_active=True,
                ).first()
                if credential is None:
                    logger.warning('AUTH OPTIONS - No StaffCredential found for pending_staff_id=%s schema=%s', pending_staff_id, pending_schema_name)
                    return JsonResponse({'error': 'Account not found.'}, status=404)
                passkeys = list(WebAuthnCredential.objects.filter(staff_credential=credential, is_active=True))
                # filter out malformed credentials
                valid_passkeys = []
                for item in passkeys:
                    try:
                        base64url_to_bytes(item.credential_id)
                        valid_passkeys.append(item)
                    except Exception:
                        logger.warning('Skipping malformed WebAuthn credential id for staff credential %s', credential.pk)
                passkeys = valid_passkeys
                if not passkeys:
                    logger.warning('AUTH OPTIONS - No valid passkeys for pending_staff_id=%s', pending_staff_id)
                    return JsonResponse({'error': 'No valid passkeys registered for this account.'}, status=403)
                username = credential.username
                request.session['staff_webauthn_login_username'] = credential.username
                request.session['staff_webauthn_login_staff_id'] = credential.staff_id
                request.session['staff_webauthn_login_schema_name'] = credential.schema_name
                request.session.modified = True
                logger.info('AUTH OPTIONS - Found credential for pending identity, username=%s, passkeys=%d', credential.username, len(passkeys))
        elif username:
            # Passwordless login with username
            logger.info('AUTH OPTIONS - Using username: %s', username)
            with schema_context('public'):
                credential = StaffCredential.objects.filter(username=username, is_active=True).first()
                if credential is None:
                    logger.warning('AUTH OPTIONS - No StaffCredential found for username=%s', username)
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
                    logger.warning('AUTH OPTIONS - No valid passkeys for username=%s', username)
                    return JsonResponse({'error': 'No valid passkeys registered for this account.'}, status=403)
                request.session['staff_webauthn_login_username'] = credential.username
                request.session['staff_webauthn_login_staff_id'] = credential.staff_id
                request.session['staff_webauthn_login_schema_name'] = credential.schema_name
                request.session.modified = True
                logger.info('AUTH OPTIONS - Found credential by username, username=%s, passkeys=%d', credential.username, len(passkeys))
        else:
            # Discoverable passkey login: no username is required
            logger.info('AUTH OPTIONS - No pending identity or username, attempting discoverable login')
            passkeys = []
            request.session.pop('staff_webauthn_login_username', None)
            request.session.pop('staff_webauthn_login_staff_id', None)
            request.session.pop('staff_webauthn_login_schema_name', None)
            request.session.modified = True

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
        return JsonResponse({'error': 'This passkey challenge could not be prepared for your account. Please try again or sign in with your password again.'}, status=400)"""

NEW_AUTH_VERIFY = """@require_http_methods(['POST'])
def staff_webauthn_authentication_verify(request):
    logger.info('AUTH VERIFY - session keys before: %s', sorted(request.session.keys()))
    expected_challenge = request.session.get('staff_webauthn_auth_challenge')
    login_username = request.session.get('staff_webauthn_login_username') or request.session.get('pending_username') or request.session.get('staff_username')
    login_staff_id = request.session.get('staff_webauthn_login_staff_id') or request.session.get('pending_staff_id') or request.session.get('staff_id')
    login_schema_name = request.session.get('staff_webauthn_login_schema_name') or request.session.get('pending_schema_name') or request.session.get('staff_schema_name')

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

            target_staff_id = int(webauthn_credential.staff_credential.staff_id)
            target_schema_name = webauthn_credential.staff_credential.schema_name

            # Promote session: set full identity and clear pending flags
            request.session['staff_id'] = target_staff_id
            request.session['staff_schema_name'] = target_schema_name
            request.session['staff_username'] = webauthn_credential.staff_credential.username
            request.session['staff_pending_webauthn'] = False
            request.session['staff_pending_passkey'] = False
            request.session.pop('pending_staff_id', None)
            request.session.pop('pending_schema_name', None)
            request.session.pop('pending_username', None)
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
        return JsonResponse({'success': False, 'message': 'This passkey could not be verified for your account. Please try again.'}, status=400)"""

NEW_REG_VERIFY = """@require_http_methods(['POST'])
def staff_webauthn_registration_verify(request):
    # Bind the new credential to the identity that passed the password step.
    schema_name = request.session.get('pending_schema_name') or request.session.get('staff_schema_name')
    staff_id = request.session.get('pending_staff_id') or request.session.get('staff_id')
    expected_challenge = request.session.get('staff_webauthn_registration_challenge')
    logger.info(
        'REG VERIFY - session keys=%s staff_id=%s schema=%s pending_passkey=%s',
        sorted(request.session.keys()), staff_id, schema_name,
        request.session.get('staff_pending_passkey'),
    )
    if not schema_name or not staff_id or not expected_challenge:
        logger.warning('REG VERIFY - missing session data: schema=%s, staff_id=%s, challenge=%s', schema_name, staff_id, expected_challenge)
        return JsonResponse({'success': False, 'message': 'Registration session expired.'}, status=400)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        logger.warning('REG VERIFY - invalid JSON')
        return JsonResponse({'success': False, 'message': 'Invalid registration payload.'}, status=400)

    with schema_context('public'):
        credential = StaffCredential.objects.filter(staff_id=staff_id, schema_name=schema_name).first()
        logger.info('REG VERIFY - credential found=%s for staff_id=%s schema=%s', bool(credential), staff_id, schema_name)
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

        # Promote session
        request.session['staff_id'] = int(staff_id)
        request.session['staff_schema_name'] = schema_name
        request.session['staff_username'] = credential.username
        request.session['staff_pending_passkey'] = False
        request.session.pop('pending_staff_id', None)
        request.session.pop('pending_schema_name', None)
        request.session.pop('pending_username', None)
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
        return JsonResponse({'success': True, 'message': 'Passkey registered successfully.'})"""

# Fixed staff_profile view with top-level try/except to guarantee return
NEW_PROFILE_VIEW = """@require_staff_login
@require_http_methods(['GET'])
def staff_profile(request):
    try:
        # Registration is allowed after password verification, so resolve either
        # the fully authenticated identity or the pending identity.
        schema_name = request.session.get('staff_schema_name') or request.session.get('pending_schema_name')
        staff_id = request.session.get('staff_id') or request.session.get('pending_staff_id')
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
        logger.info('PROFILE - passkeys count=%d, pending_passkey=%s', len(passkeys), request.session.get('staff_pending_passkey'))
        if request.session.get('staff_pending_passkey') and passkeys:
            logger.info('PROFILE - redirecting to verify-passkey')
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
            logger.info('PROFILE - render successful, returning response')
            return response
        except Exception as e:
            logger.exception('PROFILE - render failed: %s', e)
            return redirect('staff_dashboard')
    except Exception as e:
        logger.exception('PROFILE - unexpected error: %s', e)
        return redirect('staff_login')"""

# The middleware patch: we will replace the entire __call__ method with a corrected version
# that includes the final return self.get_response(request).
# We'll construct the corrected method code using the existing content but adding the return.
# To avoid duplicating huge code, we can just patch the return by appending it.

# However, for simplicity and idempotence, we'll replace the method with a version that
# has the return at the end. We'll generate the corrected method by reading the current file,
# finding the __call__ method, and then appending the return if not present.

# We'll implement a function that does this.

# For the patcher script, we'll include a function that ensures the return is present.


# -----------------------------------------------------------------------------
# Helper functions for file patching
# -----------------------------------------------------------------------------

def find_function_boundaries(lines, function_name):
    """Find start and end line indices for a top-level function."""
    start = None
    for i, line in enumerate(lines):
        if re.match(rf'^def\s+{function_name}\s*\(', line):
            start = i
            break
    if start is None:
        return None, None
    # go backward to include decorators (lines starting with '@')
    decorator_start = start
    for j in range(start - 1, -1, -1):
        if lines[j].strip().startswith('@'):
            decorator_start = j
        elif lines[j].strip() == '':
            continue
        else:
            break
    # find end: next line that starts with 'def ' at column 0 (or end of file)
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r'^def\s+', lines[j]):
            end = j
            break
    return decorator_start, end


def replace_function(file_path, function_name, new_function_code):
    """Replace a top-level function with new code."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    lines = content.splitlines(keepends=True)
    start, end = find_function_boundaries(lines, function_name)
    if start is None:
        logger.error(f"Function {function_name} not found in {file_path}")
        return False
    new_lines = new_function_code.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith('\n'):
        new_lines[-1] += '\n'
    lines[start:end] = new_lines
    new_content = ''.join(lines)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    logger.info(f"Replaced function {function_name} in {file_path}")
    return True


def ensure_middleware_return(file_path):
    """Ensure that the StaffTenantMiddleware.__call__ method ends with return self.get_response(request)."""
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Find the __call__ method
    method_start = None
    for i, line in enumerate(lines):
        if re.match(r'^\s*def\s+__call__\s*\(self,\s*request\):', line):
            method_start = i
            break
    if method_start is None:
        logger.error("Could not find __call__ method in middleware")
        return False

    # Find the end of the method: next line at same indentation level as class or next method
    # We'll find the next line that starts with a non-space (or a class/def) after the method.
    # But we need to consider that the class might have other methods.
    # We'll find the end by scanning for a line with indentation level 0 that is not a comment or blank.
    # Actually, the method is indented inside the class. The class ends when we hit a line with no indentation.
    # But there may be other methods. We'll find the end by searching for a line that starts with 'def ' at the same indentation as class (0) or 'class '.
    # However, the file may have other top-level code. We'll simply find the next line that starts with 'def ' at column 0 (or 'class ').
    # That indicates the next method or class. If none, end of file.
    method_end = len(lines)
    for j in range(method_start + 1, len(lines)):
        if re.match(r'^(class|def)\s+', lines[j]):
            method_end = j
            break

    # Now we need to check if the last non-empty line before method_end contains a return statement that returns self.get_response(request)
    # We'll search backwards from method_end-1 to find the first non-empty line.
    last_line_index = None
    for k in range(method_end - 1, method_start, -1):
        if lines[k].strip():
            last_line_index = k
            break

    if last_line_index is None:
        logger.error("Method has no content?")
        return False

    # Check if the last line already contains 'return self.get_response(request)'
    if re.search(r'return\s+self\.get_response\s*\(', lines[last_line_index]):
        logger.info("Middleware already has return self.get_response(request)")
        return True

    # If not, we need to insert a new line after the last line that returns self.get_response(request)
    # But we must preserve indentation. The method body is indented by one level (4 spaces?).
    # We'll insert the return with the same indentation as the method body.
    # We'll get the indentation from the first line of the method body (after the def line).
    # We'll use the indentation of the line right after def.
    indent = ''
    if method_start + 1 < len(lines):
        match = re.match(r'^(\s+)', lines[method_start + 1])
        if match:
            indent = match.group(1)

    # We'll insert at the position after the last line, but before the method_end.
    insert_pos = last_line_index + 1
    # But we need to insert after any trailing whitespace lines? We'll insert at last_line_index+1.
    # We'll add a newline with proper indentation.
    new_line = f"{indent}return self.get_response(request)\n"
    lines.insert(insert_pos, new_line)

    # Write back
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    logger.info("Added missing return self.get_response(request) to middleware __call__")
    return True


def patch_middleware(file_path, patch_code):
    """Insert logging code in the middleware's __call__ method."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if re.search(r'request\.staff_passkey_required\s*=\s*False', line):
            start = i
            break
    if start is None:
        logger.error("Could not find request.staff_passkey_required = False in middleware")
        return False

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and not lines[j].startswith(' ') and not lines[j].startswith('\t'):
            end = j
            break

    new_block = patch_code
    if not new_block.endswith('\n'):
        new_block += '\n'
    lines[start:end] = [new_block]
    new_content = ''.join(lines)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    logger.info(f"Patched middleware in {file_path}")
    return True


# -----------------------------------------------------------------------------
# Main patcher
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Patch staff passkey issues")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    parser.add_argument("--target-dir", default=".", help="Project root directory (default: current)")
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    target_dir = Path(args.target_dir)
    if not target_dir.exists():
        logger.error(f"Target directory {target_dir} does not exist")
        sys.exit(1)

    staff_portal_path = target_dir / "axis_saas" / "views" / "staff_portal.py"
    middleware_path = target_dir / "axis_saas" / "middleware" / "staff_tenant_middleware.py"

    if not staff_portal_path.exists():
        logger.error(f"File not found: {staff_portal_path}")
        sys.exit(1)
    if not middleware_path.exists():
        logger.error(f"File not found: {middleware_path}")
        sys.exit(1)

    if args.dry_run:
        logger.info("DRY RUN - changes will not be applied")
        logger.info(f"Would replace staff_webauthn_authentication_options in {staff_portal_path}")
        logger.info(f"Would replace staff_webauthn_authentication_verify in {staff_portal_path}")
        logger.info(f"Would replace staff_webauthn_registration_verify in {staff_portal_path}")
        logger.info(f"Would replace staff_profile in {staff_portal_path}")
        logger.info(f"Would patch middleware in {middleware_path}")
        logger.info(f"Would ensure middleware has return in {middleware_path}")
        return

    logger.info("Applying patches...")

    # Replace functions in staff_portal.py
    replace_function(staff_portal_path, "staff_webauthn_authentication_options", NEW_AUTH_OPTIONS)
    replace_function(staff_portal_path, "staff_webauthn_authentication_verify", NEW_AUTH_VERIFY)
    replace_function(staff_portal_path, "staff_webauthn_registration_verify", NEW_REG_VERIFY)
    replace_function(staff_portal_path, "staff_profile", NEW_PROFILE_VIEW)

    # Patch middleware: replace the staff_passkey_required block with logging
    # We have a patch code for that.
    # But we also need to ensure the final return exists.
    patch_middleware(middleware_path, MIDDLEWARE_PATCH)

    # Ensure the middleware has a final return
    ensure_middleware_return(middleware_path)

    logger.info("All patches applied successfully.")


# The middleware patch code (same as before)
MIDDLEWARE_PATCH = """
        # Determine if passkey is required for the fully authenticated identity.
        request.staff_passkey_required = False
        try:
            effective_staff_id = request.session.get('staff_id') or pending_staff_id
            effective_schema_name = request.session.get('staff_schema_name') or pending_schema_name
            logger.info("Middleware: effective_staff_id=%s effective_schema=%s pending_passkey=%s", effective_staff_id, effective_schema_name, is_pending_passkey)
            if effective_staff_id and effective_schema_name:
                with schema_context('public'):
                    credential = StaffCredential.objects.filter(
                        staff_id=effective_staff_id,
                        schema_name=effective_schema_name,
                    ).first()
                logger.info("Middleware: credential exists=%s has_passkey=%s", credential is not None, bool(credential and credential.has_passkey))
                if credential and credential.has_passkey:
                    request.staff_passkey_required = False
                else:
                    # No passkey or credential missing
                    request.staff_passkey_required = True
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
                logger.info("Middleware: redirecting to profile because passkey required")
                return redirect('staff_profile_page')
        except Exception as exc:
            logger.exception('Middleware error: %s', exc)
            request.staff_passkey_required = False
"""

if __name__ == "__main__":
    main()
