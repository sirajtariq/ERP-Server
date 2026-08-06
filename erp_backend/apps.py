"""
Project-level Django app configuration.

Registers default RBAC groups after migrations complete so the database
is always initialized with Admin, Sales, and Purchase groups.
"""

from django.apps import AppConfig


class ErpBackendConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "erp_backend"
    verbose_name = "ERP Backend Core"

    def ready(self) -> None:
        """Connect the post-migrate signal to seed default auth groups."""
        from django.db.models.signals import post_migrate
        import os

        from erp_backend.signals import create_default_groups

        post_migrate.connect(create_default_groups, sender=self)

        # Start the background scheduler (only in the main process)
        if os.environ.get('RUN_MAIN') == 'true':
            from erp_backend.scheduler import start_scheduler
            start_scheduler()
