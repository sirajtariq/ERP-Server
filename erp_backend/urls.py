"""
Root URL configuration for the ERP backend API.

Includes:
    - Django admin
    - Sales and Purchase module routers
    - Interactive Swagger UI at /swagger/
    - ReDoc alternative at /redoc/
"""

from django.contrib import admin
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenBlacklistView

from django.conf import settings
from django.conf.urls.static import static

from erp_backend.views import (
    UserViewSet, CustomTokenObtainPairView, PasswordChangeAPIView, 
    UserMeAPIView, BusinessSettingsAPIView, BackupSettingAPIView, TriggerBackupView, RestoreBackupView
)

router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")

RBAC_DESCRIPTION = """
## Role-Based Access Control (RBAC)

All API endpoints require authentication unless noted otherwise.

| Group      | Access                                              |
|------------|-----------------------------------------------------|
| **Admin**  | Full access to all Sales and Purchase endpoints     |
| **Sales**  | Sales module only (`/api/sales/…`)                  |
| **Purchase** | Purchase module only (`/api/purchase/…`)          |
| **Superuser** | Full access (bypasses group checks)              |

Groups are created automatically on first migration. Assign users to groups
via Django Admin (`/admin/auth/user/`).
"""

schema_view = get_schema_view(
    openapi.Info(
        title="ERP Backend API",
        default_version="v1",
        description=(
            "REST API for the desktop ERP application — Sales and Purchase modules.\n"
            + RBAC_DESCRIPTION
        ),
        contact=openapi.Contact(email="admin@erp.local"),
        license=openapi.License(name="Proprietary"),
    ),
    public=True,
    permission_classes=[permissions.AllowAny],
)

urlpatterns = [
    path("admin/", admin.site.urls),
    # Auth APIs
    path("api/auth/login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/login/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/logout/", TokenBlacklistView.as_view(), name="token_blacklist"),
    path("api/auth/password/change/", PasswordChangeAPIView.as_view(), name="password_change"),
    path("api/auth/me/", UserMeAPIView.as_view(), name="user_me"),
    # Core APIs
    path("api/settings/business/", BusinessSettingsAPIView.as_view(), name="business_settings"),
    path("api/settings/backup/", BackupSettingAPIView.as_view(), name="backup_settings"),
    path("api/settings/backup/trigger/", TriggerBackupView.as_view(), name="trigger_backup"),
    path("api/settings/backup/restore/", RestoreBackupView.as_view(), name="restore_backup"),
    path("api/", include(router.urls)),
    # Module APIs
    path("api/inventory/", include("inventory.urls")),
    path("api/sales/", include("sales.urls")),
    path("api/purchase/", include("purchase.urls")),
    path("api/employees/", include("employees.urls")),
    path("api/attendance/", include("employees.attendance_urls")),
    path("api/dashboard/", include("dashboard.urls")),
    # Interactive API documentation
    path(
        "swagger/",
        schema_view.with_ui("swagger", cache_timeout=0),
        name="schema-swagger-ui",
    ),
    path(
        "redoc/",
        schema_view.with_ui("redoc", cache_timeout=0),
        name="schema-redoc",
    ),
    path(
        "swagger.json",
        schema_view.without_ui(cache_timeout=0),
        name="schema-json",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
