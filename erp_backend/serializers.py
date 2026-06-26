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

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Add custom claims
        token['username'] = user.username
        
        # Determine roles
        roles = []
        if user.is_superuser:
            roles.append('Superuser')
        # Add group names
        roles.extend(list(user.groups.values_list('name', flat=True)))
        
        token['roles'] = roles
        return token

class PasswordChangeSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(required=False, help_text="ID of the user. If empty, changes own password.")
    old_password = serializers.CharField(required=False, allow_blank=True, help_text="Required for non-superusers/non-admins, or when changing own password.")
    new_password = serializers.CharField(required=True)

