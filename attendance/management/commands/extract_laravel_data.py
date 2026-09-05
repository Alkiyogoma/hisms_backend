"""
Django management command to extract data from Laravel database.
Usage: python manage.py extract_laravel_data --host localhost --database laravel_db --user root --password pass
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
import json
import os
from datetime import datetime

from attendance.laravel_extractor import LaravelDatabaseExtractor, LaravelFieldMapper


class Command(BaseCommand):
    help = 'Extract data from Laravel cards.hodari.ac.tz database for migration'

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
            '--output-dir',
            type=str,
            default='laravel_data_export',
            help='Directory to save extracted data'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Batch size for data extraction'
        )
        parser.add_argument(
            '--validate-only',
            action='store_true',
            help='Only validate data integrity, do not extract'
        )
        parser.add_argument(
            '--schema-info',
            action='store_true',
            help='Extract database schema information'
        )

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS('Starting Laravel data extraction...')
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
            # Create output directory
            output_dir = options['output_dir']
            os.makedirs(output_dir, exist_ok=True)
            
            # Generate extraction metadata
            extraction_metadata = {
                'extraction_date': datetime.now().isoformat(),
                'source_database': {
                    'host': options['host'],
                    'database': options['database'],
                    'port': options['port']
                },
                'batch_size': options['batch_size'],
                'files_created': []
            }

            # Schema information
            if options['schema_info']:
                self.stdout.write('Extracting database schema information...')
                schema_info = extractor.get_database_schema_info()
                
                schema_file = os.path.join(output_dir, 'schema_info.json')
                with open(schema_file, 'w') as f:
                    json.dump(schema_info, f, indent=2, default=str)
                
                extraction_metadata['files_created'].append('schema_info.json')
                extraction_metadata['schema_info'] = schema_info
                
                self.stdout.write(
                    self.style.SUCCESS(f'Schema info saved to {schema_file}')
                )

            # Data validation
            self.stdout.write('Validating data integrity...')
            validation_results = extractor.validate_data_integrity()
            
            validation_file = os.path.join(output_dir, 'validation_results.json')
            with open(validation_file, 'w') as f:
                json.dump(validation_results, f, indent=2, default=str)
            
            extraction_metadata['files_created'].append('validation_results.json')
            extraction_metadata['validation_results'] = validation_results

            if not validation_results['valid']:
                self.stdout.write(
                    self.style.WARNING('Data validation issues found:')
                )
                for issue in validation_results['issues']:
                    self.stdout.write(f'  - {issue}')

            if options['validate_only']:
                self.stdout.write(
                    self.style.SUCCESS('Validation complete. Use --schema-info for schema details.')
                )
                return

            # Extract data
            batch_size = options['batch_size']
            
            # Extract classes first (needed for student mapping)
            self.stdout.write('Extracting classes...')
            classes_data = extractor.extract_classes()
            
            classes_file = os.path.join(output_dir, 'classes.json')
            with open(classes_file, 'w') as f:
                json.dump(classes_data, f, indent=2, default=str)
            
            extraction_metadata['files_created'].append('classes.json')
            extraction_metadata['classes_count'] = len(classes_data)
            
            # Create class lookup for student mapping
            class_lookup = {cls['id']: cls['name'] for cls in classes_data}

            # Extract students
            self.stdout.write('Extracting students...')
            students_data = []
            offset = 0
            
            while True:
                batch = extractor.extract_students(batch_size, offset)
                if not batch:
                    break
                
                # Map student data
                mapped_batch = []
                for student in batch:
                    mapped_student = LaravelFieldMapper.map_student_data(student, class_lookup)
                    mapped_student['laravel_original'] = student  # Keep original for reference
                    mapped_batch.append(mapped_student)
                
                students_data.extend(mapped_batch)
                offset += batch_size
                
                self.stdout.write(f'  Extracted {len(students_data)} students...')

            students_file = os.path.join(output_dir, 'students.json')
            with open(students_file, 'w') as f:
                json.dump(students_data, f, indent=2, default=str)
            
            extraction_metadata['files_created'].append('students.json')
            extraction_metadata['students_count'] = len(students_data)

            # Extract parents
            self.stdout.write('Extracting parents...')
            parents_data = []
            offset = 0
            
            while True:
                batch = extractor.extract_parents(batch_size, offset)
                if not batch:
                    break
                
                # Map parent data
                mapped_batch = []
                for parent in batch:
                    mapped_parent = LaravelFieldMapper.map_parent_data(parent)
                    mapped_parent['laravel_original'] = parent
                    mapped_batch.append(mapped_parent)
                
                parents_data.extend(mapped_batch)
                offset += batch_size
                
                self.stdout.write(f'  Extracted {len(parents_data)} parents...')

            parents_file = os.path.join(output_dir, 'parents.json')
            with open(parents_file, 'w') as f:
                json.dump(parents_data, f, indent=2, default=str)
            
            extraction_metadata['files_created'].append('parents.json')
            extraction_metadata['parents_count'] = len(parents_data)

            # Extract student-parent relationships
            self.stdout.write('Extracting student-parent relationships...')
            relationships_data = extractor.extract_student_parent_relationships()
            
            relationships_file = os.path.join(output_dir, 'student_parent_relationships.json')
            with open(relationships_file, 'w') as f:
                json.dump(relationships_data, f, indent=2, default=str)
            
            extraction_metadata['files_created'].append('student_parent_relationships.json')
            extraction_metadata['relationships_count'] = len(relationships_data)

            # Extract attendance records
            self.stdout.write('Extracting attendance records...')
            attendance_data = []
            offset = 0
            
            while True:
                batch = extractor.extract_attendance_records(batch_size, offset)
                if not batch:
                    break
                
                # Map attendance data
                mapped_batch = []
                for attendance in batch:
                    mapped_attendance = LaravelFieldMapper.map_attendance_data(attendance)
                    mapped_attendance['laravel_original'] = attendance
                    mapped_batch.append(mapped_attendance)
                
                attendance_data.extend(mapped_batch)
                offset += batch_size
                
                self.stdout.write(f'  Extracted {len(attendance_data)} attendance records...')

            attendance_file = os.path.join(output_dir, 'attendance.json')
            with open(attendance_file, 'w') as f:
                json.dump(attendance_data, f, indent=2, default=str)
            
            extraction_metadata['files_created'].append('attendance.json')
            extraction_metadata['attendance_count'] = len(attendance_data)

            # Extract OTP codes
            self.stdout.write('Extracting OTP codes...')
            otp_data = []
            offset = 0
            
            while True:
                batch = extractor.extract_otp_codes(batch_size, offset)
                if not batch:
                    break
                
                otp_data.extend(batch)
                offset += batch_size
                
                self.stdout.write(f'  Extracted {len(otp_data)} OTP codes...')

            otp_file = os.path.join(output_dir, 'otp_codes.json')
            with open(otp_file, 'w') as f:
                json.dump(otp_data, f, indent=2, default=str)
            
            extraction_metadata['files_created'].append('otp_codes.json')
            extraction_metadata['otp_count'] = len(otp_data)

            # Extract messages
            self.stdout.write('Extracting messages...')
            messages_data = []
            offset = 0
            
            while True:
                batch = extractor.extract_messages(batch_size, offset)
                if not batch:
                    break
                
                messages_data.extend(batch)
                offset += batch_size
                
                self.stdout.write(f'  Extracted {len(messages_data)} messages...')

            messages_file = os.path.join(output_dir, 'messages.json')
            with open(messages_file, 'w') as f:
                json.dump(messages_data, f, indent=2, default=str)
            
            extraction_metadata['files_created'].append('messages.json')
            extraction_metadata['messages_count'] = len(messages_data)

            # Extract users
            self.stdout.write('Extracting users...')
            users_data = extractor.extract_users()
            
            users_file = os.path.join(output_dir, 'users.json')
            with open(users_file, 'w') as f:
                json.dump(users_data, f, indent=2, default=str)
            
            extraction_metadata['files_created'].append('users.json')
            extraction_metadata['users_count'] = len(users_data)

            # Save extraction metadata
            metadata_file = os.path.join(output_dir, 'extraction_metadata.json')
            with open(metadata_file, 'w') as f:
                json.dump(extraction_metadata, f, indent=2, default=str)

            # Summary
            self.stdout.write(
                self.style.SUCCESS('\n=== EXTRACTION COMPLETE ===')
            )
            self.stdout.write(f'Output directory: {output_dir}')
            self.stdout.write(f'Files created: {len(extraction_metadata["files_created"])}')
            self.stdout.write('\nExtracted data summary:')
            self.stdout.write(f'  - Classes: {extraction_metadata.get("classes_count", 0)}')
            self.stdout.write(f'  - Students: {extraction_metadata.get("students_count", 0)}')
            self.stdout.write(f'  - Parents: {extraction_metadata.get("parents_count", 0)}')
            self.stdout.write(f'  - Relationships: {extraction_metadata.get("relationships_count", 0)}')
            self.stdout.write(f'  - Attendance: {extraction_metadata.get("attendance_count", 0)}')
            self.stdout.write(f'  - OTP Codes: {extraction_metadata.get("otp_count", 0)}')
            self.stdout.write(f'  - Messages: {extraction_metadata.get("messages_count", 0)}')
            self.stdout.write(f'  - Users: {extraction_metadata.get("users_count", 0)}')
            
            if not validation_results['valid']:
                self.stdout.write(
                    self.style.WARNING(f'\nValidation issues found: {len(validation_results["issues"])}')
                )
                self.stdout.write('Review validation_results.json for details.')

        finally:
            extractor.disconnect()

        self.stdout.write(
            self.style.SUCCESS('Laravel data extraction completed successfully!')
        )