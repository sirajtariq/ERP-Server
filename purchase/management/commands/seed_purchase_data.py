import random
from decimal import Decimal
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from faker import Faker

from purchase.models import Vendor, PurchaseInvoice, PurchaseItem, VendorPayment, Expense
from purchase.serializers import PurchaseInvoiceSerializer, VendorPaymentSerializer

fake = Faker('en_PK')

# Data banks
VENDORS = [
    "Al-Habib Traders", "Khan Brothers Hardware", "Chaudhry Steel Suppliers", 
    "Rawalpindi Textile House", "Mian & Sons Distributors", "Bismillah Enterprises", 
    "Madina Cash & Carry", "Rehmat Foods", "Haji Sabir Grocers", "Kashmir Traders", 
    "Nawaz Electronics", "Ittifaq Corporation", "Raja Traders", "Makkah Suppliers", "Awami Store"
]
PRODUCTS = [
    "Rice 25kg Bag", "Sugar 50kg", "Cooking Oil 5L", "Dal Chana 50kg", 
    "Tea 1kg", "Salt 1kg", "Spices Box", "Milk Powder 1kg", 
    "Detergent 5kg", "Shampoo Bottle", "Biscuits Carton", "Juice Pack"
]
EXPENSE_CATEGORIES = [
    "Salary", "Shop Rent", "Electricity Bill", "Fuel", "Tea & Refreshments", 
    "Packing Material", "Loading/Unloading Labor", "Miscellaneous", 
    "Stationery", "Internet Bill", "Water Bill"
]

class Command(BaseCommand):
    help = 'Seeds the purchase app with realistic mock data.'

    def add_arguments(self, parser):
        parser.add_argument('--flush', action='store_true', help='Flush existing data before seeding.')

    def handle(self, *args, **kwargs):
        flush = kwargs['flush']

        if flush:
            self.stdout.write(self.style.WARNING('Flushing existing purchase data...'))
            with transaction.atomic():
                Expense.all_objects.all().delete()
                VendorPayment.all_objects.all().delete()
                PurchaseItem.objects.all().delete()
                PurchaseInvoice.all_objects.all().delete()
                Vendor.all_objects.all().delete()
            self.stdout.write(self.style.SUCCESS('Successfully flushed data.'))

        with transaction.atomic():
            self.seed_data()
            self.print_summary()

    def seed_data(self):
        self.stdout.write('Creating vendors...')
        vendor_objs = []
        for i, name in enumerate(VENDORS):
            phone = None
            if i >= 3: # leave first 3 without phone to test null-phone uniqueness
                phone = f"03{random.randint(10, 49):02d}{random.randint(1000000, 9999999)}"
            
            opening = Decimal('0.00')
            if random.random() > 0.6:
                opening = Decimal(str(random.randint(10, 500) * 100))
                
            vendor = Vendor.objects.create(
                vendor_name=name,
                phone=phone,
                email=fake.email() if random.random() > 0.5 else None,
                address=random.choice(["Lahore", "Karachi", "Islamabad", "Faisalabad", "Multan"]) + ", " + fake.street_address(),
                tax_number=f"NTN-{random.randint(1000000, 9999999)}" if random.random() > 0.5 else "",
                opening_payable=opening,
                payable_balance=opening, # Ensure payable_balance reflects opening
            )
            vendor_objs.append(vendor)

        self.stdout.write('Creating purchase invoices...')
        invoices = []
        num_invoices = random.randint(40, 50)
        
        # Distribute unevenly
        active_vendors = random.choices(vendor_objs, weights=[1]*3 + [5]*6 + [10]*6, k=num_invoices)
        
        for vendor in active_vendors:
            status = 'Saved' if random.random() > 0.3 else 'Draft'
            
            num_items = random.randint(1, 5)
            items_data = []
            subtotal = Decimal('0.00')
            for _ in range(num_items):
                qty = Decimal(str(random.randint(1, 50)))
                price = Decimal(str(random.randint(5, 500) * 10))
                disc = Decimal('0.00')
                if random.random() > 0.8:
                    disc = Decimal(str(random.randint(1, 5) * 10))
                
                items_data.append({
                    "product_name": random.choice(PRODUCTS),
                    "units": random.choice(["Kg", "Bag", "Ltr", "Box"]),
                    "quantity": str(qty),
                    "purchase_price": str(price),
                    "discount": str(disc),
                })
                subtotal += (qty * price) - disc
            
            vat_perc = Decimal(random.choice(['0.00', '5.00', '17.00']))
            tax_amt = (subtotal * (vat_perc / Decimal('100'))).quantize(Decimal('0.01'))
            inv_disc = Decimal('0.00')
            if random.random() > 0.8:
                inv_disc = Decimal(str(random.randint(1, 15)))
                
            net_total = subtotal + tax_amt - inv_disc
            if net_total < 0:
                inv_disc = Decimal('0.00')
                net_total = subtotal + tax_amt
                
            if status == 'Saved':
                pay_scenario = random.choice(['unpaid', 'partial', 'paid', 'overpaid'])
                if pay_scenario == 'unpaid':
                    paid = Decimal('0.00')
                    term = 'Credit'
                elif pay_scenario == 'partial':
                    paid = Decimal(str(int(net_total / 2)))
                    term = 'Credit'
                elif pay_scenario == 'paid':
                    paid = net_total
                    term = 'Cash'
                else: # overpaid
                    paid = net_total + Decimal(str(random.randint(10, 100) * 10))
                    term = 'Cash' # Fully covered means Cash
            else:
                paid = Decimal('0.00')
                term = 'Credit'

            days_ago = random.randint(0, 180)
            date_val = timezone.localdate() - timedelta(days=days_ago)
            
            bill_num = random.choice([f"INV-2024-{random.randint(1000, 9999)}", f"B-{random.randint(100, 9999)}", f"{random.randint(10000, 99999)}"])

            # Reload vendor to get latest advance balance inside the loop since previous invoices might have modified it
            vendor.refresh_from_db()

            data = {
                "vendor": {
                    "id": vendor.id,
                    "vendor_name": vendor.vendor_name,
                    "phone": vendor.phone or "",
                },
                "bill_number": bill_num,
                "date": date_val.isoformat(),
                "payment_term": term,
                "payment_method": "Cash" if paid > 0 else "",
                "paid_amount": str(paid),
                "vat_percentage": str(vat_perc),
                "invoice_discount": str(inv_disc),
                "status": status,
                "items": items_data,
            }
            
            # The serializer might raise ValidationErrors if advance consumption causes the invoice to be fully paid but term is Credit.
            # Let's adjust term based on effective coverage to prevent ValidationError:
            effective_coverage = paid + vendor.advance_balance
            if status == 'Saved':
                if effective_coverage >= net_total:
                    data['payment_term'] = 'Cash'
                else:
                    data['payment_term'] = 'Credit'

            serializer = PurchaseInvoiceSerializer(data=data)
            serializer.is_valid(raise_exception=True)
            invoice = serializer.save()
            invoices.append(invoice)

        self.stdout.write('Creating vendor payments...')
        num_payments = random.randint(15, 20)
        for _ in range(num_payments):
            vendor = random.choice(vendor_objs)
            vendor.refresh_from_db()

            unpaid_invoices = [inv for inv in invoices if inv.vendor_id == vendor.id and inv.status == 'Saved' and not inv.is_deleted]
            # Must reload invoices to get true balance_due since it might have changed
            for u_inv in unpaid_invoices:
                u_inv.refresh_from_db()
            unpaid_invoices = [inv for inv in unpaid_invoices if inv.balance_due > 0]
            
            invoice = random.choice(unpaid_invoices) if unpaid_invoices and random.random() > 0.5 else None
            
            if invoice:
                amount = invoice.balance_due
                if random.random() > 0.8: # Overpay
                    amount += Decimal(str(random.randint(1, 10) * 100))
            else:
                amount = Decimal(str(random.randint(10, 500) * 100))
                
            days_ago = random.randint(0, 180)
            date_val = timezone.localdate() - timedelta(days=days_ago)
            
            data = {
                "vendor": {
                    "id": vendor.id,
                    "vendor_name": vendor.vendor_name,
                    "phone": vendor.phone or "",
                },
                "amount_paid": str(amount.quantize(Decimal('0.01'))),
                "method": random.choice(["Cash", "Bank Transfer", "Cheque"]),
                "notes": "Payment towards account",
                "date": date_val.isoformat(),
            }
            if invoice:
                data["invoice"] = invoice.invoice_number
                
            serializer = VendorPaymentSerializer(data=data)
            serializer.is_valid(raise_exception=True)
            serializer.save()

        self.stdout.write('Creating expenses...')
        num_expenses = random.randint(20, 25)
        for _ in range(num_expenses):
            days_ago = random.randint(0, 180)
            date_val = timezone.localdate() - timedelta(days=days_ago)
            Expense.objects.create(
                category=random.choice(EXPENSE_CATEGORIES),
                amount=Decimal(str(random.randint(1, 50) * 100)),
                payment_method="Cash",
                date=date_val,
                notes=fake.sentence(),
            )

        self.stdout.write('Soft-deleting some records...')
        for vendor in random.sample(vendor_objs, 2):
            vendor.soft_delete()
            
        saved_invoices = [inv for inv in invoices if inv.status == 'Saved' and not inv.is_deleted]
        for inv in random.sample(saved_invoices, min(3, len(saved_invoices))):
            inv.refresh_from_db()
            serializer = PurchaseInvoiceSerializer()
            serializer._reverse_invoice_balance_effects(inv)
            inv.soft_delete()
            
        all_payments = list(VendorPayment.objects.filter(is_deleted=False))
        for payment in random.sample(all_payments, min(3, len(all_payments))):
            payment.refresh_from_db()
            serializer = VendorPaymentSerializer()
            serializer._reverse_payment(
                payment.vendor,
                payment.invoice,
                payment.applied_to_invoice,
                payment.applied_to_payable,
                payment.applied_to_advance,
            )
            payment.soft_delete()
            
        all_expenses = list(Expense.objects.filter(is_deleted=False))
        for exp in random.sample(all_expenses, min(3, len(all_expenses))):
            exp.soft_delete()

    def print_summary(self):
        self.stdout.write(self.style.SUCCESS('\n--- SEED SUMMARY ---'))
        self.stdout.write(f"Vendors: {Vendor.objects.count()} (Trashed: {Vendor.all_objects.filter(is_deleted=True).count()})")
        self.stdout.write(f"Purchase Invoices: {PurchaseInvoice.objects.count()} (Trashed: {PurchaseInvoice.all_objects.filter(is_deleted=True).count()})")
        self.stdout.write(f"  - Draft: {PurchaseInvoice.objects.filter(status='Draft').count()}")
        self.stdout.write(f"  - Saved: {PurchaseInvoice.objects.filter(status='Saved').count()}")
        self.stdout.write(f"Vendor Payments: {VendorPayment.objects.count()} (Trashed: {VendorPayment.all_objects.filter(is_deleted=True).count()})")
        self.stdout.write(f"Expenses: {Expense.objects.count()} (Trashed: {Expense.all_objects.filter(is_deleted=True).count()})")
        
        self.stdout.write(self.style.SUCCESS('\n--- VENDOR SPOT CHECK ---'))
        # Pick vendors that have invoices and payments to show an interesting ledger
        vendor_ids_with_invoices = PurchaseInvoice.objects.filter(status='Saved').values_list('vendor_id', flat=True)
        sample_vendors = Vendor.objects.filter(id__in=vendor_ids_with_invoices).order_by('?')[:3]
        if not sample_vendors:
            sample_vendors = Vendor.objects.order_by('?')[:3]

        for vendor in sample_vendors:
            self.stdout.write(f"\nVendor: {vendor.vendor_name} (ID: {vendor.vendor_id})")
            self.stdout.write(f"  Opening Payable: {vendor.opening_payable}")
            
            invoices = PurchaseInvoice.objects.filter(vendor=vendor, status='Saved').order_by('date', 'id')
            for inv in invoices:
                self.stdout.write(f"  + Invoice {inv.invoice_number}: Net {inv.net_total}, Direct Paid: {inv.paid_amount}, Advance Applied: {inv.advance_applied}, Term: {inv.payment_term}")
                
            payments = VendorPayment.objects.filter(vendor=vendor).order_by('date', 'id')
            for p in payments:
                # We show the payment only if it wasn't auto-created by invoice directly, or we just show all.
                # Actually VendorPaymentSerializer creates a VendorPayment when an invoice is saved with paid_amount > 0.
                # So we will see those payments too.
                self.stdout.write(f"  - Payment {p.payment_number}: Amount {p.amount_paid} (Applied to Inv: {p.applied_to_invoice}, Pay: {p.applied_to_payable}, Adv: {p.applied_to_advance})")
                
            self.stdout.write(f"  = Current Payable Balance: {vendor.payable_balance}")
            self.stdout.write(f"  = Current Advance Balance: {vendor.advance_balance}")
