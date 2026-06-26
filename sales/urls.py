"""
URL routing for the sales module API.
"""

from rest_framework.routers import DefaultRouter

from sales.views import CustomerViewSet, SalesInvoiceViewSet, SalesItemViewSet

router = DefaultRouter()
router.register(r"customers", CustomerViewSet, basename="customer")
router.register(r"invoices", SalesInvoiceViewSet, basename="sales-invoice")
router.register(r"items", SalesItemViewSet, basename="sales-item")

urlpatterns = router.urls
