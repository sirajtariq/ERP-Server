import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp_backend.settings')
django.setup()

from django.contrib.auth.models import User
from django.core.management import call_command
from rest_framework.test import APIClient

def run_demo():
    print("=== Starting RBAC Demonstration ===\n")
    
    # Trigger migrations to ensure groups exist
    call_command('migrate', verbosity=0)

    # 1. Clean up existing non-superuser accounts for a fresh demo
    User.objects.filter(is_superuser=False).delete()
    
    # Ensure superuser exists
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser('admin', 'admin@example.com', 'admin')

    print("1. Superuser 'admin' is ready.")
    
    client = APIClient(SERVER_NAME='localhost')
    
    # 2. Superuser logs in and creates an admin user 'Aleem'
    client.login(username='admin', password='admin')
    print("2. Logged in as superuser. Creating 'Admin' user 'Aleem'...")
    response = client.post('/api/users/', {
        'username': 'Aleem',
        'password': 'admin123',
        'role': 'Admin'
    })
    
    if response.status_code == 201:
        print("   -> Success: User 'Aleem' created with Admin role.")
    else:
        print("   -> Error creating Aleem:", getattr(response, 'data', response.content))
        return
    client.logout()

    # 3. Admin user logs in and creates another user
    print("\n3. Logged in as 'Aleem' (Admin). Creating 'Sales' user 'sales_user'...")
    client.login(username='Aleem', password='admin123')
    response = client.post('/api/users/', {
        'username': 'sales_user',
        'password': 'sales123',
        'role': 'Sales'
    })

    if response.status_code == 201:
        print("   -> Success: User 'sales_user' created with Sales role.")
    else:
        print("   -> Error creating sales_user:", getattr(response, 'data', response.content))
        return
    client.logout()

    # 4. Sales user logs in and tries to access restricted endpoints
    print("\n4. Logged in as 'sales_user' (Sales role).")
    client.login(username='sales_user', password='sales123')
    
    # Try accessing Purchase API
    print("   -> Attempting to access Purchase API (/api/purchase/vendors/)...")
    response_purchase = client.get('/api/purchase/vendors/')
    print(f"   -> Result: {response_purchase.status_code} Forbidden (Data: {getattr(response_purchase, 'data', response_purchase.content)})")
    
    # Try accessing Users API
    print("   -> Attempting to access Users API (/api/users/)...")
    response_users = client.get('/api/users/')
    print(f"   -> Result: {response_users.status_code} Forbidden (Data: {getattr(response_users, 'data', response_users.content)})")

    print("\n=== Demonstration Completed successfully! ===")

if __name__ == '__main__':
    run_demo()
