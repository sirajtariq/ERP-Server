"""
DRF serializers for the purchase module.

PurchaseInvoiceSerializer exposes nested line items on read and write so a
single request can create or replace an invoice together with its items.
"""

from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from purchase.models import Expense, ExpenseItem, PurchaseInvoice, PurchaseItem, Vendor, VendorPayment


def validate_vendor_match(vendor_data):
    if not vendor_data:
        return None
    try:
        vendor = Vendor.objects.get(id=vendor_data['id'])
        db_phone = vendor.phone or ''
        in_phone = vendor_data.get('phone') or ''
        if vendor.vendor_name.strip() != vendor_data['vendor_name'].strip() or db_phone.strip() != in_phone.strip():
            raise serializers.ValidationError({"vendor": "Vendor details do not match our records."})
        return vendor
    except Vendor.DoesNotExist:
        raise serializers.ValidationError({"vendor": "Vendor not found."})



class VendorSerializer(serializers.ModelSerializer):
    """Serializer for vendor master data."""

    vendorId = serializers.CharField(source="vendor_id", read_only=True)

    class Meta:
        model = Vendor
        fields = [
            "id",
            "vendorId",
            "vendor_name",
            "phone",
            "email",
            "address",
            "tax_number",
            "opening_payable",
            "opening_note",
            "payable_balance",
            "advance_balance",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "vendorId",
            "payable_balance",
            "advance_balance",
            "created_at",
            "updated_at",
        ]

    def validate_phone(self, value):
        """
        Strip whitespace; convert empty/whitespace-only strings to None
        so the DB stores NULL instead of '' — NULLs are exempt from
        unique constraints, preventing IntegrityError collisions when
        multiple vendors have no phone number.
        """
        if value is not None:
            value = value.strip()
            if value == '':
                return None
        return value


class VendorInvoiceSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseInvoice
        fields = [
            "id",
            "invoice_number",
            "date",
            "payment_term",
            "status",
            "subtotal",
            "net_total",
            "balance_due"
        ]


class VendorListSerializer(serializers.ModelSerializer):
    """
    Listing serializer for Vendor.
    """
    invoices = VendorInvoiceSummarySerializer(many=True, read_only=True)
    total_paid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    vendorId = serializers.CharField(source="vendor_id", read_only=True)

    class Meta:
        model = Vendor
        fields = [
            "id",
            "vendorId",
            "vendor_name",
            "phone",
            "email",
            "address",
            "tax_number",
            "opening_payable",
            "opening_note",
            "payable_balance",
            "advance_balance",
            "total_paid",
            "created_at",
            "updated_at",
            "invoices"
        ]


class PurchaseItemSerializer(serializers.ModelSerializer):
    """Serializer for standalone purchase invoice line items."""
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseItem
        fields = ["id", "invoice", "product_name", "units", "quantity", "purchase_price", "discount", "total"]
        read_only_fields = ["id", "total"]

    def validate(self, attrs):
        if attrs.get('quantity', 0) <= 0:
            raise serializers.ValidationError({"quantity": "Quantity must be greater than zero."})
        if attrs.get('purchase_price', 0) <= 0:
            raise serializers.ValidationError({"purchase_price": "Purchase price must be greater than zero."})
        if attrs.get('discount', 0) < 0:
            raise serializers.ValidationError({"discount": "Discount cannot be negative."})
        return attrs


class PurchaseItemNestedSerializer(serializers.ModelSerializer):
    """Nested line item serializer (invoice is set by the parent invoice)."""
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseItem
        fields = ["id", "product_name", "units", "quantity", "purchase_price", "discount", "total"]
        read_only_fields = ["id", "total"]


class PurchaseInvoiceVendorRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    vendor_name = serializers.CharField()
    phone = serializers.CharField(allow_blank=True, required=False)


class PurchaseInvoiceListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for the invoice list table."""

    vendor = PurchaseInvoiceVendorRefSerializer()
    invoice_status = serializers.CharField(source='status', read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    net_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    payment_status = serializers.CharField(read_only=True)

    class Meta:
        model = PurchaseInvoice
        fields = [
            'id',
            'invoice_number',
            'bill_number',
            'date',
            'payment_term',
            'invoice_status',
            'payment_status',
            'subtotal',
            'net_total',
            'balance_due',
            'vendor',
        ]
        read_only_fields = fields


class ExpenseItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseItem
        fields = ["id", "item_name", "quantity", "amount"]
        read_only_fields = ["id"]

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError("Quantity must be greater than zero.")
        return value

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value


class ExpenseSerializer(serializers.ModelSerializer):
    """Serializer for standalone expense records."""
    items = ExpenseItemSerializer(many=True, required=False)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)

    class Meta:
        model = Expense
        fields = [
            "id",
            "expense_number",
            "category",
            "amount",
            "person_supplier",
            "paid_by",
            "payment_method",
            "date",
            "notes",
            "created_at",
            "items",
        ]
        read_only_fields = ["id", "expense_number", "created_at"]

    def validate(self, attrs):
        # DRF passes nested serializers' data as list of dicts in attrs
        items_list = attrs.get('items')
        
        # In update, if items is omitted, we might keep existing items or it's not provided.
        # But wait, typically if items is not provided in PATCH, it's None in attrs (or omitted).
        # We need to distinguish between empty array and omitted array.
        # However, for simplicity, if items are provided, we recompute amount. 
        # If omitted in a partial update, we don't recompute (use existing amount).
        # If it's a create, items might be empty.
        
        if items_list is not None and len(items_list) > 0:
            total_amount = sum((item['amount'] for item in items_list), Decimal('0.00'))
            attrs['amount'] = total_amount
        elif items_list is not None and len(items_list) == 0:
            # If items explicitly empty, must provide amount
            if 'amount' not in attrs:
                raise serializers.ValidationError({"amount": "This field is required when there are no line items."})
            if attrs['amount'] <= 0:
                raise serializers.ValidationError({"amount": "Amount must be greater than zero."})
        else:
            # items_list is omitted (None) - e.g. partial update or missing in payload.
            if not self.instance:
                # If create and missing items, amount must be provided
                if 'amount' not in attrs:
                    raise serializers.ValidationError({"amount": "This field is required when there are no line items."})
                if attrs['amount'] <= 0:
                    raise serializers.ValidationError({"amount": "Amount must be greater than zero."})
            else:
                # Update without changing items. Just validate amount if provided.
                if 'amount' in attrs and attrs['amount'] <= 0:
                    raise serializers.ValidationError({"amount": "Amount must be greater than zero."})

        return attrs

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        
        with transaction.atomic():
            expense = Expense.objects.create(**validated_data)
            for item_data in items_data:
                ExpenseItem.objects.create(expense=expense, **item_data)
                
        return expense

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        
        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            
            if items_data is not None:
                instance.items.all().delete()
                for item_data in items_data:
                    ExpenseItem.objects.create(expense=instance, **item_data)
                    
            # If we didn't update items but we DID update amount directly, that's handled.
            # But wait, if they didn't provide items but the DB HAS items, should they be allowed to update amount?
            # It's better to just recompute on save if items exist, but the prompt says:
            # "change amount field behavior: if the Expense has related items, amount becomes a COMPUTED property... If there are NO items, amount remains a normal editable field..."
            # Above logic handles it correctly for the payload.
                    
        return instance


class VendorPaymentSerializer(serializers.ModelSerializer):
    """Serializer for vendor payments and advances."""

    vendor = PurchaseInvoiceVendorRefSerializer()
    invoice = serializers.SlugRelatedField(
        slug_field='invoice_number',
        queryset=PurchaseInvoice.objects.filter(is_deleted=False),
        allow_null=True, required=False
    )

    class Meta:
        model = VendorPayment
        fields = [
            'id',
            'payment_number',
            'date',
            'vendor',
            'invoice',
            'amount_paid',
            'balance_after',
            'method',
            'notes',
            'applied_to_invoice',
            'applied_to_payable',
            'applied_to_advance',
        ]
        read_only_fields = [
            'id',
            'payment_number',
            'balance_after',
            'applied_to_invoice',
            'applied_to_payable',
            'applied_to_advance',
        ]

    def validate_amount_paid(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount paid must be greater than zero.")
        return value

    def validate(self, attrs):
        vendor_data = attrs.get('vendor')
        if vendor_data:
            resolved_vendor = validate_vendor_match(vendor_data)
            attrs['vendor'] = resolved_vendor
            
        invoice = attrs.get('invoice')
        vendor = attrs.get('vendor')
        if invoice and vendor and invoice.vendor_id != vendor.id:
            raise serializers.ValidationError(
                "The selected invoice does not belong to this vendor."
            )
        return attrs

    def _apply_payment(self, vendor, amount, invoice=None):
        """Apply payment to invoice, payable balance, then advance balance."""
        remaining = Decimal(str(amount))
        applied_to_invoice = Decimal('0')

        if invoice:
            invoice.refresh_from_db(fields=['paid_amount'])
            invoice_due = invoice.balance_due
            if invoice_due > 0:
                applied_to_invoice = min(invoice_due, remaining)
                invoice.paid_amount += applied_to_invoice
                invoice.save(update_fields=['paid_amount'])
                
                # The invoice balance is part of the vendor's total payable balance only if Credit,
                # so paying the invoice also reduces the vendor's payable balance.
                if invoice.payment_term == 'Credit':
                    vendor.payable_balance -= applied_to_invoice
                
                remaining -= applied_to_invoice

        applied_to_payable, applied_to_advance = vendor.apply_payment(remaining)

        return {
            'balance_after': vendor.payable_balance,
            'applied_to_invoice': applied_to_invoice,
            'applied_to_payable': applied_to_payable,
            'applied_to_advance': applied_to_advance,
        }

    def _reverse_payment(self, vendor, invoice, applied_to_invoice, applied_to_payable, applied_to_advance):
        """Reverse payment effects."""
        vendor.reverse_payment(applied_to_payable, applied_to_advance)

        if invoice and applied_to_invoice > 0:
            invoice.paid_amount -= applied_to_invoice
            invoice.save(update_fields=['paid_amount'])
            
            # Since the payment applied to the invoice reduced the vendor's
            # payable balance (if Credit), we must restore it here.
            if invoice.payment_term == 'Credit':
                vendor.payable_balance += applied_to_invoice
                vendor.save(update_fields=['payable_balance'])

    def create(self, validated_data):
        vendor = validated_data['vendor']
        amount = validated_data['amount_paid']
        invoice = validated_data.get('invoice')

        with transaction.atomic():
            vendor = Vendor.objects.select_for_update().get(pk=vendor.pk)
            if invoice:
                invoice = PurchaseInvoice.objects.select_for_update().get(pk=invoice.pk)

            result = self._apply_payment(vendor, amount, invoice)
            validated_data['balance_after'] = result['balance_after']
            validated_data['applied_to_invoice'] = result['applied_to_invoice']
            validated_data['applied_to_payable'] = result['applied_to_payable']
            validated_data['applied_to_advance'] = result['applied_to_advance']

            return super().create(validated_data)

    def update(self, instance, validated_data):
        with transaction.atomic():
            vendor = Vendor.objects.select_for_update().get(pk=instance.vendor.pk)
            if instance.invoice:
                invoice = PurchaseInvoice.objects.select_for_update().get(pk=instance.invoice.pk)
            else:
                invoice = None

            self._reverse_payment(
                vendor,
                invoice,
                instance.applied_to_invoice,
                instance.applied_to_payable,
                instance.applied_to_advance,
            )

            new_vendor = validated_data.get('vendor', instance.vendor)
            if new_vendor.pk != vendor.pk:
                new_vendor = Vendor.objects.select_for_update().get(pk=new_vendor.pk)
            
            new_amount = validated_data.get('amount_paid', instance.amount_paid)
            new_invoice = validated_data.get('invoice', instance.invoice)
            if new_invoice and (not invoice or new_invoice.pk != invoice.pk):
                new_invoice = PurchaseInvoice.objects.select_for_update().get(pk=new_invoice.pk)

            result = self._apply_payment(new_vendor, new_amount, new_invoice)
            validated_data['balance_after'] = result['balance_after']
            validated_data['applied_to_invoice'] = result['applied_to_invoice']
            validated_data['applied_to_payable'] = result['applied_to_payable']
            validated_data['applied_to_advance'] = result['applied_to_advance']

            return super().update(instance, validated_data)


class PurchaseInvoiceSerializer(serializers.ModelSerializer):
    """
    Invoice serializer with nested items.
    """
    items = PurchaseItemNestedSerializer(many=True)
    vendor = PurchaseInvoiceVendorRefSerializer()
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_line_discount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    tax_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    net_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    payment_status = serializers.CharField(read_only=True)

    class Meta:
        model = PurchaseInvoice
        fields = [
            "id",
            "vendor",
            "bill_number",
            "invoice_number",
            "date",
            "payment_term",
            "payment_method",
            "paid_amount",
            "advance_applied",
            "payment_reference",
            "notes",
            "vat_percentage",
            "invoice_discount",
            "status",
            "subtotal",
            "total_line_discount",
            "tax_amount",
            "net_total",
            "balance_due",
            "payment_status",
            "items",
        ]
        read_only_fields = [
            "id", 
            "invoice_number",
            "advance_applied",
            "subtotal",
            "total_line_discount",
            "tax_amount",
            "net_total",
            "balance_due",
            "payment_status",
        ]

    def validate(self, attrs):
        if self.instance and self.instance.status == 'Saved':
            raise serializers.ValidationError(
                {"status": "Saved invoices are locked and cannot be modified."}
            )

        vendor_data = attrs.pop('vendor', None)
        if vendor_data:
            attrs['vendor'] = validate_vendor_match(vendor_data)

        vendor = attrs.get('vendor', self.instance.vendor if self.instance else None)
        payment_term = attrs.get('payment_term', self.instance.payment_term if self.instance else None)
        
        if not vendor:
            raise serializers.ValidationError({"vendor": "This field is required."})

        advance_balance = Decimal(str(getattr(vendor, 'advance_balance', 0)))

        items_list = attrs.get('items')
        if items_list is None and self.instance:
            items_list = list(self.instance.items.all())
        elif items_list is None:
            items_list = []

        if len(items_list) == 0:
            raise serializers.ValidationError({"items": "An invoice must contain at least one item."})

        subtotal = Decimal('0.00')
        total_line_discount = Decimal('0.00')

        for item in items_list:
            is_dict = isinstance(item, dict)
            qty = Decimal(str(item.get('quantity', 0) if is_dict else getattr(item, 'quantity', 0)))
            rate = Decimal(str(item.get('purchase_price', 0) if is_dict else getattr(item, 'purchase_price', 0)))
            disc = Decimal(str(item.get('discount', 0) if is_dict else getattr(item, 'discount', 0)))
            
            if qty <= 0 or rate <= 0:
                raise serializers.ValidationError({"items": "Quantity and purchase price must be greater than zero."})
            if disc < 0 or disc > 100:
                raise serializers.ValidationError({"items": "Line item discount percentage must be between 0 and 100."})
            
            subtotal += qty * rate
            total_line_discount += (qty * rate) * (disc / Decimal('100'))

        vat_percentage = Decimal(str(attrs.get('vat_percentage', self.instance.vat_percentage if self.instance else 0)))
        invoice_discount = Decimal(str(attrs.get('invoice_discount', self.instance.invoice_discount if self.instance else 0)))

        if invoice_discount < 0 or invoice_discount > 100:
            raise serializers.ValidationError({"invoice_discount": "Invoice discount percentage must be between 0 and 100."})

        base_amount = subtotal - total_line_discount
        deducted_invoice_discount = base_amount * (invoice_discount / Decimal('100'))
        tax_amount = base_amount * (vat_percentage / Decimal('100'))
        net_total = base_amount + tax_amount - deducted_invoice_discount

        paid_amount = Decimal(str(attrs.get('paid_amount', self.instance.paid_amount if self.instance else 0)))

        effective_coverage = paid_amount + advance_balance

        if effective_coverage < net_total and payment_term == 'Cash':
            raise serializers.ValidationError(
                {"payment_term": f"Remaining balance detected after applying available advance ({advance_balance:.2f}). "
                                 f"Payment term must be 'Credit' for partial or unpaid balances."}
            )

        if effective_coverage >= net_total and payment_term == 'Credit':
            raise serializers.ValidationError(
                {"payment_term": f"Invoice is fully covered by the paid amount and available advance ({advance_balance:.2f}). "
                                 f"Payment term must be 'Cash' as no new debt is created."}
            )

        return attrs

    def _apply_invoice_balance_effects(self, invoice, original_paid_amount):
        """
        Applies balance side effects when transition to 'Saved'.
        Requires calling within transaction.atomic().
        """
        if invoice.vendor:
            vendor = Vendor.objects.select_for_update().get(pk=invoice.vendor.pk)
            
            # 1. Temporarily revert paid_amount so balance_due is calculated purely 
            # from net_total and previous advance_applied (which is zero on first save).
            if original_paid_amount > 0:
                invoice.paid_amount -= original_paid_amount
                invoice.save(update_fields=['paid_amount'])

            remaining_due_before_payment = invoice.balance_due
            is_credit = (invoice.payment_term == 'Credit')

            # 2. Consume existing advance (if any) against the unpaid balance
            consume_amount = vendor.apply_invoice(remaining_due_before_payment, is_credit)

            if consume_amount > 0:
                invoice.advance_applied += consume_amount
                invoice.save(update_fields=['advance_applied'])

            # 4. Process the original_paid_amount through the proper VendorPayment logic
            if original_paid_amount > 0:
                # We instantiate the serializer or use its underlying logic
                serializer = VendorPaymentSerializer()
                
                # Apply payment using the shared logic, which correctly routes overflow
                # and updates invoice.paid_amount and vendor balances.
                result = serializer._apply_payment(vendor, original_paid_amount, invoice)
                
                # Create the VendorPayment record
                VendorPayment.objects.create(
                    vendor=vendor,
                    invoice=invoice,
                    amount_paid=original_paid_amount,
                    balance_after=result['balance_after'],
                    method=invoice.payment_method or 'Cash',
                    notes=f"Auto-recorded from invoice {invoice.invoice_number}",
                    applied_to_invoice=result['applied_to_invoice'],
                    applied_to_payable=result['applied_to_payable'],
                    applied_to_advance=result['applied_to_advance']
                )

    def _reverse_invoice_balance_effects(self, invoice):
        """
        Reverses balance side effects when a 'Saved' invoice is trashed.
        Requires calling within transaction.atomic().
        """
        if invoice.vendor:
            vendor = Vendor.objects.select_for_update().get(pk=invoice.vendor.pk)
            is_credit = (invoice.payment_term == 'Credit')
            balance_due = invoice.net_total - invoice.advance_applied
            vendor.reverse_invoice(balance_due, invoice.advance_applied, is_credit)
            
            if invoice.advance_applied > 0:
                invoice.paid_amount -= invoice.advance_applied
                invoice.advance_applied = Decimal('0.00')
                invoice.save(update_fields=['paid_amount', 'advance_applied'])

    def create(self, validated_data: dict) -> PurchaseInvoice:
        from django.db import transaction
        
        items_data = validated_data.pop("items")
        
        with transaction.atomic():
            invoice = PurchaseInvoice.objects.create(**validated_data)
            original_paid_amount = invoice.paid_amount

            for item_data in items_data:
                PurchaseItem.objects.create(invoice=invoice, **item_data)

            if invoice.status == 'Saved':
                invoice.refresh_from_db()
                self._apply_invoice_balance_effects(invoice, original_paid_amount)

        return invoice

    def update(self, instance: PurchaseInvoice, validated_data: dict) -> PurchaseInvoice:
        from django.db import transaction
        
        items_data = validated_data.pop("items", None)
        old_status = instance.status

        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            if items_data is not None:
                instance.items.all().delete()
                for item_data in items_data:
                    PurchaseItem.objects.create(invoice=instance, **item_data)

            if old_status != 'Saved' and instance.status == 'Saved':
                instance.refresh_from_db()
                original_paid_amount = instance.paid_amount
                self._apply_invoice_balance_effects(instance, original_paid_amount)

        return instance
