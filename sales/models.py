"""
Sales module data models: customers, invoices, and line items.
"""

from decimal import Decimal
from django.db import models
from django.db import transaction


class Customer(models.Model):
    """Customer master record."""

    customer_id = models.IntegerField(unique=True, editable=False, default=4000)
    customer_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, unique=True)
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
        if not self.id:
            with transaction.atomic():
                last = Customer.objects.select_for_update()\
                               .order_by('-customer_id').first()
                self.customer_id = (last.customer_id + 1) if last else 4000
        super().save(*args, **kwargs)


class SalesInvoice(models.Model):
    """Sales invoice header linked to a customer."""

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="invoices",
        null=True,
        blank=True,
    )
    walk_in_customer_name = models.CharField(max_length=255, blank=True, null=True)
    
    PAYMENT_TERM_CHOICES = (
        ('Cash', 'Cash'),
        ('Credit', 'Credit'),
    )
    payment_term = models.CharField(max_length=10, choices=PAYMENT_TERM_CHOICES, default='Credit')
    payment_method = models.CharField(max_length=50, blank=True, null=True)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
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
    date = models.DateField(auto_now_add=True)

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
            last_invoice = SalesInvoice.objects.filter(invoice_number__startswith=f'INV-{current_year}-').order_by('-id').first()
            if last_invoice:
                last_number = int(last_invoice.invoice_number.split('-')[-1])
                new_number = last_number + 1
            else:
                new_number = 1
            self.invoice_number = f'INV-{current_year}-{new_number:05d}'
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        name = self.customer.customer_name if self.customer else (self.walk_in_customer_name or "Walk-in")
        return f"{self.invoice_number} ({name})"


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
