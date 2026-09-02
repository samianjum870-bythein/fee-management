document.addEventListener('DOMContentLoaded', function () {
    const loginForm = document.getElementById('staffLoginForm');
    const profileButton = document.getElementById('enableBiometricBtn');

    function base64UrlToUint8Array(value) {
        if (!value) return new Uint8Array();
        const padded = value + '='.repeat((4 - (value.length % 4)) % 4);
        const base64 = padded.replace(/-/g, '+').replace(/_/g, '/');
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes;
    }

    function uint8ArrayToBase64Url(value) {
        let binary = '';
        const bytes = new Uint8Array(value);
        for (let i = 0; i < bytes.length; i += 1) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    if (loginForm && typeof window.PublicKeyCredential !== 'undefined') {
        loginForm.addEventListener('submit', async function (event) {
            const usernameInput = loginForm.querySelector('[name="username"]');
            const passwordInput = loginForm.querySelector('[name="password"]');
            const username = (usernameInput || {}).value || '';
            const password = (passwordInput || {}).value || '';
            if (!username || !password) return;

            const loginButton = loginForm.querySelector('button[type="submit"]');
            if (loginButton) loginButton.disabled = true;

            try {
                const prepareResponse = await fetch('/portal/staff/biometric/prepare-login/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: JSON.stringify({ username, password }),
                });
                const prepareData = await prepareResponse.json();

                if (!prepareData.ok || !prepareData.biometric_enabled) {
                    loginForm.submit();
                    return;
                }

                const publicKey = {
                    challenge: base64UrlToUint8Array(prepareData.options.challenge),
                    timeout: prepareData.options.timeout || 60000,
                    rpId: prepareData.options.rpId,
                    allowCredentials: (prepareData.options.allowCredentials || []).map((item) => ({
                        id: base64UrlToUint8Array(item.id),
                        type: item.type,
                        transports: item.transports || ['internal'],
                    })),
                    userVerification: prepareData.options.userVerification || 'preferred',
                };

                const credential = await navigator.credentials.get({ publicKey });
                const payload = {
                    assertion: {
                        id: credential.id,
                        rawId: uint8ArrayToBase64Url(new Uint8Array(credential.rawId)),
                        response: {
                            authenticatorData: uint8ArrayToBase64Url(new Uint8Array(credential.response.authenticatorData)),
                            clientDataJSON: uint8ArrayToBase64Url(new Uint8Array(credential.response.clientDataJSON)),
                            signature: uint8ArrayToBase64Url(new Uint8Array(credential.response.signature)),
                            userHandle: credential.response.userHandle ? uint8ArrayToBase64Url(new Uint8Array(credential.response.userHandle)) : null,
                        },
                        type: credential.type,
                    }
                };

                const completeResponse = await fetch('/portal/staff/biometric/complete-login/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: JSON.stringify(payload),
                });

                const completeData = await completeResponse.json();
                if (completeData.ok && completeData.redirect) {
                    window.location.href = completeData.redirect;
                    return;
                }

                loginForm.submit();
            } catch (error) {
                if (loginButton) loginButton.disabled = false;
                loginForm.submit();
            }
        });
    }

    if (profileButton) {
        profileButton.addEventListener('click', async function () {
            if (profileButton.disabled) return;

            try {
                const statusResponse = await fetch('/portal/staff/biometric/status/', {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                });
                const statusData = await statusResponse.json();
                if (statusData && statusData.enabled) {
                    profileButton.textContent = 'Biometric Enabled';
                    profileButton.disabled = true;
                    const statusBadge = document.getElementById('biometricStatus');
                    if (statusBadge) statusBadge.textContent = 'Enabled';
                    return;
                }

                const optionsResponse = await fetch('/portal/staff/biometric/registration-options/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: JSON.stringify({})
                });
                const optionsData = await optionsResponse.json();
                if (!optionsData.ok) {
                    alert(optionsData.error || 'Unable to start biometric setup.');
                    return;
                }

                const publicKey = {
                    challenge: base64UrlToUint8Array(optionsData.options.challenge),
                    rp: optionsData.options.rp,
                    user: {
                        id: base64UrlToUint8Array(optionsData.options.user.id),
                        name: optionsData.options.user.name,
                        displayName: optionsData.options.user.displayName,
                    },
                    pubKeyCredParams: optionsData.options.pubKeyCredParams || [{ type: 'public-key', alg: -7 }, { type: 'public-key', alg: -257 }],
                    timeout: optionsData.options.timeout || 60000,
                    attestation: optionsData.options.attestation || 'none',
                    authenticatorSelection: optionsData.options.authenticatorSelection || { userVerification: 'preferred', authenticatorAttachment: 'platform' },
                };

                const credential = await navigator.credentials.create({ publicKey });
                const registerResponse = await fetch('/portal/staff/biometric/register/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: JSON.stringify({
                        registration: {
                            id: credential.id,
                            rawId: uint8ArrayToBase64Url(new Uint8Array(credential.rawId)),
                            response: {
                                clientDataJSON: uint8ArrayToBase64Url(new Uint8Array(credential.response.clientDataJSON)),
                                attestationObject: uint8ArrayToBase64Url(new Uint8Array(credential.response.attestationObject)),
                            },
                            type: credential.type,
                        }
                    })
                });

                const registerData = await registerResponse.json();
                if (registerData.ok) {
                    profileButton.textContent = 'Biometric Enabled';
                    profileButton.disabled = true;
                    const statusBadge = document.getElementById('biometricStatus');
                    if (statusBadge) statusBadge.textContent = 'Enabled';
                    return;
                }

                alert(registerData.error || 'Unable to enable biometric verification.');
            } catch (error) {
                alert('This device/browser does not support fingerprint or face authentication, or the popup was cancelled.');
            }
        });
    }
});
