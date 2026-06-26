"""
DRF serializers for the purchase module.

PurchaseInvoiceSerializer exposes nested line items on read and write so a
single request can create or replace an invoice together with its items.
"""

from decimal import Decimal

from rest_framework import serializers

from purchase.models import PurchaseInvoice, PurchaseItem, Vendor


class VendorSerializer(serializers.ModelSerializer):
    """Serializer for vendor master data."""

    class Meta:
        model = Vendor
        fields = ["id", "name", "phone", "address"]
        read_only_fields = ["id"]


class PurchaseItemSerializer(serializers.ModelSerializer):
    """Serializer for standalone purchase invoice line items."""

    class Meta:
        model = PurchaseItem
        fields = ["id", "invoice", "product_name", "quantity", "purchase_price"]
        read_only_fields = ["id"]


class PurchaseItemNestedSerializer(serializers.ModelSerializer):
    """Nested line item serializer (invoice is set by the parent invoice)."""

    class Meta:
        model = PurchaseItem
        fields = ["id", "product_name", "quantity", "purchase_price"]
        read_only_fields = ["id"]


class PurchaseInvoiceSerializer(serializers.ModelSerializer):
    """
    Invoice serializer with nested items.

    On create/update, nested ``items`` are persisted atomically. If
    ``total_amount`` is omitted, it is calculated from line item totals.
    """

    items = PurchaseItemNestedSerializer(many=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)

    class Meta:
        model = PurchaseInvoice
        fields = [
            "id",
            "vendor",
            "vendor_name",
            "invoice_number",
            "date",
            "total_amount",
            "items",
        ]
        read_only_fields = ["id", "vendor_name"]

    def _calculate_total(self, items_data: list[dict]) -> Decimal:
        """Sum quantity * purchase_price for all line items."""
        return sum(
            (Decimal(item["quantity"]) * item["purchase_price"] for item in items_data),
            Decimal("0.00"),
        )

    def create(self, validated_data: dict) -> PurchaseInvoice:
        items_data = validated_data.pop("items")
        if "total_amount" not in validated_data:
            validated_data["total_amount"] = self._calculate_total(items_data)

        invoice = PurchaseInvoice.objects.create(**validated_data)
        for item_data in items_data:
            PurchaseItem.objects.create(invoice=invoice, **item_data)
        return invoice

    def update(self, instance: PurchaseInvoice, validated_data: dict) -> PurchaseInvoice:
        items_data = validated_data.pop("items", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                PurchaseItem.objects.create(invoice=instance, **item_data)
            if "total_amount" not in validated_data:
                instance.total_amount = self._calculate_total(items_data)

        instance.save()
        return instance
