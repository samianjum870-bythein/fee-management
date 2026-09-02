document.addEventListener('DOMContentLoaded', function () {
    const loginForm = document.getElementById('staffLoginForm');
    const profileButton = document.getElementById('enableBiometricBtn');

    function getCsrfToken() {
        return (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
    }

    async function readJsonResponse(response) {
        const body = await response.text();
        let data;
        try {
            data = JSON.parse(body);
        } catch (error) {
            throw new Error(`Server returned an invalid response (${response.status}). Please refresh and try again.`);
        }
        if (!response.ok) {
            throw new Error(data.error || `Request failed (${response.status})`);
        }
        return data;
    }

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

            event.preventDefault();

            const loginButton = loginForm.querySelector('button[type="submit"]');
            if (loginButton) loginButton.disabled = true;

            try {
                const prepareResponse = await fetch('/portal/staff/biometric/prepare-login/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: JSON.stringify({ username, password }),
                });
                const prepareData = await readJsonResponse(prepareResponse);

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
                            ...(item.transports ? { transports: item.transports } : {}),
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
                        'X-CSRFToken': getCsrfToken(),
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: JSON.stringify(payload),
                });

                const completeData = await readJsonResponse(completeResponse);
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

            if (!window.isSecureContext) {
                alert('Biometric setup requires HTTPS. Open the secure HTTPS address and try again.');
                return;
            }
            if (!window.PublicKeyCredential || !navigator.credentials) {
                alert('This browser does not support biometric sign-in. Please update Chrome and try again.');
                return;
            }

            const originalButtonText = profileButton.textContent;
            profileButton.disabled = true;

            try {
                const statusResponse = await fetch('/portal/staff/biometric/status/', {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                });
                const statusData = await readJsonResponse(statusResponse);
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
                        'X-CSRFToken': getCsrfToken(),
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: JSON.stringify({})
                });
                const optionsData = await readJsonResponse(optionsResponse);
                if (!optionsData.ok || !optionsData.options) {
                    throw new Error(optionsData.error || 'Unable to start biometric setup.');
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
                            'X-CSRFToken': getCsrfToken(),
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

                const registerData = await readJsonResponse(registerResponse);
                if (registerData.ok) {
                    profileButton.textContent = 'Biometric Enabled';
                    profileButton.disabled = true;
                    const statusBadge = document.getElementById('biometricStatus');
                    if (statusBadge) statusBadge.textContent = 'Enabled';
                    return;
                }

                alert(registerData.error || 'Unable to enable biometric verification.');
            } catch (error) {
                console.error('Biometric registration failed:', error);
                if (!window.isSecureContext) {
                    alert('Biometric setup requires HTTPS. Open the secure HTTPS address and try again.');
                } else if (error.name === 'NotAllowedError') {
                    alert('Biometric prompt was cancelled or permission was denied. Try again and allow the prompt.');
                } else {
                    alert('Biometric setup failed: ' + (error.message || 'Please try again.'));
                }
                profileButton.disabled = false;
                profileButton.textContent = originalButtonText;
            }
        });
    }
});
