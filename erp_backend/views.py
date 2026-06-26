from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView

from erp_backend.serializers import UserSerializer, CustomTokenObtainPairSerializer, PasswordChangeSerializer
from erp_backend.permissions import IsAdminUser, _user_in_group, ADMIN_GROUP

class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

class PasswordChangeAPIView(generics.GenericAPIView):
    """
    API to change a user's password.
    - Superusers can change any password without old password.
    - Admins can change other users' passwords without old password, but need old password for themselves.
    - Other roles can only change their own password and need old password.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = PasswordChangeSerializer

    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_id = serializer.validated_data.get('user_id')
        old_password = serializer.validated_data.get('old_password', '')
        new_password = serializer.validated_data.get('new_password')

        # Determine target user
        if user_id and user_id != request.user.id:
            target_user = get_object_or_404(User, id=user_id)
        else:
            target_user = request.user

        is_changing_self = (target_user == request.user)
        requesting_user = request.user

        # Logic checks
        if requesting_user.is_superuser:
            # Superuser can change anyone's password without old_password
            pass
        elif _user_in_group(requesting_user, ADMIN_GROUP):
            # Admin changing someone else's password
            if not is_changing_self:
                if target_user.is_superuser:
                    return Response({"detail": "Admin cannot change superuser password."}, status=status.HTTP_403_FORBIDDEN)
                # Can change without old_password
            else:
                # Admin changing their own password requires old_password
                if not requesting_user.check_password(old_password):
                    return Response({"old_password": ["Old password is required and must be correct for Admin to change own password."]}, status=status.HTTP_400_BAD_REQUEST)
        else:
            # Sales/Purchase roles
            if not is_changing_self:
                return Response({"detail": "You do not have permission to change other users' passwords."}, status=status.HTTP_403_FORBIDDEN)
            if not requesting_user.check_password(old_password):
                return Response({"old_password": ["Old password is required and must be correct."]}, status=status.HTTP_400_BAD_REQUEST)

        # Update password
        target_user.set_password(new_password)
        target_user.save()

        return Response({"detail": "Password updated successfully."}, status=status.HTTP_200_OK)

class UserViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows users to be viewed or edited.
    Requires Admin role (superuser or Admin group).
    """
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = UserSerializer
    permission_classes = [IsAdminUser]
