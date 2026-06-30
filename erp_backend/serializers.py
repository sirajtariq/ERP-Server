from django.contrib.auth.models import User, Group
from rest_framework import serializers

class UserSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(
        choices=["Admin", "Sales", "Purchase"], 
        write_only=True,
        required=True,
        help_text="Role to assign to the user (maps to Django Group)."
    )
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password', 'role']
        extra_kwargs = {
            'password': {'write_only': True}
        }

    def create(self, validated_data):
        role_name = validated_data.pop('role')
        user = User.objects.create_user(**validated_data)
        try:
            group = Group.objects.get(name=role_name)
            user.groups.add(group)
        except Group.DoesNotExist:
            pass
        return user

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

# Mapping from Django group names / superuser flag to API role constants
ROLE_MAP = {
    "superuser": "SUPER_ADMIN",
    "Admin": "ADMIN",
    "Sales": "SALE_PERSON",
    "Purchase": "PURCHASE_PERSON",
}


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Add custom claims
        token['username'] = user.username

        # Determine role using the uppercase mapping
        role = cls._resolve_role(user)
        token['role'] = role
        return token

    @classmethod
    def _resolve_role(cls, user):
        """
        Return the single primary role for a user using uppercase constants.

        Priority: SUPER_ADMIN > ADMIN > SALE_PERSON > PURCHASE_PERSON
        """
        if user.is_superuser:
            return ROLE_MAP["superuser"]

        group_names = set(user.groups.values_list("name", flat=True))

        if "Admin" in group_names:
            return ROLE_MAP["Admin"]
        if "Sales" in group_names:
            return ROLE_MAP["Sales"]
        if "Purchase" in group_names:
            return ROLE_MAP["Purchase"]

        return "UNKNOWN"

    def validate(self, attrs):
        data = super().validate(attrs)

        # Add role to the response body alongside access/refresh tokens
        data["role"] = self._resolve_role(self.user)
        data["username"] = self.user.username

        return data

class PasswordChangeSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(required=False, help_text="ID of the user. If empty, changes own password.")
    old_password = serializers.CharField(required=False, allow_blank=True, help_text="Required for non-superusers/non-admins, or when changing own password.")
    new_password = serializers.CharField(required=True)


class UserMeSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField(help_text="Role constant of the user.")

    class Meta:
        model = User
        fields = [
            'id', 
            'username', 
            'email', 
            'first_name', 
            'last_name', 
            'is_active', 
            'is_staff', 
            'is_superuser', 
            'date_joined',
            'role'
        ]

    def get_role(self, obj):
        return CustomTokenObtainPairSerializer._resolve_role(obj)


