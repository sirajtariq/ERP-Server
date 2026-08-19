"""
Sales module data models: customers, invoices, and line items.
"""

from decimal import Decimal
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from sales.base_models import SoftDeleteModel


class Customer(SoftDeleteModel):
    """Customer master record."""

    CUSTOMER_TYPE_CHOICES = (
        ('permanent', 'Permanent'),
        ('walkin', 'Walk-in'),
    )

    customer_id = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    customer_name = models.CharField(max_length=255)
    customer_type = models.CharField(max_length=10, choices=CUSTOMER_TYPE_CHOICES)
    phone = models.CharField(max_length=20, unique=True, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(default="")
    opening_credit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    opening_note = models.TextField(blank=True)
    tax_number = models.CharField(max_length=50, blank=True, null=True)
    credit_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))])
    advance_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.customer_name

    def get_calculated_balances(self):
        """Returns current credit_balance and advance_balance."""
        return self.credit_balance, self.advance_balance

    def recalculate_balances(self):
        """
        Recalculates credit_balance and advance_balance taking into account:
        - Active invoices net_total_after_returns
        - Total payments received
        - Cash refunds issued (must be subtracted from retained cash)
        - Opening credit
        """
        from decimal import Decimal

        opening = Decimal(str(self.opening_credit or '0.00'))

        # 1. Sum of net_total_after_returns for active saved invoices
        inv_sum = sum((inv.net_total_after_returns for inv in self.invoices.filter(status='Saved')), Decimal('0.00'))

        # 2. Total Payments Received
        pay_sum = sum((p.amount_received for p in self.payments.all()), Decimal('0.00'))

        # 3. Cash Refunds Issued (Money returned to customer physically)
        cash_refunds = sum(
            (r.net_return_amount for r in self.returns.filter(status='Saved', refund_type='CASH')),
            Decimal('0.00')
        )

        # Actual retained cash with us
        retained_cash = pay_sum - cash_refunds

        # Total net liability
        total_debts = opening + inv_sum

        net_balance = total_debts - retained_cash

        if net_balance >= Decimal('0.00'):
            new_credit = net_balance
            new_advance = Decimal('0.00')
        else:
            new_credit = Decimal('0.00')
            new_advance = abs(net_balance)

        self.credit_balance = new_credit
        self.advance_balance = new_advance
        self.save(update_fields=['credit_balance', 'advance_balance'])

        return self.credit_balance, self.advance_balance

    def apply_payment(self, amount: Decimal):
        """
        Leftover overflows directly into advance_balance.
        Must be called within a transaction.
        Returns a tuple: (applied_to_credit, applied_to_advance)
        """
        remaining = Decimal(str(amount))
        applied_to_credit = Decimal('0.00')
        applied_to_advance = remaining

        if remaining > 0:
            self.advance_balance += applied_to_advance
            self.save(update_fields=['advance_balance'])

        return applied_to_credit, applied_to_advance

    def reverse_payment(self, applied_to_credit: Decimal, applied_to_advance: Decimal):
        """
        Reverses a payment by decreasing advance_balance.
        """
        if applied_to_advance > 0:
            self.advance_balance -= applied_to_advance
            self.save(update_fields=['advance_balance'])

    def apply_invoice(self, amount: Decimal, is_credit: bool = True):
        """
        Consumes available advance to cover the invoice amount.
        Returns the amount of advance consumed.
        """
        remaining = Decimal(str(amount))
        consumed_advance = Decimal('0.00')

        if self.advance_balance > 0 and remaining > 0:
            consumed_advance = min(self.advance_balance, remaining)
            self.advance_balance -= consumed_advance
            self.save(update_fields=['advance_balance'])
            
        return consumed_advance

    def reverse_invoice(self, balance_due: Decimal, advance_applied: Decimal, is_credit: bool = True):
        """
        Reverses the effects of an invoice on the customer's balances.
        """
        if advance_applied > 0:
            self.advance_balance += advance_applied
            self.save(update_fields=['advance_balance'])

    def save(self, *args, **kwargs):
        is_new = not self.id
        if is_new:
            max_attempts = 5
            for attempt in range(max_attempts):
                with transaction.atomic():
                    if self.customer_type == 'walkin':
                        last = Customer.all_objects.filter(
                            customer_type='walkin'
                        ).order_by('-customer_id').first()
                        if last and getattr(last, 'customer_id', '').startswith('WI-'):
                            last_number = int(last.customer_id.split('-')[1])
                            new_id = f"WI-{last_number + 1:05d}"
                        else:
                            new_id = "WI-00001"
                    else:
                        last = Customer.all_objects.filter(
                            customer_type='permanent'
                        ).order_by('-customer_id').first()
                        if last and getattr(last, 'customer_id', '').startswith('PR-'):
                            last_number = int(last.customer_id.split('-')[1])
                            new_id = f"PR-{last_number + 1:05d}"
                        else:
                            new_id = "PR-00001"

                    self.customer_id = new_id

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
    invoice_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0.0, help_text="Discount percentage (0-100)")
    
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
        return sum((item.total for item in self.items.all()), Decimal('0.00')).quantize(Decimal('0.01'))

    @property
    def total_line_discount(self):
        return sum(((item.quantity * item.rate) * (item.discount / Decimal('100')) for item in self.items.all()), Decimal('0.00')).quantize(Decimal('0.01'))

    @property
    def tax_amount(self):
        deducted_invoice_discount = self.subtotal * (Decimal(str(self.invoice_discount)) / Decimal('100'))
        return ((self.subtotal - deducted_invoice_discount) * (Decimal(str(self.vat_percentage)) / Decimal('100'))).quantize(Decimal('0.01'))

    @property
    def net_total(self):
        deducted_invoice_discount = self.subtotal * (Decimal(str(self.invoice_discount)) / Decimal('100'))
        return ((self.subtotal - deducted_invoice_discount) + self.tax_amount).quantize(Decimal('0.01'))

    @property
    def paid(self):
        """
        Dynamically calculates the actual paid amount strictly based on:
        a) Direct payments linked to this invoice
        b) Unallocated general advances consumed sequentially (FIFO) by older unpaid invoices.
        """
        from sales.models import PaymentReceived
        
        direct_payments = sum((p.amount_received for p in self.payments.all()), Decimal('0.00'))
        
        if not self.customer:
            return min(self.net_total_after_returns, direct_payments).quantize(Decimal('0.01'))
            
        general_payments = PaymentReceived.objects.filter(
            customer=self.customer, invoice__isnull=True
        ).order_by('date', 'id')
        total_advance = sum((p.amount_received for p in general_payments), Decimal('0.00'))
        
        older_invoices = self.customer.invoices.filter(
            status='Saved', id__lt=self.id
        ).order_by('date', 'id')
        
        for inv in older_invoices:
            if total_advance <= Decimal('0.00'):
                break
            inv_direct = sum((p.amount_received for p in inv.payments.all()), Decimal('0.00'))
            inv_need = inv.net_total_after_returns - inv_direct
            if inv_need > Decimal('0.00'):
                consumed = min(total_advance, inv_need)
                total_advance -= consumed
                
        this_need = self.net_total_after_returns - direct_payments
        consumed_here = Decimal('0.00')
        if this_need > Decimal('0.00') and total_advance > Decimal('0.00'):
            consumed_here = min(total_advance, this_need)
            
        total_paid = direct_payments + consumed_here
        return min(self.net_total_after_returns, total_paid).quantize(Decimal('0.01'))

    @property
    def balance_due(self):
        net_after_returns = self.net_total_after_returns
        paid = self.paid
        pending = max(Decimal('0.00'), net_after_returns - paid)
        return pending.quantize(Decimal('0.01'))

    @property
    def total_returned_amount(self):
        from decimal import Decimal
        return sum((ret.net_return_amount for ret in self.returns.all() if ret.status == 'Saved'), Decimal('0.00')).quantize(Decimal('0.01'))

    @property
    def net_total_after_returns(self):
        return (self.net_total - self.total_returned_amount).quantize(Decimal('0.01'))

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

        if hasattr(self, 'customer') and self.customer:
            self.customer.recalculate_balances()

    def delete(self, *args, **kwargs):
        cust = self.customer if hasattr(self, 'customer') else None
        super().delete(*args, **kwargs)
        if cust:
            cust.recalculate_balances()


class SalesItem(models.Model):
    """Line item belonging to a sales invoice."""

    invoice = models.ForeignKey(
        SalesInvoice,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        'inventory.Item',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales_items'
    )
    item_code = models.CharField(max_length=100, blank=True, null=True)
    item_name = models.CharField(max_length=255)
    units = models.CharField(max_length=50, default='pcs')
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0.0, help_text="Discount percentage (0-100)")

    class Meta:
        ordering = ["id"]

    @property
    def total(self):
        return ((self.quantity * self.rate) - ((self.quantity * self.rate) * (self.discount / Decimal('100')))).quantize(Decimal('0.01'))

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
        if hasattr(self, 'customer') and self.customer:
            self.customer.recalculate_balances()

    def delete(self, *args, **kwargs):
        cust = self.customer if hasattr(self, 'customer') else None
        super().delete(*args, **kwargs)
        if cust:
            cust.recalculate_balances()

    def __str__(self) -> str:
        return f"{self.receipt_number} — {self.customer}"


class Quotation(SoftDeleteModel):
    """Sales Quotation header."""
    
    quotation_number = models.CharField(max_length=50, unique=True, blank=True)
    customer_data = models.JSONField(help_text="Stored exactly as received (customer_name, phone, etc.)")
    date = models.DateField(default=timezone.localdate)
    valid_days = models.PositiveIntegerField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    
    PAYMENT_TERM_CHOICES = (
        ('cash', 'Cash'),
        ('credit', 'Credit'),
    )
    payment_term = models.CharField(max_length=10, choices=PAYMENT_TERM_CHOICES, default='cash')
    
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    vat_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('converted', 'Converted'),
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    
    converted_invoice = models.ForeignKey(
        'SalesInvoice', on_delete=models.SET_NULL, null=True, blank=True, related_name='quotations'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]

    @property
    def subtotal(self):
        return sum((item.line_total for item in self.items.all()), Decimal('0.00')).quantize(Decimal('0.01'))

    @property
    def discount_amount(self):
        return (self.subtotal * (Decimal(str(self.discount_percentage)) / Decimal('100'))).quantize(Decimal('0.01'))

    @property
    def vat_amount(self):
        return ((self.subtotal - self.discount_amount) * (Decimal(str(self.vat_percentage)) / Decimal('100'))).quantize(Decimal('0.01'))

    @property
    def total(self):
        return (self.subtotal - self.discount_amount + self.vat_amount).quantize(Decimal('0.01'))
        
    @property
    def is_expired(self):
        if self.valid_until and self.valid_until < timezone.localdate() and self.status not in ['converted', 'rejected']:
            return True
        return False

    @property
    def validity_display(self):
        if self.valid_until is None:
            return "No Expiry"
        if self.is_expired:
            return "Expired"
        today = timezone.localdate()
        if self.valid_until == today:
            return "Expires today"
        if self.valid_until and self.valid_until > today:
            days = (self.valid_until - today).days
            return f"{days} days left"
        return ""

    @property
    def effective_status(self):
        if self.is_expired:
            return "expired"
        return self.status

    def save(self, *args, **kwargs):
        if self.valid_days is not None and self.date:
            from datetime import timedelta
            self.valid_until = self.date + timedelta(days=self.valid_days)
        else:
            self.valid_until = None
            
        if not self.quotation_number:
            from datetime import date
            current_year = date.today().year
            prefix = f'QT-{current_year}-'
            
            max_attempts = 5
            for attempt in range(max_attempts):
                with transaction.atomic():
                    last = Quotation.all_objects.filter(
                        quotation_number__startswith=prefix
                    ).order_by('-id').first()
                    
                    if last:
                        last_number = int(last.quotation_number.split('-')[-1])
                        new_number = last_number + 1
                    else:
                        new_number = 1
                        
                    self.quotation_number = f'{prefix}{new_number:05d}'
                    
                    try:
                        with transaction.atomic():
                            super().save(*args, **kwargs)
                        break
                    except IntegrityError as e:
                        if 'quotation_number' in str(e) and attempt < max_attempts - 1:
                            continue
                        raise
        else:
            super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.quotation_number


class QuotationItem(models.Model):
    """Line item belonging to a quotation."""

    quotation = models.ForeignKey(
        Quotation,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item_name = models.CharField(max_length=255)
    unit = models.CharField(max_length=50, default='pcs')
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0.0, help_text="Discount percentage (0-100)")

    class Meta:
        ordering = ["id"]

    @property
    def line_total(self):
        return (self.qty * self.rate) - ((self.qty * self.rate) * (self.discount / Decimal('100')))

    def __str__(self) -> str:
        return f"{self.item_name} x{self.qty}"


class SalesReturn(SoftDeleteModel):
    """Sales Return / Credit Note header linked to an invoice and customer."""

    STATUS_CHOICES = (
        ('Draft', 'Draft'),
        ('Saved', 'Saved'),
    )

    REFUND_TYPE_CHOICES = (
        ('STORE_CREDIT', 'Store Credit / Customer Balance'),
        ('CASH', 'Cash Refund Paid at Counter'),
    )

    return_number = models.CharField(max_length=50, unique=True, blank=True)
    invoice = models.ForeignKey(
        SalesInvoice,
        on_delete=models.PROTECT,
        related_name='returns',
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name='returns',
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Draft')
    refund_type = models.CharField(max_length=20, choices=REFUND_TYPE_CHOICES, default='STORE_CREDIT')
    return_date = models.DateField(default=timezone.localdate)
    reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    applied_to_credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    applied_to_advance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-return_date", "-id"]

    @property
    def net_return_amount(self):
        return sum((item.total for item in self.items.all()), Decimal('0.00')).quantize(Decimal('0.01'))

    def save(self, *args, **kwargs):
        if not self.return_number:
            from datetime import date
            current_year = date.today().year
            prefix = f'CN-{current_year}-'

            max_attempts = 5
            for attempt in range(max_attempts):
                with transaction.atomic():
                    last = SalesReturn.all_objects.filter(
                        return_number__startswith=prefix
                    ).order_by('-id').first()

                    if last:
                        last_number = int(last.return_number.split('-')[-1])
                        new_number = last_number + 1
                    else:
                        new_number = 1

                    self.return_number = f'{prefix}{new_number:05d}'

                    try:
                        with transaction.atomic():
                            super().save(*args, **kwargs)
                        break  # success — exit retry loop
                    except IntegrityError as e:
                        if 'return_number' in str(e) and attempt < max_attempts - 1:
                            continue
                        raise
        else:
            super().save(*args, **kwargs)

        if hasattr(self, 'customer') and self.customer:
            self.customer.recalculate_balances()

    def delete(self, *args, **kwargs):
        cust = self.customer if hasattr(self, 'customer') else None
        super().delete(*args, **kwargs)
        if cust:
            cust.recalculate_balances()

    def __str__(self) -> str:
        return self.return_number


class SalesReturnItem(SoftDeleteModel):
    """Line item belonging to a sales return / credit note."""

    sales_return = models.ForeignKey(
        SalesReturn,
        on_delete=models.CASCADE,
        related_name='items',
    )
    sales_item = models.ForeignKey(
        SalesItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='return_items',
    )
    item_name = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.0,
        help_text="Discount percentage (0-100)"
    )

    class Meta:
        ordering = ["id"]

    @property
    def total(self):
        return ((self.quantity * self.rate) - (
            (self.quantity * self.rate) * (self.discount / Decimal('100'))
        )).quantize(Decimal('0.01'))

    def __str__(self) -> str:
        return f"{self.item_name} x{self.quantity}"
