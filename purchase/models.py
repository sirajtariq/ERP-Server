"""
Purchase module data models: vendors, invoices, and line items.
"""

from decimal import Decimal

from django.db import IntegrityError, models, transaction
from django.utils import timezone
from sales.base_models import SoftDeleteModel


# ── Vendor auto-ID sequence ────────────────────────────────────────
# Single starting point for all vendors (unlike Customer which splits
# walk-in vs permanent at 8000/4000).
VENDOR_ID_START = 5000


class Vendor(SoftDeleteModel):
    """Supplier / vendor master record."""

    vendor_id = models.IntegerField(unique=True, editable=False, default=VENDOR_ID_START)
    vendor_name = models.CharField(max_length=255)
    # phone is null=True so multiple vendors with no phone store NULL
    # instead of '' — NULLs are exempt from unique constraints in SQL,
    # empty strings are not.  This deliberately fixes a known tech-debt
    # bug present in Customer.phone (which uses blank=True only).
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True)
    tax_number = models.CharField(max_length=50, blank=True)
    opening_credit = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
    )
    opening_note = models.TextField(blank=True)
    credit_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
    )
    advance_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.vendor_name

    def save(self, *args, **kwargs):
        is_new = not self.pk
        if is_new:
            max_attempts = 5
            for attempt in range(max_attempts):
                with transaction.atomic():
                    # Use all_objects to include soft-deleted records in the
                    # sequence calculation so we never reuse a vendor_id.
                    last = (
                        Vendor.all_objects
                        .order_by('-vendor_id')
                        .first()
                    )
                    self.vendor_id = (last.vendor_id + 1) if last else VENDOR_ID_START

                    try:
                        with transaction.atomic():
                            super(Vendor, self).save(*args, **kwargs)
                        break  # success — exit retry loop
                    except IntegrityError as e:
                        # NOTE: This string-matching approach is fragile tech
                        # debt inherited from the sales module's Customer.save().
                        # Could be improved with e.__cause__ inspection or a
                        # pre-check query instead.
                        if 'vendor_id' in str(e) and attempt < max_attempts - 1:
                            continue
                        raise
        else:
            super().save(*args, **kwargs)


class PurchaseInvoice(models.Model):
    """Purchase invoice header linked to a vendor."""

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    invoice_number = models.CharField(max_length=50, unique=True)
    date = models.DateField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self) -> str:
        return f"{self.invoice_number} ({self.vendor.vendor_name})"


class PurchaseItem(models.Model):
    """Line item belonging to a purchase invoice."""

    invoice = models.ForeignKey(
        PurchaseInvoice,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.product_name} x{self.quantity}"
