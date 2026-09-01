import random
import string

from django.utils import timezone
from django.db import models
from django_tenants.models import TenantMixin, DomainMixin
from decimal import Decimal
from datetime import date, timedelta
from django.contrib.auth.hashers import make_password, check_password

SCHOOL_FEATURE_CHOICES = [
    ('class_management', 'Class & Subject Management'),
    ('dashboard', 'Dashboard'),
    ('students', 'Students'),
    ('fee_collection', 'Fee Collection'),
    ('defaulters', 'Defaulters'),
    ('reports', 'Reports'),
    ('stock_management', 'Stock Management'),
    ('fee_structure', 'Fee Structure'),
    ('fee_settings', 'Fee Settings'),
    ('family_payment', 'Family Payment'),
    ('staff_management', 'Staff Management'),
]

# ------------------- Tenant Model -------------------
class SchoolClient(TenantMixin):
    name = models.CharField(max_length=100, unique=True)
    created_on = models.DateField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    admin_username = models.CharField(max_length=150, default="admin_pending")
    admin_password = models.CharField(max_length=128, default="AxisFallback123!")
    school_logo = models.FileField(upload_to="school_logos/", blank=True, null=True)
    tenant_type = models.CharField(max_length=20, choices=[("school", "School"), ("gym", "Gym")], default="school")
    enabled_features = models.JSONField(default=list, blank=True, help_text="Select the school modules enabled for this tenant.")
    
    auto_create_schema = True
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._raw_password = None

    def set_password(self, raw_password):
        """Hash and store password, keeping raw for signals."""
        self._raw_password = raw_password
        self.admin_password = make_password(raw_password)

    def check_password(self, raw_password):
        """Verify raw password against stored hash or legacy plaintext."""
        if self.admin_password and self.is_password_hashed():
            return check_password(raw_password, self.admin_password)
        return raw_password == self.admin_password

    def is_password_hashed(self):
        """Return True if admin_password looks like a Django hash."""
        return bool(self.admin_password) and self.admin_password.startswith(('pbkdf2_sha256', 'bcrypt', 'argon2'))

    def save(self, *args, **kwargs):
        raw_password = getattr(self, '_raw_password', None)
        if raw_password:
            self.admin_password = make_password(raw_password)
        elif self.admin_password and not self.is_password_hashed():
            self._raw_password = self.admin_password
            self.admin_password = make_password(self.admin_password)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name}"

    def get_available_school_features(self):
        return [choice[0] for choice in SCHOOL_FEATURE_CHOICES]

    def is_feature_enabled(self, feature_key):
        if self.tenant_type != 'school':
            return False
        if not self.enabled_features:
            return False
        return feature_key in self.enabled_features

    
class SchoolDomain(DomainMixin):
    pass

# ------------------- Student Model -------------------
class Student(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('suspended', 'Suspended'),
        ('graduated', 'Graduated'),
    ]
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
    ]
    
    name = models.CharField(max_length=150)
    father_name = models.CharField(max_length=150)
    father_cnic = models.CharField(max_length=15, help_text="35202-XXXXXXX-X")
    parent_mobile = models.CharField(max_length=15)
    grade = models.CharField(max_length=50)
    section = models.CharField(max_length=50)
    admission_date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    photo = models.ImageField(upload_to="student_photos/", blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    roll_number = models.CharField(max_length=50, unique=True, blank=True)
    custom_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    enrolled_on = models.DateTimeField(auto_now_add=True)
    default_extra_charges = models.JSONField(default=list, blank=True, null=True)
    automation_enabled = models.BooleanField(default=False, help_text="Enable monthly auto‑generation of fees")
    school_class = models.ForeignKey('SchoolClass', on_delete=models.SET_NULL, null=True, blank=True, related_name='students')

    def save(self, *args, **kwargs):
        # Ensure roll number
        if not self.roll_number:
            last = Student.objects.order_by('id').last()
            if last and last.roll_number and last.roll_number.isdigit():
                self.roll_number = str(int(last.roll_number) + 1)
            else:
                self.roll_number = "1001"
        # If school_class is set, update grade and section from it
        if self.school_class:
            self.grade = self.school_class.name
            self.section = self.school_class.section
        # Set custom_fee from FeeStructure if not set
        if not self.pk or self.custom_fee == 0:
            base = FeeStructure.objects.filter(grade=self.grade).first()
            if base:
                self.custom_fee = base.monthly_fee
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.roll_number})"

# ------------------- Fee Structure -------------------
class FeeStructure(models.Model):
    grade = models.CharField(max_length=50, unique=True)
    monthly_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.grade} - ₹{self.monthly_fee}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        Student.objects.filter(grade=self.grade).update(custom_fee=self.monthly_fee)

# ------------------- Fee Record (Monthly) -------------------
class FeeRecord(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('partial', 'Partially Paid'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('waived', 'Waived'),
    ]
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='fee_records')
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    due_date = models.DateField()
    due_date_offset = models.PositiveSmallIntegerField(default=15, help_text="Days after generation when fee is due")
    late_fee_per_day = models.DecimalField(max_digits=6, decimal_places=2, default=0.00, help_text="Late fee amount applied per day")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    remarks = models.TextField(blank=True, null=True)
    extra_charges = models.JSONField(default=list, blank=True, null=True)

    class Meta:
        unique_together = ['student', 'month', 'year']
        ordering = ['-year', '-month']

    @property
    def remaining(self):
        return self.amount - self.paid_amount
    @property
    def total_amount(self):
        """Total fee = base + extra charges + late fee accrued"""
        from decimal import Decimal
        extras = sum(Decimal(str(ch.get('amount', 0))) for ch in (self.extra_charges or []))
        return self.amount + extras + getattr(self, "late_fee_accrued", Decimal("0"))

    @property
    def remaining_total(self):
        from decimal import Decimal
        return self.total_amount - Decimal(str(self.paid_amount))

    @property
    def is_fully_paid(self):
        return self.paid_amount >= self.amount

    def save(self, *args, **kwargs):
        # Use total_amount (base + extras) for status
        if self.remaining_total <= 0:
            self.status = 'paid'
        elif self.paid_amount > 0:
            self.status = 'partial'
        elif date.today() > self.due_date and self.paid_amount == 0:
            self.status = 'overdue'
        else:
            self.status = 'pending'
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.name} - {self.month}/{self.year} - {self.get_status_display()}"

# ------------------- Payment Transaction -------------------
class PaymentTransaction(models.Model):
    PAYMENT_MODE_CHOICES = [
        ('cash', 'Cash'),
        ('bank_transfer', 'Bank Transfer'),
        ('cheque', 'Cheque'),
        ('online', 'Online'),
    ]
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='payments')
    fee_records = models.ManyToManyField(FeeRecord, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_date = models.DateField(auto_now_add=True)
    payment_mode = models.CharField(max_length=20, choices=PAYMENT_MODE_CHOICES, default='cash')
    payment_type = models.CharField(max_length=20, default='full')
    receipt_number = models.CharField(max_length=50, unique=True, blank=True)
    remarks = models.TextField(blank=True, null=True)
    extra_charges = models.JSONField(default=list, blank=True, null=True)
    created_by = models.CharField(max_length=150, blank=True)

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            today = date.today()
            prefix = f"RCPT-{today.strftime('%Y%m%d')}"
            last = PaymentTransaction.objects.filter(receipt_number__startswith=prefix).count()
            self.receipt_number = f"{prefix}-{last+1:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.receipt_number} - {self.student.name} - ₹{self.amount}"

# ------------------- School Fee Settings -------------------
class SchoolFeeSettings(models.Model):
    fee_generation_day = models.PositiveSmallIntegerField(default=1, help_text="Day of month (1-31)")
    due_date_offset = models.PositiveSmallIntegerField(default=15, help_text="Days after generation when fee is due")
    late_fee_penalty = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="Penalty %")
    default_extra_charges = models.JSONField(default=list, blank=True, null=True, help_text="Common charges used for future vouchers")
    automation_enabled = models.BooleanField(default=False, help_text="Enable monthly auto‑generation of fees")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Fee Settings"

    class Meta:
        verbose_name_plural = "Fee Settings"

# ------------------- Gym Models -------------------
class GymCustomer(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('suspended', 'Suspended'),
    ]
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
    ]
    barcode = models.CharField(max_length=50, unique=True, blank=True, null=True)

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=15)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    photo = models.ImageField(upload_to='gym_customers/', blank=True, null=True)
    membership_start = models.DateField(default=timezone.now)
    membership_end = models.DateField(blank=True, null=True)
    monthly_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    notes = models.TextField(blank=True, null=True)
    created_on = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.monthly_fee == 0:
            settings = GymSettings.objects.first()
            if settings:
                self.monthly_fee = settings.default_monthly_fee
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.phone})"

class GymSubscription(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('partial', 'Partially Paid'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('cancelled', 'Cancelled'),
    ]
    customer = models.ForeignKey(GymCustomer, on_delete=models.CASCADE, related_name='subscriptions')
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    remarks = models.TextField(blank=True, null=True)
    extra_charges = models.JSONField(default=list, blank=True, null=True)
    # new fields for multi-month & cancellation
    is_cancelled = models.BooleanField(default=False)
    cancelled_on = models.DateField(blank=True, null=True)

    class Meta:
        unique_together = ['customer', 'month', 'year']
        ordering = ['-year', '-month']

    @property
    def remaining(self):
        return self.amount - self.paid_amount
    @property
    def total_amount(self):
        """Total fee = base + extra charges + late fee accrued"""
        from decimal import Decimal
        extras = sum(Decimal(str(ch.get('amount', 0))) for ch in (self.extra_charges or []))
        return self.amount + extras + getattr(self, "late_fee_accrued", Decimal("0"))

    @property
    def remaining_total(self):
        from decimal import Decimal
        return self.total_amount - Decimal(str(self.paid_amount))

    @property
    def is_fully_paid(self):
        return self.paid_amount >= self.amount

    def save(self, *args, **kwargs):
        # Use total_amount (base + extras) for status
        if self.remaining_total <= 0:
            self.status = 'paid'
        elif self.paid_amount > 0:
            self.status = 'partial'
        elif date.today() > self.due_date and self.paid_amount == 0:
            self.status = 'overdue'
        else:
            self.status = 'pending'
        super().save(*args, **kwargs)

    def __str__(self):
        cancel = " [CANCELLED]" if self.is_cancelled else ""
        return f"{self.customer.name} - {self.month}/{self.year} - {self.get_status_display()}{cancel}"

class GymPayment(models.Model):
    PAYMENT_MODE_CHOICES = [
        ('cash', 'Cash'),
        ('bank_transfer', 'Bank Transfer'),
        ('cheque', 'Cheque'),
        ('online', 'Online'),
    ]
    customer = models.ForeignKey(GymCustomer, on_delete=models.CASCADE, related_name='payments')
    subscriptions = models.ManyToManyField(GymSubscription, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_date = models.DateField(auto_now_add=True)
    payment_mode = models.CharField(max_length=20, choices=PAYMENT_MODE_CHOICES, default='cash')
    payment_type = models.CharField(max_length=20, default='full')
    receipt_number = models.CharField(max_length=50, unique=True, blank=True)
    remarks = models.TextField(blank=True, null=True)
    extra_charges = models.JSONField(default=list, blank=True, null=True)
    created_by = models.CharField(max_length=150, blank=True)

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            today = date.today()
            prefix = f"GYM-{today.strftime('%Y%m%d')}"
            last = GymPayment.objects.filter(receipt_number__startswith=prefix).count()
            self.receipt_number = f"{prefix}-{last+1:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.receipt_number} - {self.customer.name} - ₹{self.amount}"

class GymAttendance(models.Model):
    customer = models.ForeignKey(GymCustomer, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(default=timezone.localdate)
    check_in = models.DateTimeField(default=timezone.now)
    check_out = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)   # for edit window
    duration_minutes = models.PositiveIntegerField(null=True, blank=True, help_text="Duration in minutes")

    class Meta:
        unique_together = ['customer', 'date']
        ordering = ['-date', '-check_in']

    def save(self, *args, **kwargs):
        if self.check_in and not self.date:
            from django.utils import timezone
            self.date = timezone.localdate(self.check_in)
        super().save(*args, **kwargs)

    def is_editable(self):
        """Allow editing only within 7 hours after check-in."""
        from django.utils import timezone
        return (timezone.now() - self.check_in).total_seconds() <= 7 * 3600

    def __str__(self):
        return f"{self.customer.name} - {self.date} - IN:{self.check_in.strftime('%H:%M') if self.check_in else '--'}"

class GymSettings(models.Model):
    default_monthly_fee = models.DecimalField(max_digits=10, decimal_places=2, default=500.00)
    subscription_generation_day = models.PositiveSmallIntegerField(default=1)
    due_date_offset = models.PositiveSmallIntegerField(default=15)
    late_fee_penalty = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Gym Settings"

    class Meta:
        verbose_name_plural = "Gym Settings"

# ------------------- Stock Management Models -------------------
class ProductCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name_plural = "Product Categories"

    def __str__(self):
        return self.name

class Product(models.Model):
    category = models.ForeignKey(ProductCategory, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=50, unique=True, blank=True)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.sku:
            import time
            self.sku = f"SKU-{int(time.time())}-{self.category.id if self.category else 0}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.sku})"

# ------------------- Manual Generation Log -------------------
class ManualGenerationLog(models.Model):
    LOG_TYPE_CHOICES = [
        ('manual', 'Manual'),
        ('auto', 'Auto'),
    ]
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    created_count = models.PositiveIntegerField(default=0)
    skipped_existing = models.PositiveIntegerField(default=0)
    skipped_no_fee = models.PositiveIntegerField(default=0)
    generated_at = models.DateTimeField(auto_now_add=True)
    triggered_by = models.CharField(max_length=150, blank=True, null=True)
    log_type = models.CharField(max_length=10, choices=LOG_TYPE_CHOICES, default='manual', help_text="Type of generation (manual or auto)")

    class Meta:
        ordering = ['-generated_at']

    def __str__(self):
        return f"{self.month}/{self.year} - {self.get_log_type_display()} - {self.generated_at.strftime('%Y-%m-%d %H:%M')}"

# ------------------- Notification Model -------------------
class Notification(models.Model):
    message = models.CharField(max_length=255)
    link = models.CharField(max_length=255, blank=True, null=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    extra_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.message[:50]


class StaffCredential(models.Model):
    """Authentication record for staff across all public-school tenants."""
    username = models.CharField(max_length=150, unique=True)
    password = models.CharField(max_length=128)
    visible_password = models.CharField(max_length=128, blank=True, null=True)
    staff_id = models.PositiveIntegerField()
    schema_name = models.CharField(max_length=63)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['username']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.raw_password = None

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    def increment_failed_attempts(self):
        self.failed_attempts = (self.failed_attempts or 0) + 1
        if self.failed_attempts >= 5:
            from datetime import timedelta
            self.locked_until = timezone.now() + timedelta(minutes=15)
        self.save(update_fields=['failed_attempts', 'locked_until'])
        return self.failed_attempts

    def reset_failed_attempts(self):
        self.failed_attempts = 0
        self.locked_until = None
        self.save(update_fields=['failed_attempts', 'locked_until'])

    def set_password(self, raw_password):
        self.raw_password = raw_password
        self.visible_password = raw_password
        self.password = make_password(raw_password)

    def __str__(self):
        return f"{self.username} ({self.schema_name})"


# ------------------- Staff Model -------------------
class Staff(models.Model):
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    ]
    DEPARTMENT_CHOICES = [
        ('admin', 'Administration'),
        ('teaching', 'Teaching'),
        ('support', 'Support Staff'),
        ('other', 'Other'),
    ]
    ROLE_CHOICES = [
        ('teacher', 'Teacher'),
        ('class_teacher', 'Class Teacher'),
        ('subject_teacher', 'Subject Teacher'),
        ('admin', 'Admin'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    staff_id = models.CharField(max_length=20, unique=True, blank=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    full_name = models.CharField(max_length=200, blank=True)  # computed
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    cnic = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(unique=True, blank=True, null=True)
    job_title = models.CharField(max_length=100)
    department = models.CharField(max_length=50, choices=DEPARTMENT_CHOICES, default='teaching')
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default='teacher')
    can_mark_attendance = models.BooleanField(default=True)
    can_view_fees = models.BooleanField(default=False)
    hire_date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    phone = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    photo = models.ImageField(upload_to='staff_photos/', blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_on = models.DateTimeField(auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_on']

    def generate_unique_username(self):
        base = f"{self.first_name.strip().lower()}.{self.last_name.strip().lower()}".replace(' ', '.')
        if not base or base == '.':
            base = 'staff'
        while True:
            suffix = ''.join(random.choice(string.digits) for _ in range(4))
            username = f"{base}.{suffix}"
            if not StaffCredential.objects.filter(username=username).exists():
                return username

    def generate_password(self):
        alphabet = string.ascii_letters + string.digits + '!@#$%^&*'
        while True:
            password = ''.join(random.choice(alphabet) for _ in range(14))
            if any(c.isupper() for c in password) and any(c.islower() for c in password) and any(c.isdigit() for c in password) and any(c in '!@#$%^&*' for c in password):
                return password

    def ensure_staff_credential(self, force_new=False):
        from django.db import connection
        from django_tenants.utils import schema_context
        if not self.pk:
            return None
        schema_name = getattr(connection, 'schema_name', None) or 'public'
        with schema_context('public'):
            credential = StaffCredential.objects.filter(staff_id=self.pk, schema_name=schema_name).first()
            if credential is None or force_new:
                raw_password = self.generate_password()
                username = self.generate_unique_username()
                credential = StaffCredential(username=username, staff_id=self.pk, schema_name=schema_name)
                credential.set_password(raw_password)
                credential.is_active = True
                credential.save()
                credential.raw_password = raw_password
                credential.visible_password = raw_password
                self._generated_password = raw_password
                self._generated_username = username
            else:
                credential.raw_password = getattr(self, '_generated_password', None) or credential.visible_password
                if credential.raw_password is None:
                    credential.raw_password = None
            return credential

    def get_public_credential(self, schema_name=None):
        from django_tenants.utils import schema_context
        tenant_schema = schema_name or getattr(__import__('django.db').db.connection, 'schema_name', None) or 'public'
        with schema_context('public'):
            credential = StaffCredential.objects.filter(staff_id=self.pk, schema_name=tenant_schema).first()
            if credential is not None and getattr(self, '_generated_password', None):
                credential.raw_password = self._generated_password
            elif credential is not None and not hasattr(credential, 'raw_password'):
                credential.raw_password = None
            return credential

    def save(self, *args, **kwargs):
        if not self.staff_id:
            last = Staff.objects.order_by('id').last()
            if last and last.staff_id and last.staff_id.isdigit():
                self.staff_id = str(int(last.staff_id) + 1)
            else:
                self.staff_id = "1001"
        self.full_name = f"{self.first_name} {self.last_name}".strip()
        is_new = self.pk is None
        super().save(*args, **kwargs)
        self._generated_password = None
        self._generated_username = None
        try:
            credential = self.ensure_staff_credential(force_new=is_new)
            if credential:
                self._generated_password = getattr(credential, 'raw_password', None)
                self._generated_username = credential.username
        except Exception:
            pass

    @property
    def is_online(self):
        from django.core.cache import cache
        from django.db import connection
        schema_name = getattr(connection, 'schema_name', None) or 'public'
        session_key = cache.get(f'staff_online:{schema_name}:{self.pk}')
        return bool(session_key)

    def logout_session(self):
        from django.core.cache import cache
        from django.db import connection
        from django_tenants.utils import schema_context

        from axis_saas.session_backend import SessionStore

        schema_name = getattr(connection, 'schema_name', None) or 'public'
        keys = cache.get(f'staff_session_keys:{schema_name}:{self.pk}', [])
        if isinstance(keys, str):
            keys = [keys]

        for key in list(keys):
            try:
                with schema_context('public'):
                    session = SessionStore(session_key=key)
                    session.flush()
            except Exception:
                pass

        cache.set(f'staff_session_token:{schema_name}:{self.pk}', 'logged_out', 300)
        cache.delete(f'staff_online:{schema_name}:{self.pk}')
        cache.delete(f'staff_session_keys:{schema_name}:{self.pk}')

    def __str__(self):
        return f"{self.full_name} ({self.staff_id})"


class StudentAttendance(models.Model):
    STATUS_CHOICES = [
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('late', 'Late'),
        ('holiday', 'Holiday'),
    ]
    student = models.ForeignKey('Student', on_delete=models.CASCADE, related_name='attendance_records')
    school_class = models.ForeignKey('SchoolClass', on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='present')
    teacher = models.ForeignKey('Staff', on_delete=models.SET_NULL, null=True, blank=True, related_name='marked_attendance')
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('student', 'date')
        ordering = ['-date', 'student__name']
        indexes = [models.Index(fields=['school_class', 'date'])]

    def __str__(self):
        return f"{self.student.name} - {self.date} - {self.get_status_display()}"


# ========== CLASS & SUBJECT MANAGEMENT ==========
class SchoolClass(models.Model):
    """Represents a class (e.g., Grade 5, Section A)."""
    name = models.CharField(max_length=100, help_text="Class name, e.g., 'Grade 5'")
    section = models.CharField(max_length=10, blank=True, help_text="Section, e.g., 'A'")
    description = models.TextField(blank=True, null=True)
    class_teacher = models.ForeignKey("Staff", on_delete=models.SET_NULL, null=True, blank=True, related_name="class_teacher_of")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'section']
        unique_together = ['name', 'section']

    def __str__(self):
        return f"{self.name} - {self.section}" if self.section else self.name

    def normalize_fields(self):
        """Normalize name to title case and section to uppercase."""
        if self.name:
            self.name = self.name.strip().title()
        if self.section:
            self.section = self.section.strip().upper()

    def clean(self):
        self.normalize_fields()
        # Case-insensitive uniqueness check
        if self.pk:
            existing = SchoolClass.objects.filter(
                name__iexact=self.name,
                section__iexact=self.section
            ).exclude(pk=self.pk)
        else:
            existing = SchoolClass.objects.filter(
                name__iexact=self.name,
                section__iexact=self.section
            )
        if existing.exists():
            from django.core.exceptions import ValidationError
            raise ValidationError(f"A class with name '{self.name}' and section '{self.section}' already exists (case-insensitive).")

    def save(self, *args, **kwargs):
        self.normalize_fields()
        self.full_clean()
        super().save(*args, **kwargs)

class Subject(models.Model):
    """Represents a subject (e.g., Mathematics)."""
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True, blank=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def normalize_fields(self):
        if self.name:
            self.name = self.name.strip().title()

    def clean(self):
        self.normalize_fields()
        if self.pk:
            existing = Subject.objects.filter(name__iexact=self.name).exclude(pk=self.pk)
        else:
            existing = Subject.objects.filter(name__iexact=self.name)
        if existing.exists():
            from django.core.exceptions import ValidationError
            raise ValidationError(f"A subject with name '{self.name}' already exists (case-insensitive).")

    def save(self, *args, **kwargs):
        self.normalize_fields()
        if not self.code:
            import time
            self.code = f"SUBJ-{int(time.time())}"
        self.full_clean()
        super().save(*args, **kwargs)

class ClassSubject(models.Model):
    """Links a subject to a class and assigns a teacher."""
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name='class_subjects')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='class_subjects')
    teacher = models.ForeignKey('Staff', on_delete=models.SET_NULL, null=True, blank=True, related_name='class_subjects', limit_choices_to={'status': 'active'})
    academic_year = models.CharField(max_length=20, blank=True, help_text="e.g., 2024-2025")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['school_class', 'subject']
        ordering = ['school_class', 'subject']

    def __str__(self):
        teacher_name = self.teacher.full_name if self.teacher else "Unassigned"
        return f"{self.school_class} - {self.subject} ({teacher_name})"
