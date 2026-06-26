"""
Sales module data models: customers, invoices, and line items.
"""

from django.db import models


class Customer(models.Model):
    """Customer master record."""

    customer_id = models.IntegerField(unique=True, editable=False, default=4000)
    customer_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, unique=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(default="")
    opening_credit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    opening_note = models.TextField(blank=True)

    class Meta:
        ordering = ["customer_name"]

    def __str__(self) -> str:
        return self.customer_name

    def save(self, *args, **kwargs):
        if not self.id:
            last_customer = Customer.objects.order_by('-customer_id').first()
            if last_customer and last_customer.customer_id >= 4000:
                self.customer_id = last_customer.customer_id + 1
            else:
                self.customer_id = 4000
        super().save(*args, **kwargs)


class SalesInvoice(models.Model):
    """Sales invoice header linked to a customer."""

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    invoice_number = models.CharField(max_length=50, unique=True)
    date = models.DateField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self) -> str:
        return f"{self.invoice_number} ({self.customer.name})"


class SalesItem(models.Model):
    """Line item belonging to a sales invoice."""

    invoice = models.ForeignKey(
        SalesInvoice,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    sale_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.product_name} x{self.quantity}"
