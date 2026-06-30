"""
Management command to seed sales invoices with line items.

Creates invoices for a random subset of existing customers so that some
customers have invoices and others have empty arrays.

Usage:
    python manage.py seed_invoices
    python manage.py seed_invoices --count 40
    python manage.py seed_invoices --clear
"""

import random
from decimal import Decimal

from django.core.management.base import BaseCommand

from sales.models import Customer, SalesInvoice, SalesItem
from sales.management.commands.seed_customers import FIRST_NAMES, LAST_NAMES

# Realistic product/service items
PRODUCTS = [
    ("Laptop Dell Inspiron 15", "pcs", 850.00, 1200.00),
    ("HP LaserJet Printer", "pcs", 350.00, 550.00),
    ("Office Chair Ergonomic", "pcs", 120.00, 280.00),
    ("A4 Paper (500 sheets)", "ream", 3.50, 8.00),
    ("USB Cable Type-C", "pcs", 5.00, 15.00),
    ("Wireless Mouse Logitech", "pcs", 15.00, 45.00),
    ("LED Monitor 24 inch", "pcs", 180.00, 350.00),
    ("Keyboard Mechanical", "pcs", 40.00, 120.00),
    ("External Hard Drive 1TB", "pcs", 45.00, 90.00),
    ("Webcam HD 1080p", "pcs", 25.00, 70.00),
    ("Network Cable Cat6 (10m)", "pcs", 8.00, 20.00),
    ("Toner Cartridge Black", "pcs", 30.00, 75.00),
    ("Desk Lamp LED", "pcs", 15.00, 40.00),
    ("Whiteboard 4x3 ft", "pcs", 35.00, 80.00),
    ("Marker Set (Pack of 12)", "pack", 5.00, 15.00),
    ("Paper Shredder", "pcs", 60.00, 150.00),
    ("UPS 1000VA", "pcs", 80.00, 180.00),
    ("Router WiFi 6", "pcs", 55.00, 130.00),
    ("HDMI Cable 2m", "pcs", 6.00, 18.00),
    ("Surge Protector 6-Outlet", "pcs", 10.00, 30.00),
    ("Notebook Spiral A5", "pcs", 1.50, 5.00),
    ("Pen Ballpoint (Box 50)", "box", 8.00, 20.00),
    ("Stapler Heavy Duty", "pcs", 12.00, 30.00),
    ("Filing Cabinet 4-Drawer", "pcs", 150.00, 350.00),
    ("Air Conditioner 1.5 Ton", "pcs", 400.00, 800.00),
]

PAYMENT_METHODS = ["Cash", "Bank Transfer", "Cheque", "Online", None]
NOTES_OPTIONS = [
    "Urgent delivery required",
    "Customer will pick up",
    "Deliver to warehouse",
    "Standard shipping",
    "Discount applied per agreement",
    "Follow-up needed",
    None,
    None,
    None,
]


class Command(BaseCommand):
    help = "Seed the database with sales invoices and line items."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=35,
            help="Number of invoices to create (default: 35)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing invoices and items before seeding",
        )

    def handle(self, *args, **options):
        count = options["count"]
        clear = options["clear"]

        if clear:
            items_deleted = SalesItem.objects.all().delete()[0]
            invoices_deleted = SalesInvoice.objects.all().delete()[0]
            self.stdout.write(
                self.style.WARNING(
                    f"Deleted {invoices_deleted} invoices and {items_deleted} items."
                )
            )

        existing = SalesInvoice.objects.count()
        if existing >= 20 and not clear:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Already {existing} invoices in DB. Skipping seed. "
                    f"Use --clear to reset."
                )
            )
            return

        customers = list(Customer.objects.all())
        if not customers:
            self.stdout.write(
                self.style.ERROR(
                    "No customers found. Run 'seed_customers' first."
                )
            )
            return

        # Pick ~60% of customers to have invoices (rest will have empty arrays)
        num_customers_with_invoices = max(1, int(len(customers) * 0.6))
        selected_customers = random.sample(customers, num_customers_with_invoices)

        created_invoices = 0
        created_items = 0

        for i in range(count):
            is_walkin = random.random() < 0.2
            if is_walkin:
                customer = None
                walk_in_customer_name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
                payment_term = "Cash"
            else:
                customer = random.choice(selected_customers)
                walk_in_customer_name = ""
                payment_term = random.choice(["Cash", "Credit"])

            # Randomize invoice details
            payment_method = random.choice(PAYMENT_METHODS)
            vat_pct = random.choice([0, 0, 5, 10, 15, 18])
            invoice_discount = Decimal(str(round(random.uniform(0, 50), 2))) if random.random() < 0.3 else Decimal("0.00")
            status = random.choice(["Draft", "Saved", "Saved", "Saved"])
            notes = random.choice(NOTES_OPTIONS)

            try:
                invoice = SalesInvoice.objects.create(
                    customer=customer,
                    walk_in_customer_name=walk_in_customer_name or None,
                    payment_term=payment_term,
                    payment_method=payment_method if payment_method else "",
                    vat_percentage=Decimal(str(vat_pct)),
                    invoice_discount=invoice_discount,
                    status=status,
                    notes=notes or "",
                )

                # Add 1–5 line items per invoice
                num_items = random.randint(1, 5)
                chosen_products = random.sample(PRODUCTS, min(num_items, len(PRODUCTS)))

                for product_name, units, min_rate, max_rate in chosen_products:
                    quantity = Decimal(str(random.randint(1, 20)))
                    rate = Decimal(str(round(random.uniform(min_rate, max_rate), 2)))
                    item_discount = Decimal(str(round(random.uniform(0, float(rate) * 0.1), 2))) if random.random() < 0.2 else Decimal("0.00")

                    SalesItem.objects.create(
                        invoice=invoice,
                        item_name=product_name,
                        units=units,
                        quantity=quantity,
                        rate=rate,
                        discount=item_discount,
                    )
                    created_items += 1

                # Set paid_amount for some invoices
                if payment_term == "Cash" or random.random() < 0.4:
                    # Refresh to get computed properties
                    invoice.refresh_from_db()
                    # Access net_total via property (need items)
                    net = sum(
                        (item.quantity * item.rate) - item.discount
                        for item in invoice.items.all()
                    )
                    net = net - invoice.invoice_discount
                    tax = net * (invoice.vat_percentage / Decimal("100"))
                    net_with_tax = net + tax
                    
                    if payment_term == "Cash":
                        invoice.paid_amount = net_with_tax
                    else:
                        invoice.paid_amount = net_with_tax if random.random() < 0.5 else Decimal(str(round(float(net_with_tax) * random.uniform(0.3, 0.9), 2)))
                    invoice.save()

                created_invoices += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating invoice: {e}"))

        total_invoices = SalesInvoice.objects.count()
        total_items = SalesItem.objects.count()
        customers_with = Customer.objects.filter(invoices__isnull=False).distinct().count()
        customers_without = Customer.objects.filter(invoices__isnull=True).count()

        # Recalculate customer balances to keep DB state in sync
        for c in Customer.objects.all():
            c.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {created_invoices} invoices with {created_items} line items.\n"
                f"Total in DB: {total_invoices} invoices, {total_items} items.\n"
                f"Customers with invoices: {customers_with}, without: {customers_without}"
            )
        )
