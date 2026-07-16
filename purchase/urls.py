"""
URL routing for the purchase module API.
"""

from rest_framework.routers import DefaultRouter

from purchase.views import (
    ExpenseViewSet,
    PurchaseInvoiceViewSet,
    PurchaseItemViewSet,
    VendorPaymentViewSet,
    VendorViewSet,
)

router = DefaultRouter()
router.register(r"vendors", VendorViewSet, basename="vendor")
router.register(r"vendor-payments", VendorPaymentViewSet, basename="vendor-payment")
router.register(r"invoices", PurchaseInvoiceViewSet, basename="purchase-invoice")
router.register(r"items", PurchaseItemViewSet, basename="purchase-item")
router.register(r"expenses", ExpenseViewSet, basename="expense")

urlpatterns = router.urls
