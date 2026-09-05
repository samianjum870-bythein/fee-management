from django import forms
from django.db import models
from .models import Student, FeeStructure, PaymentTransaction, SchoolFeeSettings, SchoolClass, WingCategory

class WingCategorySelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        category_id = str(getattr(value, 'value', value))
        wing_category_id = getattr(self, 'category_map', {}).get(category_id)
        if wing_category_id:
            option['attrs']['data-wing-category'] = str(wing_category_id)
        return option

class StudentForm(forms.ModelForm):
    wing_category = forms.ModelChoiceField(queryset=WingCategory.objects.none(), required=False, label='Campus / Wing')
    school_class = forms.ModelChoiceField(queryset=None, label="Class", required=True, widget=WingCategorySelect)

    class Meta:
        model = Student
        fields = ['name', 'father_name', 'father_cnic', 'parent_mobile', 'wing_category', 'school_class',
                  'admission_date', 'status', 'gender', 'date_of_birth', 'address', 'notes', 'custom_fee']
        widgets = {
            'admission_date': forms.DateInput(attrs={'type': 'date'}),
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        wing_school = kwargs.pop('wing_school', False)
        super().__init__(*args, **kwargs)
        self.wing_school = wing_school
        if not wing_school:
            self.fields.pop('wing_category')
        else:
            self.fields['wing_category'].required = True
        from django.db.models import Count
        # Annotate each class with student count and display it in the label
        if wing_school:
            categories = WingCategory.objects.filter(is_active=True).order_by('parent__name', 'name')
            self.fields['wing_category'].queryset = categories
        classes = SchoolClass.objects.filter(is_active=True).order_by('name', 'section').annotate(
            student_count=Count('students')
        )
        selected_category = self.data.get('wing_category') if self.is_bound and wing_school else getattr(self.instance, 'wing_category_id', None) if wing_school else None
        if selected_category:
            classes = classes.filter(wing_category_id=selected_category)
        self.fields['school_class'].queryset = classes
        self.fields['school_class'].widget.category_map = {
            str(class_id): category_id
            for class_id, category_id in classes.values_list('id', 'wing_category_id')
            if category_id
        }
        # Override label_from_instance to show strength
        def label_from_instance(obj):
            return f"{obj.name} – {obj.section} ({obj.student_count} students)" if obj.section else f"{obj.name} ({obj.student_count} students)"
        self.fields['school_class'].label_from_instance = label_from_instance

        if self.instance and self.instance.pk and self.instance.school_class:
            self.fields['school_class'].initial = self.instance.school_class
        if wing_school and self.instance and self.instance.pk and self.instance.wing_category:
            self.fields['wing_category'].initial = self.instance.wing_category

    def save(self, commit=True):
        instance = super().save(commit=False)
        # Populate grade & section from the selected school_class
        if instance.school_class:
            instance.grade = instance.school_class.name
            instance.section = instance.school_class.section
        if commit:
            instance.save()
        return instance

    def clean(self):
        cleaned_data = super().clean()
        if self.wing_school:
            category = cleaned_data.get('wing_category')
            selected_class = cleaned_data.get('school_class')
            if selected_class and category and selected_class.wing_category_id != category.id:
                self.add_error('school_class', 'Selected class does not belong to the selected campus / wing.')
        return cleaned_data
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
    wing_category = forms.ModelChoiceField(queryset=WingCategory.objects.none(), required=False, label='Campus / Wing')

    class Meta:
        from .models import SchoolClass
        model = SchoolClass
        fields = ['wing_category', 'name', 'section', 'description', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        wing_school = kwargs.pop('wing_school', False)
        super().__init__(*args, **kwargs)
        if not wing_school:
            self.fields.pop('wing_category')
        else:
            self.fields['wing_category'].queryset = WingCategory.objects.filter(is_active=True).order_by('parent__name', 'name')
            self.fields['wing_category'].required = True

    def clean(self):
        cleaned_data = super().clean()
        if getattr(self, 'wing_school', False) and not cleaned_data.get('wing_category'):
            self.add_error('wing_category', 'Select a campus / wing before creating a class.')
        return cleaned_data

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

