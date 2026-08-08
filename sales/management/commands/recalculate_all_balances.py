import sys
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction

from sales.models import Customer, SalesInvoice, PaymentReceived
from purchase.models import Vendor, PurchaseInvoice, VendorPayment

class Command(BaseCommand):
    help = 'Recalculates all balances for customers and vendors by replaying ledger history chronologically.'

    def handle(self, *args, **options):
        self.stdout.write("Starting full ledger recalculation...")

        with transaction.atomic():
            self.recalculate_customers()
            self.recalculate_vendors()

        self.stdout.write(self.style.SUCCESS("Successfully recalculated all balances!"))

    def recalculate_customers(self):
        customers = Customer.objects.all()
        for customer in customers:
            self.stdout.write(f"Processing Customer: {customer.customer_name}")
            
            # Reset all current dynamic fields
            customer.advance_balance = Decimal('0.00')
            customer.credit_balance = Decimal('0.00')
            customer.save(update_fields=['advance_balance', 'credit_balance'])

            invoices = list(customer.invoices.filter(status='Saved'))
            for inv in invoices:
                inv.paid_amount = Decimal('0.00')
                inv.advance_applied = Decimal('0.00')
                inv.save(update_fields=['paid_amount', 'advance_applied'])

            payments = list(customer.payments.all())
            for pay in payments:
                pay.applied_to_invoice = Decimal('0.00')
                pay.applied_to_credit = Decimal('0.00')
                pay.applied_to_advance = Decimal('0.00')
                pay.balance_after = Decimal('0.00')
                pay.save(update_fields=['applied_to_invoice', 'applied_to_credit', 'applied_to_advance', 'balance_after'])

            # Create a unified timeline
            # type 0 for invoice (process first if same date), 1 for payment
            timeline = []
            for inv in invoices:
                timeline.append({'date': inv.date, 'created_at': inv.created_at, 'type': 0, 'obj': inv})
            for pay in payments:
                timeline.append({'date': pay.date, 'created_at': pay.created_at, 'type': 1, 'obj': pay})

            timeline.sort(key=lambda x: (x['date'], x['type'], x['created_at']))

            for item in timeline:
                if item['type'] == 0:
                    inv = item['obj']
                    # Consume advance if any
                    inv.advance_applied = customer.apply_invoice(inv.balance_due, is_credit=(inv.payment_term == 'Credit'))
                    inv.save(update_fields=['advance_applied'])
                else:
                    pay = item['obj']
                    amount = Decimal(str(pay.amount_received))
                    
                    # Manual specific allocation for linked invoice
                    if pay.invoice_id:
                        inv = SalesInvoice.objects.get(id=pay.invoice_id)
                        due = inv.balance_due
                        if due > 0:
                            apply_amt = min(due, amount)
                            inv.paid_amount += apply_amt
                            inv.save(update_fields=['paid_amount'])
                            amount -= apply_amt
                            pay.applied_to_invoice += apply_amt
                    
                    # General allocations via FIFO
                    if amount > 0:
                        allocs = customer.apply_payment(amount)
                        
                        for inv_num, amt in allocs.get('invoices', []):
                            pay.applied_to_invoice += amt
                            
                        pay.applied_to_credit = allocs.get('credit', Decimal('0.00'))
                        pay.applied_to_advance = allocs.get('advance', Decimal('0.00'))
                    else:
                        customer.update_credit_balance()
                        
                    pay.balance_after = customer.credit_balance
                    pay.save(update_fields=['applied_to_invoice', 'applied_to_credit', 'applied_to_advance', 'balance_after'])
            
            customer.update_credit_balance()

    def recalculate_vendors(self):
        vendors = Vendor.objects.all()
        for vendor in vendors:
            self.stdout.write(f"Processing Vendor: {vendor.vendor_name}")
            
            vendor.advance_balance = Decimal('0.00')
            vendor.payable_balance = Decimal('0.00')
            vendor.save(update_fields=['advance_balance', 'payable_balance'])

            invoices = list(vendor.invoices.filter(status='Saved', is_deleted=False))
            for inv in invoices:
                inv.paid_amount = Decimal('0.00')
                inv.advance_applied = Decimal('0.00')
                inv.save(update_fields=['paid_amount', 'advance_applied'])

            payments = list(vendor.payments.filter(is_deleted=False))
            for pay in payments:
                pay.applied_to_invoice = Decimal('0.00')
                pay.applied_to_payable = Decimal('0.00')
                pay.applied_to_advance = Decimal('0.00')
                pay.balance_after = Decimal('0.00')
                pay.save(update_fields=['applied_to_invoice', 'applied_to_payable', 'applied_to_advance', 'balance_after'])

            timeline = []
            for inv in invoices:
                timeline.append({'date': inv.date, 'created_at': inv.created_at, 'type': 0, 'obj': inv})
            for pay in payments:
                timeline.append({'date': pay.date, 'created_at': pay.created_at, 'type': 1, 'obj': pay})

            timeline.sort(key=lambda x: (x['date'], x['type'], x['created_at']))

            for item in timeline:
                if item['type'] == 0:
                    inv = item['obj']
                    inv.advance_applied = vendor.apply_invoice(inv.balance_due, is_credit=(inv.payment_term == 'Credit'))
                    inv.save(update_fields=['advance_applied'])
                else:
                    pay = item['obj']
                    amount = Decimal(str(pay.amount_paid))
                    
                    if pay.invoice_id:
                        inv = PurchaseInvoice.objects.get(id=pay.invoice_id)
                        due = inv.balance_due
                        if due > 0:
                            apply_amt = min(due, amount)
                            inv.paid_amount += apply_amt
                            inv.save(update_fields=['paid_amount'])
                            amount -= apply_amt
                            pay.applied_to_invoice += apply_amt
                    
                    if amount > 0:
                        allocs = vendor.apply_payment(amount)
                        
                        for inv_num, amt in allocs.get('invoices', []):
                            pay.applied_to_invoice += amt
                            
                        pay.applied_to_payable = allocs.get('payable', Decimal('0.00'))
                        pay.applied_to_advance = allocs.get('advance', Decimal('0.00'))
                    else:
                        vendor.update_payable_balance()
                        
                    pay.balance_after = vendor.payable_balance
                    pay.save(update_fields=['applied_to_invoice', 'applied_to_payable', 'applied_to_advance', 'balance_after'])
            
            vendor.update_payable_balance()
