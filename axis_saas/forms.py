from django import forms
from django.db import models
from .models import Student, FeeStructure, PaymentTransaction, SchoolFeeSettings

class StudentForm(forms.ModelForm):
    school_class = forms.ModelChoiceField(queryset=None, label="Class", required=True)

    class Meta:
        model = Student
        fields = ['name', 'father_name', 'father_cnic', 'parent_mobile', 'school_class',
                  'admission_date', 'status', 'gender', 'date_of_birth', 'address', 'notes', 'custom_fee']
        widgets = {
            'admission_date': forms.DateInput(attrs={'type': 'date'}),
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import SchoolClass
        from django.db.models import Count
        # Annotate each class with student count and display it in the label
        classes = SchoolClass.objects.filter(is_active=True).order_by('name', 'section').annotate(
            student_count=Count('students')
        )
        self.fields['school_class'].queryset = classes
        # Override label_from_instance to show strength
        def label_from_instance(obj):
            return f"{obj.name} – {obj.section} ({obj.student_count} students)" if obj.section else f"{obj.name} ({obj.student_count} students)"
        self.fields['school_class'].label_from_instance = label_from_instance

        if self.instance and self.instance.pk and self.instance.school_class:
            self.fields['school_class'].initial = self.instance.school_class

    def save(self, commit=True):
        instance = super().save(commit=False)
        # Populate grade & section from the selected school_class
        if instance.school_class:
            instance.grade = instance.school_class.name
            instance.section = instance.school_class.section
        if commit:
            instance.save()
        return instance
class StaffForm(forms.ModelForm):
    class Meta:
        from .models import Staff
        model = Staff
        fields = ['first_name', 'last_name', 'gender', 'date_of_birth', 'cnic', 'email', 'job_title', 'department', 'hire_date', 'status', 'phone', 'address', 'notes']
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'hire_date': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 2}),
        }

class FeeCollectionForm(forms.Form):
    student = forms.ModelChoiceField(queryset=Student.objects.none(), label="Student")
    amount = forms.DecimalField(max_digits=10, decimal_places=2, label="Amount (₹)")
    payment_mode = forms.ChoiceField(choices=PaymentTransaction.PAYMENT_MODE_CHOICES, label="Payment Mode")
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}), label="Remarks")

class FeeStructureForm(forms.ModelForm):
    class Meta:
        model = FeeStructure
        fields = ['grade', 'monthly_fee']
        widgets = {
            'grade': forms.TextInput(attrs={'class': 'form-control'}),
            'monthly_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

class FeeSettingsForm(forms.ModelForm):
    class Meta:
        model = SchoolFeeSettings
        fields = ['fee_generation_day', 'due_date_offset', 'late_fee_penalty']
        widgets = {
            'fee_generation_day': forms.NumberInput(attrs={'min': 1, 'max': 31}),
            'due_date_offset': forms.NumberInput(attrs={'min': 1}),
            'late_fee_penalty': forms.NumberInput(attrs={'step': '0.01'}),
        }

class FamilyPaymentForm(forms.Form):
    father_cnic = forms.CharField(max_length=15, label="Father CNIC")
    amount = forms.DecimalField(max_digits=10, decimal_places=2, required=False, label="Amount (leave empty for full)")
    payment_mode = forms.ChoiceField(choices=PaymentTransaction.PAYMENT_MODE_CHOICES, label="Payment Mode")
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}), label="Remarks")
# ------------------- Class & Subject Forms -------------------
class ClassForm(forms.ModelForm):
    class Meta:
        from .models import SchoolClass
        model = SchoolClass
        fields = ['name', 'section', 'description', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 2}),
        }

class SubjectForm(forms.ModelForm):
    class Meta:
        from .models import Subject
        model = Subject
        fields = ['name', 'code', 'description', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 2}),
        }

class ClassSubjectForm(forms.ModelForm):
    class Meta:
        from .models import ClassSubject, Staff, SchoolClass, Subject
        model = ClassSubject
        fields = ['school_class', 'subject', 'teacher', 'academic_year', 'is_active']

