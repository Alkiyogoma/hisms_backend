"""
Management command to test Laravel database connection and data extraction.
This command validates the connection and provides statistics about available data.
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from attendance.laravel_extractor import LaravelDatabaseExtractor
import json


class Command(BaseCommand):
    help = 'Test Laravel database connection and extract sample data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--schema-info',
            action='store_true',
            help='Display detailed schema information',
        )
        parser.add_argument(
            '--validate',
            action='store_true',
            help='Run data integrity validation',
        )
        parser.add_argument(
            '--sample-size',
            type=int,
            default=5,
            help='Number of sample records to display (default: 5)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Testing Laravel database connection...'))
        
        # Get Laravel database configuration
        laravel_config = settings.LARAVEL_DATABASE
        
        # Initialize extractor
        extractor = LaravelDatabaseExtractor(
            host=laravel_config['HOST'],
            database=laravel_config['DATABASE'],
            user=laravel_config['USER'],
            password=laravel_config['PASSWORD'],
            port=laravel_config['PORT']
        )
        
        try:
            # Test connection
            if not extractor.connect():
                raise CommandError('Failed to connect to Laravel database')
            
            self.stdout.write(self.style.SUCCESS(' Successfully connected to Laravel database'))
            
            # Display basic statistics
            self.display_basic_statistics(extractor)
            
            # Display sample data
            self.display_sample_data(extractor, options['sample_size'])
            
            # Display schema info if requested
            if options['schema_info']:
                self.display_schema_info(extractor)
            
            # Run validation if requested
            if options['validate']:
                self.run_validation(extractor)
                
        except Exception as e:
            raise CommandError(f'Error: {str(e)}')
        finally:
            extractor.disconnect()

    def display_basic_statistics(self, extractor):
        """Display basic table statistics"""
        self.stdout.write(self.style.HTTP_INFO('\n=== Database Statistics ==='))
        
        tables = ['students', 'classes', 'attendances', 'parents', 'student_parents', 'otp_codes', 'messages', 'users']
        
        for table in tables:
            try:
                count = extractor.get_table_count(table)
                self.stdout.write(f'{table.ljust(20)}: {count:,} records')
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'{table.ljust(20)}: Error - {str(e)}'))

    def display_sample_data(self, extractor, sample_size):
        """Display sample data from key tables"""
        self.stdout.write(self.style.HTTP_INFO(f'\n=== Sample Data (showing {sample_size} records) ==='))
        
        # Sample students
        try:
            students = extractor.extract_students(batch_size=sample_size)
            self.stdout.write(self.style.SUCCESS(f'\nStudents ({len(students)} records):'))
            for student in students[:sample_size]:
                self.stdout.write(f"  ID: {student.get('student_id')}, Name: {student.get('name')}, Class: {student.get('class_id')}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error extracting students: {str(e)}'))
        
        # Sample classes
        try:
            classes = extractor.extract_classes()
            self.stdout.write(self.style.SUCCESS(f'\nClasses ({len(classes)} records):'))
            for cls in classes[:sample_size]:
                self.stdout.write(f"  ID: {cls.get('id')}, Name: {cls.get('name')}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error extracting classes: {str(e)}'))
        
        # Sample attendance
        try:
            attendance = extractor.extract_attendance_records(batch_size=sample_size)
            self.stdout.write(self.style.SUCCESS(f'\nAttendance ({len(attendance)} records):'))
            for att in attendance[:sample_size]:
                checkin = att.get('checkin', 'None')
                checkout = att.get('checkout', 'None')
                self.stdout.write(f"  Student: {att.get('student_id')}, Status: {att.get('status')}, In: {checkin}, Out: {checkout}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error extracting attendance: {str(e)}'))
        
        # Sample parents
        try:
            parents = extractor.extract_parents(batch_size=sample_size)
            self.stdout.write(self.style.SUCCESS(f'\nParents ({len(parents)} records):'))
            for parent in parents[:sample_size]:
                name = f"{parent.get('first_name', '')} {parent.get('last_name', '')}".strip()
                self.stdout.write(f"  ID: {parent.get('id')}, Name: {name}, Phone: {parent.get('phone')}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error extracting parents: {str(e)}'))

    def display_schema_info(self, extractor):
        """Display detailed schema information"""
        self.stdout.write(self.style.HTTP_INFO('\n=== Schema Information ==='))
        
        try:
            schema_info = extractor.get_database_schema_info()
            self.stdout.write(f"Database: {schema_info['database']}")
            self.stdout.write(f"Total Records: {schema_info['total_records']:,}")
            
            for table_name, table_info in schema_info['tables'].items():
                self.stdout.write(f"\nTable: {table_name}")
                self.stdout.write(f"  Records: {table_info['record_count']:,}")
                self.stdout.write("  Columns:")
                for column in table_info['columns']:
                    field_info = f"    {column['Field']} ({column['Type']})"
                    if column['Null'] == 'NO':
                        field_info += " NOT NULL"
                    if column['Key']:
                        field_info += f" {column['Key']}"
                    self.stdout.write(field_info)
                    
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error getting schema info: {str(e)}'))

    def run_validation(self, extractor):
        """Run data integrity validation"""
        self.stdout.write(self.style.HTTP_INFO('\n=== Data Validation ==='))
        
        try:
            validation_results = extractor.validate_data_integrity()
            
            if validation_results['valid']:
                self.stdout.write(self.style.SUCCESS(' Data integrity validation passed'))
            else:
                self.stdout.write(self.style.WARNING(' Data integrity issues found:'))
                for issue in validation_results['issues']:
                    self.stdout.write(f'  - {issue}')
            
            # Display statistics
            stats = validation_results['statistics']
            self.stdout.write('\nValidation Statistics:')
            for key, value in stats.items():
                self.stdout.write(f'  {key.replace("_", " ").title()}: {value:,}')
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error during validation: {str(e)}'))