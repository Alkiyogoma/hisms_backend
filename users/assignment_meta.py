"""JSON metadata for staff assignment UI (department → class → subjects)."""

import json

from django.http import JsonResponse
from django.views import View

from academics.models import GradeClass, Subject
from core.permissions import RoleRequiredMixin
from users.models import UserRole


def build_assignment_meta():
    classes = []
    for gc in GradeClass.objects.order_by("name"):
        classes.append({
            "id": gc.id,
            "name": gc.name,
            "department": gc.department,
            "subject_names": list(
                gc.subjects.filter(is_active=True).order_by("name").values_list("name", flat=True)
            ),
        })

    subjects = list(
        Subject.objects.filter(is_active=True)
        .order_by("name")
        .values("name", "department")
    )
    return {"classes": classes, "subjects": subjects}


class StaffAssignmentMetaView(RoleRequiredMixin, View):
    """GET JSON for cascading department / class / subject pickers."""

    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL]
    required_permission = "hr.view_teacherclassassignment"

    def get(self, request):
        return JsonResponse(build_assignment_meta())
