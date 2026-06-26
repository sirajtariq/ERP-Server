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

from erp_backend.views import UserViewSet, CustomTokenObtainPairView, PasswordChangeAPIView

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
    # Core APIs
    path("api/", include(router.urls)),
    # Module APIs
    path("api/sales/", include("sales.urls")),
    path("api/purchase/", include("purchase.urls")),
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
