"""
DRF serializers for the sales module.

SalesInvoiceSerializer exposes nested line items on read and write so a
single request can create or replace an invoice together with its items.
"""

from decimal import Decimal

from rest_framework import serializers

from sales.models import Customer, SalesInvoice, SalesItem


class CustomerSerializer(serializers.ModelSerializer):
    """Serializer for customer master data."""

    class Meta:
        model = Customer
        fields = ["id", "name", "phone"]
        read_only_fields = ["id"]


class SalesItemSerializer(serializers.ModelSerializer):
    """Serializer for standalone sales invoice line items."""

    class Meta:
        model = SalesItem
        fields = ["id", "invoice", "product_name", "quantity", "sale_price"]
        read_only_fields = ["id"]


class SalesItemNestedSerializer(serializers.ModelSerializer):
    """Nested line item serializer (invoice is set by the parent invoice)."""

    class Meta:
        model = SalesItem
        fields = ["id", "product_name", "quantity", "sale_price"]
        read_only_fields = ["id"]


class SalesInvoiceSerializer(serializers.ModelSerializer):
    """
    Invoice serializer with nested items.

    On create/update, nested ``items`` are persisted atomically. If
    ``total_amount`` is omitted, it is calculated from line item totals.
    """

    items = SalesItemNestedSerializer(many=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = SalesInvoice
        fields = [
            "id",
            "customer",
            "customer_name",
            "invoice_number",
            "date",
            "total_amount",
            "items",
        ]
        read_only_fields = ["id", "customer_name"]

    def _calculate_total(self, items_data: list[dict]) -> Decimal:
        """Sum quantity * sale_price for all line items."""
        return sum(
            (Decimal(item["quantity"]) * item["sale_price"] for item in items_data),
            Decimal("0.00"),
        )

    def create(self, validated_data: dict) -> SalesInvoice:
        items_data = validated_data.pop("items")
        if "total_amount" not in validated_data:
            validated_data["total_amount"] = self._calculate_total(items_data)

        invoice = SalesInvoice.objects.create(**validated_data)
        for item_data in items_data:
            SalesItem.objects.create(invoice=invoice, **item_data)
        return invoice

    def update(self, instance: SalesInvoice, validated_data: dict) -> SalesInvoice:
        items_data = validated_data.pop("items", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                SalesItem.objects.create(invoice=instance, **item_data)
            if "total_amount" not in validated_data:
                instance.total_amount = self._calculate_total(items_data)

        instance.save()
        return instance
