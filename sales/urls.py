"""
URL routing for the sales module API.
"""

from rest_framework.routers import DefaultRouter

from sales.views import CustomerViewSet, PaymentReceivedViewSet, SalesInvoiceViewSet, SalesItemViewSet, QuotationViewSet, SalesReturnViewSet

router = DefaultRouter()
router.register(r"customers", CustomerViewSet, basename="customer")
router.register(r"invoices", SalesInvoiceViewSet, basename="sales-invoice")
router.register(r"items", SalesItemViewSet, basename="sales-item")
router.register(r"payments", PaymentReceivedViewSet, basename="payment-received")
router.register(r"quotations", QuotationViewSet, basename="quotation")
router.register(r"returns", SalesReturnViewSet, basename="sales-return")

urlpatterns = router.urls
