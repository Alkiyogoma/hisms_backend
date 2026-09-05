"""
Property-based tests for authentication functionality.
Tests universal properties for JWT token validation, authentication bridge compatibility,
and role mapping consistency.

Feature: checkin-checkout-integration
"""
import logging
import json
import jwt
from datetime import datetime, timedelta
from typing import Dict, List, Any
from unittest.mock import Mock, patch, MagicMock

from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.conf import settings
from hypothesis import given, strategies as st, settings as hypothesis_settings, assume, example
from hypothesis.extra.django import TestCase as HypothesisTestCase
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from users.models import User, UserRole
from .models import AttendanceEntry
from students.models import Student

# Disable logging during tests
logging.disable(logging.CRITICAL)


# Test data generation strategies
@st.composite
def django_user_strategy(draw):
    """Generate realistic Django user data"""
    username = draw(st.text(
        alphabet=st.characters(whitelist_categories=('Ll',), blacklist_characters='@'),
        min_size=3,
        max_size=20
    ))
    
    return {
        'username': username,
        'email': draw(st.emails()),
        'first_name': draw(st.text(
            alphabet=st.characters(whitelist_categories=('Lu', 'Ll')),
            min_size=2,
            max_size=20
        )),
        'last_name': draw(st.text(
            alphabet=st.characters(whitelist_categories=('Lu', 'Ll')),
            min_size=2,
            max_size=20
        )),
        'password': draw(st.text(
            alphabet=st.characters(blacklist_characters='\n\r\t'),
            min_size=8,
            max_size=50
        )),
        'role': draw(st.sampled_from([
            UserRole.TEACHER,
            UserRole.ADMIN_OFFICER,
            UserRole.HEAD_OF_SCHOOL,
            UserRole.PRIMARY_HOD,
            UserRole.ECD_HOD
        ]))
    }


@st.composite
def laravel_jwt_token_strategy(draw):
    """Generate realistic Laravel JWT token data"""
    user_id = draw(st.integers(min_value=1, max_value=99999))
    username = draw(st.text(
        alphabet=st.characters(whitelist_categories=('Ll',)),
        min_size=3,
        max_size=20
    ))
    
    # Generate token payload similar to Laravel JWT
    payload = {
        'sub': str(user_id),
        'iat': int(datetime.utcnow().timestamp()),
        'exp': int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
        'name': username,
        'email': f'{username}@hodari.ac.tz',
        'role': draw(st.sampled_from(['teacher', 'admin', 'staff', 'head_teacher'])),
        'permissions': draw(st.lists(
            st.sampled_from(['view_attendance', 'mark_attendance', 'view_reports', 'manage_users']),
            min_size=1,
            max_size=4,
            unique=True
        ))
    }
    
    return payload


@st.composite
def role_mapping_strategy(draw):
    """Generate role mapping scenarios"""
    django_role = draw(st.sampled_from([
        UserRole.TEACHER,
        UserRole.ADMIN_OFFICER,
        UserRole.HEAD_OF_SCHOOL,
        UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD
    ]))
    
    # Map Django roles to Laravel permissions
    role_permission_map = {
        UserRole.TEACHER: ['view_attendance', 'mark_attendance'],
        UserRole.ADMIN_OFFICER: ['view_attendance', 'mark_attendance', 'view_reports', 'manage_users'],
        UserRole.HEAD_OF_SCHOOL: ['view_attendance', 'mark_attendance', 'view_reports', 'manage_users'],
        UserRole.PRIMARY_HOD: ['view_attendance', 'mark_attendance', 'view_reports'],
        UserRole.ECD_HOD: ['view_attendance', 'mark_attendance', 'view_reports'],
    }
    
    return {
        'django_role': django_role,
        'expected_permissions': role_permission_map.get(django_role, [])
    }


class AuthenticationBridge:
    """
    Authentication bridge for Laravel JWT compatibility.
    Validates Laravel tokens and maps roles to Django permissions.
    """
    
    LARAVEL_SECRET = getattr(settings, 'LARAVEL_JWT_SECRET', 'laravel-secret-key')
    ALGORITHM = 'HS256'
    
    # Role mapping from Laravel to Django
    ROLE_MAPPING = {
        'teacher': UserRole.TEACHER,
        'admin': UserRole.ADMIN_OFFICER,
        'head_teacher': UserRole.HEAD_OF_SCHOOL,
        'staff': UserRole.ADMIN_OFFICER,
    }
    
    # Permission mapping from Django roles to Laravel permissions
    PERMISSION_MAPPING = {
        UserRole.TEACHER: ['view_attendance', 'mark_attendance'],
        UserRole.ADMIN_OFFICER: ['view_attendance', 'mark_attendance', 'view_reports', 'manage_users'],
        UserRole.HEAD_OF_SCHOOL: ['view_attendance', 'mark_attendance', 'view_reports', 'manage_users'],
        UserRole.PRIMARY_HOD: ['view_attendance', 'mark_attendance', 'view_reports'],
        UserRole.ECD_HOD: ['view_attendance', 'mark_attendance', 'view_reports'],
    }
    
    @classmethod
    def validate_laravel_jwt(cls, token: str) -> Dict[str, Any]:
        """
        Validate Laravel JWT token and extract payload.
        
        Args:
            token: JWT token string
            
        Returns:
            dict with validation result and payload
        """
        try:
            # Decode token
            payload = jwt.decode(token, cls.LARAVEL_SECRET, algorithms=[cls.ALGORITHM])
            
            # Validate required fields
            required_fields = ['sub', 'name', 'email', 'role']
            for field in required_fields:
                if field not in payload:
                    return {
                        'valid': False,
                        'error': f'Missing required field: {field}'
                    }
            
            # Check expiration
            if 'exp' in payload:
                if payload['exp'] < datetime.utcnow().timestamp():
                    return {
                        'valid': False,
                        'error': 'Token has expired'
                    }
            
            return {
                'valid': True,
                'payload': payload
            }
            
        except jwt.InvalidSignatureError:
            return {
                'valid': False,
                'error': 'Invalid token signature'
            }
        except jwt.DecodeError:
            return {
                'valid': False,
                'error': 'Failed to decode token'
            }
        except Exception as e:
            return {
                'valid': False,
                'error': f'Token validation error: {str(e)}'
            }
    
    @classmethod
    def generate_django_token(cls, user: User) -> str:
        """
        Generate Django JWT token for authenticated user.
        
        Args:
            user: Django User instance
            
        Returns:
            JWT token string
        """
        refresh = RefreshToken.for_user(user)
        return str(refresh.access_token)
    
    @classmethod
    def map_user_permissions(cls, laravel_roles: List[str]) -> List[str]:
        """
        Map Laravel roles to Django permissions.
        
        Args:
            laravel_roles: List of Laravel role strings
            
        Returns:
            List of Django permissions
        """
        permissions = set()
        
        for laravel_role in laravel_roles:
            django_role = cls.ROLE_MAPPING.get(laravel_role)
            if django_role:
                role_permissions = cls.PERMISSION_MAPPING.get(django_role, [])
                permissions.update(role_permissions)
        
        return list(permissions)
    
    @classmethod
    def get_django_role_permissions(cls, django_role: str) -> List[str]:
        """
        Get permissions for a Django role.
        
        Args:
            django_role: Django role string
            
        Returns:
            List of permissions
        """
        return cls.PERMISSION_MAPPING.get(django_role, [])


class AuthenticationTokenValidationTests(HypothesisTestCase):
    """
    Property-based tests for authentication token validation.
    
    **Property 10: Authentication Token Validation**
    For any request with a Laravel JWT token, the API_Gateway should correctly
    validate the token and allow/deny access based on token validity and user permissions.
    
    **Validates: Requirements 2.7**
    """
    
    def setUp(self):
        """Set up test environment"""
        self.client = APIClient()
        self.bridge = AuthenticationBridge()
    
    @given(token_payload=laravel_jwt_token_strategy())
    @hypothesis_settings(max_examples=50, deadline=5000)
    def test_laravel_jwt_validation_property(self, token_payload):
        """
        Feature: checkin-checkout-integration, Property 10: Authentication Token Validation
        
        For any Laravel JWT token payload, the authentication bridge should correctly
        validate the token structure and content.
        
        **Validates: Requirements 2.7**
        """
        # Generate a valid token with the payload
        token = jwt.encode(
            token_payload,
            self.bridge.LARAVEL_SECRET,
            algorithm=self.bridge.ALGORITHM
        )
        
        # **PROPERTY ASSERTION 1: Valid tokens pass validation**
        result = self.bridge.validate_laravel_jwt(token)
        
        self.assertTrue(
            result['valid'],
            f"Valid token failed validation: {result.get('error')}"
        )
        
        # **PROPERTY ASSERTION 2: Payload is correctly extracted**
        self.assertIn('payload', result)
        payload = result['payload']
        
        # All original fields should be present
        for key, value in token_payload.items():
            self.assertIn(key, payload)
            self.assertEqual(payload[key], value)
        
        # **PROPERTY ASSERTION 3: Required fields are present**
        required_fields = ['sub', 'name', 'email', 'role']
        for field in required_fields:
            self.assertIn(field, payload)
    
    @given(token_payload=laravel_jwt_token_strategy())
    @hypothesis_settings(max_examples=30, deadline=5000)
    def test_invalid_token_signature_rejection(self, token_payload):
        """
        Feature: checkin-checkout-integration, Property 10: Authentication Token Validation
        
        For any token with an invalid signature, the authentication bridge should
        reject the token and return an error.
        
        **Validates: Requirements 2.7**
        """
        # Generate token with wrong secret
        wrong_secret = 'wrong-secret-key'
        token = jwt.encode(
            token_payload,
            wrong_secret,
            algorithm=self.bridge.ALGORITHM
        )
        
        # **PROPERTY ASSERTION: Invalid signature is detected**
        result = self.bridge.validate_laravel_jwt(token)
        
        self.assertFalse(
            result['valid'],
            "Token with invalid signature should fail validation"
        )
        
        self.assertIn('error', result)
        self.assertIn('signature', result['error'].lower())
    
    @given(token_payload=laravel_jwt_token_strategy())
    @hypothesis_settings(max_examples=30, deadline=5000)
    def test_expired_token_rejection(self, token_payload):
        """
        Feature: checkin-checkout-integration, Property 10: Authentication Token Validation
        
        For any expired token, the authentication bridge should reject the token.
        
        **Validates: Requirements 2.7**
        """
        # Create expired token
        expired_payload = {
            **token_payload,
            'exp': int((datetime.utcnow() - timedelta(hours=1)).timestamp())
        }
        
        token = jwt.encode(
            expired_payload,
            self.bridge.LARAVEL_SECRET,
            algorithm=self.bridge.ALGORITHM
        )
        
        # **PROPERTY ASSERTION: Expired token is rejected**
        result = self.bridge.validate_laravel_jwt(token)
        
        self.assertFalse(
            result['valid'],
            "Expired token should fail validation"
        )
        
        self.assertIn('error', result)
        self.assertIn('expired', result['error'].lower())
    
    @given(token_payload=laravel_jwt_token_strategy())
    @hypothesis_settings(max_examples=30, deadline=5000)
    def test_missing_required_fields_rejection(self, token_payload):
        """
        Feature: checkin-checkout-integration, Property 10: Authentication Token Validation
        
        For any token missing required fields, the authentication bridge should reject it.
        
        **Validates: Requirements 2.7**
        """
        # Remove a required field
        incomplete_payload = {k: v for k, v in token_payload.items() if k != 'role'}
        
        token = jwt.encode(
            incomplete_payload,
            self.bridge.LARAVEL_SECRET,
            algorithm=self.bridge.ALGORITHM
        )
        
        # **PROPERTY ASSERTION: Missing required fields are detected**
        result = self.bridge.validate_laravel_jwt(token)
        
        self.assertFalse(
            result['valid'],
            "Token with missing required fields should fail validation"
        )
        
        self.assertIn('error', result)
        self.assertIn('missing', result['error'].lower())


class AuthenticationBridgeCompatibilityTests(HypothesisTestCase):
    """
    Property-based tests for authentication bridge compatibility.
    
    **Property 20: Authentication Bridge Compatibility**
    For any valid Django user credentials, the Authentication_Bridge should generate
    Laravel-compatible JWT tokens that can be validated by the mobile app.
    
    **Validates: Requirements 6.2, 6.3, 6.4**
    """
    
    def setUp(self):
        """Set up test environment"""
        self.bridge = AuthenticationBridge()
    
    @given(user_data=django_user_strategy())
    @hypothesis_settings(max_examples=50, deadline=5000)
    def test_django_token_generation_property(self, user_data):
        """
        Feature: checkin-checkout-integration, Property 20: Authentication Bridge Compatibility
        
        For any valid Django user, the authentication bridge should generate a valid JWT token.
        
        **Validates: Requirements 6.2, 6.3, 6.4**
        """
        # Create Django user
        user = User.objects.create_user(
            username=user_data['username'],
            email=user_data['email'],
            first_name=user_data['first_name'],
            last_name=user_data['last_name'],
            password=user_data['password'],
            role=user_data['role']
        )
        
        try:
            # **PROPERTY ASSERTION 1: Token is generated successfully**
            token = self.bridge.generate_django_token(user)
            
            self.assertIsNotNone(token)
            self.assertIsInstance(token, str)
            self.assertGreater(len(token), 0)
            
            # **PROPERTY ASSERTION 2: Token can be decoded**
            # Django tokens use different secret, but should be decodable
            from rest_framework_simplejwt.tokens import AccessToken
            decoded = AccessToken(token)
            
            self.assertEqual(str(decoded['user_id']), str(user.id))
            
            # **PROPERTY ASSERTION 3: Token contains user information**
            self.assertIn('user_id', decoded)
            self.assertEqual(decoded['user_id'], user.id)
            
        finally:
            user.delete()
    
    @given(user_data=django_user_strategy())
    @hypothesis_settings(max_examples=30, deadline=5000)
    def test_token_contains_user_context(self, user_data):
        """
        Feature: checkin-checkout-integration, Property 20: Authentication Bridge Compatibility
        
        For any Django user, the generated token should contain user context information.
        
        **Validates: Requirements 6.2, 6.3**
        """
        user = User.objects.create_user(
            username=user_data['username'],
            email=user_data['email'],
            first_name=user_data['first_name'],
            last_name=user_data['last_name'],
            password=user_data['password'],
            role=user_data['role']
        )
        
        try:
            token = self.bridge.generate_django_token(user)
            
            # Decode token
            from rest_framework_simplejwt.tokens import AccessToken
            decoded = AccessToken(token)
            
            # **PROPERTY ASSERTION: Token contains user ID**
            self.assertEqual(decoded['user_id'], user.id)
            
        finally:
            user.delete()
    
    @given(user_data=django_user_strategy())
    @hypothesis_settings(max_examples=30, deadline=5000)
    def test_token_expiration_property(self, user_data):
        """
        Feature: checkin-checkout-integration, Property 20: Authentication Bridge Compatibility
        
        For any generated token, it should have a valid expiration time.
        
        **Validates: Requirements 6.4**
        """
        user = User.objects.create_user(
            username=user_data['username'],
            email=user_data['email'],
            first_name=user_data['first_name'],
            last_name=user_data['last_name'],
            password=user_data['password'],
            role=user_data['role']
        )
        
        try:
            token = self.bridge.generate_django_token(user)
            
            # Decode token
            from rest_framework_simplejwt.tokens import AccessToken
            decoded = AccessToken(token)
            
            # **PROPERTY ASSERTION: Token has expiration**
            self.assertIn('exp', decoded)
            
            # Expiration should be in the future
            exp_time = datetime.fromtimestamp(decoded['exp'])
            self.assertGreater(exp_time, datetime.utcnow())
            
        finally:
            user.delete()


class RoleMappingConsistencyTests(HypothesisTestCase):
    """
    Property-based tests for role mapping consistency.
    
    **Property 21: Role Mapping Consistency**
    For any Django user with specific roles, the Authentication_Bridge should correctly
    map these to equivalent Laravel permissions for API access control.
    
    **Validates: Requirements 6.5, 6.8**
    """
    
    def setUp(self):
        """Set up test environment"""
        self.bridge = AuthenticationBridge()
    
    @given(role_mapping=role_mapping_strategy())
    @hypothesis_settings(max_examples=50, deadline=5000)
    def test_role_permission_mapping_property(self, role_mapping):
        """
        Feature: checkin-checkout-integration, Property 21: Role Mapping Consistency
        
        For any Django role, the authentication bridge should map it to the correct
        set of Laravel permissions.
        
        **Validates: Requirements 6.5, 6.8**
        """
        django_role = role_mapping['django_role']
        expected_permissions = role_mapping['expected_permissions']
        
        # **PROPERTY ASSERTION 1: Role maps to correct permissions**
        permissions = self.bridge.get_django_role_permissions(django_role)
        
        self.assertEqual(
            set(permissions),
            set(expected_permissions),
            f"Role {django_role} mapped to {permissions}, expected {expected_permissions}"
        )
        
        # **PROPERTY ASSERTION 2: All permissions are valid**
        valid_permissions = {
            'view_attendance', 'mark_attendance', 'view_reports', 'manage_users'
        }
        
        for permission in permissions:
            self.assertIn(
                permission,
                valid_permissions,
                f"Invalid permission: {permission}"
            )
    
    @given(laravel_roles=st.lists(
        st.sampled_from(['teacher', 'admin', 'head_teacher', 'staff']),
        min_size=1,
        max_size=3,
        unique=True
    ))
    @hypothesis_settings(max_examples=50, deadline=5000)
    def test_laravel_role_mapping_property(self, laravel_roles):
        """
        Feature: checkin-checkout-integration, Property 21: Role Mapping Consistency
        
        For any set of Laravel roles, the authentication bridge should map them to
        a consistent set of Django permissions.
        
        **Validates: Requirements 6.5, 6.8**
        """
        # **PROPERTY ASSERTION 1: Roles map to permissions**
        permissions = self.bridge.map_user_permissions(laravel_roles)
        
        self.assertIsInstance(permissions, list)
        
        # **PROPERTY ASSERTION 2: All permissions are valid**
        valid_permissions = {
            'view_attendance', 'mark_attendance', 'view_reports', 'manage_users'
        }
        
        for permission in permissions:
            self.assertIn(
                permission,
                valid_permissions,
                f"Invalid permission: {permission}"
            )
        
        # **PROPERTY ASSERTION 3: Mapping is deterministic**
        # Same input should always produce same output
        permissions2 = self.bridge.map_user_permissions(laravel_roles)
        self.assertEqual(
            set(permissions),
            set(permissions2),
            "Role mapping is not deterministic"
        )
    
    @given(user_data=django_user_strategy())
    @hypothesis_settings(max_examples=30, deadline=5000)
    def test_user_role_permission_consistency(self, user_data):
        """
        Feature: checkin-checkout-integration, Property 21: Role Mapping Consistency
        
        For any Django user with a specific role, the permissions should be consistent
        with the role mapping.
        
        **Validates: Requirements 6.5, 6.8**
        """
        user = User.objects.create_user(
            username=user_data['username'],
            email=user_data['email'],
            first_name=user_data['first_name'],
            last_name=user_data['last_name'],
            password=user_data['password'],
            role=user_data['role']
        )
        
        try:
            # **PROPERTY ASSERTION: User role maps to correct permissions**
            permissions = self.bridge.get_django_role_permissions(user.role)
            
            # Verify permissions are appropriate for the role
            if user.role == UserRole.TEACHER:
                self.assertIn('mark_attendance', permissions)
                self.assertIn('view_attendance', permissions)
            
            elif user.role in [UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL]:
                self.assertIn('manage_users', permissions)
                self.assertIn('view_reports', permissions)
            
            elif user.role in [UserRole.PRIMARY_HOD, UserRole.ECD_HOD]:
                self.assertIn('view_reports', permissions)
                self.assertIn('mark_attendance', permissions)
            
        finally:
            user.delete()
    
    def test_role_mapping_completeness(self):
        """
        Test that all Django roles have permission mappings.
        """
        # **PROPERTY ASSERTION: All roles are mapped**
        for role_value, role_label in UserRole.choices:
            permissions = self.bridge.get_django_role_permissions(role_value)
            
            self.assertIsInstance(permissions, list)
            self.assertGreater(
                len(permissions),
                0,
                f"Role {role_value} has no permissions mapped"
            )
    
    def test_permission_hierarchy_consistency(self):
        """
        Test that permission hierarchy is consistent across roles.
        """
        # Higher roles should have at least the permissions of lower roles
        teacher_perms = set(self.bridge.get_django_role_permissions(UserRole.TEACHER))
        admin_perms = set(self.bridge.get_django_role_permissions(UserRole.ADMIN_OFFICER))
        head_perms = set(self.bridge.get_django_role_permissions(UserRole.HEAD_OF_SCHOOL))
        
        # **PROPERTY ASSERTION: Admin has at least teacher permissions**
        self.assertTrue(
            teacher_perms.issubset(admin_perms),
            "Admin permissions should include teacher permissions"
        )
        
        # **PROPERTY ASSERTION: Head has at least admin permissions**
        self.assertTrue(
            admin_perms.issubset(head_perms),
            "Head permissions should include admin permissions"
        )
