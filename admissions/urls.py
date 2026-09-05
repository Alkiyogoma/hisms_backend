from django.urls import path

from admissions.views import (
    AdmissionsPipelineView,
    AssessmentListView,
    AssessmentReportView,
    ApplicantDecisionView,
    ApplicantDetailView,
    ApplicantRevertView,
    ApplicantTransitionView,
    AssessmentScheduleView,
    AssessmentResultSignOffView,
    AssessmentResultEditView,
    CompleteEnrolmentView,
    ConfirmAssessmentFeePaidView,
    UnconfirmAssessmentFeeView,
    InquiryCreateView,
    InquiryEditView,
    MeetingScheduleView,
    MeetingRescheduleView,
    SendLogisticsView,
    ToggleDocumentView,
    WaitlistView,
    OfferLetterView,
    AssessmentCalendarView,
    AdmissionsActionsView,
    ParentGuardianLookupView,
    ParentNameSearchView,
    GradeCapacityCheckView,
    HodReviewSubmitView,
    InternalNoteCreateView,
    InternalNoteEditView,
    InternalNoteDeleteView,
    ApplicantPhotoUploadView,
    ParentInquiryView,
    AdminWalkInFormView,
    AdminGenerateInvoiceView,
)



app_name = "admissions"

urlpatterns = [
    path("", AdmissionsPipelineView.as_view(), name="pipeline"),
    path("assessments/", AssessmentListView.as_view(), name="assessments"),
    path("assessments/calendar/", AssessmentCalendarView.as_view(), name="assessment_calendar"),
    path("actions/", AdmissionsActionsView.as_view(), name="actions_needed"),
    path("waitlist/", WaitlistView.as_view(), name="waitlist"),

    # Public parent inquiry form (no login required)
    path("inquire/", ParentInquiryView.as_view(), name="parent_inquiry"),

    path("new/", InquiryCreateView.as_view(), name="new_inquiry"),
    path("applicant/<int:pk>/", ApplicantDetailView.as_view(), name="detail"),
    path("applicant/<int:pk>/edit/", InquiryEditView.as_view(), name="edit_inquiry"),
    path("<int:pk>/transition/", ApplicantTransitionView.as_view(), name="transition"),
    path("<int:pk>/meeting/", MeetingScheduleView.as_view(), name="meeting"),
    path("<int:pk>/meeting/reschedule/", MeetingRescheduleView.as_view(), name="meeting_reschedule"),
    path("<int:pk>/assessment/", AssessmentScheduleView.as_view(), name="assessment"),
    path("<int:pk>/assessment/result/", AssessmentResultSignOffView.as_view(), name="assessment_result"),
    path("<int:pk>/assessment/result/edit/", AssessmentResultEditView.as_view(), name="assessment_result_edit"),
    path("<int:pk>/assessment/hod-review/", HodReviewSubmitView.as_view(), name="hos_review_submit"),
    path("<int:pk>/decision/", ApplicantDecisionView.as_view(), name="decision"),
    path("<int:pk>/revert/", ApplicantRevertView.as_view(), name="revert"),
    path("<int:pk>/fee/confirm/", ConfirmAssessmentFeePaidView.as_view(), name="fee_confirm"),
    path("<int:pk>/fee/unconfirm/", UnconfirmAssessmentFeeView.as_view(), name="fee_unconfirm"),

    path("<int:pk>/logistics/send/", SendLogisticsView.as_view(), name="logistics_send"),
    path("<int:pk>/documents/", ToggleDocumentView.as_view(), name="documents"),
    path("<int:pk>/enrol/", CompleteEnrolmentView.as_view(), name="enrol"),
    path("<int:pk>/offer/", OfferLetterView.as_view(), name="offer_letter"),
    
    # AJAX APIs
    path("api/parent-lookup/", ParentGuardianLookupView.as_view(), name="api_parent_lookup"),
    path("api/parent-name-search/", ParentNameSearchView.as_view(), name="api_parent_name_search"),
    path("api/capacity-check/", GradeCapacityCheckView.as_view(), name="api_capacity_check"),

    # FR-ADM-029: Internal notes
    path("<int:pk>/internal-note/", InternalNoteCreateView.as_view(), name="internal_note"),
    path("internal-note/<int:note_pk>/edit/", InternalNoteEditView.as_view(), name="internal_note_edit"),
    path("internal-note/<int:note_pk>/delete/", InternalNoteDeleteView.as_view(), name="internal_note_delete"),
    
    # Photo upload
    path("<int:pk>/upload-photo/", ApplicantPhotoUploadView.as_view(), name="upload_photo"),

    # Walk-in parent flow
    path("<int:pk>/walkin-form/", AdminWalkInFormView.as_view(), name="walkin_form"),
    path("<int:pk>/walkin-invoice/", AdminGenerateInvoiceView.as_view(), name="walkin_invoice"),

    # Teacher assessment report (from tasks page)
    path("assessment-report/<int:pk>/", AssessmentReportView.as_view(), name="assessment_report"),
]
