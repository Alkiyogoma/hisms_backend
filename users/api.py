"""
JWT sign-in for the mobile app.

Staff use username/email + password (/api/token/). Parents have no password:
they request an SMS code for their phone number and exchange it for tokens.
"""
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from users.models import UserRole


class StaffTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Password sign-in is for staff only; parents use the phone + SMS code endpoints."""

    def validate(self, attrs):
        data = super().validate(attrs)
        if self.user.role == UserRole.PARENT:
            raise AuthenticationFailed(
                "Parents sign in with their phone number and an SMS code.", code="parent_phone_login"
            )
        return data


class StaffTokenObtainPairView(TokenObtainPairView):
    serializer_class = StaffTokenObtainPairSerializer


class _PhoneLoginThrottle(AnonRateThrottle):
    scope = "phone_login"
    rate = "10/minute"


class _PhoneLoginAPIView(APIView):
    # Pre-login endpoints: no session/JWT auth (so no CSRF), open to anyone,
    # with per-IP throttling on top of the per-number limits in users.otp.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [_PhoneLoginThrottle]


class ParentPhoneCodeRequestAPIView(_PhoneLoginAPIView):
    """POST {"phone": "0712345678"} or {"contact": "name@example.com"} -> sends a sign-in code
    if the contact is registered (same reply either way)."""

    def post(self, request):
        from users.otp import CODE_LENGTH, CODE_TTL_SECONDS, RESEND_COOLDOWN_SECONDS, OTPError, request_code
        try:
            request_code(request.data.get("contact") or request.data.get("phone"))
        except OTPError as exc:
            return Response({"detail": exc.message}, status=exc.status)
        return Response({
            "detail": "If this phone number or email is registered with the school, a sign-in code is on its way.",
            "code_length": CODE_LENGTH,
            "resend_after": RESEND_COOLDOWN_SECONDS,
            "expires_in": CODE_TTL_SECONDS,
        })


class ParentPhoneCodeVerifyAPIView(_PhoneLoginAPIView):
    """POST {"phone"|"contact": "...", "code": "1234"} -> {"access", "refresh", "user"}."""

    def post(self, request):
        from audit.models import log_event
        from users.otp import OTPError, verify_code
        try:
            user = verify_code(request.data.get("contact") or request.data.get("phone"), request.data.get("code"))
        except OTPError as exc:
            return Response({"detail": exc.message}, status=exc.status)

        user.clear_failed_logins()
        refresh = RefreshToken.for_user(user)
        log_event(
            actor=user, action_type="LOGIN", model_name="User", object_id=user.pk,
            description=f"Successful app login by {user.get_full_name() or user.username} (SMS code)",
            request=request,
        )
        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": {
                "id": user.pk,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": user.role,
            },
        }, status=status.HTTP_200_OK)
