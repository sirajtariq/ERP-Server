from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView

from erp_backend.serializers import UserSerializer, UserReadSerializer, CustomTokenObtainPairSerializer, PasswordChangeSerializer, UserMeSerializer, BusinessSettingsSerializer, BackupSettingSerializer
from erp_backend.permissions import IsAdminUser, _user_in_group, ADMIN_GROUP
from erp_backend.models import BusinessSettings, BackupSetting
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

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
    permission_classes = [IsAdminUser]

    def get_serializer_class(self):
        if self.action in ['list', 'retrieve']:
            return UserReadSerializer
        return UserSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        
        read_serializer = UserReadSerializer(serializer.instance)
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        read_serializer = UserReadSerializer(instance)
        return Response(read_serializer.data)


class UserMeAPIView(generics.RetrieveAPIView):
    """
    API to retrieve the details of the currently logged-in user.
    - Requires Authentication.
    - Password hash is omitted by UserMeSerializer.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserMeSerializer

    def get_object(self):
        return self.request.user


class BusinessSettingsAPIView(generics.RetrieveUpdateAPIView):
    """
    API endpoint for getting and updating the global business settings singleton.
    """
    serializer_class = BusinessSettingsSerializer
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_permissions(self):
        if self.request.method in ['PUT', 'PATCH']:
            self.permission_classes = [IsAdminUser]
        else:
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()

    def get_object(self):
        return BusinessSettings.get_solo()

class BackupSettingAPIView(generics.RetrieveUpdateAPIView):
    """
    API endpoint for getting and updating backup configurations.
    """
    serializer_class = BackupSettingSerializer
    permission_classes = [IsAdminUser]

    def get_object(self):
        return BackupSetting.get_settings()

    def perform_update(self, serializer):
        super().perform_update(serializer)
        # Reschedule job based on new settings
        from .scheduler import reschedule_backup_job
        reschedule_backup_job()

import os
import shutil
from django.conf import settings
from django.db import connections
from .backup_service import run_database_backup

class TriggerBackupView(APIView):
    """
    API endpoint to manually trigger a database backup.
    Requires Admin role.
    """
    permission_classes = [IsAdminUser]

    def post(self, request, *args, **kwargs):
        success, result = run_database_backup()
        if success:
            return Response({"detail": "Backup created successfully.", "file": result}, status=status.HTTP_200_OK)
        return Response({"detail": f"Backup failed: {result}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class RestoreBackupView(APIView):
    """
    API endpoint to restore a database backup from an uploaded file.
    Requires Admin role.
    """
    permission_classes = [IsAdminUser]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, *args, **kwargs):
        backup_file = request.FILES.get('backup_file')
        if not backup_file:
            return Response({"detail": "No backup file provided."}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Validation
        db_engine = settings.DATABASES['default']['ENGINE']
        if 'sqlite3' not in db_engine:
            return Response({"detail": "Restore only supported for SQLite currently."}, status=status.HTTP_400_BAD_REQUEST)
        
        if not backup_file.name.endswith('.sqlite3'):
            return Response({"detail": "Invalid file type. Expected .sqlite3 file."}, status=status.HTTP_400_BAD_REQUEST)

        db_path = settings.DATABASES['default']['NAME']
        temp_bak_path = f"{db_path}_before_restore.bak"

        try:
            # 2. Close active connections
            connections.close_all()

            # 3. Take safety copy
            if os.path.exists(db_path):
                shutil.copy2(db_path, temp_bak_path)

            # 4. Overwrite active DB
            with open(db_path, 'wb+') as destination:
                for chunk in backup_file.chunks():
                    destination.write(chunk)

            # Clean up safety copy on success
            if os.path.exists(temp_bak_path):
                os.remove(temp_bak_path)

            return Response({"detail": "Database restored successfully."}, status=status.HTTP_200_OK)

        except Exception as e:
            # Rollback
            if os.path.exists(temp_bak_path):
                shutil.copy2(temp_bak_path, db_path)
                os.remove(temp_bak_path)
            return Response({"detail": f"Restore failed: {str(e)}. Changes rolled back."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
