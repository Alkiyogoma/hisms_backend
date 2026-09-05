from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tasks'
    verbose_name = 'Task Management'

    def ready(self):
        # Import signal handlers when app is ready
        import tasks.signals
