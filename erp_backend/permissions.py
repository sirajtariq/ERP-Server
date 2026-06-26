"""
Central role-based access control (RBAC) permission classes for the ERP API.

Groups (created automatically on startup):
    - Admin:    full access to all endpoints
    - Sales:    access to sales module endpoints
    - Purchase: access to purchase module endpoints

Superusers always bypass group checks and receive full access.
"""

from rest_framework.permissions import BasePermission

# Canonical group names used across the application
ADMIN_GROUP = "Admin"
SALES_GROUP = "Sales"
PURCHASE_GROUP = "Purchase"


def _user_in_group(user, group_name: str) -> bool:
    """Return True if the authenticated user belongs to the named group."""
    return user.groups.filter(name=group_name).exists()


class IsAdminUser(BasePermission):
    """
    Grant full CRUD access to superusers and members of the 'Admin' group.

    Apply to endpoints that should be restricted to administrators only,
    or use as a composite with module-specific permissions.
    """

    message = "Admin role (superuser or Admin group) required."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return user.is_superuser or _user_in_group(user, ADMIN_GROUP)


class IsSalesUser(BasePermission):
    """
    Grant access to sales module endpoints.

    Allowed roles: 'Sales' group, 'Admin' group, or superuser.
    """

    message = "Sales role (Sales or Admin group) required."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or _user_in_group(user, ADMIN_GROUP):
            return True
        return _user_in_group(user, SALES_GROUP)


class IsPurchaseUser(BasePermission):
    """
    Grant access to purchase module endpoints.

    Allowed roles: 'Purchase' group, 'Admin' group, or superuser.
    """

    message = "Purchase role (Purchase or Admin group) required."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or _user_in_group(user, ADMIN_GROUP):
            return True
        return _user_in_group(user, PURCHASE_GROUP)
