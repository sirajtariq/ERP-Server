"""
DRF serializers for the sales module.

SalesInvoiceSerializer exposes nested line items on read and write so a
single request can create or replace an invoice together with its items.
"""

from decimal import Decimal

from django.db.models import Sum
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from sales.models import Customer, PaymentReceived, SalesInvoice, SalesItem


class CustomerListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for customer list views (no nested invoices)."""

    customerId = serializers.IntegerField(source="customer_id", read_only=True)
    customerName = serializers.CharField(source="customer_name", read_only=True)
    customerType = serializers.CharField(source="customer_type", read_only=True)
    Phone = serializers.CharField(source="phone", read_only=True)
    creditBalance = serializers.DecimalField(
        source="credit_balance", max_digits=12, decimal_places=2, read_only=True
    )
    advanceBalance = serializers.DecimalField(
        source="advance_balance", max_digits=12, decimal_places=2, read_only=True
    )
    totalPaid = serializers.SerializerMethodField()
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

    def get_totalPaid(self, obj):
        result = obj.invoices.aggregate(total=Sum("paid_amount"))
        return float(result["total"] or 0)

    def get_totalDue(self, obj):
        return float(obj.credit_balance)


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
    
    customerId = serializers.IntegerField(source="customer_id", read_only=True)
    customerName = serializers.CharField(source="customer_name")
    customerType = serializers.ChoiceField(source="customer_type",choices=['permanent', 'walkin'],required=True)
    Phone = serializers.CharField(
        source="phone",
        required=True,
        validators=[UniqueValidator(
            queryset=Customer.objects.all(),
            message="A customer with this phone number already exists."
        )],
    )
    Address = serializers.CharField(source="address", required=False, allow_blank=True)
    openingCredit = serializers.DecimalField(source="opening_credit", max_digits=12, decimal_places=2, required=False, allow_null=True)
    openingNote = serializers.CharField(source="opening_note", required=False, allow_blank=True)
    taxNumber = serializers.CharField(source="tax_number", required=False, allow_null=True,allow_blank=True)
    creditBalance = serializers.DecimalField(source="credit_balance", max_digits=12, decimal_places=2, read_only=True)
    advanceBalance = serializers.DecimalField(source="advance_balance", max_digits=12, decimal_places=2, read_only=True)
    totalPaid = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)
    invoices = CustomerInvoiceNestedSerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = ["id", "customerId", "customerName", "customerType", "Phone", "email", "Address", "openingCredit", "openingNote", "taxNumber", "creditBalance", "advanceBalance", "totalPaid", "createdAt", "updatedAt", "invoices"]
        read_only_fields = ["id", "customerId", "creditBalance", "advanceBalance","totalPaid", "createdAt", "updatedAt", "invoices"]

    def get_totalPaid(self, obj):
        result = obj.payments.aggregate(total=Sum("amount_received"))
        return float(result["total"] or 0)


class SalesItemSerializer(serializers.ModelSerializer):
    """Serializer for standalone sales invoice line items."""
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = SalesItem
        fields = ["id", "invoice", "item_name", "units", "quantity", "rate", "discount", "total"]
        read_only_fields = ["id", "total"]


class SalesItemNestedSerializer(serializers.ModelSerializer):
    """Nested line item serializer (invoice is set by the parent invoice)."""
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = SalesItem
        fields = ["id", "item_name", "units", "quantity", "rate", "discount", "total"]
        read_only_fields = ["id", "total"]


def compute_payment_status(invoice):
    """
    Shared payment-status logic for both SalesInvoiceListSerializer
    and SalesInvoiceSerializer. Uses a 0.01 tolerance to absorb
    Decimal rounding noise from tax calculations.
    """
    tolerance = Decimal('0.01')
    pending = invoice.balance_due
    paid = invoice.paid_amount

    if pending > tolerance and paid == 0:
        return "Unpaid"
    if pending > tolerance and paid > 0:
        return "Partial"
    if invoice.customer and invoice.customer.advance_balance > 0:
        return "Advance"
    return "Paid"


class SalesInvoiceListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for the invoice list table (no nested items/customer)."""

    invoiceNumber = serializers.CharField(source='invoice_number', read_only=True)
    customerName = serializers.SerializerMethodField()
    total = serializers.DecimalField(source='net_total', max_digits=12, decimal_places=2, read_only=True)
    paid = serializers.DecimalField(source='paid_amount', max_digits=12, decimal_places=2, read_only=True)
    pending = serializers.SerializerMethodField()
    paymentStatus = serializers.SerializerMethodField()

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
            'date',
        ]
        read_only_fields = fields

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
                "customer_data must be an object with customer_name, "
                "phone, customer_type, and optionally customer_id/"
                "tax_number."
            )

        customer_name = (data.get('customer_name') or '').strip()
        phone = (data.get('phone') or '').strip()
        customer_type = data.get('customer_type')
        tax_number = data.get('tax_number') or None

        if not customer_name:
            raise serializers.ValidationError(
                "customer_data.customer_name is required."
            )
        if not phone:
            raise serializers.ValidationError(
                "customer_data.phone is required."
            )
        if customer_type != 'walkin':
            raise serializers.ValidationError(
                "customer_data.customer_type must be 'walkin' — "
                "invoice creation can only generate walk-in "
                "customers. Existing permanent customers are "
                "matched automatically by phone number."
            )

        existing = Customer.objects.filter(phone=phone).first()
        if existing:
            return existing

        return Customer.objects.create(
            customer_name=customer_name,
            customer_type='walkin',
            phone=phone,
            tax_number=tax_number,
        )

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
        ]

    def get_paymentStatus(self, obj):
        return compute_payment_status(obj)

    def validate(self, attrs):
        customer = attrs.get('customer')
        payment_term = attrs.get('payment_term')

        # On partial update (PATCH), fall back to existing instance values
        # for fields not included in the request payload.
        if self.instance:
            if not customer:
                customer = self.instance.customer
            if not payment_term:
                payment_term = self.instance.payment_term

        if not customer:
            raise serializers.ValidationError(
                "customer_data is required — provide an object with "
                "customer_name, phone, and customer_type='walkin'."
            )

        if customer.customer_type == 'walkin' and payment_term == 'Credit':
            raise serializers.ValidationError(
                "Walk-in customers can only pay via Cash."
            )

        return attrs

    def _apply_invoice_balance_effects(self, invoice, original_paid_amount):
        """
        Applies all balance-affecting side effects for an invoice that
        has just become 'Saved' (either created directly as Saved, or
        transitioned from Draft to Saved via update()). Must only ever
        be called ONCE per invoice's lifecycle — calling it twice would
        double-apply advance consumption, credit_balance changes, and
        create a duplicate PaymentReceived record.
        """
        if invoice.customer:
            remaining_due = invoice.balance_due
            available_advance = invoice.customer.advance_balance

            if available_advance > 0 and remaining_due > 0:
                consume_amount = min(available_advance, remaining_due)

                invoice.advance_applied = consume_amount
                invoice.paid_amount += consume_amount
                invoice.save(update_fields=['advance_applied', 'paid_amount'])

                invoice.customer.advance_balance -= consume_amount
                invoice.customer.save(update_fields=['advance_balance'])

        if invoice.payment_term == 'Credit' and invoice.customer:
            invoice.customer.refresh_from_db(fields=['credit_balance'])
            invoice.customer.credit_balance += invoice.balance_due
            invoice.customer.save(update_fields=['credit_balance'])

        if invoice.customer and original_paid_amount > 0:
            PaymentReceived.objects.create(
                customer=invoice.customer,
                invoice=invoice,
                amount_received=original_paid_amount,
                balance_after=invoice.customer.credit_balance,
                method=invoice.payment_method or 'Cash',
                notes=f"Auto-recorded from invoice {invoice.invoice_number}",
            )

    def create(self, validated_data: dict) -> SalesInvoice:
        items_data = validated_data.pop("items")

        invoice = SalesInvoice.objects.create(**validated_data)
        original_paid_amount = invoice.paid_amount

        for item_data in items_data:
            SalesItem.objects.create(invoice=invoice, **item_data)

        if invoice.status == 'Saved':
            self._apply_invoice_balance_effects(invoice, original_paid_amount)

        return invoice

    def update(self, instance: SalesInvoice, validated_data: dict) -> SalesInvoice:
        items_data = validated_data.pop("items", None)
        old_status = instance.status

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                SalesItem.objects.create(invoice=instance, **item_data)

        if old_status != 'Saved' and instance.status == 'Saved':
            instance.refresh_from_db()
            original_paid_amount = instance.paid_amount
            self._apply_invoice_balance_effects(instance, original_paid_amount)

        return instance


class PaymentReceivedSerializer(serializers.ModelSerializer):
    """Serializer for daily income / payment received records."""

    customer = serializers.SlugRelatedField(
        slug_field='customer_id',
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
        """Apply payment: invoice balance_due first, then credit_balance, then advance."""
        remaining = Decimal(str(amount))
        applied_to_invoice = Decimal('0')
        applied_to_credit = Decimal('0')
        applied_to_advance = Decimal('0')

        # Step 1 — pay down the specific invoice's own balance_due first,
        # capped so we never push invoice.paid_amount past its net_total
        if invoice:
            invoice.refresh_from_db(fields=['paid_amount'])
            invoice_due = invoice.balance_due
            if invoice_due > 0:
                applied_to_invoice = min(invoice_due, remaining)
                invoice.paid_amount += applied_to_invoice
                invoice.save(update_fields=['paid_amount'])
                remaining -= applied_to_invoice

        # Step 2 — any leftover clears the customer's general credit_balance
        if customer.credit_balance > 0 and remaining > 0:
            applied_to_credit = min(customer.credit_balance, remaining)
            customer.credit_balance -= applied_to_credit
            remaining -= applied_to_credit

        # Step 3 — anything still remaining is a genuine overpayment -> advance
        if remaining > 0:
            applied_to_advance = remaining
            customer.advance_balance += applied_to_advance

        customer.save(update_fields=['credit_balance', 'advance_balance'])

        return {
            'balance_after': customer.credit_balance,
            'applied_to_invoice': applied_to_invoice,
            'applied_to_credit': applied_to_credit,
            'applied_to_advance': applied_to_advance,
        }

    def _reverse_payment(self, customer, invoice, applied_to_invoice, applied_to_credit, applied_to_advance):
        """Reverse a previously applied payment using the STORED split amounts."""
        # Reverse in the exact reverse order
        if applied_to_advance > 0:
            customer.advance_balance -= applied_to_advance

        if applied_to_credit > 0:
            customer.credit_balance += applied_to_credit

        customer.save(update_fields=['credit_balance', 'advance_balance'])

        if invoice and applied_to_invoice > 0:
            invoice.paid_amount -= applied_to_invoice
            invoice.save(update_fields=['paid_amount'])

    # ── Create / Update ─────────────────────────────────────────────

    def create(self, validated_data):
        customer = validated_data['customer']
        amount = validated_data['amount_received']
        invoice = validated_data.get('invoice')

        result = self._apply_payment(customer, amount, invoice)
        validated_data['balance_after'] = result['balance_after']
        validated_data['applied_to_invoice'] = result['applied_to_invoice']
        validated_data['applied_to_credit'] = result['applied_to_credit']
        validated_data['applied_to_advance'] = result['applied_to_advance']

        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Reverse the OLD payment using its STORED split values
        self._reverse_payment(
            instance.customer,
            instance.invoice,
            instance.applied_to_invoice,
            instance.applied_to_credit,
            instance.applied_to_advance,
        )

        # Apply the NEW payment values fresh
        new_customer = validated_data.get('customer', instance.customer)
        new_amount = validated_data.get('amount_received', instance.amount_received)
        new_invoice = validated_data.get('invoice', instance.invoice)

        result = self._apply_payment(new_customer, new_amount, new_invoice)
        validated_data['balance_after'] = result['balance_after']
        validated_data['applied_to_invoice'] = result['applied_to_invoice']
        validated_data['applied_to_credit'] = result['applied_to_credit']
        validated_data['applied_to_advance'] = result['applied_to_advance']

        return super().update(instance, validated_data)

