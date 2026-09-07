"""
Display grade helper function to compute display grade for a student.
Uses django.apps to avoid circular imports.
"""

from django.apps import apps

def get_student_display_grade(student):
    """
    Return the display grade string for a student based on its school_class and wing_category.
    For wing school: "Main (Sub) - Class - Section"
    For single school: "Class - Section"
    Falls back to student.grade/student.section if school_class is None.
    """
    if not student.school_class:
        if student.section:
            return f"{student.grade} - {student.section}"
        return student.grade

    cls = student.school_class
    if cls.wing_category:
        main = cls.wing_category.parent
        sub = cls.wing_category
        if cls.section:
            return f"{main.name} ({sub.name}) - {cls.name} - {cls.section}"
        else:
            return f"{main.name} ({sub.name}) - {cls.name}"
    else:
        if cls.section:
            return f"{cls.name} - {cls.section}"
        else:
            return cls.name
