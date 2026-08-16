from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone

from inventory.models import Item, StockMovement
import inventory.services as services


class Command(BaseCommand):
    help = "Seed realistic mock inventory items and stock movement history for ERP backend."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Soft-delete existing active items before seeding new mock data.",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            count = Item.objects.filter(is_deleted=False).update(is_deleted=True)
            self.stdout.write(self.style.WARNING(f"Soft-deleted {count} existing active inventory items."))

        mock_items_data = [
            {
                "item_code": "OIL-20W50-1L",
                "name": "Engine Oil 20W-50 (1L)",
                "category": "Lubricants",
                "unit": "liter",
                "purchase_rate": Decimal("850.00"),
                "sale_rate": Decimal("1100.00"),
                "opening_stock": Decimal("150.00"),
                "min_stock": Decimal("20.00"),
                "description": "High performance mineral engine oil for passenger cars and light commercial vehicles.",
                "extra_movements": [
                    {"type": "in", "qty": Decimal("50.00"), "reason": "Purchase Order #PO-1001", "ref_type": "purchase"},
                    {"type": "out", "qty": Decimal("15.00"), "reason": "Sales Invoice #INV-5001", "ref_type": "sale"},
                ]
            },
            {
                "item_code": "OIL-10W40-4L",
                "name": "Fully Synthetic Oil 10W-40 (4L)",
                "category": "Lubricants",
                "unit": "can",
                "purchase_rate": Decimal("4200.00"),
                "sale_rate": Decimal("5500.00"),
                "opening_stock": Decimal("45.00"),
                "min_stock": Decimal("10.00"),
                "description": "Advanced synthetic formula offering superior engine cleanliness and wear protection.",
                "extra_movements": [
                    {"type": "out", "qty": Decimal("5.00"), "reason": "Sales Invoice #INV-5002", "ref_type": "sale"},
                ]
            },
            {
                "item_code": "FLT-OIL-001",
                "name": "High Performance Oil Filter",
                "category": "Filters",
                "unit": "pcs",
                "purchase_rate": Decimal("350.00"),
                "sale_rate": Decimal("550.00"),
                "opening_stock": Decimal("80.00"),
                "min_stock": Decimal("15.00"),
                "description": "Heavy-duty spin-on oil filter with premium filtration media.",
                "extra_movements": [
                    {"type": "out", "qty": Decimal("12.00"), "reason": "Sales Invoice #INV-5003", "ref_type": "sale"},
                ]
            },
            {
                "item_code": "FLT-AIR-002",
                "name": "Heavy Duty Air Filter Element",
                "category": "Filters",
                "unit": "pcs",
                "purchase_rate": Decimal("650.00"),
                "sale_rate": Decimal("950.00"),
                "opening_stock": Decimal("6.00"),
                "min_stock": Decimal("10.00"),  # Low Stock state
                "description": "High air-flow capacity element engineered to block micro-particles.",
                "extra_movements": []
            },
            {
                "item_code": "BRK-PAD-F01",
                "name": "Ceramic Front Brake Pad Set",
                "category": "Brakes",
                "unit": "set",
                "purchase_rate": Decimal("2200.00"),
                "sale_rate": Decimal("3200.00"),
                "opening_stock": Decimal("25.00"),
                "min_stock": Decimal("5.00"),
                "description": "Low-dust, noise-free ceramic friction formula for smooth stopping power.",
                "extra_movements": [
                    {"type": "out", "qty": Decimal("4.00"), "reason": "Sales Invoice #INV-5004", "ref_type": "sale"},
                ]
            },
            {
                "item_code": "BRK-SHU-R02",
                "name": "Rear Brake Shoe Set",
                "category": "Brakes",
                "unit": "set",
                "purchase_rate": Decimal("1800.00"),
                "sale_rate": Decimal("2600.00"),
                "opening_stock": Decimal("0.00"),
                "min_stock": Decimal("5.00"),  # Out of Stock state
                "description": "Durable steel backing plate bonded friction lining for rear drum brakes.",
                "extra_movements": []
            },
            {
                "item_code": "BAT-12V-65A",
                "name": "Maintenance Free Battery 12V 65Ah",
                "category": "Electrical",
                "unit": "pcs",
                "purchase_rate": Decimal("12500.00"),
                "sale_rate": Decimal("16000.00"),
                "opening_stock": Decimal("12.00"),
                "min_stock": Decimal("3.00"),
                "description": "Sealed lead-acid starter battery with high cold-cranking amps.",
                "extra_movements": [
                    {"type": "in", "qty": Decimal("5.00"), "reason": "Purchase Order #PO-1004", "ref_type": "purchase"},
                    {"type": "out", "qty": Decimal("2.00"), "reason": "Sales Invoice #INV-5005", "ref_type": "sale"},
                ]
            },
            {
                "item_code": "PLG-IRID-04",
                "name": "Iridium Spark Plug (Pack of 4)",
                "category": "Engine Parts",
                "unit": "pack",
                "purchase_rate": Decimal("2800.00"),
                "sale_rate": Decimal("4000.00"),
                "opening_stock": Decimal("30.00"),
                "min_stock": Decimal("5.00"),
                "description": "Fine-wire iridium tip ensures maximum spark energy and extended engine service life.",
                "extra_movements": []
            },
            {
                "item_code": "TYR-195-65",
                "name": "Radial Tyre 195/65 R15",
                "category": "Tyres",
                "unit": "pcs",
                "purchase_rate": Decimal("11500.00"),
                "sale_rate": Decimal("14500.00"),
                "opening_stock": Decimal("16.00"),
                "min_stock": Decimal("4.00"),
                "description": "All-season radial passenger tyre featuring reinforced sidewalls and quiet tread pattern.",
                "extra_movements": [
                    {"type": "out", "qty": Decimal("4.00"), "reason": "Sales Invoice #INV-5006", "ref_type": "sale"},
                ]
            },
            {
                "item_code": "SHK-ABS-F01",
                "name": "Front Shock Absorber Pair",
                "category": "Suspension",
                "unit": "pair",
                "purchase_rate": Decimal("18500.00"),
                "sale_rate": Decimal("23500.00"),
                "opening_stock": Decimal("2.00"),
                "min_stock": Decimal("4.00"),  # Low Stock state
                "description": "Gas-pressurized twin-tube struts for superior ride stability.",
                "extra_movements": []
            },
            {
                "item_code": "WPR-BLD-22",
                "name": "Silicone Wiper Blades 22 inch",
                "category": "Accessories",
                "unit": "pair",
                "purchase_rate": Decimal("950.00"),
                "sale_rate": Decimal("1500.00"),
                "opening_stock": Decimal("50.00"),
                "min_stock": Decimal("10.00"),
                "description": "Hydrophobic silicone wiper refill for streak-free all-weather visibility.",
                "extra_movements": [
                    {"type": "out", "qty": Decimal("8.00"), "reason": "Sales Invoice #INV-5007", "ref_type": "sale"},
                ]
            },
            {
                "item_code": "CLT-KIT-001",
                "name": "Heavy Duty Clutch Kit Assembly",
                "category": "Engine Parts",
                "unit": "set",
                "purchase_rate": Decimal("14500.00"),
                "sale_rate": Decimal("19000.00"),
                "opening_stock": Decimal("0.00"),
                "min_stock": Decimal("2.00"),  # Out of Stock state
                "description": "Complete clutch replacement kit including pressure plate, clutch disc, and release bearing.",
                "extra_movements": []
            },
        ]

        created_count = 0
        updated_count = 0

        for item_data in mock_items_data:
            code = item_data["item_code"]
            extra_movements = item_data.pop("extra_movements", [])

            item, created = Item.objects.get_or_create(
                item_code=code,
                is_deleted=False,
                defaults=item_data
            )

            if created:
                created_count += 1
                # If opening stock > 0 and no opening stock movement exists yet, record it
                if item.opening_stock > Decimal("0.00") and not item.stock_movements.filter(reference_type="opening").exists():
                    services.record_stock_movement(
                        item=item,
                        movement_type="in",
                        quantity=item.opening_stock,
                        reason="Opening Stock",
                        ref_type="opening"
                    )

                # Process extra movements
                for mov in extra_movements:
                    services.record_stock_movement(
                        item=item,
                        movement_type=mov["type"],
                        quantity=mov["qty"],
                        reason=mov["reason"],
                        ref_type=mov.get("ref_type", "manual")
                    )
            else:
                updated_count += 1

        summary = services.calculate_inventory_global_kpis()
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully processed seed data: {created_count} items created, {updated_count} existing items retained.\n"
                f"Global KPIs Summary -> Total Active Items: {summary['totalItems']}, "
                f"Total Stock Value: Rs. {summary['totalStockValue']}, "
                f"Low Stock Items: {summary['lowStockCount']}, Out of Stock Items: {summary['outOfStockCount']}"
            )
        )
