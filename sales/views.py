"""
Sales module API viewsets with RBAC enforcement.
"""

from decimal import Decimal

from django.db import transaction

from django.db.models import Sum
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework import status as drf_status

from erp_backend.permissions import IsSalesUser
from sales.models import Customer, PaymentReceived, SalesInvoice, SalesItem
from sales.pagination import CustomPageNumberPagination
from sales.serializers import (
    CustomerListSerializer,
    CustomerSerializer,
    PaymentReceivedSerializer,
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
        customer_type = self.request.query_params.get("type")
        if name:
            qs = qs.filter(customer_name__icontains=name)
        if customer_type:
            qs = qs.filter(customer_type=customer_type)
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
            openapi.Parameter(
                "type", openapi.IN_QUERY,
                description="Filter by customer type: 'permanent' or 'walkin'",
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
        instance = self.get_object()
        instance.soft_delete()
        return Response(
            {"message": "Customer moved to trash."},
            status=drf_status.HTTP_200_OK
        )

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    @action(detail=False, methods=['get'], url_path='trash')
    def trash(self, request):
        deleted_customers = Customer.all_objects.filter(is_deleted=True)
        page = self.paginate_queryset(deleted_customers)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, customer_id=None):
        customer = Customer.all_objects.filter(
            customer_id=customer_id, is_deleted=True
        ).first()
        if not customer:
            return Response(
                {"error": "Customer not found in trash."},
                status=drf_status.HTTP_404_NOT_FOUND
            )
        customer.restore()
        return Response({"message": "Customer restored successfully."})

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    @action(detail=True, methods=['delete'], url_path='permanent-delete')
    def permanent_delete(self, request, customer_id=None):
        if not request.user.is_superuser:
            return Response(
                {"error": "Only superadmin can permanently delete."},
                status=drf_status.HTTP_403_FORBIDDEN
            )
        customer = Customer.all_objects.filter(
            customer_id=customer_id, is_deleted=True
        ).first()
        if not customer:
            return Response(
                {"error": "Not found in trash."},
                status=drf_status.HTTP_404_NOT_FOUND
            )
        customer.delete()  # actual hard delete via Django's default
        return Response({"message": "Permanently deleted."})

    @action(detail=True, methods=["get"], url_path="ledger")
    def ledger(self, request, **kwargs):
        """Return full customer ledger with summary, transactions, and payment details."""
        customer = self.get_object()
        opening_credit = float(customer.opening_credit or 0)

        invoices = customer.invoices.filter(status="Saved").order_by("date", "id")
        payments = customer.payments.all().order_by("date", "id")

        # net_total is a Python @property, so we calculate aggregates in memory.
        all_invoices = list(
            invoices.select_related("customer").prefetch_related("items")
        )
        all_payments = list(payments.select_related("invoice"))

        credit_sales = float(sum(
            inv.net_total for inv in all_invoices if inv.payment_term == "Credit"
        ))
        cash_return = float(sum(
            float(inv.paid_amount) for inv in all_invoices if inv.payment_term == "Cash"
        ))
        total_paid = float(sum(
            float(pay.amount_received) for pay in all_payments
        ))
        total_purchases = float(sum(inv.net_total for inv in all_invoices))
        total_invoices = len(all_invoices)

        ledger_rows = []

        # opening entry
        if opening_credit > 0:
            opening_date = customer.created_at.date() if customer.created_at else None
            ledger_rows.append({
                "date": opening_date.isoformat() if opening_date else None,
                "voucher": "OPENING",
                "description": "Opening Balance",
                "debit": opening_credit,
                "credit": 0,
                "balance": 0,
                "_sort_ts": customer.created_at,
            })

        # debit entries from invoices
        for inv in all_invoices:
            net = float(inv.net_total)
            ledger_rows.append({
                "date": inv.date.isoformat() if inv.date else None,
                "voucher": inv.invoice_number,
                "description": f"Invoice - {inv.payment_term}",
                "debit": net,
                "credit": 0,
                "balance": 0,
                "_sort_ts": inv.created_at,
            })

        # credit entries from payments
        for pay in all_payments:
            description = (
                f"Payment - {pay.invoice.invoice_number}"
                if pay.invoice else "General Payment"
            )
            ledger_rows.append({
                "date": pay.date.isoformat() if pay.date else None,
                "voucher": pay.receipt_number,
                "description": description,
                "debit": 0,
                "credit": float(pay.amount_received),
                "balance": 0,
                "_sort_ts": pay.created_at,
            })

        # sort purely by actual datetime timestamp
        ledger_rows.sort(key=lambda r: r['_sort_ts'])

        # unified running balance pass over ALL rows (including OPENING)
        running_balance = Decimal('0')
        for row in ledger_rows:
            running_balance += Decimal(str(row['debit'])) - Decimal(str(row['credit']))
            row['balance'] = float(running_balance)

        # derive remaining/advance from the ledger's own final balance
        final_balance = Decimal(str(ledger_rows[-1]['balance'])) if ledger_rows else Decimal('0')

        if final_balance >= 0:
            remaining_balance = float(final_balance)
            available_advance = 0.0
        else:
            remaining_balance = 0.0
            available_advance = float(abs(final_balance))

        summary = {
            "creditSales": credit_sales,
            "cashReturn": cash_return,
            "advanceApplied": 0,
            "totalCollected": total_paid,
            "remainingBalance": remaining_balance,
            "totalInvoices": total_invoices,
            "openingCredit": opening_credit,
            "availableAdvance": available_advance,
            "closingBalance": remaining_balance,
        }

        final_payment_details = {
            "openingBalance": opening_credit,
            "totalPurchases": total_purchases,
            "paymentsReceived": total_paid,
            "advanceUsed": 0,
            "totalCollected": total_paid,
            "availableAdvance": available_advance,
            "remainingBalance": remaining_balance,
        }

        # remove internal keys before returning
        for row in ledger_rows:
            row.pop('_sort_ts', None)

        return Response({
            "summary": summary,
            "ledger": ledger_rows,
            "finalPaymentDetails": final_payment_details,
        })

    @action(detail=True, methods=['post'], url_path='convert-to-permanent')
    def convert_to_permanent(self, request, **kwargs):
        """Convert a walk-in customer to a permanent customer."""
        customer = self.get_object()

        if customer.customer_type == 'permanent':
            return Response(
                {"error": "Customer is already a permanent customer."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        phone = request.data.get('phone')
        if not phone:
            return Response(
                {"error": "Phone number is required to convert to permanent."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            last = Customer.objects.select_for_update()\
                           .filter(customer_type='permanent')\
                           .order_by('-customer_id').first()
            new_id = (last.customer_id + 1) if last else 4000

            customer.customer_type = 'permanent'
            customer.customer_id = new_id
            customer.phone = phone
            customer.save()

        return Response({
            "message": "Customer converted to permanent successfully.",
            "customerId": customer.customer_id,
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
        invoice = self.get_object()

        # Reverse the customer's credit_balance if this was a Credit invoice
        if invoice.customer and invoice.payment_term == 'Credit':
            invoice.customer.credit_balance -= invoice.balance_due
            invoice.customer.save(update_fields=['credit_balance'])

        invoice.soft_delete()

        return Response(
            {"message": "Invoice moved to trash. Customer balance adjusted."},
            status=drf_status.HTTP_200_OK
        )

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    @action(detail=False, methods=['get'], url_path='trash')
    def trash(self, request):
        deleted_invoices = SalesInvoice.all_objects.filter(is_deleted=True)
        page = self.paginate_queryset(deleted_invoices)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, pk=None):
        invoice = SalesInvoice.all_objects.filter(
            id=pk, is_deleted=True
        ).first()
        if not invoice:
            return Response(
                {"error": "Not found in trash."},
                status=drf_status.HTTP_404_NOT_FOUND
            )

        # Re-apply the customer's credit_balance if this was a Credit invoice
        if invoice.customer and invoice.payment_term == 'Credit':
            invoice.customer.credit_balance += invoice.balance_due
            invoice.customer.save(update_fields=['credit_balance'])

        invoice.restore()
        return Response({"message": "Invoice restored, balance re-applied."})

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    @action(detail=True, methods=['delete'], url_path='permanent-delete')
    def permanent_delete(self, request, pk=None):
        if not request.user.is_superuser:
            return Response(
                {"error": "Only superuser can permanently delete."},
                status=drf_status.HTTP_403_FORBIDDEN
            )
        invoice = SalesInvoice.all_objects.filter(
            id=pk, is_deleted=True
        ).first()
        if not invoice:
            return Response(
                {"error": "Not found in trash."},
                status=drf_status.HTTP_404_NOT_FOUND
            )
        invoice.delete()  # actual hard delete via Django's default
        return Response({"message": "Permanently deleted."})


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


class PaymentReceivedViewSet(viewsets.ModelViewSet):
    """CRUD operations for daily income / payment received records."""

    queryset = PaymentReceived.objects.select_related('customer', 'invoice').all()
    serializer_class = PaymentReceivedSerializer
    permission_classes = [IsSalesUser]
    pagination_class = CustomPageNumberPagination
    filter_backends = [OrderingFilter]
    ordering_fields = "__all__"
    ordering = ["-date", "-id"]

    def get_queryset(self):
        qs = super().get_queryset()
        from_date = self.request.query_params.get('from')
        to_date = self.request.query_params.get('to')
        customer = self.request.query_params.get('customer')
        if from_date and to_date:
            qs = qs.filter(date__range=[from_date, to_date])
        if customer:
            qs = qs.filter(customer__customer_name__icontains=customer)
        return qs

    @swagger_auto_schema(
        operation_description=SALES_PERMISSION_NOTE,
        manual_parameters=[
            openapi.Parameter(
                'from', openapi.IN_QUERY,
                description="Start date for filtering (YYYY-MM-DD)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                'to', openapi.IN_QUERY,
                description="End date for filtering (YYYY-MM-DD)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                'customer', openapi.IN_QUERY,
                description="Filter by customer name (case-insensitive, partial match)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                'page', openapi.IN_QUERY,
                description="Page number (default: 1)",
                type=openapi.TYPE_INTEGER,
            ),
            openapi.Parameter(
                'page_size', openapi.IN_QUERY,
                description="Results per page (default: 10, max: 100)",
                type=openapi.TYPE_INTEGER,
            ),
            openapi.Parameter(
                'ordering', openapi.IN_QUERY,
                description="Sort field. Prefix with '-' for descending. "
                            "E.g. '-date', 'amount_received', 'receipt_number'",
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
        payment = self.get_object()
        serializer = self.get_serializer()
        serializer._reverse_payment(
            payment.customer, payment.invoice,
            payment.applied_to_invoice, payment.applied_to_credit,
            payment.applied_to_advance
        )
        payment.soft_delete()
        return Response(
            {"message": "Payment moved to trash. Balances adjusted."},
            status=drf_status.HTTP_200_OK,
        )

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    @action(detail=False, methods=['get'], url_path='trash')
    def trash(self, request):
        deleted = PaymentReceived.all_objects.filter(is_deleted=True)
        page = self.paginate_queryset(deleted)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @swagger_auto_schema(operation_description=SALES_PERMISSION_NOTE)
    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, pk=None):
        payment = PaymentReceived.all_objects.filter(
            id=pk, is_deleted=True
        ).first()
        if not payment:
            return Response(
                {"error": "Not found in trash."},
                status=drf_status.HTTP_404_NOT_FOUND,
            )
        serializer = self.get_serializer()
        result = serializer._apply_payment(
            payment.customer, payment.amount_received, payment.invoice
        )
        payment.balance_after = result['balance_after']
        payment.applied_to_invoice = result['applied_to_invoice']
        payment.applied_to_credit = result['applied_to_credit']
        payment.applied_to_advance = result['applied_to_advance']
        payment.save(update_fields=[
            'balance_after', 'applied_to_invoice',
            'applied_to_credit', 'applied_to_advance',
        ])
        payment.restore()
        return Response({"message": "Payment restored, balances re-applied."})
