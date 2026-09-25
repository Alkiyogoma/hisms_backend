"""
Import students from Google Sheets JSON export.
Usage: python manage.py import_from_sheets /path/to/all_students.json
"""
import json
import re
from datetime import datetime, date

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from academics.models import AcademicYear, GradeClass, Term
from students.models import (
    EnrollmentHistory,
    ParentGuardian,
    Student,
    StudentGuardian,
    StudentSibling,
)


TAB_TO_CLASS = {
    'Pre K': 'Pre-K',
    'Kindergarten': 'Kindergarten',
    'Pre-School': 'Pre-School',
    'ABC': 'ABC',
    'Grade 1': 'Grade 1',
    'Grade 2': 'Grade 2',
    'Grade 3': 'Grade 3',
    'Grade 4': 'Grade 4',
    'Grade 5': 'Grade 5',
    'Grade 6': 'Grade 6',
    'Grade 7': 'Grade 7',
    'Grade 8': 'Grade 8',
}

GENDER_MAP = {
    'Male': 'male', 'Female': 'female',
    'M': 'male', 'F': 'female',
    'male': 'male', 'female': 'female',
}


def parse_dob(dob_str):
    """Parse DOB from various formats to date object."""
    if not dob_str or not dob_str.strip():
        return None
    dob_str = dob_str.strip()
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%d-%m-%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(dob_str, fmt).date()
        except ValueError:
            continue
    # Text format: "5 September 2023"
    m = re.match(r'(\d{1,2})\s+(\w+)\s+(\d{4})', dob_str)
    if m:
        try:
            return date(int(m.group(3)), datetime.strptime(m.group(2), '%B').month, int(m.group(1)))
        except ValueError:
            pass
    # "2 July 2023"
    m = re.match(r'(\d{1,2})\s+(\w+)\s+(\d{4})', dob_str)
    if m:
        try:
            return date(int(m.group(3)), datetime.strptime(m.group(2), '%B').month, int(m.group(1)))
        except ValueError:
            pass
    return None


def parse_phone(phone_str):
    """Normalize phone number. Returns first valid phone."""
    if not phone_str or not phone_str.strip():
        return ''
    phone = phone_str.strip()
    # Take first phone if multiple separated by /
    if '/' in phone:
        phone = phone.split('/')[0].strip()
    # Remove spaces and dashes
    phone = re.sub(r'[\s\-]', '', phone)
    # If starts with 0, keep as is (local format)
    if phone.startswith('0'):
        return phone
    # If starts with 255, convert to 0 prefix
    if phone.startswith('255'):
        return '0' + phone[3:]
    # If 9 digits starting with 6 or 7, add 0
    if len(phone) == 9 and phone[0] in '67':
        return '0' + phone
    # If has digits, return as is
    if any(c.isdigit() for c in phone):
        digits = re.sub(r'\D', '', phone)
        return digits
    return ''


def split_name(full_name):
    """Split full name into first_name and last_name."""
    parts = full_name.strip().split()
    if len(parts) == 0:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    # First word is first_name, rest is last_name
    return parts[0], ' '.join(parts[1:])


class Command(BaseCommand):
    help = 'Import students from Google Sheets JSON export to live server'

    def add_arguments(self, parser):
        parser.add_argument('json_file', type=str, help='Path to all_students.json')

    def handle(self, *args, **options):
        json_file = options['json_file']
        
        with open(json_file) as f:
            all_data = json.load(f)
        
        with transaction.atomic():
            self._import(all_data)

    def _import(self, all_data):
        # Get current academic year
        ay = AcademicYear.objects.filter(is_current=True).first()
        if not ay:
            self.stderr.write(self.style.ERROR('No current academic year found'))
            return
        
        self.stdout.write(f'Academic year: {ay.name}')
        
        # Get or verify all GradeClass records exist
        existing_classes = set(GradeClass.objects.values_list('name', flat=True))
        for target_class in TAB_TO_CLASS.values():
            if target_class not in existing_classes:
                self.stderr.write(self.style.WARNING(f'GradeClass "{target_class}" not found - creating it'))
                dept = 'ECD' if target_class in ('Pre-K', 'Kindergarten', 'Pre-School', 'ABC') else \
                       'PRIMARY' if target_class.startswith('Grade') and int(target_class.split()[-1]) <= 6 else \
                       'LOWER_SECONDARY'
                GradeClass.objects.create(name=target_class, department=dept, max_capacity=35)
        
        # Get the next admission number sequence
        max_existing = Student.objects.filter(
            admission_no__startswith='ADM-2026-'
        ).values_list('admission_no', flat=True)
        seq = 0
        for adm in max_existing:
            m = re.match(r'ADM-2026-(\d+)', adm)
            if m:
                seq = max(seq, int(m.group(1)))
        
        student_count = 0
        guardian_count = 0
        link_count = 0
        errors = []
        
        # Process each tab
        for tab_name in ['Pre K', 'Kindergarten', 'Pre-School', 'ABC',
                         'Grade 1', 'Grade 2', 'Grade 3', 'Grade 4',
                         'Grade 5', 'Grade 6', 'Grade 7', 'Grade 8']:
            rows = all_data.get(tab_name, [])
            if not rows:
                continue
            
            target_class = TAB_TO_CLASS[tab_name]
            self.stdout.write(f'\n{tab_name} -> {target_class}: {len(rows)} students')
            
            for row in rows:
                seq += 1
                admission_no = f'ADM-2026-{seq:03d}'
                
                # Parse name
                full_name = row.get('Student Names', '').strip()
                first_name, last_name = split_name(full_name)
                if not first_name:
                    errors.append(f'{tab_name}: Empty name')
                    continue
                
                # Parse DOB
                dob = parse_dob(row.get('DOB', ''))
                
                # Parse gender
                gender_raw = row.get('Gender', '').strip()
                gender = GENDER_MAP.get(gender_raw, gender_raw.lower() if gender_raw else '')
                
                # Parse status
                status_raw = row.get('Status', '').strip().upper()
                status = 'active' if status_raw == 'ACTIVE' else 'withdrawn' if status_raw == 'INACTIVE' else 'active'
                
                # Parse phone (father's primary)
                phone = parse_phone(row.get('Father Phone', ''))
                if not phone:
                    phone = parse_phone(row.get('Mother Phone', ''))
                if not phone:
                    phone = parse_phone(row.get('Guardian Phone', ''))
                
                # Parse admission year
                admission_year_str = row.get('Admission Year', '').strip()
                enrolment_date = None
                if admission_year_str:
                    # Try "January 2026" format
                    m = re.match(r'(\w+)\s+(\d{4})', admission_year_str)
                    if m:
                        try:
                            month = datetime.strptime(m.group(1), '%B').month
                            enrolment_date = date(int(m.group(2)), month, 1)
                        except ValueError:
                            pass
                    # Try date format
                    if not enrolment_date:
                        for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
                            try:
                                enrolment_date = datetime.strptime(admission_year_str, fmt).date()
                            except ValueError:
                                continue
                
                # Data consent
                consent_raw = row.get('Data Consent', '').strip().upper()
                has_consent = consent_raw == 'YES'
                
                # Create student
                try:
                    student = Student.objects.create(
                        admission_no=admission_no,
                        first_name=first_name,
                        last_name=last_name,
                        date_of_birth=dob,
                        gender=gender,
                        class_name=target_class,
                        phone=phone,
                        status=status,
                        academic_year=ay,
                        enrolment_date=enrolment_date,
                    )
                    student_count += 1
                except Exception as e:
                    errors.append(f'{tab_name} {full_name}: Student create error: {e}')
                    continue
                
                # Create father guardian
                father_name = row.get('Father Name', '').strip()
                father_phone = parse_phone(row.get('Father Phone', ''))
                father_email = row.get('Father Email', '').strip()
                father_guardian = None
                if father_name and father_name.lower() not in ('not provided', 'not given', 'n/a', ''):
                    if not father_phone:
                        father_phone = '000000000'  # placeholder
                    father_guardian, created = ParentGuardian.objects.get_or_create(
                        full_name=father_name,
                        phone=father_phone,
                        defaults={
                            'email': father_email or '',
                        }
                    )
                    if created:
                        guardian_count += 1
                    elif father_email and not father_guardian.email:
                        father_guardian.email = father_email
                        father_guardian.save(update_fields=['email'])
                    
                    sg, sg_created = StudentGuardian.objects.get_or_create(
                        student=student,
                        guardian=father_guardian,
                        defaults={
                            'relationship': 'father',
                            'is_primary': True,
                        }
                    )
                    if sg_created:
                        link_count += 1
                
                # Create mother guardian
                mother_name = row.get('Mother Name', '').strip()
                mother_phone = parse_phone(row.get('Mother Phone', ''))
                mother_email = row.get('Mother Email', '').strip()
                mother_guardian = None
                if mother_name and mother_name.lower() not in ('not provided', 'not given', 'n/a', ''):
                    if not mother_phone:
                        mother_phone = '000000000'  # placeholder
                    mother_guardian, created = ParentGuardian.objects.get_or_create(
                        full_name=mother_name,
                        phone=mother_phone,
                        defaults={
                            'email': mother_email or '',
                        }
                    )
                    if created:
                        guardian_count += 1
                    elif mother_email and not mother_guardian.email:
                        mother_guardian.email = mother_email
                        mother_guardian.save(update_fields=['email'])
                    
                    sg, sg_created = StudentGuardian.objects.get_or_create(
                        student=student,
                        guardian=mother_guardian,
                        defaults={
                            'relationship': 'mother',
                            'is_primary': False,
                        }
                    )
                    if sg_created:
                        link_count += 1
                
                # Create enrollment history
                EnrollmentHistory.objects.create(
                    student=student,
                    academic_year=ay,
                    class_name=target_class,
                    action='enrolled',
                    notes=f'Imported from Google Sheets ({tab_name} tab)',
                )
        
        # Summary
        self.stdout.write(f'\n{"="*60}')
        self.stdout.write(self.style.SUCCESS(f'STUDENTS CREATED: {student_count}'))
        self.stdout.write(self.style.SUCCESS(f'GUARDIANS CREATED: {guardian_count}'))
        self.stdout.write(self.style.SUCCESS(f'STUDENT-GUARDIAN LINKS: {link_count}'))
        
        if errors:
            self.stderr.write(f'\nERRORS ({len(errors)}):')
            for e in errors:
                self.stderr.write(self.style.ERROR(f'  {e}'))
        
        # Final count per class
        self.stdout.write(f'\nFINAL CLASS COUNTS:')
        for tab_name in ['Pre K', 'Kindergarten', 'Pre-School', 'ABC',
                         'Grade 1', 'Grade 2', 'Grade 3', 'Grade 4',
                         'Grade 5', 'Grade 6', 'Grade 7', 'Grade 8']:
            target_class = TAB_TO_CLASS[tab_name]
            count = Student.objects.filter(class_name=target_class, is_archived=False).count()
            self.stdout.write(f'  {target_class}: {count}')
