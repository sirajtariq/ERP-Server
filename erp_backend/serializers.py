from django.contrib.auth.models import User, Group
from rest_framework import serializers
from .models import UserProfile

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            'fullname', 'phone', 'cnic', 'address', 'designation',
            'dateofjoining', 'employmenttype', 'basicsalary', 'salarytype'
        ]

class UserReadSerializer(serializers.ModelSerializer):
    isActive = serializers.BooleanField(source='is_active')
    dateJoined = serializers.DateTimeField(source='date_joined')
    role = serializers.SerializerMethodField()
    profile = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'isActive', 'dateJoined', 'role', 'profile']

    def get_role(self, obj):
        return CustomTokenObtainPairSerializer._resolve_role(obj)
        
    def get_profile(self, obj):
        if hasattr(obj, 'profile') and obj.profile:
            return UserProfileSerializer(obj.profile).data
        return None

class UserSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(
        choices=["Admin", "Sales", "Purchase"], 
        write_only=True,
        required=True,
        help_text="Role to assign to the user (maps to Django Group)."
    )
    fullname = serializers.CharField(write_only=True, required=True)
    phone = serializers.CharField(write_only=True, required=True)
    cnic = serializers.CharField(write_only=True, required=True)
    address = serializers.CharField(write_only=True, required=False, allow_null=True, allow_blank=True)
    designation = serializers.CharField(write_only=True, required=True)
    dateofjoining = serializers.DateField(write_only=True, required=True)
    employmenttype = serializers.ChoiceField(
        choices=['fulltime', 'parttime', 'contract'], write_only=True, required=True
    )
    basicsalary = serializers.DecimalField(
        max_digits=10, decimal_places=2, write_only=True, required=False, allow_null=True
    )
    salarytype = serializers.ChoiceField(
        choices=['monthly', 'daily', 'perjob'], write_only=True, required=False, allow_null=True, allow_blank=True
    )
    
    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'password', 'role',
            'fullname', 'phone', 'cnic', 'address', 'designation',
            'dateofjoining', 'employmenttype', 'basicsalary', 'salarytype'
        ]
        extra_kwargs = {
            'password': {'write_only': True}
        }

    def create(self, validated_data):
        role_name = validated_data.pop('role')
        
        # Pop profile fields
        profile_data = {
            'fullname': validated_data.pop('fullname', ''),
            'phone': validated_data.pop('phone', ''),
            'cnic': validated_data.pop('cnic', ''),
            'address': validated_data.pop('address', None),
            'designation': validated_data.pop('designation', ''),
            'dateofjoining': validated_data.pop('dateofjoining', None),
            'employmenttype': validated_data.pop('employmenttype', 'fulltime'),
            'basicsalary': validated_data.pop('basicsalary', None),
            'salarytype': validated_data.pop('salarytype', None),
        }

        user = User.objects.create_user(**validated_data)
        
        from .models import UserProfile
        UserProfile.objects.create(user=user, **profile_data)

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
    fullname = serializers.CharField(source='profile.fullname', read_only=True)
    phone = serializers.CharField(source='profile.phone', read_only=True)
    cnic = serializers.CharField(source='profile.cnic', read_only=True)
    address = serializers.CharField(source='profile.address', read_only=True)
    designation = serializers.CharField(source='profile.designation', read_only=True)
    dateofjoining = serializers.DateField(source='profile.dateofjoining', read_only=True)
    employmenttype = serializers.CharField(source='profile.employmenttype', read_only=True)
    basicsalary = serializers.DecimalField(source='profile.basicsalary', max_digits=10, decimal_places=2, read_only=True)
    salarytype = serializers.CharField(source='profile.salarytype', read_only=True)

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
            'role',
            'fullname', 'phone', 'cnic', 'address', 'designation',
            'dateofjoining', 'employmenttype', 'basicsalary', 'salarytype'
        ]

    def get_role(self, obj):
        return CustomTokenObtainPairSerializer._resolve_role(obj)


from .models import BusinessSettings

class BusinessSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessSettings
        fields = [
            'logo', 'business_name', 'contact', 'whatsapp', 
            'email', 'address', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
