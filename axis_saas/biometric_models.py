import base64

from django.db import models


class StaffBiometricCredential(models.Model):
    """A WebAuthn credential authored by the browser for a staff member."""

    staff_id = models.PositiveIntegerField(db_index=True)
    schema_name = models.CharField(max_length=63, db_index=True)
    credential_id = models.TextField(unique=True)
    public_key = models.TextField(blank=True, default='')
    sign_count = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('staff_id', 'schema_name', 'credential_id')]

    def __str__(self):
        return f'Staff biometric {self.staff_id} ({self.schema_name})'

    @property
    def credential_id_bytes(self):
        if not self.credential_id:
            return b''
        padded = self.credential_id + '=' * (-len(self.credential_id) % 4)
        try:
            return base64.urlsafe_b64decode(padded.encode('ascii'))
        except Exception:
            return self.credential_id.encode('utf-8')

    @property
    def public_key_bytes(self):
        if not self.public_key:
            return b''
        try:
            padded = self.public_key + '=' * (-len(self.public_key) % 4)
            return base64.urlsafe_b64decode(padded.encode('ascii'))
        except Exception:
            return self.public_key.encode('utf-8')
