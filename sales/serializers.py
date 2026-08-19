"""
DRF serializers for the sales module.

SalesInvoiceSerializer exposes nested line items on read and write so a
single request can create or replace an invoice together with its items.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from sales.models import Customer, PaymentReceived, SalesInvoice, SalesItem, Quotation, QuotationItem, SalesReturn, SalesReturnItem


class CustomerListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for customer list views (no nested invoices)."""

    customerId = serializers.CharField(source="customer_id", read_only=True)
    customerName = serializers.CharField(source="customer_name", read_only=True)
    customerType = serializers.CharField(source="customer_type", read_only=True)
    Phone = serializers.CharField(source="phone", read_only=True)
    creditBalance = serializers.SerializerMethodField()
    advanceBalance = serializers.SerializerMethodField()
    totalPaid = serializers.DecimalField(source="annotated_total_paid", max_digits=12, decimal_places=2, read_only=True)
    totalDue = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = [
            "id",
            "customerId",
            "customerName",
            "customerType",
            "Phone",
            "creditBalance",
            "advanceBalance",
            "totalPaid",
            "totalDue",
        ]
        read_only_fields = fields

    def get_creditBalance(self, obj):
        return obj.credit_balance

    def get_advanceBalance(self, obj):
        return obj.advance_balance

    def get_totalDue(self, obj):
        return obj.credit_balance


class CustomerInvoiceNestedSerializer(serializers.ModelSerializer):
    """Lightweight invoice serializer nested inside Customer responses."""

    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    net_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = SalesInvoice
        fields = [
            "id",
            "invoice_number",
            "date",
            "payment_term",
            "status",
            "subtotal",
            "net_total",
            "balance_due",
        ]
        read_only_fields = fields


class CustomerSerializer(serializers.ModelSerializer):
    """Serializer for customer master data with nested invoices."""
    
    customerId = serializers.CharField(source="customer_id", read_only=True)
    customerName = serializers.CharField(source="customer_name")
    customerType = serializers.ChoiceField(source="customer_type",choices=['permanent', 'walkin'],required=True)
    Phone = serializers.CharField(source="phone",required=True, validators=[UniqueValidator(queryset=Customer.objects.all(),message="Customer with this phone number already exists.")],)
    Address = serializers.CharField(source="address", required=False, allow_blank=True)
    openingCredit = serializers.DecimalField(source="opening_credit", max_digits=12, decimal_places=2, required=False, allow_null=True)
    openingNote = serializers.CharField(source="opening_note", required=False, allow_blank=True)
    taxNumber = serializers.CharField(source="tax_number", required=False, allow_null=True,allow_blank=True)
    creditBalance = serializers.SerializerMethodField()
    advanceBalance = serializers.SerializerMethodField()
    totalPaid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)
    invoices = CustomerInvoiceNestedSerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = ["id", "customerId", "customerName", "customerType", "Phone", "email", "Address", "openingCredit", "openingNote", "taxNumber", "creditBalance", "advanceBalance", "totalPaid", "createdAt", "updatedAt", "invoices"]
        read_only_fields = ["id", "customerId", "creditBalance", "advanceBalance","totalPaid", "createdAt", "updatedAt", "invoices"]

    def get_creditBalance(self, obj):
        return obj.credit_balance

    def get_advanceBalance(self, obj):
        return obj.advance_balance

    def get_totalPaid(self, obj):
        result = obj.payments.aggregate(total=Sum("amount_received"))
        return result["total"] or Decimal('0.00')

    def create(self, validated_data):
        opening = validated_data.get('opening_credit') or Decimal('0.00')
        validated_data['credit_balance'] = opening
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if 'opening_credit' in validated_data:
            old_opening = instance.opening_credit or Decimal('0.00')
            new_opening = validated_data.get('opening_credit') or Decimal('0.00')
            diff = new_opening - old_opening
            instance.credit_balance = instance.credit_balance + diff
            if instance.credit_balance < Decimal('0.00'):
                rem_neg = abs(instance.credit_balance)
                instance.credit_balance = Decimal('0.00')
                instance.advance_balance = max(Decimal('0.00'), instance.advance_balance - rem_neg)
        return super().update(instance, validated_data)


class SalesItemSerializer(serializers.ModelSerializer):
    """Serializer for standalone sales invoice line items."""
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    isReturned = serializers.SerializerMethodField()

    class Meta:
        model = SalesItem
        fields = ["id", "invoice", "item_name", "units", "quantity", "rate", "discount", "total", "isReturned"]
        read_only_fields = ["id", "total"]

    def get_isReturned(self, obj):
        return bool(obj.return_items.all())


class SalesItemNestedSerializer(serializers.ModelSerializer):
    """Nested line item serializer (invoice is set by the parent invoice)."""
    itemId = serializers.IntegerField(source='product_id', required=False, allow_null=True)
    itemCode = serializers.CharField(source='item_code', required=False, allow_null=True, allow_blank=True)
    name = serializers.CharField(source='item_name', required=False, allow_blank=True)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, required=True)
    unitPrice = serializers.DecimalField(source='rate', max_digits=12, decimal_places=2, required=True)
    discount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0.00'))
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    isReturned = serializers.SerializerMethodField()

    class Meta:
        model = SalesItem
        fields = ["id", "itemId", "itemCode", "name", "units", "quantity", "unitPrice", "discount", "total", "isReturned"]
        read_only_fields = ["id", "total"]

    def get_isReturned(self, obj):
        return bool(obj.return_items.all())


def compute_payment_status(invoice):
    """
    Shared payment-status logic for both SalesInvoiceListSerializer
    and SalesInvoiceSerializer. Uses a 0.01 tolerance to absorb
    Decimal rounding noise from tax calculations.
    """
    tolerance = Decimal('0.01')
    pending = invoice.balance_due
    total_paid = invoice.paid
    net_after_returns = invoice.net_total_after_returns

    if pending <= tolerance or (total_paid >= net_after_returns - tolerance and net_after_returns > tolerance):
        return "Paid"
    elif total_paid > tolerance:
        return "Partial"
    else:
        return "Unpaid"


class SalesInvoiceListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for the invoice list table (no nested items/customer)."""

    invoiceNumber = serializers.CharField(source='invoice_number', read_only=True)
    customerName = serializers.SerializerMethodField()
    total = serializers.DecimalField(source='net_total', max_digits=12, decimal_places=2, read_only=True)
    paid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    pending = serializers.SerializerMethodField()
    paymentStatus = serializers.SerializerMethodField()
    invoiceStatus = serializers.CharField(source='status', read_only=True)
    returnedItemsCount = serializers.SerializerMethodField()
    totalReturnedAmount = serializers.DecimalField(source='total_returned_amount', max_digits=12, decimal_places=2, read_only=True)
    netTotalAfterReturns = serializers.DecimalField(source='net_total_after_returns', max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = SalesInvoice
        fields = [
            'id',
            'invoiceNumber',
            'customerName',
            'total',
            'paid',
            'pending',
            'paymentStatus',
            'invoiceStatus',
            'date',
            'returnedItemsCount',
            'totalReturnedAmount',
            'netTotalAfterReturns',
        ]
        read_only_fields = fields

    def get_returnedItemsCount(self, obj):
        return sum(1 for item in obj.items.all() if item.return_items.all())

    def get_customerName(self, obj):
        return obj.customer.customer_name if obj.customer else None

    def get_pending(self, obj):
        value = obj.balance_due
        if abs(value) < Decimal('0.01'):
            value = Decimal('0.00')
        else:
            value = value.quantize(Decimal('0.01'))
        return str(value)

    def get_paymentStatus(self, obj):
        return compute_payment_status(obj)


class CustomerDataField(serializers.Field):
    """
    Writable + readable field for the invoice's customer.

    WRITE (to_internal_value): accepts a dict with customer_name,
    phone, customer_type (must be 'walkin'), and optional
    customer_id/tax_number. Looks up an existing Customer by PHONE
    NUMBER (the authoritative match, regardless of customer_id) —
    if found, links to that existing customer (of any type); if
    not found, creates a new Customer with customer_type='walkin'.

    READ (to_representation): outputs the resolved customer's full
    info using CustomerListSerializer.
    """

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError(
                "customer_data must be an object with customer_name, phone, customer_type, and optionally customer_id/tax_number."
            )

        customer_name = (data.get('customer_name') or '').strip()
        if not customer_name:
            customer_name = "General"
        phone = (data.get('phone') or '').strip()
        customer_type = data.get('customer_type')
        tax_number = data.get('tax_number') or None

        # if not customer_name:
        #     raise serializers.ValidationError("customer_data.customer_name is required.")
        if not phone:
            raise serializers.ValidationError("customer_data.phone is required.")
            
        existing = Customer.all_objects.filter(phone=phone).first()
        if existing:
            if getattr(existing, 'is_deleted', False):
                existing.restore()
            return existing

        if customer_type != 'walkin':
            raise serializers.ValidationError(
                "customer_data.customer_type must be 'walkin' — invoice creation can only generate walk-in customers."
            )

        return {
            'is_new_customer': True,
            'customer_name': customer_name,
            'customer_type': 'walkin',
            'phone': phone,
            'tax_number': tax_number,
        }

    def to_representation(self, value):
        if not value:
            return None
        return CustomerListSerializer(value).data


class SalesInvoiceSerializer(serializers.ModelSerializer):
    items = SalesItemNestedSerializer(many=True)
    customer_data = CustomerDataField(source='customer')
    paymentStatus = serializers.SerializerMethodField()
    invoiceStatus = serializers.ChoiceField(
        source='status',
        choices=SalesInvoice.STATUS_CHOICES,
    )
    date = serializers.DateField(required=False)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_line_discount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    tax_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    net_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    returnedItemsCount = serializers.SerializerMethodField()
    totalReturnedAmount = serializers.DecimalField(source='total_returned_amount', max_digits=12, decimal_places=2, read_only=True)
    netTotalAfterReturns = serializers.DecimalField(source='net_total_after_returns', max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = SalesInvoice
        fields = [
            "id",
            "invoice_number",
            "date",
            "customer_data",
            "payment_term",
            "payment_method",
            "paid_amount",
            "payment_reference",
            "notes",
            "vat_percentage",
            "invoice_discount",
            "invoiceStatus",
            "paymentStatus",
            "items",
            "subtotal",
            "total_line_discount",
            "tax_amount",
            "net_total",
            "balance_due",
            "advance_applied",
            "returnedItemsCount",
            "totalReturnedAmount",
            "netTotalAfterReturns",
        ]
        read_only_fields = [
            "id", 
            "invoice_number", 
            "subtotal", 
            "total_line_discount", 
            "tax_amount", 
            "net_total", 
            "balance_due",
            "advance_applied",
            "paymentStatus",
            "returnedItemsCount",
            "totalReturnedAmount",
            "netTotalAfterReturns",
        ]

    def get_returnedItemsCount(self, obj):
        return sum(1 for item in obj.items.all() if item.return_items.all())

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['paid_amount'] = str(instance.paid)
        return rep

    def get_paymentStatus(self, obj):
        return compute_payment_status(obj)

    def validate(self, attrs):
        from decimal import Decimal

        if self.instance:
            has_payments = (
                self.instance.paid_amount > 0 or 
                self.instance.advance_applied > 0 or 
                self.instance.payments.exists()
            )
            new_status = attrs.get('status') or attrs.get('invoiceStatus')
            if has_payments and new_status == 'Draft':
                raise serializers.ValidationError(
                    {"status": "Cannot revert or delete Sales Invoice because payments/advances are attached to it."}
                )

        # 1. Hard Lock: Reject any modification if the invoice is already 'Saved' EXCEPT if it has zero stock movements (stuck invoice)
        if self.instance and self.instance.status == 'Saved':
            from inventory.models import StockMovement
            has_stock_movements = StockMovement.objects.filter(
                reference_type='sales_invoice',
                reference_id=self.instance.id
            ).exists()
            if has_stock_movements:
                raise serializers.ValidationError(
                    {"invoiceStatus": "Saved invoices are locked and cannot be modified. To make changes, delete the invoice to reverse balances and recreate it."}
                )

        # 2. Extract or resolve customer and payment term for creation or update instances
        customer = attrs.get('customer')
        payment_term = attrs.get('payment_term')

        if self.instance:
            if not customer:
                customer = self.instance.customer
            if not payment_term:
                payment_term = self.instance.payment_term

        if not customer:
            raise serializers.ValidationError("customer_data is required.")

        # Resolve customer type and current advance balance from DB
        if isinstance(customer, dict):
            c_type = customer.get('customer_type')
            advance_balance = Decimal('0.00')
        else:
            c_type = customer.customer_type
            advance_balance = Decimal(str(getattr(customer, 'advance_balance', 0)))

        # 3. Validation: Ensure the invoice has at least one item
        items_list = attrs.get('items')
        if items_list is None and self.instance:
            items_list = list(self.instance.items.all())
        elif items_list is None:
            items_list = []

        if len(items_list) == 0:
            raise serializers.ValidationError({"items": "An invoice must contain at least one item."})

        # 4. Line items validation and calculations
        subtotal = Decimal('0.00')
        total_line_discount = Decimal('0.00')

        for item in items_list:
            is_dict = isinstance(item, dict)
            qty = Decimal(str(item.get('quantity', 0) if is_dict else getattr(item, 'quantity', 0)))
            rate = Decimal(str(item.get('rate', 0) if is_dict else getattr(item, 'rate', 0)))
            disc = Decimal(str(item.get('discount', 0) if is_dict else getattr(item, 'discount', 0)))
            
            if qty <= 0 or rate <= 0:
                raise serializers.ValidationError({"items": "Quantity and rate must be greater than zero."})
            if disc < 0 or disc > 100:
                raise serializers.ValidationError({"items": "Line item discount percentage must be between 0 and 100."})
            
            subtotal += qty * rate
            total_line_discount += (qty * rate) * (disc / Decimal('100'))

        # 5. Header level charges and discounts calculations
        vat_percentage = Decimal(str(attrs.get('vat_percentage', self.instance.vat_percentage if self.instance else 0)))
        invoice_discount = Decimal(str(attrs.get('invoice_discount', self.instance.invoice_discount if self.instance else 0)))

        if invoice_discount < 0 or invoice_discount > 100:
            raise serializers.ValidationError({"invoice_discount": "Invoice discount percentage must be between 0 and 100."})

        # Base amount is just subtotal because items have already subtracted their discounts in item.total? Wait!
        # In the serializer, we just calculated subtotal = sum(qty * rate), and total_line_discount = sum(discount_amounts).
        # So base_amount should be subtotal - total_line_discount. This is correct for the serializer manually computing it.
        base_amount = subtotal - total_line_discount
        deducted_invoice_discount = base_amount * (invoice_discount / Decimal('100'))
        tax_amount = (base_amount - deducted_invoice_discount) * (vat_percentage / Decimal('100'))
        net_total = (base_amount - deducted_invoice_discount) + tax_amount

        paid_amount = Decimal(str(attrs.get('paid_amount', self.instance.paid_amount if self.instance else 0)))

        # 6. Business Rule enforcement per customer type and term mapping
        if c_type == 'walkin':
            if payment_term != 'Cash':
                raise serializers.ValidationError({"payment_term": "Walk-in customers can only pay via Cash."})
            if paid_amount != net_total:
                raise serializers.ValidationError(
                    f"Walk-in invoices must be paid in full. "
                    f"Expected net total: {net_total:.2f}, received paid_amount: {paid_amount:.2f}."
                )
        else:
            effective_coverage = paid_amount + advance_balance

            # Case 1: Coverage is less than bill total -> Must be Credit
            if effective_coverage < net_total and payment_term == 'Cash':
                raise serializers.ValidationError(
                    {"payment_term": f"Remaining balance detected after applying available advance ({advance_balance:.2f}). "
                                     f"Payment term must be 'Credit' for partial or unpaid balances."}
                )

            # Case 2: Coverage clears or exceeds the bill -> Must be Cash
            if effective_coverage >= net_total and payment_term == 'Credit':
                raise serializers.ValidationError(
                    {"payment_term": f"Invoice is fully covered by the paid amount and available advance ({advance_balance:.2f}). "
                                     f"Payment term must be 'Cash' as no new debt is created."}
                )

        # 7. Stock availability pre-validation if saving or transitioning to 'Saved'
        target_status = attrs.get('status') or attrs.get('invoiceStatus')
        if not target_status and self.instance:
            target_status = self.instance.status

        if target_status == 'Saved':
            from inventory.models import Item, StockMovement
            from inventory.services import get_item_current_stock
            from django.db import models

            for item in items_list:
                is_dict = isinstance(item, dict)
                name_str = item.get('item_name') if is_dict else getattr(item, 'item_name', '')
                req_qty = Decimal(str(item.get('quantity', 0) if is_dict else getattr(item, 'quantity', 0)))

                inv_item = Item.objects.filter(
                    models.Q(name__iexact=name_str) | models.Q(item_code__iexact=name_str),
                    is_deleted=False
                ).first()
                if not inv_item:
                    inv_item = Item.objects.filter(name__icontains=name_str, is_deleted=False).first()

                if inv_item:
                    avail_stock = get_item_current_stock(inv_item)
                    if self.instance and self.instance.status == 'Saved':
                        prev_movement = StockMovement.objects.filter(
                            reference_type='sales_invoice',
                            reference_id=self.instance.id,
                            item=inv_item
                        ).first()
                        if prev_movement:
                            avail_stock += prev_movement.quantity

                    if req_qty > avail_stock:
                        raise serializers.ValidationError({
                            "detail": f"Insufficient stock for '{inv_item.name}'. Available: {avail_stock:.2f}, Requested: {req_qty:.2f}"
                        })

        return attrs

    def _apply_invoice_balance_effects(self, invoice, original_paid_amount):
        """
        Applies all balance-affecting side effects for an invoice that
        has just become 'Saved'.
        """
        if invoice.customer:
            customer = Customer.objects.select_for_update().get(pk=invoice.customer.pk)
            
            # Temporarily revert paid_amount so balance_due is pure
            if original_paid_amount > 0:
                invoice.paid_amount -= original_paid_amount
                invoice.save(update_fields=['paid_amount'])
                
            remaining_due = invoice.balance_due
            is_credit = (invoice.payment_term == 'Credit')
            
            # Consume advance & add to credit balance
            consume_amount = customer.apply_invoice(remaining_due, is_credit)
            
            if consume_amount > 0:
                invoice.advance_applied = consume_amount
                invoice.paid_amount += consume_amount
                invoice.save(update_fields=['advance_applied', 'paid_amount'])

            # Process the original_paid_amount through proper PaymentReceived logic
            if original_paid_amount > 0:
                serializer = PaymentReceivedSerializer()
                result = serializer._apply_payment(customer, original_paid_amount, invoice)
                
                if customer:
                    customer.recalculate_balances()
                    customer.refresh_from_db()
                    current_balance_after = customer.credit_balance
                else:
                    current_balance_after = Decimal('0.00')
                
                PaymentReceived.objects.create(
                    customer=customer,
                    invoice=invoice,
                    amount_received=original_paid_amount,
                    balance_after=current_balance_after,
                    method=invoice.payment_method or 'Cash',
                    notes=f"Auto-recorded from invoice {invoice.invoice_number}",
                    applied_to_invoice=result.get('applied_to_invoice', Decimal('0.00')),
                    applied_to_credit=result.get('applied_to_credit', Decimal('0.00')),
                    applied_to_advance=result.get('applied_to_advance', Decimal('0.00'))
                )

    def _reverse_invoice_balance_effects(self, invoice):
        """
        Reverses balance side effects when a 'Saved' invoice is trashed.
        """
        if invoice.customer:
            customer = Customer.objects.select_for_update().get(pk=invoice.customer.pk)
            is_credit = (invoice.payment_term == 'Credit')
            balance_due = invoice.net_total - invoice.advance_applied
            customer.reverse_invoice(balance_due, invoice.advance_applied, is_credit)
            
            if invoice.advance_applied > 0:
                invoice.paid_amount -= invoice.advance_applied
                invoice.advance_applied = Decimal('0.00')
                invoice.save(update_fields=['paid_amount', 'advance_applied'])

        from inventory.services import reverse_document_stock
        reverse_document_stock('sales_invoice', invoice.id)

    def create(self, validated_data: dict) -> SalesInvoice:
        items_data = validated_data.pop("items")
        customer_data = validated_data.pop("customer")

        with transaction.atomic():
            if isinstance(customer_data, dict) and customer_data.get('is_new_customer'):
                customer_data.pop('is_new_customer', None)
                from sales.utils import get_or_create_customer_from_data
                customer = get_or_create_customer_from_data(customer_data)
            else:
                customer = customer_data

            invoice = SalesInvoice.objects.create(customer=customer, **validated_data)
            original_paid_amount = invoice.paid_amount

            for item_data in items_data:
                SalesItem.objects.create(invoice=invoice, **item_data)

            if (invoice.paid_amount > 0 or invoice.advance_applied > 0) and invoice.status == 'Draft':
                invoice.status = 'Saved'
                invoice.save(update_fields=['status'])

            if invoice.status == 'Saved':
                # Pull freshly calculated database fields before running accounting side-effects
                invoice.refresh_from_db()
                self._apply_invoice_balance_effects(invoice, original_paid_amount)
                from inventory.services import process_sales_invoice_stock
                process_sales_invoice_stock(invoice)

        return invoice

    def update(self, instance: SalesInvoice, validated_data: dict) -> SalesInvoice:
        items_data = validated_data.pop("items", None)
        customer_data = validated_data.pop("customer", None)
        old_status = instance.status

        with transaction.atomic():
            if customer_data is not None:
                if isinstance(customer_data, dict) and customer_data.get('is_new_customer'):
                    customer_data.pop('is_new_customer', None)
                    from sales.utils import get_or_create_customer_from_data
                    customer = get_or_create_customer_from_data(customer_data)
                else:
                    customer = customer_data
                instance.customer = customer

            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            if (instance.paid_amount > 0 or instance.advance_applied > 0) and instance.status == 'Draft':
                instance.status = 'Saved'
                instance.save(update_fields=['status'])

            if items_data is not None:
                instance.items.all().delete()
                for item_data in items_data:
                    SalesItem.objects.create(invoice=instance, **item_data)

            if old_status != 'Saved' and instance.status == 'Saved':
                instance.refresh_from_db()
                original_paid_amount = instance.paid_amount
                self._apply_invoice_balance_effects(instance, original_paid_amount)
                from inventory.services import process_sales_invoice_stock
                process_sales_invoice_stock(instance)
            elif instance.status == 'Saved':
                from inventory.services import process_sales_invoice_stock, reverse_document_stock
                reverse_document_stock('sales_invoice', instance.id)
                process_sales_invoice_stock(instance)

        return instance


class PaymentReceivedSerializer(serializers.ModelSerializer):
    """Serializer for daily income / payment received records."""

    customer = serializers.PrimaryKeyRelatedField(
        queryset=Customer.objects.all()
    )
    customerName = serializers.CharField(
        source='customer.customer_name', read_only=True
    )
    invoiceNumber = serializers.CharField(
        source='invoice.invoice_number', read_only=True, default=None
    )

    class Meta:
        model = PaymentReceived
        fields = [
            'id',
            'receipt_number',
            'date',
            'customer',
            'customerName',
            'invoice',
            'invoiceNumber',
            'amount_received',
            'balance_after',
            'method',
            'notes',
            'applied_to_invoice',
            'applied_to_credit',
            'applied_to_advance',
        ]
        read_only_fields = [
            'id',
            'receipt_number',
            'balance_after',
            'applied_to_invoice',
            'applied_to_credit',
            'applied_to_advance',
        ]

    # ── Validation ──────────────────────────────────────────────────

    def validate_amount_received(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Amount received must be greater than zero."
            )
        return value

    def validate(self, attrs):
        invoice = attrs.get('invoice')
        customer = attrs.get('customer')
        if invoice and customer and invoice.customer_id != customer.id:
            raise serializers.ValidationError(
                "The selected invoice does not belong to this customer."
            )
        return attrs

    # ── Balance helpers ─────────────────────────────────────────────

    def _apply_payment(self, customer, amount, invoice=None):
        """Apply payment: invoice balance_due first (FIFO for pending invoices if general payment)."""
        remaining = Decimal(str(amount))
        applied_to_invoice = Decimal('0.00')
        applied_to_credit = Decimal('0.00')
        applied_to_advance = Decimal('0.00')

        # Step 1 — Selected invoice first
        if invoice:
            if invoice.status == 'Draft':
                invoice.status = 'Saved'
                invoice.save(update_fields=['status'])
                SalesInvoiceSerializer()._apply_invoice_balance_effects(invoice, Decimal('0.00'))
            invoice.refresh_from_db(fields=['paid_amount', 'status'])
            invoice_due = invoice.balance_due
            if invoice_due > 0:
                pay_inv = min(invoice_due, remaining)
                invoice.paid_amount += pay_inv
                invoice.save(update_fields=['paid_amount'])
                remaining -= pay_inv
                applied_to_invoice += pay_inv

        # Step 2 — General/Remaining payment: check and clear pending saved invoices in FIFO order (oldest first)
        if remaining > 0 and not invoice:
            pending_invoices = customer.invoices.filter(status='Saved').order_by('date', 'id')
            for inv in pending_invoices:
                if invoice and inv.id == invoice.id:
                    continue
                inv.refresh_from_db(fields=['paid_amount'])
                inv_due = inv.balance_due
                if inv_due > 0:
                    pay_inv = min(inv_due, remaining)
                    inv.paid_amount += pay_inv
                    inv.save(update_fields=['paid_amount'])
                    remaining -= pay_inv
                    applied_to_credit += pay_inv
                    if remaining == 0:
                        break

        # Step 3 — Apply remaining to advance_balance
        applied_to_advance = remaining

        return {
            'applied_to_invoice': applied_to_invoice,
            'applied_to_credit': applied_to_credit,
            'applied_to_advance': applied_to_advance,
        }

    def _reverse_payment(self, customer, invoice, applied_to_invoice, applied_to_credit, applied_to_advance):
        """Reverse a previously applied payment using stored split amounts."""
        rem_inv_rev = applied_to_invoice + applied_to_credit

        if rem_inv_rev > 0:
            if invoice and applied_to_invoice > 0:
                invoice.refresh_from_db(fields=['paid_amount'])
                rev_amount = min(invoice.paid_amount, applied_to_invoice)
                if rev_amount > 0:
                    invoice.paid_amount -= rev_amount
                    invoice.save(update_fields=['paid_amount'])
                    rem_inv_rev -= rev_amount

            if rem_inv_rev > 0:
                paid_invoices = customer.invoices.filter(paid_amount__gt=0).order_by('-date', '-id')
                for inv in paid_invoices:
                    if invoice and inv.id == invoice.id:
                        continue
                    inv.refresh_from_db(fields=['paid_amount'])
                    rev_amount = min(inv.paid_amount, rem_inv_rev)
                    if rev_amount > 0:
                        inv.paid_amount -= rev_amount
                        inv.save(update_fields=['paid_amount'])
                        rem_inv_rev -= rev_amount
                        if rem_inv_rev == 0:
                            break

    # ── Create / Update ─────────────────────────────────────────────

    def create(self, validated_data):
        with transaction.atomic():
            # 1. Save the payment record with dummy zeroes for tracking
            validated_data['balance_after'] = Decimal('0.00')
            validated_data['applied_to_invoice'] = Decimal('0.00')
            validated_data['applied_to_credit'] = Decimal('0.00')
            validated_data['applied_to_advance'] = Decimal('0.00')
            payment = super().create(validated_data)

            # 2. Process payment allocation
            customer = payment.customer
            amount = payment.amount_received
            invoice = payment.invoice
            
            result = self._apply_payment(customer, amount, invoice)
            payment.applied_to_invoice = result['applied_to_invoice']
            payment.applied_to_credit = result['applied_to_credit']
            payment.applied_to_advance = result['applied_to_advance']

            # 3. Recalculate customer balances now that payment exists
            customer.recalculate_balances()

            # 4. Update payment.balance_after
            payment.balance_after = customer.credit_balance

            # 5. Save tracking fields
            payment.save(update_fields=['balance_after', 'applied_to_invoice', 'applied_to_credit', 'applied_to_advance'])

            return payment

    def update(self, instance, validated_data):
        with transaction.atomic():
            self._reverse_payment(
                instance.customer,
                instance.invoice,
                instance.applied_to_invoice,
                instance.applied_to_credit,
                instance.applied_to_advance,
            )

            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            
            # Save the updated payment with dummy zeroes
            instance.balance_after = Decimal('0.00')
            instance.applied_to_invoice = Decimal('0.00')
            instance.applied_to_credit = Decimal('0.00')
            instance.applied_to_advance = Decimal('0.00')
            instance.save()

            # Process allocation
            result = self._apply_payment(instance.customer, instance.amount_received, instance.invoice)
            instance.applied_to_invoice = result['applied_to_invoice']
            instance.applied_to_credit = result['applied_to_credit']
            instance.applied_to_advance = result['applied_to_advance']

            # Recalculate customer balances
            instance.customer.recalculate_balances()

            # Update payment.balance_after
            instance.balance_after = instance.customer.credit_balance
            
            instance.save(update_fields=['balance_after', 'applied_to_invoice', 'applied_to_credit', 'applied_to_advance'])

            return instance


class QuotationItemNestedSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuotationItem
        fields = [
            'id',
            'item_name',
            'unit',
            'qty',
            'rate',
            'discount',
            'line_total',
        ]
        read_only_fields = ['id', 'line_total']


class QuotationListSerializer(serializers.ModelSerializer):
    effective_status = serializers.ReadOnlyField()
    validity_display = serializers.ReadOnlyField()

    class Meta:
        model = Quotation
        fields = [
            'id',
            'quotation_number',
            'customer_data',
            'total',
            'valid_days',
            'validity_display',
            'status',
            'effective_status',
            'date',
        ]
        read_only_fields = fields


class QuotationDetailSerializer(serializers.ModelSerializer):
    items = QuotationItemNestedSerializer(many=True)
    effective_status = serializers.ReadOnlyField()
    validity_display = serializers.ReadOnlyField()

    class Meta:
        model = Quotation
        fields = [
            'id',
            'quotation_number',
            'customer_data',
            'date',
            'valid_days',
            'valid_until',
            'payment_term',
            'discount_percentage',
            'vat_percentage',
            'subtotal',
            'discount_amount',
            'vat_amount',
            'total',
            'status',
            'effective_status',
            'validity_display',
            'converted_invoice',
            'items',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'quotation_number', 'valid_until', 'subtotal',
            'discount_amount', 'vat_amount', 'total', 'effective_status',
            'validity_display', 'converted_invoice', 'created_at', 'updated_at'
        ]

    def create(self, validated_data: dict) -> Quotation:
        items_data = validated_data.pop("items", [])
        quotation = Quotation.objects.create(**validated_data)
        for item_data in items_data:
            QuotationItem.objects.create(quotation=quotation, **item_data)
        return quotation

    def update(self, instance: Quotation, validated_data: dict) -> Quotation:
        items_data = validated_data.pop("items", None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                QuotationItem.objects.create(quotation=instance, **item_data)

        return instance


class SalesReturnItemNestedSerializer(serializers.ModelSerializer):
    """Nested line item serializer for sales returns."""
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = SalesReturnItem
        fields = ['id', 'sales_item', 'item_name', 'quantity', 'rate', 'discount', 'total']
        read_only_fields = ['id', 'total']


class SalesReturnListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for sales return list views."""

    returnNumber = serializers.CharField(source='return_number', read_only=True)
    customerName = serializers.SerializerMethodField()
    invoiceNumber = serializers.CharField(source='invoice.invoice_number', read_only=True)
    netReturnAmount = serializers.DecimalField(
        source='net_return_amount', max_digits=12, decimal_places=2, read_only=True
    )
    returnDate = serializers.DateField(source='return_date', read_only=True)

    class Meta:
        model = SalesReturn
        fields = [
            'id',
            'returnNumber',
            'customerName',
            'invoiceNumber',
            'netReturnAmount',
            'status',
            'returnDate',
        ]
        read_only_fields = fields

    def get_customerName(self, obj):
        return obj.customer.customer_name if obj.customer else None


class SalesReturnSerializer(serializers.ModelSerializer):
    """Full detail serializer for sales return / credit note CRUD."""

    items = SalesReturnItemNestedSerializer(many=True)
    customerName = serializers.CharField(source='customer.customer_name', read_only=True)
    invoiceNumber = serializers.CharField(source='invoice.invoice_number', read_only=True)
    net_return_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = SalesReturn
        fields = [
            'id',
            'return_number',
            'invoice',
            'customer',
            'customerName',
            'invoiceNumber',
            'status',
            'refund_type',
            'return_date',
            'reason',
            'notes',
            'items',
            'net_return_amount',
            'applied_to_credit',
            'applied_to_advance',
            'created_at',
        ]
        read_only_fields = [
            'id',
            'return_number',
            'customer',
            'customerName',
            'invoiceNumber',
            'net_return_amount',
            'applied_to_credit',
            'applied_to_advance',
            'created_at',
        ]

    # ── Validation ──────────────────────────────────────────────────

    def validate(self, attrs):
        # 1. Absolute Hard Lock: Reject any modification if the return is already 'Saved'
        if self.instance and self.instance.status == 'Saved':
            raise serializers.ValidationError(
                {"status": "Saved credit notes are locked and cannot be modified."}
            )

        # 2. Invoice Validation: must be Saved and not deleted
        invoice = attrs.get('invoice', getattr(self.instance, 'invoice', None))
        if not invoice:
            raise serializers.ValidationError({"invoice": "An invoice is required."})

        # Re-fetch to ensure we check current DB state
        try:
            invoice_obj = SalesInvoice.all_objects.get(pk=invoice.pk)
        except SalesInvoice.DoesNotExist:
            raise serializers.ValidationError({"invoice": "Invoice not found."})

        if invoice_obj.status != 'Saved':
            raise serializers.ValidationError(
                {"invoice": "Returns can only be created against invoices with status 'Saved'."}
            )
        if invoice_obj.is_deleted:
            raise serializers.ValidationError(
                {"invoice": "Returns cannot be created against deleted invoices."}
            )

        # 3. Items validation
        items_list = attrs.get('items')
        if items_list is None and self.instance:
            items_list = []
        elif items_list is None:
            items_list = []

        if len(items_list) == 0:
            raise serializers.ValidationError(
                {"items": "A sales return must contain at least one item."}
            )

        # 4. Quantity Cap Validation
        return_amount = Decimal('0.00')
        for item_data in items_list:
            sales_item = item_data.get('sales_item')
            qty = Decimal(str(item_data.get('quantity', 0)))
            rate = Decimal(str(item_data.get('rate', 0)))
            disc = Decimal(str(item_data.get('discount', 0)))

            return_amount += (qty * rate) - ((qty * rate) * (disc / Decimal('100')))

            if qty <= 0:
                raise serializers.ValidationError(
                    {"items": "Return quantity must be greater than zero."}
                )
            if rate <= 0:
                raise serializers.ValidationError(
                    {"items": "Return rate must be greater than zero."}
                )
            if disc < 0 or disc > 100:
                raise serializers.ValidationError(
                    {"items": "Discount percentage must be between 0 and 100."}
                )

            if sales_item:
                # Verify the sales_item belongs to the linked invoice
                if sales_item.invoice_id != invoice.pk:
                    raise serializers.ValidationError(
                        {"items": f"Item '{item_data.get('item_name', '')}' does not belong to the selected invoice."}
                    )

                # Calculate previously returned quantity for this specific sales_item
                prev_returned_qs = SalesReturnItem.objects.filter(
                    sales_item=sales_item,
                    sales_return__status='Saved',
                    sales_return__is_deleted=False,
                )
                if self.instance:
                    prev_returned_qs = prev_returned_qs.exclude(sales_return=self.instance)

                prev_returned = prev_returned_qs.aggregate(
                    total=Sum('quantity')
                )['total'] or Decimal('0.00')

                available = sales_item.quantity - prev_returned
                if qty > available:
                    raise serializers.ValidationError(
                        {"items": f"Cannot return {qty} of '{sales_item.item_name}'. "
                                  f"Only {available} remaining (original: {sales_item.quantity}, "
                                  f"previously returned: {prev_returned})."}
                    )

        # 5. Cash Refund Validation
        refund_type = attrs.get('refund_type', getattr(self.instance, 'refund_type', 'STORE_CREDIT'))
        if refund_type == 'CASH':
            customer = attrs.get('customer')
            if not customer:
                customer = invoice.customer if invoice else getattr(self.instance, 'customer', None)
            
            if customer:
                customer.refresh_from_db()
                customer_pending_balance = customer.credit_balance
                if customer_pending_balance > 0:
                    return_amount = return_amount.quantize(Decimal('0.01'))
                    allowed_cash = max(Decimal('0.00'), return_amount - customer_pending_balance).quantize(Decimal('0.01'))
                    if return_amount > allowed_cash + Decimal('0.01'):
                        raise serializers.ValidationError(
                            f"Customer has an outstanding balance of Rs. {customer_pending_balance}. Cash refund cannot exceed Rs. {allowed_cash}. Return amount must be adjusted against pending balance first."
                        )

        return attrs

    # ── Balance helpers ─────────────────────────────────────────────

    def _apply_return_balance_effects(self, sales_return):
        """
        Apply balance effects when a SalesReturn transitions to 'Saved'.

        STORE_CREDIT: Reduces credit_balance first, remainder goes to advance_balance.
        CASH: No customer balance changes — treated as direct cash outflow.

        Must be called inside a transaction.atomic() block.
        """
        if sales_return.refund_type == 'CASH':
            # Cash refund: no customer balance changes
            sales_return.applied_to_credit = Decimal('0.00')
            sales_return.applied_to_advance = Decimal('0.00')
            sales_return.save(update_fields=['applied_to_credit', 'applied_to_advance'])
            
            # AUTOMATIC DAILY CASH OUTFLOW / EXPENSE TRACKING
            if sales_return.net_return_amount > 0:
                from purchase.models import Expense
                Expense.objects.create(
                    category="Sales Return Cash Refund",
                    amount=sales_return.net_return_amount,
                    person_supplier=sales_return.customer.customer_name,
                    paid_by="System",
                    payment_method="Cash",
                    date=sales_return.return_date,
                    notes=f"Auto-generated cash refund for Sales Return {sales_return.return_number}"
                )
            return

        customer = Customer.objects.select_for_update().get(pk=sales_return.customer_id)
        net_amount = sales_return.net_return_amount
        remaining = net_amount

        applied_to_credit = Decimal('0.00')
        applied_to_advance = Decimal('0.00')

        # Step 1: Reduce credit_balance (down to minimum of 0)
        if customer.credit_balance > 0 and remaining > 0:
            applied_to_credit = min(customer.credit_balance, remaining)
            customer.credit_balance -= applied_to_credit
            remaining -= applied_to_credit

        # Step 2: Any remainder goes to advance_balance
        if remaining > 0:
            applied_to_advance = remaining
            customer.advance_balance += applied_to_advance

        customer.save(update_fields=['credit_balance', 'advance_balance'])

        # Store the split for precise reversal
        sales_return.applied_to_credit = applied_to_credit
        sales_return.applied_to_advance = applied_to_advance
        sales_return.save(update_fields=['applied_to_credit', 'applied_to_advance'])

    def _reverse_return_balance_effects(self, sales_return):
        """
        Reverse balance effects using the STORED split amounts.

        STORE_CREDIT: Reverses credit/advance adjustments.
        CASH: No-op (no balances were modified).

        Must be called inside a transaction.atomic() block.
        """
        if sales_return.refund_type == 'CASH':
            if sales_return.net_return_amount > 0:
                from purchase.models import Expense
                Expense.objects.filter(
                    category="Sales Return Cash Refund",
                    notes__contains=sales_return.return_number
                ).delete()
        else:
            customer = Customer.objects.select_for_update().get(pk=sales_return.customer_id)

            if sales_return.applied_to_advance > 0:
                customer.advance_balance -= sales_return.applied_to_advance

            if sales_return.applied_to_credit > 0:
                customer.credit_balance += sales_return.applied_to_credit

            customer.save(update_fields=['credit_balance', 'advance_balance'])

        from inventory.services import reverse_document_stock
        reverse_document_stock('sales_return', sales_return.id)

    # ── Create / Update ─────────────────────────────────────────────

    def create(self, validated_data: dict) -> SalesReturn:
        items_data = validated_data.pop('items')
        invoice = validated_data['invoice']

        # Auto-set customer from invoice
        validated_data['customer'] = invoice.customer

        with transaction.atomic():
            sales_return = SalesReturn.objects.create(**validated_data)

            for item_data in items_data:
                SalesReturnItem.objects.create(sales_return=sales_return, **item_data)

            if sales_return.status == 'Saved':
                sales_return.refresh_from_db()
                self._apply_return_balance_effects(sales_return)
                from inventory.services import process_sales_return_stock
                process_sales_return_stock(sales_return)

        return sales_return

    def update(self, instance: SalesReturn, validated_data: dict) -> SalesReturn:
        items_data = validated_data.pop('items', None)
        old_status = instance.status

        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            if items_data is not None:
                instance.items.all().delete()
                for item_data in items_data:
                    SalesReturnItem.objects.create(sales_return=instance, **item_data)

            if old_status != 'Saved' and instance.status == 'Saved':
                instance.refresh_from_db()
                self._apply_return_balance_effects(instance)
                from inventory.services import process_sales_return_stock
                process_sales_return_stock(instance)
            elif instance.status == 'Saved':
                from inventory.services import process_sales_return_stock, reverse_document_stock
                reverse_document_stock('sales_return', instance.id)
                process_sales_return_stock(instance)

        return instance
