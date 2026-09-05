class AssessmentReportSubmitView(RoleRequiredMixin, View):
    """Handle primary assessment report submission from the tasks page."""
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD, UserRole.TEACHER, UserRole.ADMIN_OFFICER,
    ]

    EOT_SUBJECTS = [
        {"key": "math_eot", "label": "Mathematics"},
        {"key": "english_eot", "label": "English"},
        {"key": "science_eot", "label": "Science"},
        {"key": "average_eot", "label": "Average"},
    ]
    CAMBRIDGE_SUBJECTS = [
        {"key": "math_cam", "label": "Mathematics Paper 1"},
        {"key": "english_cam", "label": "English Paper 1"},
        {"key": "science_cam", "label": "Science Paper 1"},
        {"key": "average_cam", "label": "Average"},
    ]
    WORK_HABITS = [
        {"key": "wh_follows_directions", "label": "Follows directions"},
        {"key": "wh_works_independently", "label": "Works well independently"},
        {"key": "wh_not_disturb", "label": "Does not disturb others"},
        {"key": "wh_completes_neatly", "label": "Completes work neatly"},
    ]
    PERSONAL_TRAITS = [
        {"key": "pt_honest", "label": "Is honest"},
        {"key": "pt_flexibility", "label": "Displays flexibility"},
        {"key": "pt_attention", "label": "Attention span"},
        {"key": "pt_creativity", "label": "Displays creativity"},
    ]
    SOCIAL_TRAITS = [
        {"key": "st_courteous", "label": "Is courteous"},
        {"key": "st_self_control", "label": "Exhibits self-control"},
        {"key": "st_respects", "label": "Respects authority"},
        {"key": "st_relates_well", "label": "Relates well with others"},
    ]

    def _build_section(self, subjects, data):
        result = []
        for s in subjects:
            result.append({
                "key": s["key"],
                "label": s["label"],
                "pct": data.get("pct_%s" % s["key"], ""),
                "grade": data.get("grade_%s" % s["key"], ""),
                "remark": data.get("remark_%s" % s["key"], ""),
            })
        return result

    def _build_traits(self, traits, data):
        result = []
        for t in traits:
            result.append({
                "key": t["key"],
                "label": t["label"],
                "val": data.get("trait_%s" % t["key"], ""),
            })
        return result

    def _strip(self, val):
        import re
        if not val:
            return val
        return re.sub(r'<[^>]+>', '', val).strip()

    def _get_all_grade_keys(self):
        keys = []
        for s in self.EOT_SUBJECTS + self.CAMBRIDGE_SUBJECTS:
            keys.append("grade_%s" % s["key"])
        for t in self.WORK_HABITS + self.PERSONAL_TRAITS + self.SOCIAL_TRAITS:
            keys.append("trait_%s" % t["key"])
        return keys

    def get(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        if task.assigned_to != request.user and request.user.role not in (UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied()

        saved_data = task.metadata.get("assessment_data", {})

        from django.utils import timezone
        ctx = {
            "task": task,
            "eot": self._build_section(self.EOT_SUBJECTS, saved_data),
            "cambridge": self._build_section(self.CAMBRIDGE_SUBJECTS, saved_data),
            "work_habits": self._build_traits(self.WORK_HABITS, saved_data),
            "personal_traits": self._build_traits(self.PERSONAL_TRAITS, saved_data),
            "social_traits": self._build_traits(self.SOCIAL_TRAITS, saved_data),
            "teacher_comments": saved_data.get("teacher_comments", ""),
            "teacher_name": saved_data.get("teacher_name", ""),
            "report_date": saved_data.get("report_date", timezone.now().date().isoformat()),
            "saved": request.GET.get("saved"),
        }
        from django.shortcuts import render
        return render(request, "tasks/_assessment_report_form.html", ctx)

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        if task.assigned_to != request.user and request.user.role not in (UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied()

        import json

        assessment_data = {}

        for s in self.EOT_SUBJECTS + self.CAMBRIDGE_SUBJECTS:
            assessment_data["pct_%s" % s["key"]] = request.POST.get("pct_%s" % s["key"], "").strip()
            assessment_data["grade_%s" % s["key"]] = request.POST.get("grade_%s" % s["key"], "").strip()
            assessment_data["remark_%s" % s["key"]] = request.POST.get("remark_%s" % s["key"], "").strip()

        for t in self.WORK_HABITS + self.PERSONAL_TRAITS + self.SOCIAL_TRAITS:
            assessment_data["trait_%s" % t["key"]] = request.POST.get("trait_%s" % t["key"], "").strip()

        teacher_comments = self._strip(request.POST.get("teacher_comments", ""))
        teacher_name = self._strip(request.POST.get("teacher_name", ""))
        report_date = request.POST.get("report_date", "").strip()

        assessment_data["teacher_comments"] = teacher_comments
        assessment_data["teacher_name"] = teacher_name
        assessment_data["report_date"] = report_date

        task.metadata["assessment_data"] = assessment_data
        task.save(update_fields=["metadata"])

        applicant_id = task.metadata.get("applicant_id")
        if applicant_id:
            try:
                from admissions.models import AssessmentSchedule
                assessment = AssessmentSchedule.objects.get(applicant_id=int(applicant_id))
                assessment.teacher_comments = "[%s] %s" % (teacher_name, teacher_comments)
                assessment.hod_comments = json.dumps(assessment_data)
                assessment.save(update_fields=["teacher_comments", "hod_comments", "updated_at"])
            except Exception:
                pass

        action = request.POST.get("action", "submit_report")

        if action == "submit_report":
            task.mark_completed()
            TaskHistory.objects.create(
                task=task, action='completed',
                changed_by=request.user, comment="Assessment report submitted"
            )

        from django.shortcuts import render
        ctx = {
            "task": task,
            "eot": self._build_section(self.EOT_SUBJECTS, assessment_data),
            "cambridge": self._build_section(self.CAMBRIDGE_SUBJECTS, assessment_data),
            "work_habits": self._build_traits(self.WORK_HABITS, assessment_data),
            "personal_traits": self._build_traits(self.PERSONAL_TRAITS, assessment_data),
            "social_traits": self._build_traits(self.SOCIAL_TRAITS, assessment_data),
            "teacher_comments": teacher_comments,
            "teacher_name": teacher_name,
            "report_date": report_date,
            "saved": True,
            "draft": action == "save_draft",
        }
        return render(request, "tasks/_assessment_report_form.html", ctx)
