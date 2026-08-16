from rest_framework.routers import DefaultRouter

from inventory.views import ItemViewSet

router = DefaultRouter()
router.register(r"items", ItemViewSet, basename="inventory-item")

urlpatterns = router.urls
