"""
Purchase module API viewsets with RBAC enforcement.
"""

from django.db import IntegrityError

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework import status as drf_status
from rest_framework.exceptions import ValidationError as serializers_ValidationError

from erp_backend.permissions import IsPurchaseUser, OnlyAdminCanDelete
from purchase.models import PurchaseInvoice, PurchaseItem, Vendor
from purchase.serializers import (
    PurchaseInvoiceSerializer,
    PurchaseItemSerializer,
    VendorSerializer,
)
from sales.pagination import CustomPageNumberPagination

PURCHASE_PERMISSION_NOTE = (
    "Requires authentication. Allowed roles: Purchase group, Admin group, "
    "or superuser."
)


class VendorViewSet(viewsets.ModelViewSet):
    """CRUD operations for vendors."""

    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer
    permission_classes = [IsPurchaseUser, OnlyAdminCanDelete]
    lookup_field = "vendor_id"
    pagination_class = CustomPageNumberPagination
    filter_backends = [OrderingFilter]
    ordering_fields = "__all__"
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = Vendor.objects.all()
        name = self.request.query_params.get("name")
        if name:
            qs = qs.filter(vendor_name__icontains=name)
        return qs

    # ── Standard CRUD ───────────────────────────────────────────────

    @swagger_auto_schema(
        operation_description=PURCHASE_PERMISSION_NOTE,
        manual_parameters=[
            openapi.Parameter(
                "name", openapi.IN_QUERY,
                description="Search vendors by name (case-insensitive, partial match)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "page", openapi.IN_QUERY,
                description="Page number (default: 1)",
                type=openapi.TYPE_INTEGER,
            ),
            openapi.Parameter(
                "page_size", openapi.IN_QUERY,
                description="Results per page (default: 10, max: 100)",
                type=openapi.TYPE_INTEGER,
            ),
            openapi.Parameter(
                "ordering", openapi.IN_QUERY,
                description="Sort field. Prefix with '-' for descending. "
                            "E.g. 'vendor_name', '-created_at', 'credit_balance'",
                type=openapi.TYPE_STRING,
            ),
        ],
    )
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
        instance = self.get_object()
        instance.soft_delete()
        return Response(
            {"message": "Vendor moved to trash."},
            status=drf_status.HTTP_200_OK
        )

    # ── IntegrityError handling for duplicate phone ──────────────────
    # NOTE: This string-matching approach is fragile tech debt inherited
    # from the sales module (see CustomerViewSet).  Could be improved
    # later with try/except IntegrityError + inspecting e.__cause__ or a
    # pre-check query instead.

    def perform_create(self, serializer):
        try:
            serializer.save()
        except IntegrityError as e:
            if 'phone' in str(e):
                raise serializers_ValidationError(
                    {"phone": "A vendor with this phone number already exists."}
                )
            raise

    def perform_update(self, serializer):
        try:
            serializer.save()
        except IntegrityError as e:
            if 'phone' in str(e):
                raise serializers_ValidationError(
                    {"phone": "A vendor with this phone number already exists."}
                )
            raise

    # ── Trash / Restore / Permanent Delete ───────────────────────────

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=False, methods=['get'], url_path='trash')
    def trash(self, request):
        deleted_vendors = Vendor.all_objects.filter(is_deleted=True)
        page = self.paginate_queryset(deleted_vendors)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, vendor_id=None):
        vendor = Vendor.all_objects.filter(
            vendor_id=vendor_id, is_deleted=True
        ).first()
        if not vendor:
            return Response(
                {"error": "Vendor not found in trash."},
                status=drf_status.HTTP_404_NOT_FOUND
            )
        vendor.restore()
        return Response({"message": "Vendor restored successfully."})

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=True, methods=['delete'], url_path='permanent-delete')
    def permanent_delete(self, request, vendor_id=None):
        if not request.user.is_superuser:
            return Response(
                {"error": "Only superadmin can permanently delete."},
                status=drf_status.HTTP_403_FORBIDDEN
            )
        # TODO: guard once PurchaseInvoice/VendorPayment exist —
        # should prevent deletion of vendors with related records.
        vendor = Vendor.all_objects.filter(
            vendor_id=vendor_id, is_deleted=True
        ).first()
        if not vendor:
            return Response(
                {"error": "Not found in trash."},
                status=drf_status.HTTP_404_NOT_FOUND
            )
        vendor.delete()  # actual hard delete via Django's default
        return Response({"message": "Permanently deleted."})

    # TODO: Implement vendor ledger action once PurchaseInvoice and
    # VendorPayment models exist. Should mirror CustomerViewSet.ledger
    # with purchase-side transactions.




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
