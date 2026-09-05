"""
Seed all 8 FRD-required user roles for UAT testing.
Usage: python manage.py seed_users
Creates demo users with predictable credentials.
"""
from django.core.management.base import BaseCommand
from users.models import User, UserRole


DEMO_USERS = [
    {
        "username": "superadmin",
        "password": "Hodari@SA1",
        "role": UserRole.SUPER_ADMIN,
        "first_name": "System",
        "last_name": "Administrator",
        "email": "superadmin@hodari.ac.tz",
        "is_staff": True,
        "is_superuser": True,
    },
    {
        "username": "hos",
        "password": "hodari123",
        "role": UserRole.HEAD_OF_SCHOOL,
        "first_name": "Amina",
        "last_name": "Salim",
        "email": "hos@hodari.ac.tz",
    },
    {
        "username": "primaryhod",
        "password": "Hodari@HOD1",
        "role": UserRole.PRIMARY_HOD,
        "first_name": "John",
        "last_name": "Kamau",
        "email": "primaryhod@hodari.ac.tz",
    },
    {
        "username": "ecdhod",
        "password": "Hodari@ECD1",
        "role": UserRole.ECD_HOD,
        "first_name": "Sarah",
        "last_name": "Mwangi",
        "email": "ecdhod@hodari.ac.tz",
    },
    {
        "username": "admin",
        "password": "Hodari@ADM1",
        "role": UserRole.ADMIN_OFFICER,
        "first_name": "Fatuma",
        "last_name": "Juma",
        "email": "admin@hodari.ac.tz",
        "is_staff": True,
    },
    {
        "username": "finance",
        "password": "Hodari@FIN1",
        "role": UserRole.FINANCE_OFFICER,
        "first_name": "David",
        "last_name": "Mutua",
        "email": "finance@hodari.ac.tz",
    },
    {
        "username": "teacher",
        "password": "Hodari@TCH1",
        "role": UserRole.TEACHER,
        "first_name": "Grace",
        "last_name": "Otieno",
        "email": "teacher@hodari.ac.tz",
    },
    {
        "username": "parent",
        "password": "Hodari@PAR1",
        "role": UserRole.PARENT,
        "first_name": "James",
        "last_name": "Njoroge",
        "email": "parent@hodari.ac.tz",
    },
]


class Command(BaseCommand):
    help = "Create demo users for all 8 FRD-required roles"

    def handle(self, *args, **options):
        self.stdout.write("--- Seeding demo users ---")
        created_count = 0
        for data in DEMO_USERS:
            username = data["username"]
            password = data["password"]
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "role": data["role"],
                    "first_name": data.get("first_name", ""),
                    "last_name": data.get("last_name", ""),
                    "email": data.get("email", ""),
                },
            )
            if created:
                user.set_password(password)
                user.save()
                created_count += 1
                self.stdout.write(f"  CREATED  {username:15} ({user.role})")
            else:
                # Update role and force password reset for UAT consistency
                user.role = data["role"]
                user.set_password(password)
                user.first_name = data.get("first_name", user.first_name)
                user.last_name = data.get("last_name", user.last_name)
                user.save()
                self.stdout.write(f"  UPDATED  {username:15} ({user.role}) - Password Reset")

        self.stdout.write(self.style.SUCCESS(f"\n--- Done: {created_count} users created ---"))
        self.stdout.write("\n  Demo credentials:")
        self.stdout.write("  %-16s %-22s %s" % ("Username", "Password", "Role"))
        self.stdout.write("  " + "-" * 56)
        for u in DEMO_USERS:
            self.stdout.write("  %-16s %-22s %s" % (
                u.get("username", ""),
                u.get("password", ""),
                u.get("role", ""),
            ))
        self.stdout.write("  URL: http://127.0.0.1:8000/")

