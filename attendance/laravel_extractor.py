"""
Laravel database connection and data extraction for migration to Django.
Extracts data from Laravel cards.hodari.ac.tz MySQL database.
"""
import mysql.connector
from mysql.connector import Error
from django.conf import settings
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class LaravelDatabaseExtractor:
    """
    Extracts data from Laravel MySQL database for migration to Django.
    Handles connection management and data extraction with proper error handling.
    """
    
    def __init__(self, host: str = None, database: str = None, user: str = None, password: str = None, port: int = None):
        # Use Django settings if parameters not provided
        if not all([host, database, user, password]):
            laravel_config = getattr(settings, 'LARAVEL_DATABASE', {})
            host = host or laravel_config.get('HOST', '127.0.0.1')
            database = database or laravel_config.get('DATABASE', 'hodari_checkin')
            user = user or laravel_config.get('USER', 'hodari_checkin')
            password = password or laravel_config.get('PASSWORD', '')
            port = port or laravel_config.get('PORT', 3306)
        
        self.connection_config = {
            'host': host,
            'database': database,
            'user': user,
            'password': password,
            'port': port,
            'charset': 'utf8mb4',
            'collation': 'utf8mb4_unicode_ci',
            'autocommit': True,
            'connection_timeout': 30,
            'sql_mode': 'TRADITIONAL'
        }
        self.connection = None
    
    @classmethod
    def from_settings(cls):
        """Create extractor instance using Django settings configuration"""
        return cls()
    
    def connect(self) -> bool:
        """Establish connection to Laravel MySQL database"""
        try:
            self.connection = mysql.connector.connect(**self.connection_config)
            if self.connection.is_connected():
                db_info = self.connection.get_server_info()
                logger.info(f"Connected to Laravel database: {self.connection_config['database']} (MySQL {db_info})")
                return True
        except Error as e:
            logger.error(f"Failed to connect to Laravel database: {e}")
            return False
        return False
    
    def disconnect(self):
        """Close database connection"""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            logger.info("Laravel database connection closed")
    
    def __enter__(self):
        """Context manager entry"""
        if not self.connect():
            raise Exception("Failed to establish database connection")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()
    
    def execute_query(self, query: str, params: tuple = None) -> List[Dict]:
        """Execute query and return results as list of dictionaries"""
        if not self.connection or not self.connection.is_connected():
            raise Exception("Database connection not established")
        
        try:
            cursor = self.connection.cursor(dictionary=True, buffered=True)
            cursor.execute(query, params)
            results = cursor.fetchall()
            cursor.close()
            return results
        except Error as e:
            logger.error(f"Query execution failed: {e}")
            logger.error(f"Query: {query}")
            if params:
                logger.error(f"Parameters: {params}")
            raise
    
    def get_table_count(self, table_name: str) -> int:
        """Get total record count for a table"""
        query = f"SELECT COUNT(*) as count FROM `{table_name}`"
        result = self.execute_query(query)
        return result[0]['count'] if result else 0
    
    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database"""
        query = "SELECT COUNT(*) as count FROM information_schema.tables WHERE table_schema = %s AND table_name = %s"
        result = self.execute_query(query, (self.connection_config['database'], table_name))
        return result[0]['count'] > 0 if result else False
    
    def extract_students(self, batch_size: int = 1000, offset: int = 0) -> List[Dict]:
        """
        Extract student records from Laravel database.
        
        Args:
            batch_size: Number of records per batch
            offset: Starting offset for pagination
            
        Returns:
            List of student dictionaries
        """
        if not self.table_exists('students'):
            logger.warning("Students table does not exist in Laravel database")
            return []
        
        query = """
        SELECT 
            id,
            student_id,
            name,
            phone,
            class_id,
            image,
            status,
            created_at,
            updated_at
        FROM `students` 
        WHERE status = 'active'
        ORDER BY id
        LIMIT %s OFFSET %s
        """
        
        try:
            return self.execute_query(query, (batch_size, offset))
        except Exception as e:
            logger.error(f"Error extracting students: {e}")
            return []
    
    def extract_classes(self) -> List[Dict]:
        """Extract class/grade information from Laravel database"""
        if not self.table_exists('classes'):
            logger.warning("Classes table does not exist in Laravel database")
            return []
        
        query = """
        SELECT 
            id,
            name,
            created_at,
            updated_at
        FROM `classes`
        ORDER BY id
        """
        
        try:
            return self.execute_query(query)
        except Exception as e:
            logger.error(f"Error extracting classes: {e}")
            return []
    
    def extract_attendance_records(self, batch_size: int = 1000, offset: int = 0, date_filter: str = None) -> List[Dict]:
        """
        Extract attendance records from Laravel database.
        
        Args:
            batch_size: Number of records per batch
            offset: Starting offset for pagination
            date_filter: Optional date filter (YYYY-MM-DD format)
            
        Returns:
            List of attendance dictionaries
        """
        if not self.table_exists('attendances'):
            logger.warning("Attendances table does not exist in Laravel database")
            return []
        
        query = """
        SELECT 
            attendance_id,
            student_id,
            class_id,
            status,
            checkin,
            checkout,
            checkin_by,
            checkout_by,
            parent_name,
            reason,
            created_at,
            updated_at
        FROM `attendances`
        """
        
        params = []
        if date_filter:
            query += " WHERE DATE(created_at) = %s"
            params.append(date_filter)
        
        query += " ORDER BY attendance_id LIMIT %s OFFSET %s"
        params.extend([batch_size, offset])
        
        try:
            return self.execute_query(query, tuple(params))
        except Exception as e:
            logger.error(f"Error extracting attendance records: {e}")
            return []
    
    def extract_parents(self, batch_size: int = 1000, offset: int = 0) -> List[Dict]:
        """
        Extract parent records from Laravel database.
        
        Args:
            batch_size: Number of records per batch
            offset: Starting offset for pagination
            
        Returns:
            List of parent dictionaries
        """
        if not self.table_exists('parents'):
            logger.warning("Parents table does not exist in Laravel database")
            return []
        
        query = """
        SELECT 
            id,
            first_name,
            last_name,
            email,
            phone,
            secondary_phone,
            address,
            city,
            state,
            postal_code,
            occupation,
            created_at,
            updated_at
        FROM `parents`
        ORDER BY id
        LIMIT %s OFFSET %s
        """
        
        try:
            return self.execute_query(query, (batch_size, offset))
        except Exception as e:
            logger.error(f"Error extracting parents: {e}")
            return []
    
    def extract_student_parent_relationships(self) -> List[Dict]:
        """Extract student-parent relationship data"""
        if not self.table_exists('student_parents'):
            logger.warning("Student_parents table does not exist in Laravel database")
            return []
        
        query = """
        SELECT 
            student_id,
            parent_id,
            relationship,
            is_primary_contact,
            can_pickup,
            created_at,
            updated_at
        FROM `student_parents`
        ORDER BY student_id, parent_id
        """
        
        try:
            return self.execute_query(query)
        except Exception as e:
            logger.error(f"Error extracting student-parent relationships: {e}")
            return []
    
    def extract_otp_codes(self, batch_size: int = 1000, offset: int = 0, active_only: bool = True) -> List[Dict]:
        """
        Extract OTP codes from Laravel database.
        
        Args:
            batch_size: Number of records per batch
            offset: Starting offset for pagination
            active_only: If True, only extract non-expired, unverified codes
            
        Returns:
            List of OTP code dictionaries
        """
        if not self.table_exists('otp_codes'):
            logger.warning("OTP_codes table does not exist in Laravel database")
            return []
        
        query = """
        SELECT 
            id,
            parent_id,
            code,
            expires_at,
            verified,
            created_at,
            updated_at
        FROM `otp_codes`
        """
        
        params = []
        if active_only:
            query += " WHERE verified = 0 AND expires_at > NOW()"
        
        query += " ORDER BY id LIMIT %s OFFSET %s"
        params.extend([batch_size, offset])
        
        try:
            return self.execute_query(query, tuple(params))
        except Exception as e:
            logger.error(f"Error extracting OTP codes: {e}")
            return []
    
    def extract_messages(self, batch_size: int = 1000, offset: int = 0, status_filter: int = None) -> List[Dict]:
        """
        Extract SMS message queue from Laravel database.
        
        Args:
            batch_size: Number of records per batch
            offset: Starting offset for pagination
            status_filter: Optional status filter (0=pending, 1=sent, 2=failed)
            
        Returns:
            List of message dictionaries
        """
        if not self.table_exists('messages'):
            logger.warning("Messages table does not exist in Laravel database")
            return []
        
        query = """
        SELECT 
            id,
            phone,
            message,
            status,
            created_at,
            updated_at
        FROM `messages`
        """
        
        params = []
        if status_filter is not None:
            query += " WHERE status = %s"
            params.append(status_filter)
        
        query += " ORDER BY id LIMIT %s OFFSET %s"
        params.extend([batch_size, offset])
        
        try:
            return self.execute_query(query, tuple(params))
        except Exception as e:
            logger.error(f"Error extracting messages: {e}")
            return []
    
    def extract_users(self) -> List[Dict]:
        """Extract user accounts from Laravel database"""
        if not self.table_exists('users'):
            logger.warning("Users table does not exist in Laravel database")
            return []
        
        query = """
        SELECT 
            id,
            name,
            email,
            email_verified_at,
            created_at,
            updated_at
        FROM `users`
        ORDER BY id
        """
        
        try:
            return self.execute_query(query)
        except Exception as e:
            logger.error(f"Error extracting users: {e}")
            return []
    
    def get_database_schema_info(self) -> Dict:
        """Get information about Laravel database schema"""
        tables_query = "SHOW TABLES"
        tables = self.execute_query(tables_query)
        
        schema_info = {
            'database': self.connection_config['database'],
            'tables': {},
            'total_records': 0
        }
        
        for table_row in tables:
            table_name = list(table_row.values())[0]  # Get table name from result
            
            # Get table info
            count_query = f"SELECT COUNT(*) as count FROM {table_name}"
            count_result = self.execute_query(count_query)
            record_count = count_result[0]['count'] if count_result else 0
            
            # Get column info
            columns_query = f"DESCRIBE {table_name}"
            columns = self.execute_query(columns_query)
            
            schema_info['tables'][table_name] = {
                'record_count': record_count,
                'columns': columns
            }
            schema_info['total_records'] += record_count
        
        return schema_info
    
    def validate_data_integrity(self) -> Dict:
        """Validate data integrity and relationships in Laravel database"""
        validation_results = {
            'valid': True,
            'issues': [],
            'statistics': {}
        }
        
        try:
            # Check for orphaned attendance records
            orphaned_attendance_query = """
            SELECT COUNT(*) as count 
            FROM attendances a 
            LEFT JOIN students s ON a.student_id = s.student_id 
            WHERE s.student_id IS NULL
            """
            orphaned_count = self.execute_query(orphaned_attendance_query)[0]['count']
            
            if orphaned_count > 0:
                validation_results['valid'] = False
                validation_results['issues'].append(f"Found {orphaned_count} orphaned attendance records")
            
            # Check for students without classes
            students_no_class_query = """
            SELECT COUNT(*) as count 
            FROM students s 
            LEFT JOIN classes c ON s.class_id = c.id 
            WHERE c.id IS NULL AND s.status = 'active'
            """
            no_class_count = self.execute_query(students_no_class_query)[0]['count']
            
            if no_class_count > 0:
                validation_results['issues'].append(f"Found {no_class_count} students without valid classes")
            
            # Get statistics
            validation_results['statistics'] = {
                'total_students': self.get_table_count('students'),
                'active_students': len(self.extract_students(batch_size=10000)),
                'total_attendance': self.get_table_count('attendances'),
                'total_parents': self.get_table_count('parents'),
                'total_otp_codes': self.get_table_count('otp_codes'),
                'total_messages': self.get_table_count('messages'),
                'orphaned_attendance': orphaned_count,
                'students_no_class': no_class_count
            }
            
        except Exception as e:
            validation_results['valid'] = False
            validation_results['issues'].append(f"Validation error: {str(e)}")
        
        return validation_results


class LaravelFieldMapper:
    """
    Maps Laravel database fields to Django model fields.
    Handles data type conversions and field name mappings.
    """
    
    # Field mapping configurations
    STUDENT_FIELD_MAP = {
        'student_id': 'laravel_student_id',
        'name': 'full_name',
        'phone': 'phone',
        'class_id': 'class_name',  # Will need class name lookup
        'image': 'image',
        'status': 'status'
    }
    
    ATTENDANCE_FIELD_MAP = {
        'attendance_id': 'laravel_attendance_id',
        'student_id': 'student__laravel_student_id',
        'checkin': 'check_in_time',
        'checkout': 'check_out_time',
        'status': 'status',
        'checkin_by': 'marked_by',
        'checkout_by': 'checkout_by',
        'parent_name': 'parent_name',
        'reason': 'reason',
        'class_id': 'class_name'
    }
    
    PARENT_FIELD_MAP = {
        'id': 'laravel_parent_id',
        'first_name': 'first_name',
        'last_name': 'last_name',
        'email': 'email',
        'phone': 'phone',
        'secondary_phone': 'secondary_phone',
        'address': 'address',
        'city': 'city',
        'state': 'state',
        'postal_code': 'postal_code',
        'occupation': 'occupation'
    }
    
    @staticmethod
    def map_student_data(laravel_student: Dict, class_lookup: Dict) -> Dict:
        """Map Laravel student data to Django Student model format"""
        mapped_data = {}
        
        # Map basic fields
        for laravel_field, django_field in LaravelFieldMapper.STUDENT_FIELD_MAP.items():
            if laravel_field in laravel_student:
                if laravel_field == 'class_id':
                    # Convert class ID to class name
                    class_id = laravel_student[laravel_field]
                    mapped_data['class_name'] = class_lookup.get(class_id, f"Class {class_id}")
                elif laravel_field == 'name':
                    # Split name into first and last name
                    full_name = laravel_student[laravel_field] or ""
                    name_parts = full_name.strip().split(' ', 1)
                    mapped_data['first_name'] = name_parts[0] if name_parts else ""
                    mapped_data['last_name'] = name_parts[1] if len(name_parts) > 1 else ""
                else:
                    mapped_data[django_field] = laravel_student[laravel_field]
        
        # Generate admission number if not present
        if 'admission_no' not in mapped_data:
            mapped_data['admission_no'] = f"L{laravel_student.get('student_id', 'UNK')}"
        
        return mapped_data
    
    @staticmethod
    def map_attendance_data(laravel_attendance: Dict) -> Dict:
        """Map Laravel attendance data to Django AttendanceEntry model format"""
        mapped_data = {}
        
        for laravel_field, django_field in LaravelFieldMapper.ATTENDANCE_FIELD_MAP.items():
            if laravel_field in laravel_attendance and laravel_attendance[laravel_field] is not None:
                if laravel_field == 'status':
                    # Map Laravel status to Django status
                    status = laravel_attendance[laravel_field]
                    mapped_data['status'] = 'present' if status == 'present' else 'absent'
                elif laravel_field in ['checkin', 'checkout']:
                    # Handle datetime fields
                    timestamp = laravel_attendance[laravel_field]
                    if timestamp:
                        mapped_data[LaravelFieldMapper.ATTENDANCE_FIELD_MAP[laravel_field]] = timestamp
                else:
                    mapped_data[django_field] = laravel_attendance[laravel_field]
        
        # Derive status from checkin if not set
        if 'status' not in mapped_data:
            has_checkin = laravel_attendance.get('checkin') is not None
            mapped_data['status'] = 'present' if has_checkin else 'absent'
        
        # Set early departure flag
        checkout_time = laravel_attendance.get('checkout')
        if checkout_time:
            if isinstance(checkout_time, datetime):
                is_early = checkout_time.hour < 15 or (checkout_time.hour == 15 and checkout_time.minute < 30)
                mapped_data['is_early_departure'] = is_early
        
        return mapped_data
    
    @staticmethod
    def map_parent_data(laravel_parent: Dict) -> Dict:
        """Map Laravel parent data to Django LaravelParent model format"""
        mapped_data = {}
        
        for laravel_field, django_field in LaravelFieldMapper.PARENT_FIELD_MAP.items():
            if laravel_field in laravel_parent:
                mapped_data[django_field] = laravel_parent[laravel_field]
        
        return mapped_data