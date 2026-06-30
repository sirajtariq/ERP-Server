"""
Sales module API viewsets with RBAC enforcement.
"""

from decimal import Decimal

from django.db.models import Sum
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from erp_backend.permissions import IsSalesUser
from sales.models import Customer, SalesInvoice, SalesItem
from sales.pagination import CustomPageNumberPagination
from sales.serializers import (
    CustomerListSerializer,
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

    queryset = Customer.objects.prefetch_related("invoices").all()
    serializer_class = CustomerSerializer
    permission_classes = [IsSalesUser]
    lookup_field = "customer_id"
    pagination_class = CustomPageNumberPagination
    filter_backends = [OrderingFilter]
    ordering_fields = "__all__"
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return CustomerListSerializer
        return CustomerSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        name = self.request.query_params.get("name")
        if name:
            qs = qs.filter(customer_name__icontains=name)
        return qs

    @swagger_auto_schema(
        operation_description=SALES_PERMISSION_NOTE,
        manual_parameters=[
            openapi.Parameter(
                "name", openapi.IN_QUERY,
                description="Search customers by name (case-insensitive, partial match)",
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
                            "E.g. 'customer_name', '-created_at', 'credit_balance'",
                type=openapi.TYPE_STRING,
            ),
        ],
    )
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

    @action(detail=True, methods=["get"], url_path="ledger")
    def ledger(self, request, **kwargs):
        """Return full customer ledger with summary, transactions, and payment details."""
        customer = self.get_object()
        opening_credit = float(customer.opening_credit or 0)

        invoices = (
            SalesInvoice.objects
            .filter(customer=customer, status="Saved")
            .order_by("date", "id")
        )

        # net_total is a Python @property, so we calculate aggregates in memory.
        all_invoices = list(
            invoices.select_related("customer").prefetch_related("items")
        )

        credit_sales = float(sum(
            inv.net_total for inv in all_invoices if inv.payment_term == "Credit"
        ))
        cash_return = float(sum(
            float(inv.paid_amount) for inv in all_invoices if inv.payment_term == "Cash"
        ))
        total_collected = float(sum(float(inv.paid_amount) for inv in all_invoices))
        total_purchases = float(sum(inv.net_total for inv in all_invoices))
        total_invoices = len(all_invoices)

        remaining_balance = float(customer.credit_balance)
        available_advance = float(customer.advance_balance)

        summary = {
            "creditSales": credit_sales,
            "cashReturn": cash_return,
            "advanceApplied": 0,
            "totalCollected": total_collected,
            "remainingBalance": remaining_balance,
            "totalInvoices": total_invoices,
            "openingCredit": opening_credit,
            "availableAdvance": available_advance,
            "closingBalance": remaining_balance,
        }

        # ---------- build ledger entries ----------
        ledger_entries = []
        running_balance = 0.0

        # opening entry
        if opening_credit > 0:
            running_balance += opening_credit
            ledger_entries.append({
                "date": customer.created_at.isoformat() if customer.created_at else None,
                "voucher": "OPENING",
                "description": "Opening Balance",
                "debit": opening_credit,
                "credit": 0,
                "balance": running_balance,
            })

        for inv in all_invoices:
            net = float(inv.net_total)
            paid = float(inv.paid_amount)
            inv_date = inv.date.isoformat() if inv.date else None

            # debit entry (invoice)
            running_balance += net
            ledger_entries.append({
                "date": inv_date,
                "voucher": inv.invoice_number,
                "description": f"Invoice - {inv.payment_term}",
                "debit": net,
                "credit": 0,
                "balance": running_balance,
            })

            # credit entry (payment)
            if paid > 0:
                running_balance -= paid
                ledger_entries.append({
                    "date": inv_date,
                    "voucher": inv.invoice_number,
                    "description": "Payment Received",
                    "debit": 0,
                    "credit": paid,
                    "balance": running_balance,
                })

        # ---------- final payment details ----------
        final_payment_details = {
            "openingBalance": opening_credit,
            "totalPurchases": total_purchases,
            "paymentsReceived": total_collected,
            "advanceUsed": 0,
            "totalCollected": total_collected,
            "availableAdvance": available_advance,
            "remainingBalance": remaining_balance,
        }

        return Response({
            "summary": summary,
            "ledger": ledger_entries,
            "finalPaymentDetails": final_payment_details,
        })


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
    pagination_class = CustomPageNumberPagination
    filter_backends = [OrderingFilter]
    ordering_fields = "__all__"
    ordering = ["-date", "-id"]

    def get_queryset(self):
        qs = super().get_queryset()
        name = self.request.query_params.get("name")
        customer_type = self.request.query_params.get("type")
        if name:
            qs = qs.filter(customer__customer_name__icontains=name)
        if customer_type == 'walkin':
            qs = qs.filter(customer__isnull=True)
        elif customer_type == 'loyal':
            qs = qs.filter(customer__isnull=False)
        return qs

    @swagger_auto_schema(
        operation_description=SALES_PERMISSION_NOTE,
        manual_parameters=[
            openapi.Parameter(
                "name", openapi.IN_QUERY,
                description="Search invoices by customer name (case-insensitive, partial match)",
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
                            "E.g. '-date', 'invoice_number', 'paid_amount'",
                type=openapi.TYPE_STRING,
            ),
        ],
    )
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
