"""
DRF serializers for the sales module.

SalesInvoiceSerializer exposes nested line items on read and write so a
single request can create or replace an invoice together with its items.
"""

from decimal import Decimal

from django.db.models import Sum
from rest_framework import serializers

from sales.models import Customer, SalesInvoice, SalesItem


class CustomerListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for customer list views (no nested invoices)."""

    customerId = serializers.IntegerField(source="customer_id", read_only=True)
    customerName = serializers.CharField(source="customer_name", read_only=True)
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
    Phone = serializers.CharField(source="phone")
    Address = serializers.CharField(source="address")
    openingCredit = serializers.DecimalField(source="opening_credit", max_digits=12, decimal_places=2, required=False, allow_null=True)
    openingNote = serializers.CharField(source="opening_note", required=False, allow_blank=True)

    taxNumber = serializers.CharField(source="tax_number", required=False, allow_null=True)
    creditBalance = serializers.DecimalField(source="credit_balance", max_digits=12, decimal_places=2, read_only=True)
    advanceBalance = serializers.DecimalField(source="advance_balance", max_digits=12, decimal_places=2, read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)
    invoices = CustomerInvoiceNestedSerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = ["id", "customerId", "customerName", "Phone", "email", "Address", "openingCredit", "openingNote", "taxNumber", "creditBalance", "advanceBalance", "createdAt", "updatedAt", "invoices"]
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
            "walk_in_customer_name",
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
            "balance_due"
        ]

    def validate(self, attrs):
        customer = attrs.get('customer')
        payment_term = attrs.get('payment_term')
        walk_in_name = attrs.get('walk_in_customer_name')

        if not customer and payment_term == 'Credit':
            raise serializers.ValidationError(
                "Walk-in customers can only pay via Cash."
            )
        if not customer and not walk_in_name:
            raise serializers.ValidationError(
                "Either a customer or walk-in name is required."
            )
        if customer and walk_in_name:
            raise serializers.ValidationError(
                "Provide either a customer or walk-in name, not both."
            )
        return attrs

    def create(self, validated_data: dict) -> SalesInvoice:
        items_data = validated_data.pop("items")
        invoice = SalesInvoice.objects.create(**validated_data)
        for item_data in items_data:
            SalesItem.objects.create(invoice=invoice, **item_data)

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
