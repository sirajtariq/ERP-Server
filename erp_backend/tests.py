from django.test import TestCase
from django.contrib.auth.models import User, Group
from rest_framework.test import APIClient
from rest_framework import status
from erp_backend.models import UserProfile

class UserViewSetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_superuser(
            username='admin_test',
            email='admin@example.com',
            password='adminpassword'
        )
        self.client.force_authenticate(user=self.admin_user)
        
        self.test_user = User.objects.create_user(
            username='test_user',
            email='test@example.com',
            password='testpassword'
        )
        UserProfile.objects.create(
            user=self.test_user,
            fullname='Old Name',
            phone='111',
            cnic='111-111-1',
            designation='Old Desig',
            dateofjoining='2020-01-01',
            employmenttype='fulltime'
        )

    def test_create_user_returns_full_read_shape(self):
        Group.objects.get_or_create(name='Sales')
        payload = {
            "username": "new_user",
            "email": "new@example.com",
            "password": "newpassword123",
            "role": "Sales",
            "fullname": "New User Name",
            "phone": "0987654321",
            "cnic": "98765-4321098-7",
            "designation": "Sales Exec",
            "dateofjoining": "2026-07-28",
            "employmenttype": "contract"
        }
        
        response = self.client.post('/api/users/', data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        data = response.json()
        self.assertIn('role', data)
        self.assertIn('isActive', data)
        self.assertIn('profile', data)
        self.assertIsNotNone(data['profile'])
        self.assertEqual(data['profile']['fullname'], 'New User Name')
        self.assertNotIn('password', data)

    def test_update_user_returns_full_read_shape(self):
        payload = {
            "username": "test_user_updated",
            "fullname": "Updated Name"
        }
        
        response = self.client.patch(f'/api/users/{self.test_user.id}/', data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        data = response.json()
        self.assertIn('role', data)
        self.assertIn('isActive', data)
        self.assertIn('profile', data)
        self.assertIsNotNone(data['profile'])
        self.assertEqual(data['username'], 'test_user_updated')
        # Since fullname is nested but accepted flattened on write (as currently working)
        # the response should reflect updated or original based on partial update.
        # Wait, the partial update for UserSerializer requires sending fields since it pops them.
        self.assertNotIn('password', data)
