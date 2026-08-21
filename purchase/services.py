from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.utils import timezone
from purchase.models import Expense


def _quantize_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@transaction.atomic
def record_auto_expense(
    amount,
    date=None,
    category="Salary",
    description="",
    payment_method="Cash",
    paid_by="",
    reference_type=None,
    reference_id=None,
) -> Expense:
    """
    Creates an automated Expense record linked to a reference (e.g. salary_payment).
    Wrapped in transaction.atomic.
    """
    if date is None:
        date = timezone.localdate()

    quantized_amount = _quantize_decimal(amount)

    expense = Expense.objects.create(
        category=category,
        amount=quantized_amount,
        person_supplier=paid_by or "Auto-Generated",
        paid_by=paid_by,
        payment_method=payment_method or "Cash",
        date=date,
        notes=description,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    return expense


@transaction.atomic
def reverse_auto_expense(reference_type: str, reference_id: int):
    """
    Deletes or soft-deletes any Expense record linked to reference_type and reference_id.
    Wrapped in transaction.atomic.
    """
    if not reference_type or not reference_id:
        return

    expenses = Expense.objects.filter(
        reference_type=reference_type,
        reference_id=reference_id
    )
    for exp in expenses:
        exp.delete()


from rest_framework.exceptions import ValidationError
from inventory.models import Item
from purchase.models import PurchaseItem

@transaction.atomic
def process_purchase_invoice_items(invoice, items_data):
    """
    Processes line items for a purchase invoice. 
    Creates inventory items on-the-fly if marked as new or missing an item_id.
    Wrapped in transaction.atomic to ensure no orphan records or partial failures.
    """
    for item_data in items_data:
        is_new = item_data.pop('is_new', False)
        category = item_data.pop('category', None) or 'General'
        sale_rate = item_data.pop('sale_rate', None) or Decimal('0.00')
        
        if is_new or not item_data.get('product_id'):
            item_code = item_data.get('item_code')
            
            if item_code:
                code_exists = Item.objects.filter(item_code__iexact=item_code, is_deleted=False).exists()
                if code_exists:
                    raise ValidationError(
                        {"items": f"Item code '{item_code}' already exists. Please enter a different unique code."}
                    )
            
            item_name = item_data.get('product_name', 'Unknown Item')
            units = item_data.get('units') or 'pcs'
            purchase_price = item_data.get('purchase_price', Decimal('0.00'))
            
            if not item_code:
                import uuid
                item_code = f"ITM-{uuid.uuid4().hex[:6].upper()}"
                
            new_item = Item.objects.create(
                item_code=item_code,
                name=item_name,
                category=category,
                unit=units,
                purchase_rate=purchase_price,
                sale_rate=sale_rate,
                opening_stock=Decimal('0.00'),
                min_stock=Decimal('0.00')
            )
            item_data['product_id'] = new_item.id
            item_data['item_code'] = new_item.item_code
            
        PurchaseItem.objects.create(invoice=invoice, **item_data)

