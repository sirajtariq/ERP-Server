"""
Sales module data models: customers, invoices, and line items.
"""

from django.db import models


class Customer(models.Model):
    """Customer master record."""

    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


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
