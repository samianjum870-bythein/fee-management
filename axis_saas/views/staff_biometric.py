import base64
import json
import logging
from functools import wraps

from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context
from webauthn import generate_authentication_options, generate_registration_options, verify_authentication_response, verify_registration_response
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json_dict
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialType,
    UserVerificationRequirement,
)

from axis_saas.models import Staff, StaffBiometricCredential, StaffCredential


logger = logging.getLogger(__name__)


def biometric_json_errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except Exception as error:
            logger.exception('Biometric request failed: %s %s', request.method, request.path)
            return JsonResponse(
                {
                    'ok': False,
                    'error': f'Biometric service failed ({error.__class__.__name__}): {error}',
                },
                status=500,
            )

    return wrapped


def _rp_id(request):
    host = request.get_host().split(':')[0]
    return host or 'localhost'


def _origin(request):
    scheme = 'https' if request.is_secure() else 'http'
    return f'{scheme}://{request.get_host()}'


def _get_staff_from_session(request):
    staff_id = request.session.get('staff_id')
    schema_name = request.session.get('staff_schema_name')
    if not staff_id or not schema_name:
        return None, None
    return staff_id, schema_name


@biometric_json_errors
@require_http_methods(['GET'])
def staff_biometric_status(request):
    staff_id, schema_name = _get_staff_from_session(request)
    if not staff_id or not schema_name:
        return JsonResponse({'enabled': False, 'status': 'not_logged_in'}, status=401)

    with schema_context('public'):
        enabled = StaffBiometricCredential.objects.filter(
            staff_id=staff_id,
            schema_name=schema_name,
            enabled=True,
        ).exists()
    return JsonResponse({'enabled': enabled, 'status': 'ok'})


@biometric_json_errors
@require_http_methods(['POST'])
def staff_biometric_registration_options(request):
    staff_id, schema_name = _get_staff_from_session(request)
    if not staff_id or not schema_name:
        return JsonResponse({'error': 'Login required.'}, status=401)

    with schema_context(schema_name):
        staff = Staff.objects.filter(pk=staff_id).first()
        if not staff:
            return JsonResponse({'error': 'Staff not found.'}, status=404)
    with schema_context('public'):
        credential = StaffCredential.objects.filter(staff_id=staff_id, schema_name=schema_name).first()
    user_name = credential.username if credential else staff.email or staff.full_name

    options = generate_registration_options(
        rp_id=_rp_id(request),
        rp_name='AXIS Staff Portal',
        user_name=user_name,
        user_id=str(staff_id).encode('utf-8'),
        user_display_name=staff.full_name,
        timeout=60000,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=None,
    )
    serialized_options = options_to_json_dict(options)
    if not serialized_options.get('challenge') or not serialized_options.get('user', {}).get('id'):
        return JsonResponse({'ok': False, 'error': 'Biometric options were generated incorrectly.'}, status=500)
    request.session['staff_biometric_challenge'] = bytes_to_base64url(options.challenge)
    return JsonResponse({'ok': True, 'options': serialized_options})


@biometric_json_errors
@require_http_methods(['POST'])
def staff_biometric_register(request):
    staff_id, schema_name = _get_staff_from_session(request)
    if not staff_id or not schema_name:
        return JsonResponse({'error': 'Login required.'}, status=401)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid payload.'}, status=400)

    challenge = request.session.get('staff_biometric_challenge')
    registration = payload.get('registration') or payload
    credential = payload.get('credential') or registration
    credential_id = credential.get('id') or credential.get('credential_id') or ''
    if not credential_id:
        return JsonResponse({'error': 'Registration response missing credential id.'}, status=400)

    expected_challenge = base64url_to_bytes(challenge) if challenge else None
    verified = verify_registration_response(
        credential={
            'id': credential_id,
            'rawId': base64url_to_bytes(credential.get('rawId', credential_id)),
            'response': {
                'clientDataJSON': base64url_to_bytes(credential.get('response', {}).get('clientDataJSON', '')),
                'attestationObject': base64url_to_bytes(credential.get('response', {}).get('attestationObject', '')),
            },
            'type': 'public-key',
        },
        expected_challenge=expected_challenge or b'',
        expected_rp_id=_rp_id(request),
        expected_origin=_origin(request),
        require_user_verification=False,
    )

    with schema_context('public'):
        StaffBiometricCredential.objects.update_or_create(
            staff_id=staff_id,
            schema_name=schema_name,
            credential_id=credential_id,
            defaults={'public_key': bytes_to_base64url(verified.credential_public_key), 'enabled': True, 'sign_count': verified.sign_count},
        )
    request.session.pop('staff_biometric_challenge', None)
    return JsonResponse({'ok': True, 'enabled': True, 'message': 'Biometric enabled successfully.'})


@biometric_json_errors
@require_http_methods(['POST'])
def staff_biometric_prepare_login(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid login payload.'}, status=400)

    username = (payload.get('username') or '').strip()
    password = payload.get('password') or ''
    with schema_context('public'):
        credential = StaffCredential.objects.filter(username=username).first() if username else None
    if not credential or not credential.is_active or credential.locked_until and credential.locked_until > timezone.now():
        return JsonResponse({'ok': False, 'error': 'Invalid user or locked account.'}, status=401)

    if not credential.check_password(password):
        return JsonResponse({'ok': False, 'error': 'Invalid username or password.'}, status=401)

    with schema_context(credential.schema_name):
        staff = Staff.objects.filter(pk=credential.staff_id).first()
    if not staff or staff.status != 'active':
        return JsonResponse({'ok': False, 'error': 'Staff account is inactive or missing.'}, status=403)

    with schema_context('public'):
        biometric = StaffBiometricCredential.objects.filter(staff_id=staff.pk, schema_name=credential.schema_name, enabled=True).first()
    if not biometric:
        return JsonResponse({'ok': True, 'biometric_enabled': False, 'message': 'No biometric credential found.'})

    options = generate_authentication_options(
        rp_id=_rp_id(request),
        challenge=None,
        timeout=60000,
        allow_credentials=[
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(biometric.credential_id),
                type=PublicKeyCredentialType.PUBLIC_KEY,
            )
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    request.session['staff_pending_biometric_login'] = {
        'username': username,
        'schema_name': credential.schema_name,
        'staff_id': staff.pk,
        'challenge': bytes_to_base64url(options.challenge),
        'credential_id': biometric.credential_id,
    }
    return JsonResponse({'ok': True, 'biometric_enabled': True, 'options': options_to_json_dict(options)})


@biometric_json_errors
@require_http_methods(['POST'])
def staff_biometric_complete_login(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid assertion payload.'}, status=400)

    pending = request.session.get('staff_pending_biometric_login') or {}
    if not pending:
        return JsonResponse({'ok': False, 'error': 'Biometric login was not started.'}, status=400)

    assertion = payload.get('assertion') or payload
    credential_id = assertion.get('id') or assertion.get('credential_id') or ''
    with schema_context('public'):
        biometric = StaffBiometricCredential.objects.filter(
            staff_id=pending['staff_id'],
            schema_name=pending['schema_name'],
            credential_id=credential_id,
            enabled=True,
        ).first()
    if not biometric:
        return JsonResponse({'ok': False, 'error': 'This device is not registered for this account.'}, status=403)

    verification = verify_authentication_response(
        credential={
            'id': credential_id,
            'rawId': base64url_to_bytes(assertion.get('rawId', credential_id)),
            'response': {
                'clientDataJSON': base64url_to_bytes(assertion.get('response', {}).get('clientDataJSON', '')),
                'authenticatorData': base64url_to_bytes(assertion.get('response', {}).get('authenticatorData', '')),
                'signature': base64url_to_bytes(assertion.get('response', {}).get('signature', '')),
                'userHandle': base64url_to_bytes(assertion.get('response', {}).get('userHandle', '')) if assertion.get('response', {}).get('userHandle') else None,
            },
            'type': 'public-key',
        },
        expected_challenge=base64url_to_bytes(pending.get('challenge') or b''),
        expected_rp_id=_rp_id(request),
        expected_origin=_origin(request),
        credential_public_key=biometric.public_key_bytes,
        credential_current_sign_count=biometric.sign_count,
        require_user_verification=False,
    )

    biometric.sign_count = verification.new_sign_count
    with schema_context('public'):
        biometric.save(update_fields=['sign_count'])

    request.session.flush()
    request.session['staff_id'] = pending['staff_id']
    request.session['staff_schema_name'] = pending['schema_name']
    request.session['staff_username'] = pending['username']
    request.session['staff_role'] = 'teacher'
    request.session['staff_name'] = Staff.objects.filter(pk=pending['staff_id']).first().full_name if Staff.objects.filter(pk=pending['staff_id']).exists() else pending['username']
    request.session['staff_session_token'] = __import__('uuid').uuid4().hex
    request.session.set_expiry(1800)
    request.session.modified = True
    request.session.pop('staff_pending_biometric_login', None)
    cache.set(f"staff_session_token:{pending['schema_name']}:{pending['staff_id']}", request.session['staff_session_token'], 1800)
    return JsonResponse({'ok': True, 'redirect': '/portal/staff/dashboard/'})
