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
