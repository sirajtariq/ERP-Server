from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError

from inventory.models import Item, StockMovement
import inventory.services as services

User = get_user_model()


class InventoryServiceTestCase(TestCase):
    def setUp(self):
        self.item = Item.objects.create(
            item_code="OIL-001",
            name="Engine Oil 20W50",
            category="Lubricants",
            unit="liter",
            purchase_rate=Decimal("100.00"),
            sale_rate=Decimal("150.00"),
            opening_stock=Decimal("10.00"),
            min_stock=Decimal("5.00"),
        )
        # Record opening stock
        services.record_stock_movement(
            item=self.item,
            movement_type='in',
            quantity=Decimal("10.00"),
            reason='Opening Stock',
            ref_type='opening'
        )

    def test_current_stock_calculation(self):
        current_stock = services.get_item_current_stock(self.item)
        self.assertEqual(current_stock, Decimal("10.00"))

        services.record_stock_movement(
            item=self.item,
            movement_type='in',
            quantity=Decimal("5.00"),
            reason='Purchase',
        )
        self.assertEqual(services.get_item_current_stock(self.item), Decimal("15.00"))

        services.record_stock_movement(
            item=self.item,
            movement_type='out',
            quantity=Decimal("4.00"),
            reason='Sale',
        )
        self.assertEqual(services.get_item_current_stock(self.item), Decimal("11.00"))

    def test_calculate_item_summary(self):
        summary = services.calculate_item_summary(self.item)
        self.assertEqual(summary["current_stock"], Decimal("10.00"))
        self.assertEqual(summary["stock_value"], Decimal("1000.00"))  # 10 * 100
        self.assertEqual(summary["profit_per_unit"], Decimal("50.00"))  # 150 - 100
        self.assertEqual(summary["profit_margin_pct"], 50.0)  # (50/100)*100
        self.assertEqual(summary["stock_status"], "in_stock")

    def test_insufficient_stock_error(self):
        with self.assertRaises(ValidationError):
            services.record_stock_movement(
                item=self.item,
                movement_type='out',
                quantity=Decimal("20.00"),
                reason='Sale Exception',
            )

    def test_global_kpis(self):
        Item.objects.create(
            item_code="FIL-001",
            name="Oil Filter",
            category="Filters",
            unit="pcs",
            purchase_rate=Decimal("50.00"),
            sale_rate=Decimal("80.00"),
            opening_stock=Decimal("0.00"),
            min_stock=Decimal("2.00"),
        )
        kpis = services.calculate_inventory_global_kpis()
        self.assertEqual(kpis["totalItems"], 2)
        self.assertEqual(kpis["lowStockCount"], 0)
        self.assertEqual(kpis["outOfStockCount"], 1)  # FIL-001 has 0 stock


class InventoryAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="testadmin", password="password123")
        self.client.force_authenticate(user=self.user)

    def test_create_item_with_opening_stock(self):
        payload = {
            "item_code": "BRK-001",
            "name": "Brake Pad Front",
            "category": "Brakes",
            "unit": "set",
            "purchase_rate": "200.00",
            "sale_rate": "300.00",
            "opening_stock": "20.00",
            "min_stock": "5.00",
            "description": "Premium front brake pads",
        }
        response = self.client.post("/api/inventory/items/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["currentStock"], "20.00")

        # Verify stock movement recorded automatically
        item_id = response.data["id"]
        item = Item.objects.get(pk=item_id)
        movements = StockMovement.objects.filter(item=item)
        self.assertEqual(movements.count(), 1)
        self.assertEqual(movements.first().reason, "Opening Stock")

    def test_create_item_with_camelcase_payload(self):
        payload = {
            "itemCode": "BRK-CAMEL-01",
            "name": "CamelCase Brake Pad",
            "category": "Brakes",
            "unit": "set",
            "purchaseRate": "250.00",
            "saleRate": "380.00",
            "openingStock": "15.00",
            "minStock": "5.00",
            "description": "Brake pad created via camelCase payload",
        }
        response = self.client.post("/api/inventory/items/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["itemCode"], "BRK-CAMEL-01")
        self.assertEqual(response.data["currentStock"], "15.00")

    def test_duplicate_item_code_validation(self):
        Item.objects.create(
            item_code="TYR-001",
            name="Tyre 195/65R15",
            category="Tyres",
            unit="pcs",
        )
        payload = {
            "itemCode": "tyr-001",  # Case-insensitive duplicate check via camelCase key
            "name": "Another Tyre",
            "category": "Tyres",
            "unit": "pcs",
        }
        response = self.client.post("/api/inventory/items/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("itemCode", response.data)

    def test_item_list_envelope_with_summary(self):
        Item.objects.create(
            item_code="BAT-001",
            name="12V Battery",
            category="Electrical",
            unit="pcs",
            purchase_rate=Decimal("5000.00"),
            sale_rate=Decimal("6500.00"),
        )
        response = self.client.get("/api/inventory/items/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("summary", response.data)
        self.assertIn("totalItems", response.data["summary"])
        self.assertIn("totalStockValue", response.data["summary"])
        self.assertIn("results", response.data)
        item_data = response.data["results"][0]
        self.assertIn("itemCode", item_data)
        self.assertIn("purchaseRate", item_data)
        self.assertIn("saleRate", item_data)
        self.assertIn("profitMarginPct", item_data)
        self.assertIn("currentStock", item_data)
        self.assertIn("stockValue", item_data)
        self.assertIn("stockStatus", item_data)

    def test_soft_delete(self):
        item = Item.objects.create(
            item_code="WPR-001",
            name="Wiper Blade",
            category="Accessories",
            unit="pair",
        )
        response = self.client.delete(f"/api/inventory/items/{item.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        item.refresh_from_db()
        self.assertTrue(item.is_deleted)
        self.assertEqual(Item.objects.filter(is_deleted=False).count(), 0)

    def test_stock_adjustment_action(self):
        item = Item.objects.create(
            item_code="PLG-001",
            name="Spark Plug",
            category="Engine Parts",
            unit="pcs",
            purchase_rate=Decimal("300.00"),
            sale_rate=Decimal("450.00"),
        )
        # Adjust stock IN
        payload = {
            "type": "in",
            "qty": "50.00",
            "reason": "Stock Purchase Inward",
            "notes": "Batch #1002"
        }
        response = self.client.post(f"/api/inventory/items/{item.id}/adjust_stock/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["itemSummary"]["currentStock"], "50.00")

        # Adjust stock OUT
        payload_out = {
            "type": "out",
            "qty": "10.00",
            "reason": "Damaged Stock",
        }
        response_out = self.client.post(f"/api/inventory/items/{item.id}/adjust_stock/", payload_out, format="json")
        self.assertEqual(response_out.status_code, status.HTTP_200_OK)
        self.assertEqual(response_out.data["itemSummary"]["currentStock"], "40.00")

    def test_calculate_item_list_metrics_service(self):
        item = Item.objects.create(
            item_code="OIL-002",
            name="Synthetic Oil",
            category="Lubricants",
            unit="liter",
            purchase_rate=Decimal("850.00"),
            sale_rate=Decimal("1100.00"),
            min_stock=Decimal("10.00"),
        )
        services.record_stock_movement(item, "in", Decimal("120.00"), "Initial Stock")
        metrics = services.calculate_item_list_metrics(item)
        self.assertEqual(metrics["current_stock"], Decimal("120.00"))
        self.assertEqual(metrics["stock_value"], Decimal("102000.00"))
        self.assertEqual(metrics["profit_margin_pct"], 29.41)
        self.assertEqual(metrics["stock_status"], "in_stock")

    def test_item_list_filtering_and_pagination(self):
        item1 = Item.objects.create(
            item_code="ITM-001",
            name="Engine Oil 20W-50",
            category="Lubricants",
            unit="liter",
            purchase_rate=Decimal("850.00"),
            sale_rate=Decimal("1100.00"),
            min_stock=Decimal("10.00"),
        )
        services.record_stock_movement(item1, "in", Decimal("120.00"), "Purchase")

        item2 = Item.objects.create(
            item_code="ITM-002",
            name="Air Filter High Performance",
            category="Filters",
            unit="pcs",
            purchase_rate=Decimal("300.00"),
            sale_rate=Decimal("500.00"),
            min_stock=Decimal("5.00"),
        )
        services.record_stock_movement(item2, "in", Decimal("3.00"), "Purchase")  # Low stock

        item3 = Item.objects.create(
            item_code="ITM-003",
            name="Brake Shoe Rear",
            category="Brakes",
            unit="set",
            purchase_rate=Decimal("1500.00"),
            sale_rate=Decimal("2200.00"),
            min_stock=Decimal("5.00"),
        )
        # Out of stock (0 stock)

        # Test filtering by name
        res_name = self.client.get("/api/inventory/items/?name=Engine")
        self.assertEqual(res_name.status_code, status.HTTP_200_OK)
        self.assertEqual(res_name.data["count"], 1)

        # Test filtering by code
        res_code = self.client.get("/api/inventory/items/?code=ITM-002")
        self.assertEqual(res_code.status_code, status.HTTP_200_OK)
        self.assertEqual(res_code.data["count"], 1)

        # Test filtering by category
        res_cat = self.client.get("/api/inventory/items/?category=Lubricants")
        self.assertEqual(res_cat.status_code, status.HTTP_200_OK)
        self.assertEqual(res_cat.data["count"], 1)

        # Test filtering by status=low
        res_low = self.client.get("/api/inventory/items/?status=low")
        self.assertEqual(res_low.status_code, status.HTTP_200_OK)
        self.assertEqual(res_low.data["count"], 1)
        self.assertEqual(res_low.data["results"][0]["itemCode"], "ITM-002")

        # Test pagination: page_size=2
        res_page = self.client.get("/api/inventory/items/?page=1&page_size=2")
        self.assertEqual(res_page.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_page.data["results"]), 2)
        self.assertIsNotNone(res_page.data["next"])

