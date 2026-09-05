from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self):
        # Ensure RoleConfig is registered so M2M string references resolve
        import users.role_models  # noqa: F401
