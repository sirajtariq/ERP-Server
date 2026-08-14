from rest_framework.routers import DefaultRouter

from inventory.views import ItemViewSet, StockMovementViewSet

router = DefaultRouter()
router.register(r"items", ItemViewSet, basename="inventory-item")
router.register(r"movements", StockMovementViewSet, basename="inventory-movement")

urlpatterns = router.urls
