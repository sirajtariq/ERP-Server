from django.contrib.auth.models import User, Group
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from erp_backend.permissions import ADMIN_GROUP, SALES_GROUP

class UserMeAPITests(APITestCase):
    def setUp(self):
        # Retrieve or create standard groups (may be auto-seeded via post_migrate signals)
        self.admin_group, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
        self.sales_group, _ = Group.objects.get_or_create(name=SALES_GROUP)
        
        # Create users
        self.admin_user = User.objects.create_user(
            username='admin_user',
            email='admin@example.com',
            password='adminpassword',
            first_name='Admin',
            last_name='User'
        )
        self.admin_user.groups.add(self.admin_group)

        self.sales_user = User.objects.create_user(
            username='sales_user',
            email='sales@example.com',
            password='salespassword',
            first_name='Sales',
            last_name='User'
        )
        self.sales_user.groups.add(self.sales_group)

        self.superuser = User.objects.create_superuser(
            username='super_user',
            email='super@example.com',
            password='superpassword'
        )
        
        # Endpoint URL
        self.url = reverse('user_me')

    def test_get_me_unauthenticated(self):
        """Test that fetching user details without authentication is blocked."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_me_authenticated_admin(self):
        """Test retrieving profile details for an admin user."""
        # Login to obtain JWT
        login_url = reverse('token_obtain_pair')
        login_response = self.client.post(login_url, {
            'username': 'admin_user',
            'password': 'adminpassword'
        })
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        access_token = login_response.data['access']

        # Add JWT header
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        
        # Call /api/auth/me/
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify details
        data = response.data
        self.assertEqual(data['username'], 'admin_user')
        self.assertEqual(data['email'], 'admin@example.com')
        self.assertEqual(data['first_name'], 'Admin')
        self.assertEqual(data['last_name'], 'User')
        self.assertEqual(data['role'], 'ADMIN')
        self.assertNotIn('password', data)
        self.assertNotIn('password_hash', data)
        self.assertNotIn('password_hashed', data)

    def test_get_me_authenticated_sales(self):
        """Test retrieving profile details for a sales user."""
        login_url = reverse('token_obtain_pair')
        login_response = self.client.post(login_url, {
            'username': 'sales_user',
            'password': 'salespassword'
        })
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        access_token = login_response.data['access']

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        data = response.data
        self.assertEqual(data['username'], 'sales_user')
        self.assertEqual(data['role'], 'SALE_PERSON')
        self.assertNotIn('password', data)

    def test_get_me_authenticated_superuser(self):
        """Test retrieving profile details for a superuser."""
        login_url = reverse('token_obtain_pair')
        login_response = self.client.post(login_url, {
            'username': 'super_user',
            'password': 'superpassword'
        })
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        access_token = login_response.data['access']

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        data = response.data
        self.assertEqual(data['username'], 'super_user')
        self.assertEqual(data['role'], 'SUPER_ADMIN')
        self.assertNotIn('password', data)
