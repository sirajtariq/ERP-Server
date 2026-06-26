"""
URL routing for the purchase module API.
"""

from rest_framework.routers import DefaultRouter

from purchase.views import PurchaseInvoiceViewSet, PurchaseItemViewSet, VendorViewSet

router = DefaultRouter()
router.register(r"vendors", VendorViewSet, basename="vendor")
router.register(r"invoices", PurchaseInvoiceViewSet, basename="purchase-invoice")
router.register(r"items", PurchaseItemViewSet, basename="purchase-item")

urlpatterns = router.urls
