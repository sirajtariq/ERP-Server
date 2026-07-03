"""
DRF serializers for the sales module.

SalesInvoiceSerializer exposes nested line items on read and write so a
single request can create or replace an invoice together with its items.
"""

from decimal import Decimal

from django.db.models import Sum
from rest_framework import serializers

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
    Phone = serializers.CharField(source="phone", required=False, allow_blank=True, allow_null=True)
    Address = serializers.CharField(source="address")
    openingCredit = serializers.DecimalField(source="opening_credit", max_digits=12, decimal_places=2, required=False, allow_null=True)
    openingNote = serializers.CharField(source="opening_note", required=False, allow_blank=True)
    taxNumber = serializers.CharField(source="tax_number", required=False, allow_null=True,allow_blank=True)
    creditBalance = serializers.DecimalField(source="credit_balance", max_digits=12, decimal_places=2, read_only=True)
    advanceBalance = serializers.DecimalField(source="advance_balance", max_digits=12, decimal_places=2, read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)
    invoices = CustomerInvoiceNestedSerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = ["id", "customerId", "customerName", "customerType", "Phone", "email", "Address", "openingCredit", "openingNote", "taxNumber", "creditBalance", "advanceBalance", "createdAt", "updatedAt", "invoices"]
        read_only_fields = ["id", "customerId", "creditBalance", "advanceBalance", "createdAt", "updatedAt", "invoices"]


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


class SalesInvoiceSerializer(serializers.ModelSerializer):
    items = SalesItemNestedSerializer(many=True)
    customer_data = CustomerSerializer(source='customer', read_only=True)
    
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
            "customer",
            "customer_data",
            "payment_term",
            "payment_method",
            "paid_amount",
            "payment_reference",
            "notes",
            "vat_percentage",
            "invoice_discount",
            "status",
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
            "date", 
            "customer_data", 
            "subtotal", 
            "total_line_discount", 
            "tax_amount", 
            "net_total", 
            "balance_due",
            "advance_applied"
        ]

    def validate(self, attrs):
        customer = attrs.get('customer')
        payment_term = attrs.get('payment_term')
        walk_in_name = self.initial_data.get('walk_in_customer_name')

        if not customer and not walk_in_name:
            raise serializers.ValidationError(
                "Either a customer or walk-in name is required."
            )

        # Check credit restriction for existing walk-in customer
        if customer and hasattr(customer, 'customer_type') and customer.customer_type == 'walkin' and payment_term == 'Credit':
            raise serializers.ValidationError(
                "Walk-in customers can only pay via Cash."
            )

        # Check credit restriction for new walk-in (customer not yet created)
        if not customer and walk_in_name and payment_term == 'Credit':
            raise serializers.ValidationError(
                "Walk-in customers can only pay via Cash."
            )

        return attrs

    def create(self, validated_data: dict) -> SalesInvoice:
        items_data = validated_data.pop("items")

        # Auto-create walk-in customer if no customer provided
        if not validated_data.get('customer'):
            walk_in_name = self.initial_data.get('walk_in_customer_name')
            if walk_in_name:
                walkin_customer = Customer.objects.create(
                    customer_name=walk_in_name,
                    customer_type='walkin',
                )
                validated_data['customer'] = walkin_customer

        invoice = SalesInvoice.objects.create(**validated_data)
        # Capture the original paid_amount from the request BEFORE advance
        # consumption modifies it — used later for auto-PaymentReceived.
        original_paid_amount = invoice.paid_amount

        for item_data in items_data:
            SalesItem.objects.create(invoice=invoice, **item_data)

        # Advance consumption: if customer has advance_balance, apply it
        # towards this invoice's balance_due before any credit_balance update.
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

        # Update credit_balance when payment_term is Credit
        if invoice.payment_term == 'Credit' and invoice.customer:
            invoice.customer.refresh_from_db(fields=['credit_balance'])
            invoice.customer.credit_balance += invoice.balance_due
            invoice.customer.save(update_fields=['credit_balance'])

        # Auto-record a PaymentReceived entry for visibility in Daily Income.
        # Only for the ORIGINAL paid_amount from the request, not the advance
        # consumption portion (which is tracked via advance_applied instead).
        if invoice.customer and original_paid_amount > 0:
            PaymentReceived.objects.create(
                customer=invoice.customer,
                invoice=invoice,
                amount_received=original_paid_amount,
                balance_after=invoice.customer.credit_balance,
                method=invoice.payment_method or 'Cash',
                notes=f"Auto-recorded from invoice {invoice.invoice_number}",
            )

        return invoice

    def update(self, instance: SalesInvoice, validated_data: dict) -> SalesInvoice:
        items_data = validated_data.pop("items", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                SalesItem.objects.create(invoice=instance, **item_data)
        return instance


class PaymentReceivedSerializer(serializers.ModelSerializer):
    """Serializer for daily income / payment received records."""

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

