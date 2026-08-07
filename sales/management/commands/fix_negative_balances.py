from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from sales.models import Customer
from purchase.models import Vendor

class Command(BaseCommand):
    help = 'Fixes negative credit/payable and advance balances for customers and vendors'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting to fix negative balances...")
        
        with transaction.atomic():
            # 1. Customers
            customers = Customer.all_objects.filter(credit_balance__lt=0)
            for customer in customers:
                amount = abs(customer.credit_balance)
                self.stdout.write(f"Customer {customer.customer_name} (ID: {customer.id}) has negative credit_balance: {customer.credit_balance}. Moving {amount} to advance_balance.")
                customer.advance_balance += amount
                customer.credit_balance = Decimal('0.00')
                customer.save(update_fields=['credit_balance', 'advance_balance'])

            neg_adv_customers = Customer.all_objects.filter(advance_balance__lt=0)
            for customer in neg_adv_customers:
                amount = abs(customer.advance_balance)
                self.stdout.write(f"Customer {customer.customer_name} (ID: {customer.id}) has negative advance_balance: {customer.advance_balance}. Moving {amount} to credit_balance.")
                customer.credit_balance += amount
                customer.advance_balance = Decimal('0.00')
                customer.save(update_fields=['credit_balance', 'advance_balance'])

            # 2. Vendors
            vendors = Vendor.all_objects.filter(payable_balance__lt=0)
            for vendor in vendors:
                amount = abs(vendor.payable_balance)
                self.stdout.write(f"Vendor {vendor.vendor_name} (ID: {vendor.id}) has negative payable_balance: {vendor.payable_balance}. Moving {amount} to advance_balance.")
                vendor.advance_balance += amount
                vendor.payable_balance = Decimal('0.00')
                vendor.save(update_fields=['payable_balance', 'advance_balance'])

            neg_adv_vendors = Vendor.all_objects.filter(advance_balance__lt=0)
            for vendor in neg_adv_vendors:
                amount = abs(vendor.advance_balance)
                self.stdout.write(f"Vendor {vendor.vendor_name} (ID: {vendor.id}) has negative advance_balance: {vendor.advance_balance}. Moving {amount} to payable_balance.")
                vendor.payable_balance += amount
                vendor.advance_balance = Decimal('0.00')
                vendor.save(update_fields=['payable_balance', 'advance_balance'])

        self.stdout.write(self.style.SUCCESS("Successfully fixed negative balances."))
