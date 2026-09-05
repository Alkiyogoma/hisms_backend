from django.urls import path

from .views import (
    DisciplineDetailView,
    DisciplineHODQueueView,
    DisciplineListView,
    DisciplineParentConfirmView,
    DisciplineReviewView,
    DisciplineSubmitView,
)

app_name = "discipline"

urlpatterns = [
    path("", DisciplineListView.as_view(), name="list"),
    path("submit/", DisciplineSubmitView.as_view(), name="submit"),
    path("<int:pk>/", DisciplineDetailView.as_view(), name="detail"),
    path("<int:pk>/review/", DisciplineReviewView.as_view(), name="review"),
    path("<int:pk>/parent-confirm/", DisciplineParentConfirmView.as_view(), name="parent_confirm"),
    path("hod-queue/", DisciplineHODQueueView.as_view(), name="hod_queue"),
]
