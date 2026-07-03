"""
Management command to seed ~70 realistic customer records.

Usage:
    uv run python manage.py seed_customers
    uv run python manage.py seed_customers --count 80
    uv run python manage.py seed_customers --clear   # delete existing customers first
"""

import random
import time
from decimal import Decimal

from django.core.management.base import BaseCommand

from sales.models import Customer

# Diverse realistic customer data pools
FIRST_NAMES = [
    "Ahmed", "Fatima", "Mohammed", "Aisha", "Omar", "Zainab", "Ali",
    "Maryam", "Hassan", "Sara", "Khalid", "Noor", "Ibrahim", "Huda",
    "Yusuf", "Layla", "Tariq", "Amina", "Bilal", "Safiya", "Hamza",
    "Khadija", "Usman", "Rabia", "Saad", "Samira", "Faisal", "Nashwa",
    "Imran", "Lubna", "Rizwan", "Hiba", "Naveed", "Sana", "Asif",
    "Bushra", "Kamran", "Farida", "Junaid", "Nadia",
]

LAST_NAMES = [
    "Khan", "Ahmad", "Ali", "Hassan", "Hussein", "Sheikh", "Malik",
    "Qureshi", "Siddiqui", "Ansari", "Mirza", "Butt", "Chaudhry",
    "Iqbal", "Raza", "Javed", "Aslam", "Baig", "Dar", "Farooq",
    "Gill", "Haider", "Jabbar", "Karim", "Latif", "Mushtaq", "Naqvi",
    "Patel", "Rafiq", "Shah", "Tahir", "Umar", "Waqar", "Yaqoob",
    "Zafar", "Abbasi", "Bukhari", "Durrani", "Ghani", "Hameed",
]

COMPANY_SUFFIXES = [
    "Trading Co.", "Enterprises", "Industries", "& Sons", "Group",
    "Solutions", "International", "Corp.", "LLC", "& Associates",
    "Distributors", "Supplies", "Services", "Holdings", "Mart",
]

CITIES = [
    "Karachi", "Lahore", "Islamabad", "Rawalpindi", "Faisalabad",
    "Multan", "Peshawar", "Quetta", "Sialkot", "Gujranwala",
    "Hyderabad", "Bahawalpur", "Sargodha", "Sukkur", "Larkana",
]

STREETS = [
    "Main Boulevard", "Commercial Area", "Industrial Zone", "Market Road",
    "Mall Road", "GT Road", "University Road", "Airport Road",
    "Shahrah-e-Faisal", "Defence Housing", "Gulberg", "Johar Town",
    "Saddar", "Clifton", "Blue Area",
]

EMAIL_DOMAINS = [
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "business.pk", "company.com", "enterprise.net",
]

# Ratio of walk-in customers to generate (rest will be permanent)
WALKIN_RATIO = 0.15


def generate_phone():
    """Generate a Pakistani-style phone number."""
    prefixes = ["0300", "0301", "0302", "0303", "0304", "0305",
                "0311", "0312", "0313", "0321", "0322", "0323",
                "0331", "0332", "0333", "0341", "0342", "0343",
                "0345", "0346", "0347"]
    return f"{random.choice(prefixes)}{random.randint(1000000, 9999999)}"


def generate_customer_data(index, customer_type="permanent"):
    """Generate a single customer's data dictionary."""
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)

    # ~40% chance of being a business name instead of personal
    if random.random() < 0.4:
        name = f"{first} {last} {random.choice(COMPANY_SUFFIXES)}"
    else:
        name = f"{first} {last}"

    city = random.choice(CITIES)
    street = random.choice(STREETS)
    house = random.randint(1, 500)
    address = f"{house} {street}, {city}"

    email_user = f"{first.lower()}.{last.lower()}{random.randint(1, 99)}"
    email = f"{email_user}@{random.choice(EMAIL_DOMAINS)}"

    if customer_type == "permanent":
        # Permanent customers have full financial history
        opening_credit = (
            Decimal(str(round(random.uniform(0, 50000), 2)))
            if random.random() < 0.6 else None
        )
        opening_note = "Opening balance as of account setup" if opening_credit else ""
        tax_number = f"NTN-{random.randint(1000000, 9999999)}" if random.random() < 0.5 else None
        phone = generate_phone()
    else:
        # Walk-in customers: minimal data, no opening balances/credit history
        opening_credit = None
        opening_note = ""
        tax_number = None
        # Some walk-ins may not have a phone at all (optional field)
        phone = generate_phone() if random.random() < 0.5 else None

    return {
        "customer_name": name,
        "phone": phone,
        "email": email if customer_type == "permanent" else None,
        "address": address if customer_type == "permanent" else "",
        "opening_credit": opening_credit,
        "opening_note": opening_note,
        "tax_number": tax_number,
        "customer_type": customer_type,
    }


class Command(BaseCommand):
    help = "Seed the database with realistic customer records (permanent + walk-in) for testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=70,
            help="Number of customers to create (default: 70)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing customers before seeding",
        )
        parser.add_argument(
            "--walkin-ratio",
            type=float,
            default=WALKIN_RATIO,
            help="Fraction of customers that should be walk-in (default: 0.15)",
        )

    def handle(self, *args, **options):
        count = options["count"]
        clear = options["clear"]
        walkin_ratio = options["walkin_ratio"]

        if clear:
            # Customer FK on SalesInvoice uses on_delete=PROTECT, so any
            # existing invoices must be removed first or the customer
            # delete will be blocked.
            from sales.models import SalesInvoice, SalesItem
            invoices_deleted = SalesInvoice.objects.all().count()
            SalesItem.objects.all().delete()
            SalesInvoice.objects.all().delete()
            if invoices_deleted:
                self.stdout.write(
                    self.style.WARNING(
                        f"Deleted {invoices_deleted} invoices (and their items) "
                        f"first, since they reference customers via a PROTECTed FK."
                    )
                )

            deleted, _ = Customer.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} existing customers."))

        existing = Customer.objects.count()
        if existing >= 60 and not clear:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Already {existing} customers in DB. Skipping seed. "
                    f"Use --clear to reset."
                )
            )
            return

        created_permanent = 0
        created_walkin = 0
        skipped = 0

        for i in range(count):
            customer_type = "walkin" if random.random() < walkin_ratio else "permanent"
            data = generate_customer_data(i, customer_type=customer_type)

            # Ensure unique phone only when a phone is actually provided
            if data["phone"] and Customer.objects.filter(phone=data["phone"]).exists():
                skipped += 1
                continue

            try:
                Customer.objects.create(**data)
                if customer_type == "permanent":
                    created_permanent += 1
                else:
                    created_walkin += 1
                # Small stagger so created_at timestamps differ for sorting tests
                time.sleep(0.05)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating customer: {e}"))
                skipped += 1

        total_created = created_permanent + created_walkin
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully created {total_created} customers "
                f"({created_permanent} permanent, {created_walkin} walk-in, "
                f"{skipped} skipped). Total in DB: {Customer.objects.count()}"
            )
        )