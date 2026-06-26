"""
Management command to seed default RBAC groups and an optional admin user.

Usage:
    uv run python manage.py init_erp_data
    uv run python manage.py init_erp_data --create-superuser
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from erp_backend.permissions import ADMIN_GROUP, PURCHASE_GROUP, SALES_GROUP

User = get_user_model()


class Command(BaseCommand):
    help = "Create default RBAC groups (Admin, Sales, Purchase) and optionally a superuser."

    def add_arguments(self, parser):
        parser.add_argument(
            "--create-superuser",
            action="store_true",
            help="Create a default superuser (admin / admin) if none exists.",
        )

    def handle(self, *args, **options):
        for group_name in (ADMIN_GROUP, SALES_GROUP, PURCHASE_GROUP):
            group, created = Group.objects.get_or_create(name=group_name)
            status = "Created" if created else "Already exists"
            self.stdout.write(f"{status}: group '{group.name}'")

        if options["create_superuser"] and not User.objects.filter(is_superuser=True).exists():
            User.objects.create_superuser(
                username="admin",
                email="admin@erp.local",
                password="admin",
            )
            self.stdout.write(self.style.SUCCESS("Superuser created: admin / admin"))
        elif options["create_superuser"]:
            self.stdout.write("Superuser already exists — skipped.")

        self.stdout.write(self.style.SUCCESS("ERP data initialization complete."))
