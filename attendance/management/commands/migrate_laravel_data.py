"""
Django management command to migrate data from Laravel database to Django models.
Usage: python manage.py migrate_laravel_data --host localhost --database laravel_db --user root --password pass
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
import json
import os
from datetime import datetime

from attendance.laravel_extractor import LaravelDatabaseExtractor
from attendance.data_migrator import DataMigrator


class Command(BaseCommand):
    help = 'Migrate data from Laravel cards.hodari.ac.tz database to Django models'

    def add_arguments(self, parser):
        parser.add_argument(
            '--host',
            type=str,
            default='localhost',
            help='Laravel database host'
        )
        parser.add_argument(
            '--database',
            type=str,
            required=True,
            help='Laravel database name'
        )
        parser.add_argument(
            '--user',
            type=str,
            required=True,
            help='Laravel database user'
        )
        parser.add_argument(
            '--password',
            type=str,
            required=True,
            help='Laravel database password'
        )
        parser.add_argument(
            '--port',
            type=int,
            default=3306,
            help='Laravel database port'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Batch size for data migration'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Perform dry run without making database changes'
        )
        parser.add_argument(
            '--output-dir',
            type=str,
            default='migration_reports',
            help='Directory to save migration reports'
        )
        parser.add_argument(
            '--operation',
            type=str,
            choices=['full', 'classes', 'students', 'parents', 'attendance', 'otp', 'messages', 'validate'],
            default='full',
            help='Specific migration operation to perform'
        )

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS('Starting Laravel data migration to Django...')
        )

        # Initialize extractor
        extractor = LaravelDatabaseExtractor(
            host=options['host'],
            database=options['database'],
            user=options['user'],
            password=options['password'],
            port=options['port']
        )

        # Connect to database
        if not extractor.connect():
            raise CommandError('Failed to connect to Laravel database')

        try:
            # Initialize migrator
            migrator = DataMigrator(extractor, batch_size=options['batch_size'])
            
            if options['dry_run']:
                migrator.set_dry_run(True)
                self.stdout.write(
                    self.style.WARNING('DRY RUN MODE: No database changes will be made')
                )

            # Create output directory
            output_dir = options['output_dir']
            os.makedirs(output_dir, exist_ok=True)

            # Perform migration based on operation
            operation = options['operation']
            
            if operation == 'full':
                self.stdout.write('Performing full migration...')
                report = migrator.run_full_migration()
                
            elif operation == 'classes':
                self.stdout.write('Migrating classes...')
                result = migrator.migrate_classes()
                migrator.report.add_result('classes', result)
                report = migrator.generate_migration_report()
                
            elif operation == 'students':
                self.stdout.write('Migrating students...')
                result = migrator.migrate_students()
                migrator.report.add_result('students', result)
                report = migrator.generate_migration_report()
                
            elif operation == 'parents':
                self.stdout.write('Migrating parent relationships...')
                result = migrator.migrate_parent_relationships()
                migrator.report.add_result('parent_relationships', result)
                report = migrator.generate_migration_report()
                
            elif operation == 'attendance':
                self.stdout.write('Migrating attendance records...')
                result = migrator.migrate_attendance_records()
                migrator.report.add_result('attendance_records', result)
                report = migrator.generate_migration_report()
                
            elif operation == 'otp':
                self.stdout.write('Migrating OTP codes...')
                result = migrator.migrate_otp_codes()
                migrator.report.add_result('otp_codes', result)
                report = migrator.generate_migration_report()
                
            elif operation == 'messages':
                self.stdout.write('Migrating messages...')
                result = migrator.migrate_messages()
                migrator.report.add_result('messages', result)
                report = migrator.generate_migration_report()
                
            elif operation == 'validate':
                self.stdout.write('Validating migration...')
                validation_report = migrator.validate_migration()
                report = migrator.generate_migration_report()
                report.results['validation'] = validation_report

            # Save migration report
            report_filename = f"migration_report_{operation}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            report_path = os.path.join(output_dir, report_filename)
            
            with open(report_path, 'w') as f:
                json.dump(report.to_dict(), f, indent=2, default=str)

            # Display summary
            self.stdout.write(
                self.style.SUCCESS('\n=== MIGRATION COMPLETE ===')
            )
            self.stdout.write(f'Operation: {operation}')
            self.stdout.write(f'Report saved to: {report_path}')
            self.stdout.write(f'Duration: {report.get_duration():.2f} seconds')
            
            if options['dry_run']:
                self.stdout.write(
                    self.style.WARNING('DRY RUN: No actual changes were made')
                )

            # Display operation results
            self.stdout.write('\nOperation Results:')
            for op_name, op_result in report.results.items():
                if op_name == 'validation':
                    continue
                    
                if isinstance(op_result, dict) and 'total_processed' in op_result:
                    self.stdout.write(
                        f'  {op_name}: {op_result["successful"]}/{op_result["total_processed"]} successful '
                        f'({op_result["success_rate"]:.1f}%)'
                    )
                    
                    if op_result['failed'] > 0:
                        self.stdout.write(
                            self.style.ERROR(f'    Failures: {op_result["failed"]}')
                        )
                        
                    if op_result['skipped'] > 0:
                        self.stdout.write(
                            self.style.WARNING(f'    Skipped: {op_result["skipped"]}')
                        )

            # Display overall statistics
            overall_stats = report.overall_stats
            self.stdout.write(f'\nOverall Statistics:')
            self.stdout.write(f'  Total Records Processed: {overall_stats["total_records_processed"]}')
            self.stdout.write(f'  Successful: {overall_stats["total_successful"]}')
            self.stdout.write(f'  Failed: {overall_stats["total_failed"]}')
            self.stdout.write(f'  Skipped: {overall_stats["total_skipped"]}')
            
            success_rate = (
                overall_stats["total_successful"] / overall_stats["total_records_processed"] * 100
                if overall_stats["total_records_processed"] > 0 else 0
            )
            self.stdout.write(f'  Success Rate: {success_rate:.1f}%')

            # Display validation results if available
            if 'validation' in report.results:
                validation = report.results['validation']
                self.stdout.write('\nValidation Results:')
                
                if validation.get('valid', True):
                    self.stdout.write(
                        self.style.SUCCESS('   Migration validation passed')
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR('   Migration validation failed')
                    )
                    
                if 'issues' in validation and validation['issues']:
                    self.stdout.write('  Issues found:')
                    for issue in validation['issues']:
                        self.stdout.write(f'    - {issue}')
                        
                if 'statistics' in validation:
                    stats = validation['statistics']
                    self.stdout.write('  Migration Statistics:')
                    self.stdout.write(f'    Migrated Students: {stats.get("migrated_students", 0)}')
                    self.stdout.write(f'    Migrated Attendance: {stats.get("migrated_attendance", 0)}')
                    self.stdout.write(f'    Migrated Parents: {stats.get("migrated_parents", 0)}')
                    self.stdout.write(f'    Migrated OTP Codes: {stats.get("migrated_otp_codes", 0)}')
                    self.stdout.write(f'    Migrated Messages: {stats.get("migrated_messages", 0)}')

            # Display errors if any
            if 'errors' in report.results and report.results['errors']:
                self.stdout.write(
                    self.style.ERROR('\nErrors encountered:')
                )
                for error in report.results['errors']:
                    self.stdout.write(f'  - {error}')

            # Display warnings for failed operations
            for op_name, op_result in report.results.items():
                if isinstance(op_result, dict) and op_result.get('errors'):
                    if len(op_result['errors']) <= 5:  # Show first 5 errors
                        self.stdout.write(
                            self.style.ERROR(f'\n{op_name} errors:')
                        )
                        for error in op_result['errors'][:5]:
                            self.stdout.write(f'  - {error}')
                    else:
                        self.stdout.write(
                            self.style.ERROR(f'\n{op_name}: {len(op_result["errors"])} errors (see report for details)')
                        )

            # Final status
            if overall_stats["total_failed"] == 0:
                self.stdout.write(
                    self.style.SUCCESS('\n Migration completed successfully!')
                )
            elif overall_stats["total_successful"] > 0:
                self.stdout.write(
                    self.style.WARNING('\n Migration completed with some failures')
                )
            else:
                self.stdout.write(
                    self.style.ERROR('\n Migration failed')
                )

        except Exception as e:
            raise CommandError(f'Migration failed: {str(e)}')
            
        finally:
            extractor.disconnect()

        self.stdout.write(
            self.style.SUCCESS('Laravel data migration process completed!')
        )