import os
import sys
import django
from django.core.management import call_command

def setup_database():
    print("Running migrations...")
    call_command("migrate", interactive=False)
    
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    if not User.objects.filter(username="superadmin").exists():
        print("Creating superadmin user...")
        User.objects.create_superuser("superadmin", "admin@example.com", "Admin!@#")
        print("superadmin created successfully.")
    else:
        print("superadmin user already exists.")

def main():
    # Set production mode flag so settings.py uses DEBUG=False and ALLOWED_HOSTS=['*']
    os.environ["ERP_DESKTOP_PROD"] = "1"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "erp_backend.settings")
    
    django.setup()
    
    # Run setup tasks
    setup_database()
    
    print("Starting server with Waitress on 127.0.0.1:8000...")
    from waitress import serve
    from erp_backend.wsgi import application
    serve(application, host='127.0.0.1', port=8000)

if __name__ == "__main__":
    main()
