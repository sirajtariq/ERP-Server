"""
URL routing for the purchase module API.
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from purchase.views import (
    DailyOutflowsView,
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

urlpatterns = [
    path("daily-outflows/", DailyOutflowsView.as_view(), name="daily-outflows"),
] + router.urls
