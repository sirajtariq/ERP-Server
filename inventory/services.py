from decimal import Decimal
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone
from rest_framework import serializers

from inventory.models import Item, StockMovement


def get_item_current_stock(item: Item) -> Decimal:
    """
    Calculate dynamic current stock for an item from all recorded StockMovements.
    Current Stock = Total In - Total Out
    Returns Decimal('0.00') if no movements exist.
    """
    aggregates = item.stock_movements.aggregate(
        total_in=Sum('quantity', filter=Q(type='in')),
        total_out=Sum('quantity', filter=Q(type='out'))
    )
    total_in = aggregates['total_in'] or Decimal('0.00')
    total_out = aggregates['total_out'] or Decimal('0.00')
    return total_in - total_out


def calculate_item_summary(item: Item, start_date=None, end_date=None) -> dict:
    """
    Calculate comprehensive stock and financial metrics for a single item.
    - current_stock
    - total_in / total_out (optionally filtered by date range)
    - stock_value (current_stock * purchase_rate)
    - profit_per_unit (sale_rate - purchase_rate)
    - profit_margin_pct ((sale_rate - purchase_rate) / purchase_rate * 100)
    - stock_status ('out_of_stock', 'low_stock', 'in_stock')
    """
    current_stock = get_item_current_stock(item)

    movements_qs = item.stock_movements.all()
    if start_date:
        movements_qs = movements_qs.filter(date__gte=start_date)
    if end_date:
        movements_qs = movements_qs.filter(date__lte=end_date)

    in_out_agg = movements_qs.aggregate(
        total_in=Sum('quantity', filter=Q(type='in')),
        total_out=Sum('quantity', filter=Q(type='out'))
    )
    total_in = in_out_agg['total_in'] or Decimal('0.00')
    total_out = in_out_agg['total_out'] or Decimal('0.00')

    purchase_rate = item.purchase_rate or Decimal('0.00')
    sale_rate = item.sale_rate or Decimal('0.00')

    stock_value = current_stock * purchase_rate
    profit_per_unit = sale_rate - purchase_rate

    if purchase_rate > Decimal('0.00'):
        margin_dec = ((sale_rate - purchase_rate) / purchase_rate) * Decimal('100.00')
        profit_margin_pct = float(round(margin_dec, 2))
    else:
        profit_margin_pct = 0.0

    if current_stock <= Decimal('0.00'):
        stock_status = 'out_of_stock'
    elif current_stock <= item.min_stock:
        stock_status = 'low_stock'
    else:
        stock_status = 'in_stock'

    return {
        "current_stock": current_stock,
        "total_in": total_in,
        "total_out": total_out,
        "stock_value": stock_value,
        "profit_per_unit": profit_per_unit,
        "profit_margin_pct": profit_margin_pct,
        "stock_status": stock_status,
    }


def calculate_item_list_metrics(item: Item) -> dict:
    """
    Calculate lightweight metrics required for the Inventory Items table list view.
    - current_stock: Total In - Total Out
    - stock_value: (current_stock * purchase_rate)
    - profit_margin_pct: ((sale_rate - purchase_rate) / purchase_rate * 100)
    - stock_status: 'out_of_stock' | 'low_stock' | 'in_stock'
    """
    current_stock = get_item_current_stock(item)
    purchase_rate = item.purchase_rate or Decimal('0.00')
    sale_rate = item.sale_rate or Decimal('0.00')

    stock_value = current_stock * purchase_rate

    if purchase_rate > Decimal('0.00'):
        margin_dec = ((sale_rate - purchase_rate) / purchase_rate) * Decimal('100.00')
        profit_margin_pct = float(round(margin_dec, 2))
    else:
        profit_margin_pct = 0.00

    if current_stock <= Decimal('0.00'):
        stock_status = 'out_of_stock'
    elif current_stock <= item.min_stock:
        stock_status = 'low_stock'
    else:
        stock_status = 'in_stock'

    return {
        "current_stock": current_stock,
        "stock_value": stock_value,
        "profit_margin_pct": profit_margin_pct,
        "stock_status": stock_status,
    }


def calculate_inventory_global_kpis() -> dict:
    """
    Compute aggregate KPI metrics across all active (non-deleted) items:
    - total_items: Count of items
    - total_stock_value: Sum of (current_stock * purchase_rate)
    - total_potential_revenue: Sum of (current_stock * sale_rate)
    - low_stock_count: Count of items where 0 < current_stock <= min_stock
    - out_of_stock_count: Count of items where current_stock <= 0
    """
    active_items = Item.objects.filter(is_deleted=False)
    total_items = active_items.count()

    total_stock_value = Decimal('0.00')
    total_potential_revenue = Decimal('0.00')
    low_stock_count = 0
    out_of_stock_count = 0

    for item in active_items:
        metrics = calculate_item_list_metrics(item)
        current_stock = metrics["current_stock"]
        purchase_rate = item.purchase_rate or Decimal('0.00')
        sale_rate = item.sale_rate or Decimal('0.00')

        total_stock_value += (current_stock * purchase_rate)
        total_potential_revenue += (current_stock * sale_rate)

        if metrics["stock_status"] == 'out_of_stock':
            out_of_stock_count += 1
        elif metrics["stock_status"] == 'low_stock':
            low_stock_count += 1

    return {
        "totalItems": total_items,
        "totalStockValue": f"{total_stock_value:.2f}",
        "totalPotentialRevenue": f"{total_potential_revenue:.2f}",
        "lowStockCount": low_stock_count,
        "outOfStockCount": out_of_stock_count,
    }


def record_stock_movement(
    item: Item,
    movement_type: str,
    quantity,
    reason: str,
    date=None,
    notes: str = None,
    ref_type: str = 'manual',
    ref_id: int = None
) -> StockMovement:
    """
    Atomically record a stock movement with row-level locking to prevent concurrency races.
    Validates stock availability for 'out' movements.
    """
    if movement_type not in ['in', 'out']:
        raise serializers.ValidationError({"type": "Invalid movement type. Must be 'in' or 'out'."})

    quantity_dec = Decimal(str(quantity))
    if quantity_dec <= Decimal('0.00'):
        raise serializers.ValidationError({"quantity": "Quantity must be greater than zero."})

    with transaction.atomic():
        locked_item = Item.objects.filter(pk=item.pk).select_for_update().first()
        if not locked_item:
            locked_item = item

        stock_before = get_item_current_stock(locked_item)

        if movement_type == 'out' and quantity_dec > stock_before:
            raise serializers.ValidationError({
                "qty": "Cannot deduct more than available stock.",
                "quantity": "Cannot deduct more than available stock."
            })

        if movement_type == 'in':
            stock_after = stock_before + quantity_dec
        else:
            stock_after = stock_before - quantity_dec

        if date is None:
            date = timezone.now().date()

        movement = StockMovement.objects.create(
            item=locked_item,
            date=date,
            type=movement_type,
            quantity=quantity_dec,
            reason=reason,
            notes=notes,
            stock_before=stock_before,
            stock_after=stock_after,
            reference_type=ref_type,
            reference_id=ref_id
        )

        return movement


def process_sales_invoice_stock(invoice) -> list:
    """
    Evaluates and records outward stock movements for all line items in a SalesInvoice.
    Runs inside transaction.atomic().
    Validates available stock using select_for_update() and get_item_current_stock().
    Raises serializers.ValidationError if requested_qty > current_stock.
    """
    movements = []
    with transaction.atomic():
        for line_item in invoice.items.all():
            item_name = line_item.item_name
            # Resolve item in inventory catalog (exact match or partial match)
            item = Item.objects.filter(
                Q(name__iexact=item_name) | Q(item_code__iexact=item_name),
                is_deleted=False
            ).select_for_update().first()

            if not item:
                item = Item.objects.filter(
                    name__icontains=item_name,
                    is_deleted=False
                ).select_for_update().first()

            if not item:
                continue

            current_stock = get_item_current_stock(item)
            requested_qty = Decimal(str(line_item.quantity))

            if requested_qty > current_stock:
                raise serializers.ValidationError({
                    "detail": f"Insufficient stock for '{item.name}'. Available: {current_stock:.2f}, Requested: {requested_qty:.2f}"
                })

            inv_date = getattr(invoice, 'date', None) or timezone.now().date()
            inv_no = getattr(invoice, 'invoice_number', None) or f"ID-{invoice.id}"

            movement = record_stock_movement(
                item=item,
                movement_type='out',
                quantity=requested_qty,
                reason='Sale',
                date=inv_date,
                notes=f"Sales Invoice #{inv_no}",
                ref_type='sales_invoice',
                ref_id=invoice.id
            )
            movements.append(movement)
    return movements


def process_purchase_bill_stock(bill) -> list:
    """
    Evaluates and records inward stock movements for all line items in a PurchaseInvoice / Bill.
    Runs inside transaction.atomic().
    Updates item master purchase_rate to bill_line.purchase_price.
    """
    movements = []
    with transaction.atomic():
        for line_item in bill.items.all():
            product_name = line_item.product_name
            item = Item.objects.filter(
                Q(name__iexact=product_name) | Q(item_code__iexact=product_name),
                is_deleted=False
            ).select_for_update().first()

            if not item:
                item = Item.objects.filter(
                    name__icontains=product_name,
                    is_deleted=False
                ).select_for_update().first()

            if not item:
                continue

            qty = Decimal(str(line_item.quantity))
            bill_date = getattr(bill, 'date', None) or timezone.now().date()
            bill_no = getattr(bill, 'invoice_number', None) or f"ID-{bill.id}"

            movement = record_stock_movement(
                item=item,
                movement_type='in',
                quantity=qty,
                reason='Purchase',
                date=bill_date,
                notes=f"Purchase Bill #{bill_no}",
                ref_type='purchase_bill',
                ref_id=bill.id
            )
            movements.append(movement)

            # Master Purchase Rate Auto-Update
            new_purchase_rate = Decimal(str(line_item.purchase_price))
            item.purchase_rate = new_purchase_rate
            item.save(update_fields=['purchase_rate', 'updated_at'])

    return movements


def reverse_document_stock(ref_type: str, ref_id: int) -> int:
    """
    Purges stock movement audit records matching reference_type and reference_id.
    Runs inside transaction.atomic().
    Restores dynamic current stock automatically for all affected items.
    """
    with transaction.atomic():
        deleted_count, _ = StockMovement.objects.filter(
            reference_type=ref_type,
            reference_id=ref_id
        ).delete()
    return deleted_count
