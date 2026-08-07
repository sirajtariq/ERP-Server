"""
Purchase module API viewsets with RBAC enforcement.
"""

from django.db import IntegrityError
from django.db.models import DecimalField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework import status as drf_status
from rest_framework.exceptions import ValidationError as serializers_ValidationError

from django.db import transaction
from djangorestframework_camel_case.render import CamelCaseJSONRenderer
from djangorestframework_camel_case.parser import CamelCaseJSONParser, CamelCaseMultiPartParser, CamelCaseFormParser
from decimal import Decimal

class PurchaseCamelCaseMixin:
    renderer_classes = [CamelCaseJSONRenderer]
    parser_classes = [CamelCaseJSONParser, CamelCaseMultiPartParser, CamelCaseFormParser]

from erp_backend.permissions import IsPurchaseUser, OnlyAdminCanDelete
from purchase.models import Expense, PurchaseInvoice, PurchaseItem, Vendor, VendorPayment
from purchase.serializers import (
    ExpenseSerializer,
    PurchaseInvoiceListSerializer,
    PurchaseInvoiceSerializer,
    PurchaseItemSerializer,
    VendorListSerializer,
    VendorPaymentSerializer,
    VendorSerializer,
)
from sales.pagination import CustomPageNumberPagination

PURCHASE_PERMISSION_NOTE = (
    "Requires authentication. Allowed roles: Purchase group, Admin group, "
    "or superuser."
)


class VendorViewSet(PurchaseCamelCaseMixin, viewsets.ModelViewSet):
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
        
        if self.action == "list":
            active_invoices_prefetch = Prefetch(
                "invoices",
                queryset=PurchaseInvoice.objects.filter(is_deleted=False).order_by("-date").prefetch_related("items")
            )
            qs = qs.prefetch_related(active_invoices_prefetch).annotate(
                total_paid=Coalesce(
                    Sum('invoices__paid_amount', filter=Q(invoices__is_deleted=False)),
                    Value(0, output_field=DecimalField()),
                    output_field=DecimalField()
                )
            )

        name = self.request.query_params.get("name")
        vendor_id = self.request.query_params.get("vendor_id")
        if name:
            qs = qs.filter(vendor_name__icontains=name)
        if vendor_id:
            qs = qs.filter(vendor_id__icontains=vendor_id)
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return VendorListSerializer
        return super().get_serializer_class()

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
                            "E.g. 'vendor_name', '-created_at', 'payable_balance'",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "vendor_id", openapi.IN_QUERY,
                description="Search vendors by vendor_id (e.g. VN-00000, partial match)",
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

    @swagger_auto_schema(
        operation_description="Return full vendor ledger with summary, transactions, and payment details.",
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
        vendor = self.get_object()
        
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        
        if from_date and to_date:
            prior_invoices = vendor.invoices.filter(is_deleted=False, status='Saved', date__lt=from_date)
            prior_payments = vendor.payments.filter(is_deleted=False, date__lt=from_date)
            balance_before_range = Decimal(str(vendor.opening_payable or '0.00'))
            for inv in prior_invoices:
                balance_before_range += Decimal(str(inv.net_total))
            for pay in prior_payments:
                balance_before_range -= Decimal(str(pay.amount_paid))
            for inv in prior_invoices:
                balance_before_range -= Decimal(str(inv.advance_applied))
            opening_payable_for_range = balance_before_range
            opening_desc = "Balance Brought Forward"
            
            invoices = vendor.invoices.filter(is_deleted=False, status="Saved", date__range=[from_date, to_date]).order_by("date", "id")
            payments = vendor.payments.filter(is_deleted=False, date__range=[from_date, to_date]).order_by("date", "id")
        else:
            opening_payable_for_range = Decimal(str(vendor.opening_payable or '0.00'))
            opening_desc = "Opening Balance"
            
            invoices = vendor.invoices.filter(is_deleted=False, status="Saved").order_by("date", "id")
            payments = vendor.payments.filter(is_deleted=False).order_by("date", "id")

        opening_payable = Decimal(str(opening_payable_for_range))

        all_invoices = list(invoices.select_related("vendor").prefetch_related("items"))
        all_payments = list(payments.select_related("invoice"))

        credit_purchases = sum((Decimal(str(inv.net_total)) for inv in all_invoices if inv.payment_term == "Credit"), Decimal('0.00'))
        cash_purchases = sum((Decimal(str(inv.paid_amount)) for inv in all_invoices if inv.payment_term == "Cash"), Decimal('0.00'))
        total_paid_out = sum((Decimal(str(pay.amount_paid)) for pay in all_payments), Decimal('0.00'))
        total_purchases_amt = sum((Decimal(str(inv.net_total)) for inv in all_invoices), Decimal('0.00'))
        total_invoices = len(all_invoices)
        total_advance_applied = sum((Decimal(str(inv.advance_applied)) for inv in all_invoices), Decimal('0.00'))

        ledger_rows = []

        if opening_payable != Decimal('0.00'):
            opening_date = vendor.created_at.date() if vendor.created_at else None
            ledger_rows.append({
                "date": opening_date.isoformat() if opening_date else None,
                "voucher": "OPENING",
                "description": opening_desc,
                "referenceType": None,
                "referenceId": None,
                "debit": Decimal('0.00'),
                "credit": opening_payable,
                "balance": Decimal('0.00'),
                "_sort_ts": vendor.created_at,
            })

        for inv in all_invoices:
            net = Decimal(str(inv.net_total))
            ledger_rows.append({
                "date": inv.date.isoformat() if inv.date else None,
                "voucher": inv.invoice_number,
                "description": f"Invoice - {inv.payment_term}",
                "referenceType": "invoice",
                "referenceId": inv.id,
                "debit": Decimal('0.00'),
                "credit": net,
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
                    "debit": Decimal(str(inv.advance_applied)),
                    "credit": Decimal('0.00'),
                    "balance": Decimal('0.00'),
                    "_sort_ts": inv.created_at,
                })

        for pay in all_payments:
            description = f"Payment - {pay.invoice.invoice_number}" if pay.invoice else "General Payment"
            ledger_rows.append({
                "date": pay.date.isoformat() if pay.date else None,
                "voucher": pay.payment_number,
                "description": description,
                "referenceType": "payment",
                "referenceId": pay.id,
                "debit": Decimal(str(pay.amount_paid)),
                "credit": Decimal('0.00'),
                "balance": Decimal('0.00'),
                "_sort_ts": pay.created_at,
            })

        ledger_rows.sort(key=lambda r: r['_sort_ts'])

        running_balance = Decimal('0.00')
        for row in ledger_rows:
            debit_val = Decimal(str(row['debit']))
            credit_val = Decimal(str(row['credit']))
            running_balance += credit_val - debit_val
            row['balance'] = running_balance

        final_balance = ledger_rows[-1]['balance'] if ledger_rows else Decimal('0.00')

        if final_balance >= 0:
            remaining_balance = final_balance
            available_advance = Decimal('0.00')
        else:
            remaining_balance = Decimal('0.00')
            available_advance = abs(final_balance)

        summary = {
            "creditPurchases": credit_purchases,
            "cashPurchases": cash_purchases,
            "advanceApplied": total_advance_applied,
            "totalPaid": total_paid_out,
            "remainingBalance": remaining_balance,
            "totalInvoices": total_invoices,
            "openingPayable": opening_payable,
            "availableAdvance": available_advance,
            "closingBalance": remaining_balance,
        }

        final_payment_details = {
            "openingPayable": opening_payable,
            "totalPurchases": total_purchases_amt,
            "paymentsMade": total_paid_out,
            "advanceUsed": total_advance_applied,
            "totalPaid": total_paid_out,
            "availableAdvance": available_advance,
            "remainingBalance": remaining_balance,
        }

        for row in ledger_rows:
            row.pop('_sort_ts', None)

        vendor_info = {
            "vendorId": vendor.vendor_id,
            "vendorName": vendor.vendor_name,
            "phone": vendor.phone,
        }

        vendor_invoices = list(vendor.invoices.filter(status="Saved").values("id", "invoice_number"))

        return Response({
            "vendor": vendor_info,
            "invoices": vendor_invoices,
            "summary": summary,
            "ledger": ledger_rows,
            "finalPaymentDetails": final_payment_details,
        })


class PurchaseInvoiceViewSet(PurchaseCamelCaseMixin, viewsets.ModelViewSet):
    """
    CRUD operations for purchase invoices.

    Responses include nested ``items`` for each invoice.
    """

    queryset = PurchaseInvoice.objects.select_related("vendor").prefetch_related(
        "items"
    )
    serializer_class = PurchaseInvoiceSerializer
    permission_classes = [IsPurchaseUser, OnlyAdminCanDelete]
    pagination_class = CustomPageNumberPagination
    filter_backends = [OrderingFilter]
    ordering_fields = "__all__"
    ordering = ["-date", "-id"]

    def get_serializer_class(self):
        if self.action == "list":
            return PurchaseInvoiceListSerializer
        return PurchaseInvoiceSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        vendor = self.request.query_params.get("vendor")
        bill_number = self.request.query_params.get("bill_number")
        invoice_number = self.request.query_params.get("invoice_number")
        status_filter = self.request.query_params.get("status")
        payment_term = self.request.query_params.get("payment_term")

        if vendor:
            qs = qs.filter(vendor__vendor_id=vendor)
        if bill_number:
            qs = qs.filter(bill_number__icontains=bill_number)
        if invoice_number:
            qs = qs.filter(invoice_number__icontains=invoice_number)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if payment_term:
            qs = qs.filter(payment_term=payment_term)

        return qs

    @swagger_auto_schema(
        operation_description=PURCHASE_PERMISSION_NOTE,
        manual_parameters=[
            openapi.Parameter("vendor", openapi.IN_QUERY, description="Filter by vendor_id", type=openapi.TYPE_INTEGER),
            openapi.Parameter("bill_number", openapi.IN_QUERY, description="Filter by bill_number (icontains)", type=openapi.TYPE_STRING),
            openapi.Parameter("invoice_number", openapi.IN_QUERY, description="Filter by invoice_number (icontains)", type=openapi.TYPE_STRING),
            openapi.Parameter("status", openapi.IN_QUERY, description="Filter by status", type=openapi.TYPE_STRING),
            openapi.Parameter("payment_term", openapi.IN_QUERY, description="Filter by payment_term", type=openapi.TYPE_STRING),
            openapi.Parameter("page", openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter("page_size", openapi.IN_QUERY, description="Results per page", type=openapi.TYPE_INTEGER),
            openapi.Parameter("ordering", openapi.IN_QUERY, description="Sort field. E.g. '-date', 'net_total', 'balance_due'", type=openapi.TYPE_STRING),
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
        invoice = self.get_object()

        with transaction.atomic():
            if invoice.status == 'Saved':
                serializer = self.get_serializer()
                serializer._reverse_invoice_balance_effects(invoice)

            invoice.soft_delete()

        return Response(
            {"message": "Invoice moved to trash. Vendor balance adjusted."},
            status=drf_status.HTTP_200_OK
        )

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=False, methods=['get'], url_path='trash')
    def trash(self, request):
        deleted_invoices = PurchaseInvoice.all_objects.filter(is_deleted=True)
        page = self.paginate_queryset(deleted_invoices)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, pk=None):
        with transaction.atomic():
            invoice = PurchaseInvoice.all_objects.filter(id=pk, is_deleted=True).first()
            if not invoice:
                return Response({"error": "Not found in trash."}, status=drf_status.HTTP_404_NOT_FOUND)

            if invoice.status == 'Saved':
                serializer = self.get_serializer()
                serializer._apply_invoice_balance_effects(invoice, Decimal('0.00'))

            invoice.restore()

        return Response({"message": "Invoice restored, balance re-applied."})

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=True, methods=['delete'], url_path='permanent-delete')
    def permanent_delete(self, request, pk=None):
        if not request.user.is_superuser:
            return Response(
                {"error": "Only superuser can permanently delete."},
                status=drf_status.HTTP_403_FORBIDDEN
            )
        invoice = PurchaseInvoice.all_objects.filter(
            id=pk, is_deleted=True
        ).first()
        if not invoice:
            return Response(
                {"error": "Not found in trash."},
                status=drf_status.HTTP_404_NOT_FOUND
            )
        invoice.delete()
        return Response({"message": "Permanently deleted."})


class PurchaseItemViewSet(PurchaseCamelCaseMixin, viewsets.ModelViewSet):
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


class VendorPaymentViewSet(PurchaseCamelCaseMixin, viewsets.ModelViewSet):
    """CRUD operations for vendor payments."""

    queryset = VendorPayment.objects.select_related("vendor", "invoice")
    serializer_class = VendorPaymentSerializer
    permission_classes = [IsPurchaseUser, OnlyAdminCanDelete]
    pagination_class = CustomPageNumberPagination
    filter_backends = [OrderingFilter]
    ordering_fields = "__all__"
    ordering = ["-date", "-id"]

    def get_queryset(self):
        qs = super().get_queryset()
        vendor = self.request.query_params.get("vendor")
        invoice = self.request.query_params.get("invoice")

        if vendor:
            qs = qs.filter(vendor__vendor_id=vendor)
        if invoice:
            qs = qs.filter(invoice__invoice_number=invoice)
            
        return qs

    @swagger_auto_schema(
        operation_description=PURCHASE_PERMISSION_NOTE,
        manual_parameters=[
            openapi.Parameter("vendor", openapi.IN_QUERY, description="Filter by vendor_id", type=openapi.TYPE_INTEGER),
            openapi.Parameter("invoice", openapi.IN_QUERY, description="Filter by invoice_number", type=openapi.TYPE_STRING),
            openapi.Parameter("page", openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter("page_size", openapi.IN_QUERY, description="Results per page", type=openapi.TYPE_INTEGER),
            openapi.Parameter("ordering", openapi.IN_QUERY, description="Sort field", type=openapi.TYPE_STRING),
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
        payment = self.get_object()

        with transaction.atomic():
            serializer = self.get_serializer()
            serializer._reverse_payment(
                payment.vendor,
                payment.invoice,
                payment.applied_to_invoice,
                payment.applied_to_payable,
                payment.applied_to_advance,
            )
            payment.soft_delete()

        return Response(
            {"message": "Payment moved to trash. Vendor balance adjusted."},
            status=drf_status.HTTP_200_OK
        )

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=False, methods=['get'], url_path='trash')
    def trash(self, request):
        deleted_payments = VendorPayment.all_objects.filter(is_deleted=True)
        page = self.paginate_queryset(deleted_payments)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, pk=None):
        with transaction.atomic():
            payment = VendorPayment.all_objects.filter(id=pk, is_deleted=True).first()
            if not payment:
                return Response({"error": "Not found in trash."}, status=drf_status.HTTP_404_NOT_FOUND)

            serializer = self.get_serializer()
            serializer._apply_payment(
                payment.vendor,
                payment.amount_paid,
                payment.invoice
            )
            payment.restore()

        return Response({"message": "Payment restored, balance re-applied."})

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=True, methods=['delete'], url_path='permanent-delete')
    def permanent_delete(self, request, pk=None):
        if not request.user.is_superuser:
            return Response(
                {"error": "Only superuser can permanently delete."},
                status=drf_status.HTTP_403_FORBIDDEN
            )
        payment = VendorPayment.all_objects.filter(
            id=pk, is_deleted=True
        ).first()
        if not payment:
            return Response(
                {"error": "Not found in trash."},
                status=drf_status.HTTP_404_NOT_FOUND
            )
        payment.delete()
        return Response({"message": "Permanently deleted."})


class ExpenseViewSet(PurchaseCamelCaseMixin, viewsets.ModelViewSet):
    """CRUD operations for standalone expenses."""

    queryset = Expense.objects.all()
    serializer_class = ExpenseSerializer
    permission_classes = [IsPurchaseUser, OnlyAdminCanDelete]
    pagination_class = CustomPageNumberPagination
    filter_backends = [OrderingFilter]
    ordering_fields = "__all__"
    ordering = ["-date", "-id"]

    def get_queryset(self):
        qs = super().get_queryset()
        category = self.request.query_params.get("category")
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")

        if category:
            qs = qs.filter(category__icontains=category)
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)

        return qs

    @swagger_auto_schema(
        operation_description=PURCHASE_PERMISSION_NOTE,
        manual_parameters=[
            openapi.Parameter("category", openapi.IN_QUERY, description="Filter by category (icontains)", type=openapi.TYPE_STRING),
            openapi.Parameter("date_from", openapi.IN_QUERY, description="Filter by date >= YYYY-MM-DD", type=openapi.TYPE_STRING),
            openapi.Parameter("date_to", openapi.IN_QUERY, description="Filter by date <= YYYY-MM-DD", type=openapi.TYPE_STRING),
            openapi.Parameter("page", openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter("page_size", openapi.IN_QUERY, description="Results per page", type=openapi.TYPE_INTEGER),
            openapi.Parameter("ordering", openapi.IN_QUERY, description="Sort field", type=openapi.TYPE_STRING),
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
            {"message": "Expense moved to trash."},
            status=drf_status.HTTP_200_OK
        )

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=False, methods=['get'], url_path='trash')
    def trash(self, request):
        deleted_expenses = Expense.all_objects.filter(is_deleted=True)
        page = self.paginate_queryset(deleted_expenses)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, pk=None):
        expense = Expense.all_objects.filter(id=pk, is_deleted=True).first()
        if not expense:
            return Response({"error": "Not found in trash."}, status=drf_status.HTTP_404_NOT_FOUND)
        expense.restore()
        return Response({"message": "Expense restored."})

    @swagger_auto_schema(operation_description=PURCHASE_PERMISSION_NOTE)
    @action(detail=True, methods=['delete'], url_path='permanent-delete')
    def permanent_delete(self, request, pk=None):
        if not request.user.is_superuser:
            return Response(
                {"error": "Only superuser can permanently delete."},
                status=drf_status.HTTP_403_FORBIDDEN
            )
        expense = Expense.all_objects.filter(id=pk, is_deleted=True).first()
        if not expense:
            return Response(
                {"error": "Not found in trash."},
                status=drf_status.HTTP_404_NOT_FOUND
            )
        expense.delete()
        return Response({"message": "Permanently deleted."})
