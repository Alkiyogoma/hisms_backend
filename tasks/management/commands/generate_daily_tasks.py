"""
Management command: generate_daily_tasks
Run via cron or scheduler to create recurring tasks for all active users.
Usage: python manage.py generate_daily_tasks
"""

from django.core.management.base import BaseCommand

from tasks.services import generate_daily_tasks, cleanup_completed_tasks


class Command(BaseCommand):
    help = "Generate daily tasks based on user roles and module events"

    def handle(self, *args, **options):
        self.stdout.write("Generating daily tasks...")
        tasks = generate_daily_tasks()
        self.stdout.write(self.style.SUCCESS(f"Created {len(tasks)} new tasks."))

        # Also clean up old completed tasks
        deleted_count, _ = cleanup_completed_tasks(days_old=30)
        if deleted_count:
            self.stdout.write(f"Archived {deleted_count} completed tasks older than 30 days.")
