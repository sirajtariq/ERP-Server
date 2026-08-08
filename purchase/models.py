"""
Purchase module data models: vendors, invoices, and line items.
"""

from decimal import Decimal
from django.core.validators import MinValueValidator

from django.db import IntegrityError, models, transaction
from django.utils import timezone
from sales.base_models import SoftDeleteModel


# ── Vendor auto-ID sequence ────────────────────────────────────────
# Single starting point for all vendors (unlike Customer which splits
# walk-in vs permanent at 8000/4000).
VENDOR_ID_START = 5000


class Vendor(SoftDeleteModel):
    """Supplier / vendor master record."""

    vendor_id = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    vendor_name = models.CharField(max_length=255)
    # phone is null=True so multiple vendors with no phone store NULL
    # instead of '' — NULLs are exempt from unique constraints in SQL,
    # empty strings are not.  This deliberately fixes a known tech-debt
    # bug present in Customer.phone (which uses blank=True only).
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True)
    tax_number = models.CharField(max_length=50, blank=True)
    opening_payable = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
    )
    opening_note = models.TextField(blank=True)
    payable_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))]
    )
    advance_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.vendor_name

    @property
    def unpaid_opening_payable(self):
        total_applied = sum((p.applied_to_payable for p in self.payments.all()), Decimal('0.00'))
        return max(Decimal('0.00'), Decimal(str(self.opening_payable or '0.00')) - total_applied)

    def update_payable_balance(self):
        """
        Recalculates and updates the top-level payable_balance based on the formula:
        payableBalance = (Unpaid Opening Payable) + (Sum of unpaid Saved invoice balances)
        """
        unpaid_invoices = sum(
            (inv.balance_due for inv in self.invoices.filter(status='Saved') if inv.balance_due > 0), 
            Decimal('0.00')
        )
        self.payable_balance = self.unpaid_opening_payable + unpaid_invoices
        self.save(update_fields=['payable_balance'])

    def apply_payment(self, amount: Decimal):
        """
        Applies a payment using 3-step FIFO engine:
        1. Pending Invoices
        2. Opening Balance
        3. Advance Balance
        Returns a dict with allocation details.
        """
        remaining = Decimal(str(amount))
        allocations = {'invoices': [], 'payable': Decimal('0.00'), 'advance': Decimal('0.00')}

        # Step 1: Pending Invoices (FIFO)
        invoices = sorted(
            [inv for inv in self.invoices.filter(status='Saved') if inv.balance_due > 0],
            key=lambda x: (x.date, x.created_at)
        )
        for inv in invoices:
            if remaining <= 0:
                break
            due = inv.balance_due
            apply_amount = min(due, remaining)
            inv.paid_amount += apply_amount
            inv.save(update_fields=['paid_amount'])
            remaining -= apply_amount
            allocations['invoices'].append((inv.invoice_number, apply_amount))

        # Step 2: Opening Balance
        if remaining > 0 and self.unpaid_opening_payable > 0:
            apply_amount = min(self.unpaid_opening_payable, remaining)
            allocations['payable'] = apply_amount
            remaining -= apply_amount

        # Step 3: Advance Balance
        if remaining > 0:
            allocations['advance'] = remaining
            self.advance_balance += remaining
            self.save(update_fields=['advance_balance'])

        self.update_payable_balance()
        return allocations

    def reverse_payment(self, payment_record):
        """
        Reverses a payment by rolling back advance, and updating the payable balance.
        (Invoice rollbacks are handled separately in the serializer).
        """
        if payment_record.applied_to_advance > 0:
            self.advance_balance -= payment_record.applied_to_advance
            self.save(update_fields=['advance_balance'])
            
        self.update_payable_balance()

    def apply_invoice(self, amount: Decimal, is_credit: bool = True):
        """
        Consumes available advance to cover the invoice amount.
        """
        remaining = Decimal(str(amount))
        consumed_advance = Decimal('0.00')

        if self.advance_balance > 0 and remaining > 0:
            consumed_advance = min(self.advance_balance, remaining)
            self.advance_balance -= consumed_advance
            self.save(update_fields=['advance_balance'])
            
        self.update_payable_balance()
        return consumed_advance

    def reverse_invoice(self, balance_due: Decimal, advance_applied: Decimal, is_credit: bool = True):
        """
        Reverses the effects of an invoice on the vendor's balances.
        """
        if advance_applied > 0:
            self.advance_balance += advance_applied
            self.save(update_fields=['advance_balance'])
            
        self.update_payable_balance()

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
                    if last and getattr(last, 'vendor_id', '').startswith('VN-'):
                        last_number = int(last.vendor_id.split('-')[1])
                        new_id = f"VN-{last_number + 1:05d}"
                    else:
                        new_id = "VN-00001"
                    self.vendor_id = new_id

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


class PurchaseInvoice(SoftDeleteModel):
    """Purchase invoice header linked to a vendor."""

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    bill_number = models.CharField(max_length=50, db_index=True, blank=True)
    invoice_number = models.CharField(max_length=50, unique=True, editable=False)
    date = models.DateField(default=timezone.localdate)
    
    PAYMENT_TERM_CHOICES = (
        ('Cash', 'Cash'),
        ('Credit', 'Credit'),
    )
    payment_term = models.CharField(max_length=10, choices=PAYMENT_TERM_CHOICES)
    payment_method = models.CharField(max_length=50, blank=True)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    advance_applied = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    payment_reference = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    vat_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    invoice_discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), help_text="Discount percentage (0-100)")
    
    STATUS_CHOICES = (
        ('Draft', 'Draft'),
        ('Saved', 'Saved'),
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Draft')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    @property
    def subtotal(self):
        return sum((item.quantity * item.purchase_price for item in self.items.all()), Decimal('0.00')).quantize(Decimal('0.01'))

    @property
    def total_line_discount(self):
        return sum(((item.quantity * item.purchase_price) * (item.discount / Decimal('100')) for item in self.items.all()), Decimal('0.00')).quantize(Decimal('0.01'))

    @property
    def tax_amount(self):
        return ((self.subtotal - self.total_line_discount) * (self.vat_percentage / Decimal('100'))).quantize(Decimal('0.01'))

    @property
    def net_total(self):
        base_amount = self.subtotal - self.total_line_discount
        deducted_invoice_discount = base_amount * (self.invoice_discount / Decimal('100'))
        return (base_amount + self.tax_amount - deducted_invoice_discount).quantize(Decimal('0.01'))

    @property
    def balance_due(self):
        return (self.net_total - self.paid_amount - self.advance_applied).quantize(Decimal('0.01'))

    @property
    def payment_status(self):
        covered = self.paid_amount + self.advance_applied
        if covered <= Decimal('0.00'):
            return "Unpaid"
        elif covered < self.net_total:
            return "Partial"
        elif covered == self.net_total:
            return "Paid"
        else:
            return "Advance"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            from datetime import date
            current_year = date.today().year
            prefix = f'PI-{current_year}-'
            
            max_attempts = 5
            for attempt in range(max_attempts):
                with transaction.atomic():
                    # Counter is a global continuous sequence regardless of year changes.
                    last_invoice = PurchaseInvoice.all_objects.filter(
                        invoice_number__startswith='PI-'
                    ).order_by('-id').first()
                    
                    if last_invoice:
                        last_number = int(last_invoice.invoice_number.split('-')[-1])
                        new_number = last_number + 1
                    else:
                        new_number = 1
                        
                    self.invoice_number = f'{prefix}{new_number:05d}'
                    
                    try:
                        with transaction.atomic():
                            super().save(*args, **kwargs)
                        break
                    except IntegrityError as e:
                        if 'invoice_number' in str(e) and attempt < max_attempts - 1:
                            continue
                        raise
        else:
            super().save(*args, **kwargs)

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
    units = models.CharField(max_length=50, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), help_text="Discount percentage (0-100)")

    class Meta:
        ordering = ["id"]

    @property
    def total(self):
        return ((self.quantity * self.purchase_price) - ((self.quantity * self.purchase_price) * (self.discount / Decimal('100')))).quantize(Decimal('0.01'))

    def __str__(self) -> str:
        return f"{self.product_name} x{self.quantity}"


class VendorPayment(SoftDeleteModel):
    """Vendor payment and advance tracking."""

    vendor = models.ForeignKey(
        Vendor, on_delete=models.PROTECT, related_name='payments'
    )
    invoice = models.ForeignKey(
        PurchaseInvoice, on_delete=models.PROTECT,
        null=True, blank=True, related_name='payments'
    )
    payment_number = models.CharField(max_length=50, unique=True, editable=False)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    date = models.DateField(default=timezone.localdate)
    applied_to_invoice = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    applied_to_payable = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    applied_to_advance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def save(self, *args, **kwargs):
        if not self.payment_number:
            from datetime import date
            current_year = date.today().year
            prefix = f'SP-{current_year}-'
            max_attempts = 5
            for attempt in range(max_attempts):
                with transaction.atomic():
                    last = VendorPayment.all_objects.filter(
                        payment_number__startswith='SP-'
                    ).order_by('-id').first()
                    if last:
                        last_number = int(last.payment_number.split('-')[-1])
                        new_number = last_number + 1
                    else:
                        new_number = 1
                    self.payment_number = f'{prefix}{new_number:05d}'
                    try:
                        with transaction.atomic():
                            super().save(*args, **kwargs)
                        break
                    except IntegrityError as e:
                        if 'payment_number' in str(e) and attempt < max_attempts - 1:
                            continue
                        raise
        else:
            super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.payment_number} — {self.vendor}"


class Expense(SoftDeleteModel):
    """Standalone expense tracking."""

    expense_number = models.CharField(max_length=50, unique=True, editable=False)
    category = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    person_supplier = models.CharField(max_length=255, blank=True)
    paid_by = models.CharField(max_length=255, blank=True)
    payment_method = models.CharField(max_length=50, blank=True)
    date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def save(self, *args, **kwargs):
        if not self.expense_number:
            from datetime import date
            current_year = date.today().year
            prefix = f'EXP-{current_year}-'
            max_attempts = 5
            for attempt in range(max_attempts):
                with transaction.atomic():
                    last = Expense.all_objects.filter(
                        expense_number__startswith='EXP-'
                    ).order_by('-id').first()
                    if last:
                        last_number = int(last.expense_number.split('-')[-1])
                        new_number = last_number + 1
                    else:
                        new_number = 1
                    self.expense_number = f'{prefix}{new_number:05d}'
                    try:
                        with transaction.atomic():
                            super().save(*args, **kwargs)
                        break
                    except IntegrityError as e:
                        if 'expense_number' in str(e) and attempt < max_attempts - 1:
                            continue
                        raise
        else:
            super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.expense_number} — {self.category}"


class ExpenseItem(models.Model):
    """Line item for an itemized expense."""

    expense = models.ForeignKey(
        Expense,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item_name = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.item_name} x{self.quantity}"
