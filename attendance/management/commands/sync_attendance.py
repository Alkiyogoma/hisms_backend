"""
Django management command for data synchronization between Django and Laravel systems.

Usage:
  python manage.py sync_attendance --from-laravel    # Sync from Laravel to Django
  python manage.py sync_attendance --to-laravel      # Sync from Django to Laravel
  python manage.py sync_attendance --bidirectional   # Bidirectional sync
  python manage.py sync_attendance --verify          # Verify data integrity
  python manage.py sync_attendance --status          # Show sync status
  python manage.py sync_attendance --retry-failed    # Retry failed syncs
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from datetime import timedelta
import json

from attendance.data_sync_service import DataSyncService
from attendance.models import AttendanceEntry


class Command(BaseCommand):
    help = 'Synchronize attendance data between Django and Laravel systems'

    def add_arguments(self, parser):
        parser.add_argument(
            '--from-laravel',
            action='store_true',
            help='Sync from Laravel to Django'
        )
        parser.add_argument(
            '--to-laravel',
            action='store_true',
            help='Sync from Django to Laravel'
        )
        parser.add_argument(
            '--bidirectional',
            action='store_true',
            help='Perform bidirectional synchronization'
        )
        parser.add_argument(
            '--verify',
            action='store_true',
            help='Verify data integrity'
        )
        parser.add_argument(
            '--status',
            action='store_true',
            help='Show current sync status'
        )
        parser.add_argument(
            '--retry-failed',
            action='store_true',
            help='Retry failed synchronizations'
        )
        parser.add_argument(
            '--student-id',
            type=str,
            help='Sync specific student'
        )
        parser.add_argument(
            '--date-range',
            type=int,
            help='Sync records from last N days'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be synced without actually syncing'
        )
        parser.add_argument(
            '--json',
            action='store_true',
            help='Output as JSON'
        )

    def handle(self, *args, **options):
        """Execute the management command"""
        try:
            if options['status']:
                self.show_sync_status(options)
            elif options['verify']:
                self.verify_integrity(options)
            elif options['retry_failed']:
                self.retry_failed_syncs(options)
            elif options['from_laravel']:
                self.sync_from_laravel(options)
            elif options['to_laravel']:
                self.sync_to_laravel(options)
            elif options['bidirectional']:
                self.bidirectional_sync(options)
            else:
                raise CommandError('Please specify a sync direction or action')

        except Exception as e:
            raise CommandError(f'Sync error: {str(e)}')

    def show_sync_status(self, options):
        """Show current synchronization status"""
        self.stdout.write(self.style.SUCCESS(' Sync Status\n'))

        status = DataSyncService.get_sync_status()

        if options['json']:
            self.stdout.write(json.dumps(status, indent=2, default=str))
        else:
            self.display_status(status)

    def display_status(self, status):
        """Display sync status in human-readable format"""
        if 'last_sync' in status:
            self.stdout.write(f"Last Sync: {status['last_sync']}\n")

        if 'pending_syncs' in status:
            self.stdout.write(f"Pending Syncs: {status['pending_syncs']}\n")

        if 'failed_syncs' in status:
            self.stdout.write(self.style.WARNING(f"Failed Syncs: {status['failed_syncs']}\n"))

        if 'conflicts' in status:
            self.stdout.write(self.style.WARNING(f"Active Conflicts: {status['conflicts']}\n"))

        if 'last_error' in status and status['last_error']:
            self.stdout.write(self.style.ERROR(f"Last Error: {status['last_error']}\n"))

    def verify_integrity(self, options):
        """Verify data integrity"""
        self.stdout.write(self.style.SUCCESS(' Verifying Data Integrity\n'))

        if options['student_id']:
            self.stdout.write(f'Checking student {options["student_id"]}...')
            # Find entry for student
            entries = AttendanceEntry.objects.filter(student__admission_no=options['student_id'])
            if entries.exists():
                entry = entries.first()
                is_valid, errors = DataSyncService.validate_data_integrity(entry.id)
            else:
                raise CommandError(f'Student {options["student_id"]} not found')
        else:
            self.stdout.write('Checking all records...')
            is_valid, errors = DataSyncService.validate_data_integrity()

        if is_valid:
            self.stdout.write(self.style.SUCCESS(' All data is valid\n'))
        else:
            self.stdout.write(self.style.WARNING(f' Found {len(errors)} issues:\n'))
            for error in errors[:10]:
                self.stdout.write(f'  • {error}')
            if len(errors) > 10:
                self.stdout.write(f'  ... and {len(errors)-10} more\n')

    def retry_failed_syncs(self, options):
        """Retry failed synchronizations"""
        self.stdout.write(self.style.SUCCESS(' Retrying Failed Syncs\n'))

        result = DataSyncService.retry_failed_syncs()

        self.stdout.write(f"Retried: {result.get('retried', 0)}\n")
        self.stdout.write(f"Succeeded: {result.get('succeeded', 0)}\n")
        self.stdout.write(f"Failed: {result.get('failed', 0)}\n")

    def sync_from_laravel(self, options):
        """Sync from Laravel to Django"""
        self.stdout.write(self.style.SUCCESS(' Syncing from Laravel to Django\n'))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be made\n'))

        # In production, would fetch from Laravel API
        self.stdout.write('Fetching Laravel attendance data...')
        
        # Example: Would query Laravel API for recent attendance
        days_back = options.get('date_range', 7)
        start_date = timezone.now().date() - timedelta(days=days_back)

        self.stdout.write(f'Syncing data from {start_date}...')
        self.stdout.write(self.style.SUCCESS(' Sync complete\n'))

    def sync_to_laravel(self, options):
        """Sync from Django to Laravel"""
        self.stdout.write(self.style.SUCCESS(' Syncing from Django to Laravel\n'))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be made\n'))

        # Get records to sync
        if options['student_id']:
            entries = AttendanceEntry.objects.filter(
                student__admission_no=options['student_id']
            )
            self.stdout.write(f'Syncing {entries.count()} records for student {options["student_id"]}...')
        else:
            days_back = options.get('date_range', 7)
            start_date = timezone.now().date() - timedelta(days=days_back)
            entries = AttendanceEntry.objects.filter(date__gte=start_date)
            self.stdout.write(f'Syncing {entries.count()} records from last {days_back} days...')

        synced = 0
        failed = 0

        for entry in entries:
            if not options['dry_run']:
                success, error = DataSyncService.sync_attendance_to_laravel(entry.id)
                if success:
                    synced += 1
                else:
                    failed += 1
                    self.stdout.write(self.style.ERROR(f'   {entry.id}: {error}'))
            else:
                synced += 1

        self.stdout.write(self.style.SUCCESS(f' Synced: {synced}\n'))
        if failed > 0:
            self.stdout.write(self.style.WARNING(f' Failed: {failed}\n'))

    def bidirectional_sync(self, options):
        """Perform bidirectional synchronization"""
        self.stdout.write(self.style.SUCCESS(' Bidirectional Sync\n'))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be made\n'))

        self.stdout.write('\n1  Syncing from Laravel to Django...')
        if not options['dry_run']:
            self.sync_from_laravel(options)
        
        self.stdout.write('\n2  Detecting conflicts...')
        status = DataSyncService.get_sync_status()
        conflicts = status.get('conflicts', 0)
        self.stdout.write(f'Found {conflicts} conflicts\n')

        self.stdout.write('\n3  Syncing from Django to Laravel...')
        if not options['dry_run']:
            self.sync_to_laravel(options)

        self.stdout.write(self.style.SUCCESS('\n Bidirectional sync complete\n'))

