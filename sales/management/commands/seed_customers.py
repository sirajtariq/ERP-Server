"""
Management command to seed ~70 realistic customer records.

Usage:
    python manage.py seed_customers
    python manage.py seed_customers --count 80
    python manage.py seed_customers --clear   # delete existing customers first
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


def generate_phone():
    """Generate a Pakistani-style phone number."""
    prefixes = ["0300", "0301", "0302", "0303", "0304", "0305",
                "0311", "0312", "0313", "0321", "0322", "0323",
                "0331", "0332", "0333", "0341", "0342", "0343",
                "0345", "0346", "0347"]
    return f"{random.choice(prefixes)}{random.randint(1000000, 9999999)}"


def generate_customer_data(index):
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

    # Randomize financials
    opening_credit = Decimal(str(round(random.uniform(0, 50000), 2))) if random.random() < 0.6 else None
    opening_note = f"Opening balance as of account setup" if opening_credit else ""
    tax_number = f"NTN-{random.randint(1000000, 9999999)}" if random.random() < 0.5 else None

    return {
        "customer_name": name,
        "phone": generate_phone(),
        "email": email,
        "address": address,
        "opening_credit": opening_credit,
        "opening_note": opening_note,
        "tax_number": tax_number,
    }


class Command(BaseCommand):
    help = "Seed the database with realistic customer records for testing."

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

    def handle(self, *args, **options):
        count = options["count"]
        clear = options["clear"]

        if clear:
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

        created = 0
        skipped = 0
        for i in range(count):
            data = generate_customer_data(i)
            # Ensure unique phone
            if Customer.objects.filter(phone=data["phone"]).exists():
                skipped += 1
                continue
            try:
                Customer.objects.create(**data)
                created += 1
                # Small stagger so created_at timestamps differ for sorting tests
                time.sleep(0.05)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating customer: {e}"))
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully created {created} customers "
                f"({skipped} skipped). Total: {Customer.objects.count()}"
            )
        )
