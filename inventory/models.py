from decimal import Decimal
from django.db import models
from django.utils import timezone


class Item(models.Model):
    """
    Inventory Item model.
    Stores metadata, rates, opening stock configuration, and soft-deletion flag.
    NO mathematical logic or stock calculation methods exist on this model.
    All stock calculations are handled by inventory.services.
    """
    item_code = models.CharField(max_length=100, db_index=True)
    name = models.CharField(max_length=255, db_index=True)
    category = models.CharField(max_length=100, db_index=True)
    unit = models.CharField(max_length=50)
    purchase_rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    sale_rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    opening_stock = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    min_stock = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    description = models.TextField(blank=True, null=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f"{self.item_code} - {self.name}"


class StockMovement(models.Model):
    """
    Audit log of all stock movements (In / Out).
    Created exclusively via inventory.services.record_stock_movement.
    """
    MOVEMENT_TYPES = (
        ('in', 'In'),
        ('out', 'Out'),
    )

    item = models.ForeignKey(
        Item,
        related_name='stock_movements',
        on_delete=models.CASCADE
    )
    date = models.DateField(default=timezone.now)
    type = models.CharField(max_length=10, choices=MOVEMENT_TYPES)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=100)
    notes = models.TextField(blank=True, null=True)
    stock_before = models.DecimalField(max_digits=12, decimal_places=2)
    stock_after = models.DecimalField(max_digits=12, decimal_places=2)
    reference_type = models.CharField(max_length=50, default='manual')
    reference_id = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"{self.type.upper()} {self.quantity} {self.item.unit} - {self.item.name} ({self.reason})"
