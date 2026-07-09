"""
Sales module data models: customers, invoices, and line items.
"""

from decimal import Decimal
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from sales.base_models import SoftDeleteModel


class Customer(SoftDeleteModel):
    """Customer master record."""

    CUSTOMER_TYPE_CHOICES = (
        ('permanent', 'Permanent'),
        ('walkin', 'Walk-in'),
    )

    customer_id = models.IntegerField(unique=True, editable=False, default=4000)
    customer_name = models.CharField(max_length=255)
    customer_type = models.CharField(max_length=10, choices=CUSTOMER_TYPE_CHOICES)
    phone = models.CharField(max_length=20, unique=True, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(default="")
    opening_credit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    opening_note = models.TextField(blank=True)
    tax_number = models.CharField(max_length=50, blank=True, null=True)
    credit_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    advance_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.customer_name

    def save(self, *args, **kwargs):
        is_new = not self.id
        if is_new:
            max_attempts = 5
            for attempt in range(max_attempts):
                with transaction.atomic():
                    if self.customer_type == 'walkin':
                        start_id = 8000
                        # FIX: Use all_objects instead of objects to see soft-deleted records
                        last = Customer.all_objects.filter(
                            customer_type='walkin'
                        ).order_by('-customer_id').first()
                    else:
                        start_id = 4000
                        # FIX: Use all_objects instead of objects to see soft-deleted records
                        last = Customer.all_objects.filter(
                            customer_type='permanent'
                        ).order_by('-customer_id').first()

                    self.customer_id = (last.customer_id + 1) if last else start_id

                    try:
                        with transaction.atomic():
                            super(Customer, self).save(*args, **kwargs)
                        break  # success — exit retry loop
                    except IntegrityError as e:
                        if 'customer_id' in str(e) and attempt < max_attempts - 1:
                            continue
                        raise  
        else:
            super().save(*args, **kwargs)


class SalesInvoice(SoftDeleteModel):
    """Sales invoice header linked to a customer."""

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="invoices",
        null=True,
        blank=True,
    )

    
    PAYMENT_TERM_CHOICES = (
        ('Cash', 'Cash'),
        ('Credit', 'Credit'),
    )
    payment_term = models.CharField(max_length=10, choices=PAYMENT_TERM_CHOICES, default='Credit')
    payment_method = models.CharField(max_length=50, blank=True, null=True)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    advance_applied = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_reference = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    vat_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    invoice_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    
    STATUS_CHOICES = (
        ('Draft', 'Draft'),
        ('Saved', 'Saved'),
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Draft')
    
    invoice_number = models.CharField(max_length=50, unique=True, blank=True)
    date = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    @property
    def subtotal(self):
        return sum((item.total for item in self.items.all()), Decimal('0.00'))

    @property
    def total_line_discount(self):
        return sum((item.discount for item in self.items.all()), Decimal('0.00'))

    @property
    def tax_amount(self):
        return (self.subtotal - Decimal(str(self.invoice_discount))) * (Decimal(str(self.vat_percentage)) / Decimal('100'))

    @property
    def net_total(self):
        return (self.subtotal - Decimal(str(self.invoice_discount))) + self.tax_amount

    @property
    def balance_due(self):
        return self.net_total - Decimal(str(self.paid_amount))

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            from datetime import date
            current_year = date.today().year
            prefix = f'INV-{current_year}-'
            
            max_attempts = 5
            for attempt in range(max_attempts):
                with transaction.atomic():
                    # FIX: Use all_objects to safely include soft-deleted invoices too
                    last_invoice = SalesInvoice.all_objects.filter(
                        invoice_number__startswith=prefix
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
                        break  # success — exit retry loop
                    except IntegrityError as e:
                        if 'invoice_number' in str(e) and attempt < max_attempts - 1:
                            # Collision — loop again to compute a fresh number
                            continue
                        raise
        else:
            super().save(*args, **kwargs)


class SalesItem(models.Model):
    """Line item belonging to a sales invoice."""

    invoice = models.ForeignKey(
        SalesInvoice,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item_name = models.CharField(max_length=255)
    units = models.CharField(max_length=50, default='pcs')
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)

    class Meta:
        ordering = ["id"]

    @property
    def total(self):
        return (self.quantity * self.rate) - self.discount

    def __str__(self) -> str:
        return f"{self.item_name} x{self.quantity}"


class PaymentReceived(SoftDeleteModel):
    """Daily income / payment received record."""

    PAYMENT_METHOD_CHOICES = (
        ('Cash', 'Cash'),
        ('Bank Transfer', 'Bank Transfer'),
        ('JazzCash', 'JazzCash'),
        ('EasyPaisa', 'EasyPaisa'),
        ('Cheque', 'Cheque'),
    )

    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='payments'
    )
    invoice = models.ForeignKey(
        SalesInvoice, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='payments'
    )
    receipt_number = models.CharField(max_length=50, unique=True, blank=True)
    amount_received = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=50, default='Cash')
    notes = models.TextField(blank=True, null=True)
    date = models.DateField(null=True, blank=True)
    applied_to_invoice = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    applied_to_credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    applied_to_advance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def save(self, *args, **kwargs):
        from datetime import date as _date

        if not self.date:
            self.date = _date.today()

        if not self.receipt_number:
            year = self.date.year
            prefix = f'REC-{year}-'
            with transaction.atomic():
                last = (
                    PaymentReceived.all_objects
                    .select_for_update()
                    .filter(receipt_number__startswith=prefix)
                    .order_by('-id')
                    .first()
                )
                if last:
                    last_seq = int(last.receipt_number.split('-')[-1])
                    new_seq = last_seq + 1
                else:
                    new_seq = 1
                self.receipt_number = f'{prefix}{new_seq:05d}'

        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.receipt_number} — {self.customer}"
