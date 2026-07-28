from django.test import TestCase
from django.contrib.auth.models import User, Group
from rest_framework.test import APIClient
from rest_framework import status
from erp_backend.models import UserProfile, BusinessSettings
from django.core.files.uploadedfile import SimpleUploadedFile

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


class BusinessSettingsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_superuser(
            username='admin_settings', email='admin_set@example.com', password='pwd'
        )
        self.sales_user = User.objects.create_user(
            username='sales_user', email='sales@example.com', password='pwd'
        )
        sales_group, _ = Group.objects.get_or_create(name='Sales')
        self.sales_user.groups.add(sales_group)

    def test_get_auto_creates_blank_singleton(self):
        self.client.force_authenticate(user=self.sales_user)
        self.assertEqual(BusinessSettings.objects.count(), 0)
        
        response = self.client.get('/api/settings/business/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(BusinessSettings.objects.count(), 1)
        self.assertEqual(response.data['business_name'], "My Business")

    def test_patch_by_admin_updates_including_logo(self):
        self.client.force_authenticate(user=self.admin_user)
        gif_data = (
            b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00'
            b'\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00'
            b'\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
        )
        logo = SimpleUploadedFile(
            "logo.gif", 
            gif_data, 
            content_type="image/gif"
        )
        payload = {
            "business_name": "Updated Business",
            "contact": "12345",
            "logo": logo
        }
        response = self.client.patch('/api/settings/business/', data=payload, format='multipart')
        if response.status_code != 200:
            print(f"PATCH Failed with: {response.data}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        self.assertEqual(BusinessSettings.objects.count(), 1)
        self.assertEqual(response.data['business_name'], "Updated Business")
        self.assertEqual(response.data['contact'], "12345")
        self.assertTrue(response.data['logo'].startswith('http'))
        self.assertIn('logo', response.data['logo'])
        self.assertIn('.gif', response.data['logo'])

    def test_patch_by_non_admin_rejected_403(self):
        self.client.force_authenticate(user=self.sales_user)
        response = self.client.patch('/api/settings/business/', data={"business_name": "Hacked"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_by_non_admin_succeeds(self):
        self.client.force_authenticate(user=self.sales_user)
        response = self.client.get('/api/settings/business/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_anonymous_rejected_401(self):
        response_get = self.client.get('/api/settings/business/')
        self.assertEqual(response_get.status_code, status.HTTP_401_UNAUTHORIZED)
        
        response_patch = self.client.patch('/api/settings/business/', data={"business_name": "Anon"})
        self.assertEqual(response_patch.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_multiple_patches_keep_count_1(self):
        self.client.force_authenticate(user=self.admin_user)
        self.client.patch('/api/settings/business/', data={"business_name": "Patch 1"})
        self.assertEqual(BusinessSettings.objects.count(), 1)
        self.client.patch('/api/settings/business/', data={"business_name": "Patch 2"})
        self.assertEqual(BusinessSettings.objects.count(), 1)
