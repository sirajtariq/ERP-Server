"""
Sales module API viewsets with RBAC enforcement.
"""

from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets

from erp_backend.permissions import IsSalesUser
from sales.models import Customer, SalesInvoice, SalesItem
from sales.serializers import (
    CustomerSerializer,
    SalesInvoiceSerializer,
    SalesItemSerializer,
)

SALES_PERMISSION_NOTE = (
    "Requires authentication. Allowed roles: Sales group, Admin group, "
    "or superuser."
)


class CustomerViewSet(viewsets.ModelViewSet):
    """CRUD operations for customers."""

    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [IsSalesUser]

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)


class SalesInvoiceViewSet(viewsets.ModelViewSet):
    """
    CRUD operations for sales invoices.

    Responses include nested ``items`` for each invoice.
    """

    queryset = SalesInvoice.objects.select_related("customer").prefetch_related(
        "items"
    )
    serializer_class = SalesInvoiceSerializer
    permission_classes = [IsSalesUser]

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)


class SalesItemViewSet(viewsets.ModelViewSet):
    """CRUD operations for standalone sales line items."""

    queryset = SalesItem.objects.select_related("invoice")
    serializer_class = SalesItemSerializer
    permission_classes = [IsSalesUser]

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)
