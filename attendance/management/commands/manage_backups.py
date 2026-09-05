"""
Django management command for backup and restore operations.

Usage:
  python manage.py manage_backups --create          # Create backup
  python manage.py manage_backups --list            # List backups
  python manage.py manage_backups --verify [ID]     # Verify backup integrity
  python manage.py manage_backups --restore [ID]    # Restore from backup
  python manage.py manage_backups --restore [ID] --dry-run  # Preview restore
  python manage.py manage_backups --cleanup         # Remove old backups
  python manage.py manage_backups --cleanup --days 30  # Backups older than 30 days
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from datetime import datetime
import json

from attendance.backup_service import BackupManager, RollbackManager


class Command(BaseCommand):
    help = 'Manage attendance data backups and restore operations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--create',
            action='store_true',
            help='Create a new backup'
        )
        parser.add_argument(
            '--list',
            action='store_true',
            help='List all available backups'
        )
        parser.add_argument(
            '--verify',
            type=str,
            help='Verify backup integrity (provide backup ID)'
        )
        parser.add_argument(
            '--restore',
            type=str,
            help='Restore from backup (provide backup ID)'
        )
        parser.add_argument(
            '--cleanup',
            action='store_true',
            help='Remove old backups'
        )
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Age threshold for cleanup in days (default: 30)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without applying them'
        )
        parser.add_argument(
            '--json',
            action='store_true',
            help='Output as JSON'
        )

    def handle(self, *args, **options):
        """Execute the management command"""
        try:
            backup_manager = BackupManager()

            if options['create']:
                self.create_backup(backup_manager, options)
            elif options['list']:
                self.list_backups(backup_manager, options)
            elif options['verify']:
                self.verify_backup(backup_manager, options['verify'], options)
            elif options['restore']:
                self.restore_backup(backup_manager, options['restore'], options)
            elif options['cleanup']:
                self.cleanup_backups(backup_manager, options['days'], options)
            else:
                raise CommandError('Please specify an action (--create, --list, --verify, --restore, or --cleanup)')

        except Exception as e:
            raise CommandError(f'Backup error: {str(e)}')

    def create_backup(self, backup_manager, options):
        """Create a new backup"""
        self.stdout.write(self.style.SUCCESS(' Creating Backup\n'))
        self.stdout.write('Backing up attendance data...')

        backup_id, metadata = backup_manager.create_backup()

        self.stdout.write(self.style.SUCCESS(f' Backup Created\n'))
        self.stdout.write(f'Backup ID: {backup_id}\n')
        self.stdout.write(f'Timestamp: {metadata.timestamp}\n')
        self.stdout.write(f'Records: {metadata.record_count}\n')

        if options['json']:
            self.stdout.write(json.dumps({
                'backup_id': backup_id,
                'timestamp': metadata.timestamp.isoformat(),
                'record_count': metadata.record_count
            }, indent=2))

    def list_backups(self, backup_manager, options):
        """List all available backups"""
        self.stdout.write(self.style.SUCCESS(' Available Backups\n'))

        backups = backup_manager.list_backups()

        if not backups:
            self.stdout.write('No backups found\n')
            return

        if options['json']:
            backup_list = []
            for backup_id, metadata in backups:
                backup_list.append({
                    'id': backup_id,
                    'timestamp': metadata.timestamp.isoformat(),
                    'records': metadata.record_count
                })
            self.stdout.write(json.dumps(backup_list, indent=2))
        else:
            for backup_id, metadata in backups:
                timestamp = metadata.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                self.stdout.write(f'{backup_id:<40} {timestamp:<20} {metadata.record_count:>6} records\n')

    def verify_backup(self, backup_manager, backup_id, options):
        """Verify backup integrity"""
        self.stdout.write(self.style.SUCCESS(f' Verifying Backup: {backup_id}\n'))

        is_valid, message = backup_manager.verify_backup(backup_id)

        if is_valid:
            self.stdout.write(self.style.SUCCESS(' Backup is valid\n'))
        else:
            self.stdout.write(self.style.ERROR(' Backup validation failed\n'))

        self.stdout.write(f'Status: {message}\n')

        if options['json']:
            self.stdout.write(json.dumps({
                'backup_id': backup_id,
                'valid': is_valid,
                'message': message
            }, indent=2))

    def restore_backup(self, backup_manager, backup_id, options):
        """Restore from backup"""
        if options['dry_run']:
            self.stdout.write(self.style.WARNING(' Restore (DRY RUN - No changes will be made)\n'))
        else:
            self.stdout.write(self.style.SUCCESS(' Restoring from Backup\n'))

        self.stdout.write(f'Backup ID: {backup_id}\n')

        # Get backup metadata
        metadata = backup_manager.get_backup_metadata(backup_id)
        if not metadata:
            raise CommandError(f'Backup {backup_id} not found')

        self.stdout.write(f'Backup created: {metadata.timestamp}\n')
        self.stdout.write(f'Records in backup: {metadata.record_count}\n')

        if options['dry_run']:
            self.stdout.write('\n DRY RUN - Preview only\n')
            self.stdout.write('This backup would restore:\n')
            if metadata.record_count:
                self.stdout.write(f'  • {metadata.record_count} attendance records\n')
            return

        # Perform actual restore
        self.stdout.write('\nRestoring data...')
        rollback_manager = RollbackManager(backup_manager)
        result = rollback_manager.rollback_to_backup(backup_id, dry_run=False)

        self.stdout.write(self.style.SUCCESS(' Restore Complete\n'))

        if options['json']:
            self.stdout.write(json.dumps(result, indent=2, default=str))
        else:
            if 'attendance' in result:
                self.stdout.write(f"Attendance entries restored: {result['attendance'].get('restored', 0)}\n")
            if 'messages' in result:
                self.stdout.write(f"Messages restored: {result['messages'].get('restored', 0)}\n")
            if 'notifications' in result:
                self.stdout.write(f"Notifications restored: {result['notifications'].get('restored', 0)}\n")

    def cleanup_backups(self, backup_manager, days, options):
        """Remove old backups"""
        self.stdout.write(self.style.SUCCESS(f'  Cleaning Up Backups (older than {days} days)\n'))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN - No backups will be deleted\n'))
            self.stdout.write('Backups that would be deleted:\n')

            backups = backup_manager.list_backups()
            cutoff_date = timezone.now() - timezone.timedelta(days=days)

            deleted_count = 0
            for backup_id, metadata in backups:
                if metadata.timestamp < cutoff_date:
                    self.stdout.write(f'  • {backup_id} ({metadata.timestamp})\n')
                    deleted_count += 1

            self.stdout.write(f'\nWould delete: {deleted_count} backups\n')
            return

        self.stdout.write(f'Deleting backups older than {days} days...')
        deleted_count = backup_manager.delete_old_backups(days=days)

        self.stdout.write(self.style.SUCCESS(f' Cleanup Complete\n'))
        self.stdout.write(f'Deleted: {deleted_count} backups\n')

