"""
Database initialization signals for the ERP backend.
"""

from django.contrib.auth.models import Group

from erp_backend.permissions import ADMIN_GROUP, PURCHASE_GROUP, SALES_GROUP

DEFAULT_GROUPS = (ADMIN_GROUP, SALES_GROUP, PURCHASE_GROUP)


def create_default_groups(sender, **kwargs) -> None:
    """
    Ensure Admin, Sales, and Purchase groups exist after migrations.

    Connected from ErpBackendConfig.ready() via the post_migrate signal so
    groups are created idempotently whenever the database schema is ready.
    """
    for group_name in DEFAULT_GROUPS:
        Group.objects.get_or_create(name=group_name)
