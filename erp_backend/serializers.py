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
        fields = ['id', 'username', 'password', 'role']
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
