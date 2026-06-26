from django.contrib.auth.models import User
from rest_framework import viewsets
from erp_backend.serializers import UserSerializer
from erp_backend.permissions import IsAdminUser

class UserViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows users to be viewed or edited.
    Requires Admin role (superuser or Admin group).
    """
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = UserSerializer
    permission_classes = [IsAdminUser]
