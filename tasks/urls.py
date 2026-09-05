"""
Task Module - URL Configuration
API endpoints + HTML page routes for task management
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from . import template_views as tv

app_name = 'tasks'

# Create router for ViewSet (API)
router = DefaultRouter()
router.register(r'api', views.TaskViewSet, basename='task-api')

urlpatterns = [
    #  HTML pages 
    path('', tv.TaskDashboardView.as_view(), name='dashboard'),
    path('<uuid:pk>/', tv.TaskDetailView.as_view(), name='detail'),
    path('<uuid:pk>/complete/', tv.TaskMarkCompleteView.as_view(), name='mark_complete'),
    path('<uuid:pk>/action/', tv.TaskTakeActionView.as_view(), name='take_action'),
    path('<uuid:pk>/defer/', tv.TaskDeferView.as_view(), name='defer'),
    path('counts/api/', tv.TaskCountsAPIView.as_view(), name='counts_api'),
    path('seed/', tv.SeedTasksView.as_view(), name='seed'),
    path('<uuid:pk>/assessment-report/', tv.AssessmentReportSubmitView.as_view(), name='assessment_report_submit'),

    #  REST API (via DRF router) 
    path('api/', include(router.urls)),
    path('api/counts/', views.user_task_counts, name='task-counts'),
    path('api/overdue/', views.overdue_tasks, name='task-overdue'),
]
