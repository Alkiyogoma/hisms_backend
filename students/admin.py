from django.contrib import admin

from students.models import ParentGuardian, Student, StudentGuardian, StudentSibling, LaravelParent, StudentLaravelParent


class StudentGuardianInline(admin.TabularInline):
    model = StudentGuardian
    extra = 0


class StudentLaravelParentInline(admin.TabularInline):
    model = StudentLaravelParent
    extra = 0


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("admission_no", "last_name", "first_name", "class_name", "status", "laravel_student_id", "created_at")
    list_filter = ("status", "class_name", "gender")
    search_fields = ("admission_no", "first_name", "last_name", "laravel_student_id", "qr_code")
    inlines = [StudentGuardianInline, StudentLaravelParentInline]
    readonly_fields = ("created_at", "updated_at")


@admin.register(ParentGuardian)
class ParentGuardianAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "user", "created_at")
    search_fields = ("full_name", "phone", "email", "user__username")
    list_filter = ("is_archived", "preferred_language", "pdpa_consent_given")


@admin.register(LaravelParent)
class LaravelParentAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "laravel_parent_id", "guardian", "created_at")
    search_fields = ("first_name", "last_name", "phone", "email")
    list_filter = ("city", "state", "created_at")
    readonly_fields = ("created_at", "updated_at")


@admin.register(StudentLaravelParent)
class StudentLaravelParentAdmin(admin.ModelAdmin):
    list_display = ("student", "parent", "relationship", "is_primary_contact", "can_pickup")
    list_filter = ("relationship", "is_primary_contact", "can_pickup", "emergency_contact")
    search_fields = ("student__first_name", "student__last_name", "parent__first_name", "parent__last_name")


@admin.register(StudentSibling)
class StudentSiblingAdmin(admin.ModelAdmin):
    list_display = ("student_a", "student_b", "created_at")
