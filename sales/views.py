"""
Sales module API viewsets with RBAC enforcement.
"""

from decimal import Decimal

from django.db import transaction

from django.db.models import Sum, DecimalField
from django.db.models.functions import Coalesce
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework import status as drf_status

from erp_backend.permissions import IsSalesUser, OnlyAdminCanDelete
from sales.models import Customer, PaymentReceived, SalesInvoice, SalesItem, Quotation, QuotationItem
from sales.pagination import CustomPageNumberPagination
from sales.serializers import (
    CustomerListSerializer,
    CustomerSerializer,
    PaymentReceivedSerializer,
    SalesInvoiceListSerializer,
    SalesInvoiceSerializer,
    SalesItemSerializer,
    QuotationListSerializer,
    QuotationDetailSerializer,
)

SALES_PERMISSION_NOTE = (
    "Requires authentication. Allowed roles: Sales group, Admin group, "
    "or superuser."
)


class CustomerViewSet(viewsets.ModelViewSet):
    """CRUD operations for customers."""

    queryset = Customer.objects.prefetch_related("invoices").all()
    serializer_class = CustomerSerializer
    permission_classes = [IsSalesUser, OnlyAdminCanDelete]
    lookup_field = "customer_id"
    pagination_class = CustomPageNumberPagination
    filter_backends = [OrderingFilter]
    ordering_fields = "__all__"
    ordering = ["-created_at"]

    # def get_serializer_class(self):
    #     if self.action == "list":
    #         return CustomerListSerializer
    #     return CustomerSerializer

    def get_queryset(self):
        # Database level par hi single query me total sum nikalna
        qs = Customer.objects.annotate(
            annotated_total_paid=Coalesce(
                Sum('invoices__paid_amount'), 
                Decimal('0.00'), 
                output_field=DecimalField()
            )
        )
        
        name = self.request.query_params.get("name")
        customer_type = self.request.query_params.get("type")
        customer_id = self.request.query_params.get("customer_id")
        if name:
            qs = qs.filter(customer_name__icontains=name)
        if customer_type:
            qs = qs.filter(customer_type=customer_type)
        if customer_id:
            qs = qs.filter(customer_id__icontains=customer_id)
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
            openapi.Parameter(
                "customer_id", openapi.IN_QUERY,
                description="Search customers by customer_id (e.g. PR-00000, partial match)",
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

    @swagger_auto_schema(
        operation_description="Return full customer ledger with summary, transactions, and payment details.",
        manual_parameters=[
            openapi.Parameter(
                "from", openapi.IN_QUERY,
                description="Start date for filtering (YYYY-MM-DD)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "to", openapi.IN_QUERY,
                description="End date for filtering (YYYY-MM-DD)",
                type=openapi.TYPE_STRING,
            ),
        ]
    )
    @action(detail=True, methods=["get"], url_path="ledger")
    def ledger(self, request, **kwargs):
        customer = self.get_object()
        
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        
        if from_date and to_date:
            prior_invoices = customer.invoices.filter(status='Saved', date__lt=from_date)
            prior_payments = customer.payments.filter(date__lt=from_date)
            balance_before_range = Decimal(str(customer.opening_credit or '0.00'))
            for inv in prior_invoices:
                balance_before_range += Decimal(str(inv.net_total))
            for pay in prior_payments:
                balance_before_range -= Decimal(str(pay.amount_received))
            for inv in prior_invoices:
                balance_before_range -= Decimal(str(inv.advance_applied))
            opening_credit_for_range = balance_before_range
            opening_desc = "Balance Brought Forward"
            
            invoices = customer.invoices.filter(status="Saved", date__range=[from_date, to_date]).order_by("date", "id")
            payments = customer.payments.filter(date__range=[from_date, to_date]).order_by("date", "id")
        else:
            opening_credit_for_range = Decimal(str(customer.opening_credit or '0.00'))
            opening_desc = "Opening Balance"
            
            invoices = customer.invoices.filter(status="Saved").order_by("date", "id")
            payments = customer.payments.all().order_by("date", "id")

        opening_credit = Decimal(str(opening_credit_for_range))

        all_invoices = list(invoices.select_related("customer").prefetch_related("items"))
        all_payments = list(payments.select_related("invoice"))

        credit_sales = sum((Decimal(str(inv.net_total)) for inv in all_invoices if inv.payment_term == "Credit"), Decimal('0.00'))
        cash_return = sum((Decimal(str(inv.paid_amount)) for inv in all_invoices if inv.payment_term == "Cash"), Decimal('0.00'))
        total_paid = sum((Decimal(str(pay.amount_received)) for pay in all_payments), Decimal('0.00'))
        total_purchases = sum((Decimal(str(inv.net_total)) for inv in all_invoices), Decimal('0.00'))
        total_invoices = len(all_invoices)
        total_advance_applied = sum((Decimal(str(inv.advance_applied)) for inv in all_invoices), Decimal('0.00'))

        ledger_rows = []

        if opening_credit != Decimal('0.00'):
            opening_date = customer.created_at.date() if customer.created_at else None
            ledger_rows.append({
                "date": opening_date.isoformat() if opening_date else None,
                "voucher": "OPENING",
                "description": opening_desc,
                "referenceType": None,
                "referenceId": None,
                "debit": opening_credit,
                "credit": Decimal('0.00'),
                "balance": Decimal('0.00'),
                "_sort_ts": customer.created_at,
            })

        for inv in all_invoices:
            net = Decimal(str(inv.net_total))
            ledger_rows.append({
                "date": inv.date.isoformat() if inv.date else None,
                "voucher": inv.invoice_number,
                "description": f"Invoice - {inv.payment_term}",
                "referenceType": "invoice",
                "referenceId": inv.id,
                "debit": net,
                "credit": Decimal('0.00'),
                "balance": Decimal('0.00'),
                "_sort_ts": inv.created_at,
            })
            if inv.advance_applied > 0:
                ledger_rows.append({
                    "date": inv.date.isoformat() if inv.date else None,
                    "voucher": f"ADV-{inv.invoice_number}",
                    "description": "Advance Applied",
                    "referenceType": "invoice",
                    "referenceId": inv.id,
                    "debit": Decimal('0.00'),
                    "credit": Decimal(str(inv.advance_applied)),
                    "balance": Decimal('0.00'),
                    "_sort_ts": inv.created_at,
                })

        for pay in all_payments:
            description = f"Payment - {pay.invoice.invoice_number}" if pay.invoice else "General Payment"
            ledger_rows.append({
                "date": pay.date.isoformat() if pay.date else None,
                "voucher": pay.receipt_number,
                "description": description,
                "referenceType": "payment",
                "referenceId": pay.id,
                "debit": Decimal('0.00'),
                "credit": Decimal(str(pay.amount_received)),
                "balance": Decimal('0.00'),
                "_sort_ts": pay.created_at,
            })

        ledger_rows.sort(key=lambda r: r['_sort_ts'])

        running_balance = Decimal('0.00')
        for row in ledger_rows:
            # FIX: Ensure strict Decimal casting right before math operations
            debit_val = Decimal(str(row['debit']))
            credit_val = Decimal(str(row['credit']))
            running_balance += debit_val - credit_val
            row['balance'] = running_balance

        final_balance = ledger_rows[-1]['balance'] if ledger_rows else Decimal('0.00')

        if final_balance >= 0:
            remaining_balance = final_balance
            available_advance = Decimal('0.00')
        else:
            remaining_balance = Decimal('0.00')
            available_advance = abs(final_balance)

        summary = {
            "creditSales": credit_sales,
            "cashReturn": cash_return,
            "advanceApplied": total_advance_applied,
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
            "advanceUsed": total_advance_applied,
            "totalCollected": total_paid,
            "availableAdvance": available_advance,
            "remainingBalance": remaining_balance,
        }

        for row in ledger_rows:
            row.pop('_sort_ts', None)

        customer_info = {
            "customerId": customer.customer_id,
            "customerName": customer.customer_name,
            "phone": customer.phone,
            "customerType": customer.customer_type,
        }

        customer_invoices = list(customer.invoices.filter(status="Saved").values("id", "invoice_number"))

        return Response({
            "customer": customer_info,
            "invoices": customer_invoices,
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
    permission_classes = [IsSalesUser, OnlyAdminCanDelete]

    def get_serializer_class(self):
        if self.action == "list":
            return SalesInvoiceListSerializer
        return SalesInvoiceSerializer
    pagination_class = CustomPageNumberPagination
    filter_backends = [OrderingFilter]
    ordering_fields = "__all__"
    ordering = ["-date", "-id"]

    def get_queryset(self):
        qs = super().get_queryset()
        name = self.request.query_params.get("name")
        invoice_number = self.request.query_params.get("invoice_number")
        customer_id = self.request.query_params.get("customer_id")
        customer_type = self.request.query_params.get("type")
        status = self.request.query_params.get("status")
        start_date = self.request.query_params.get("start_date")
        end_date = self.request.query_params.get("end_date")

        if name:
            qs = qs.filter(customer__customer_name__icontains=name)
        if invoice_number:
            qs = qs.filter(invoice_number__icontains=invoice_number)
        if customer_id:
            qs = qs.filter(customer__customer_id=customer_id)
        if status:
            qs = qs.filter(status=status)
            
        if start_date and end_date:
            qs = qs.filter(date__range=[start_date, end_date])
        elif start_date:
            qs = qs.filter(date__gte=start_date)
        elif end_date:
            qs = qs.filter(date__lte=end_date)

        if customer_type == 'walkin':
            qs = qs.filter(customer__isnull=True)
        elif customer_type == 'permanent':
            qs = qs.filter(customer__customer_type='permanent')
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
                "invoice_number", openapi.IN_QUERY,
                description="Search invoices by invoice number (case-insensitive, partial match)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "customer_id", openapi.IN_QUERY,
                description="Search invoices by customer_id (exact match)",
                type=openapi.TYPE_INTEGER,
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
            openapi.Parameter(
                "status", openapi.IN_QUERY,
                description="Filter by invoice status (e.g. 'Saved', 'Draft')",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "start_date", openapi.IN_QUERY,
                description="Start date for filtering (YYYY-MM-DD)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "end_date", openapi.IN_QUERY,
                description="End date for filtering (YYYY-MM-DD)",
                type=openapi.TYPE_STRING,
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description=(
            SALES_PERMISSION_NOTE +
            "\n\ncustomer_data must be an object: "
            '{"customer_id": null, "customer_name": "Raza Khan", '
            '"phone": "03001234567", "customer_type": "walkin", '
            '"tax_number": null}. '
            "The phone number is used to look up any existing customer "
            "(of any type) — if found, the invoice links to that existing "
            "customer; otherwise a new walk-in customer is created. "
            "customer_type must always be 'walkin' in this payload — "
            "invoice creation cannot create permanent customers."
        )
    )
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

        # FIX: Atomic transaction block
        with transaction.atomic():
            if invoice.status == 'Saved':
                if invoice.customer and invoice.payment_term == 'Credit':
                    invoice.customer.refresh_from_db(fields=['credit_balance'])
                    invoice.customer.credit_balance -= invoice.balance_due
                    invoice.customer.save(update_fields=['credit_balance'])

                if invoice.customer and invoice.advance_applied > 0:
                    invoice.customer.refresh_from_db(fields=['advance_balance'])
                    invoice.customer.advance_balance += invoice.advance_applied
                    invoice.customer.save(update_fields=['advance_balance'])

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
        # FIX: Atomic transaction block
        with transaction.atomic():
            invoice = SalesInvoice.all_objects.filter(id=pk, is_deleted=True).first()
            if not invoice:
                return Response({"error": "Not found in trash."}, status=drf_status.HTTP_404_NOT_FOUND)

            if invoice.status == 'Saved':
                if invoice.customer and invoice.payment_term == 'Credit':
                    invoice.customer.refresh_from_db(fields=['credit_balance'])
                    invoice.customer.credit_balance += invoice.balance_due
                    invoice.customer.save(update_fields=['credit_balance'])

                if invoice.customer and invoice.advance_applied > 0:
                    invoice.customer.refresh_from_db(fields=['advance_balance'])
                    invoice.customer.advance_balance -= invoice.advance_applied
                    invoice.customer.save(update_fields=['advance_balance'])

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

    @swagger_auto_schema(operation_description="Get all invoices with full nested items array (Future-Proof/Export API)")
    @action(detail=False, methods=['get'], url_path='all-with-items')
    def all_with_items(self, request):
        # Filtering aur ordering automatic standard viewset ki apply hogi
        queryset = self.filter_queryset(self.get_queryset())
        
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = SalesInvoiceSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
            
        serializer = SalesInvoiceSerializer(queryset, many=True)
        return Response(serializer.data)


class SalesItemViewSet(viewsets.ModelViewSet):
    """CRUD operations for standalone sales line items."""

    queryset = SalesItem.objects.select_related("invoice")
    serializer_class = SalesItemSerializer
    permission_classes = [IsSalesUser, OnlyAdminCanDelete]

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
    permission_classes = [IsSalesUser, OnlyAdminCanDelete]
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
        
        # FIX: Atomic execution for balance safety
        with transaction.atomic():
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
        # FIX: Atomic execution
        with transaction.atomic():
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


class QuotationViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing sales quotations.
    """
    permission_classes = [IsSalesUser, OnlyAdminCanDelete]
    pagination_class = CustomPageNumberPagination

    filter_backends = [OrderingFilter]
    ordering_fields = "__all__"
    ordering = ["-date", "-id"]

    def get_queryset(self):
        # We compute effective_status on read and use annotations if needed for filtering, 
        # or we just filter using python logic if needed, but normally we just query DB.
        queryset = Quotation.all_objects.all() if self.request.user.is_superuser else Quotation.objects.all()
        
        status = self.request.query_params.get('status')
        customer_name = self.request.query_params.get('customer_name')
        quotation_number = self.request.query_params.get('quotation_number')
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')

        if status:
            queryset = queryset.filter(status=status)
        if customer_name:
            queryset = queryset.filter(customer_data__customer_name__icontains=customer_name)
        if quotation_number:
            queryset = queryset.filter(quotation_number__icontains=quotation_number)
            
        if start_date and end_date:
            queryset = queryset.filter(date__range=[start_date, end_date])
        elif start_date:
            queryset = queryset.filter(date__gte=start_date)
        elif end_date:
            queryset = queryset.filter(date__lte=end_date)
            
        return queryset

    @swagger_auto_schema(
        operation_description="API endpoint for managing sales quotations.",
        manual_parameters=[
            openapi.Parameter(
                "customer_name", openapi.IN_QUERY,
                description="Search quotations by customer name (case-insensitive, partial match)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "quotation_number", openapi.IN_QUERY,
                description="Search quotations by quotation number (case-insensitive, partial match)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "status", openapi.IN_QUERY,
                description="Filter by quotation status",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "start_date", openapi.IN_QUERY,
                description="Start date for filtering (YYYY-MM-DD)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "end_date", openapi.IN_QUERY,
                description="End date for filtering (YYYY-MM-DD)",
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
                description="Sort field. Prefix with '-' for descending.",
                type=openapi.TYPE_STRING,
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_serializer_class(self):
        if self.action == 'list':
            from sales.serializers import QuotationListSerializer
            return QuotationListSerializer
        from sales.serializers import QuotationDetailSerializer
        return QuotationDetailSerializer

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status == 'converted':
            return Response(
                {"detail": "Converted quotations cannot be deleted."}, 
                status=drf_status.HTTP_400_BAD_REQUEST
            )
        return super().destroy(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status == 'converted':
            return Response(
                {"detail": "Converted quotations cannot be edited."},
                status=drf_status.HTTP_400_BAD_REQUEST
            )
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def convert(self, request, pk=None):
        from django.utils import timezone
        
        # We start atomic and then select_for_update to prevent double conversion
        with transaction.atomic():
            # Use select_for_update to lock the row and prevent race conditions
            quotation = Quotation.objects.select_for_update().filter(pk=pk).first()
            if not quotation:
                return Response({"detail": "Not found."}, status=404)
            
            if quotation.status == 'converted':
                return Response(
                    {"detail": "Quotation is already converted."}, 
                    status=drf_status.HTTP_400_BAD_REQUEST
                )
                
            if quotation.is_expired:
                return Response(
                    {"detail": "This quotation has expired and cannot be converted."}, 
                    status=drf_status.HTTP_400_BAD_REQUEST
                )

            # Look up or create the customer for the invoice
            from sales.utils import get_or_create_customer_from_data
            customer = get_or_create_customer_from_data(quotation.customer_data)
            
            # Map Quotation fields to SalesInvoice fields exactly
            invoice = SalesInvoice.objects.create(
                customer=customer,
                date=timezone.localdate(),
                payment_term=quotation.payment_term,
                # Explicit Mappings:
                invoice_discount=quotation.discount_percentage,
                vat_percentage=quotation.vat_percentage,
                status='Draft'  # Draft status as safety
            )
            
            for q_item in quotation.items.all():
                SalesItem.objects.create(
                    invoice=invoice,
                    item_name=q_item.item_name,
                    rate=q_item.rate,
                    # Explicit Mappings:
                    units=q_item.unit,
                    quantity=q_item.qty,
                    discount=q_item.discount
                )
                
            quotation.status = 'converted'
            quotation.converted_invoice = invoice
            quotation.save(update_fields=['status', 'converted_invoice', 'updated_at'])
            
            from sales.serializers import QuotationDetailSerializer, SalesInvoiceSerializer
            
            # Return both records
            return Response({
                "detail": "Quotation successfully converted to invoice.",
                "quotation": QuotationDetailSerializer(quotation).data,
                "invoice": SalesInvoiceSerializer(invoice).data
            }, status=drf_status.HTTP_201_CREATED)
