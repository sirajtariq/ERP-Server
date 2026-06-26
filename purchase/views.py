"""
Purchase module API viewsets with RBAC enforcement.
"""

from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets

from erp_backend.permissions import IsPurchaseUser
from purchase.models import PurchaseInvoice, PurchaseItem, Vendor
from purchase.serializers import (
    PurchaseInvoiceSerializer,
    PurchaseItemSerializer,
    VendorSerializer,
)

PURCHASE_PERMISSION_NOTE = (
    "Requires authentication. Allowed roles: Purchase group, Admin group, "
    "or superuser."
)


class VendorViewSet(viewsets.ModelViewSet):
    """CRUD operations for vendors."""

    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer
    permission_classes = [IsPurchaseUser]

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)


class PurchaseInvoiceViewSet(viewsets.ModelViewSet):
    """
    CRUD operations for purchase invoices.

    Responses include nested ``items`` for each invoice.
    """

    queryset = PurchaseInvoice.objects.select_related("vendor").prefetch_related(
        "items"
    )
    serializer_class = PurchaseInvoiceSerializer
    permission_classes = [IsPurchaseUser]

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)


class PurchaseItemViewSet(viewsets.ModelViewSet):
    """CRUD operations for standalone purchase line items."""

    queryset = PurchaseItem.objects.select_related("invoice")
    serializer_class = PurchaseItemSerializer
    permission_classes = [IsPurchaseUser]

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)
