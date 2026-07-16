"""
Tests for the purchase module — Vendor model, serializer, and viewset.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from purchase.models import Vendor, VENDOR_ID_START


class VendorModelTests(TestCase):
    """Direct model-level tests for Vendor."""

    def test_vendor_id_starts_at_5000(self):
        """First vendor should get vendor_id = VENDOR_ID_START (5000)."""
        v = Vendor.objects.create(vendor_name="Test Vendor")
        self.assertEqual(v.vendor_id, VENDOR_ID_START)

    def test_vendor_id_auto_increments(self):
        """Subsequent vendors should get sequential vendor_ids."""
        v1 = Vendor.objects.create(vendor_name="Vendor 1")
        v2 = Vendor.objects.create(vendor_name="Vendor 2")
        v3 = Vendor.objects.create(vendor_name="Vendor 3")
        self.assertEqual(v1.vendor_id, 5000)
        self.assertEqual(v2.vendor_id, 5001)
        self.assertEqual(v3.vendor_id, 5002)

    def test_soft_delete_and_all_objects(self):
        """Soft-deleted vendor is excluded from default manager but visible via all_objects."""
        v = Vendor.objects.create(vendor_name="Deletable")
        v.soft_delete()
        self.assertEqual(Vendor.objects.filter(pk=v.pk).count(), 0)
        self.assertEqual(Vendor.all_objects.filter(pk=v.pk).count(), 1)

    def test_restore_after_soft_delete(self):
        """Restoring a trashed vendor makes it visible in default queryset again."""
        v = Vendor.objects.create(vendor_name="Restorable")
        v.soft_delete()
        self.assertFalse(Vendor.objects.filter(pk=v.pk).exists())
        v.restore()
        self.assertTrue(Vendor.objects.filter(pk=v.pk).exists())

    def test_null_phone_allows_multiple_vendors_without_phone(self):
        """Two vendors with phone=None should not violate unique constraint."""
        Vendor.objects.create(vendor_name="No Phone 1", phone=None)
        Vendor.objects.create(vendor_name="No Phone 2", phone=None)
        self.assertEqual(Vendor.objects.filter(phone__isnull=True).count(), 2)

    def test_decimal_defaults(self):
        """Financial fields default to Decimal('0.00'), not float."""
        v = Vendor.objects.create(vendor_name="Default Vendor")
        v.refresh_from_db()
        self.assertEqual(v.opening_payable, Decimal('0.00'))
        self.assertEqual(v.payable_balance, Decimal('0.00'))
        self.assertEqual(v.advance_balance, Decimal('0.00'))


class VendorAPITestMixin:
    """Shared setup for API-level vendor tests."""

    def setUp(self):
        # Create groups (may already exist from erp_backend signal seeding)
        self.purchase_group, _ = Group.objects.get_or_create(name="Purchase")
        self.admin_group, _ = Group.objects.get_or_create(name="Admin")
        self.sales_group, _ = Group.objects.get_or_create(name="Sales")

        # Purchase user (non-admin)
        self.purchase_user = User.objects.create_user(
            username="purchaser", password="testpass123"
        )
        self.purchase_user.groups.add(self.purchase_group)

        # Admin user
        self.admin_user = User.objects.create_user(
            username="admin_user", password="testpass123"
        )
        self.admin_user.groups.add(self.admin_group)

        # Superuser
        self.superuser = User.objects.create_superuser(
            username="superadmin", password="testpass123"
        )

        # Sales-only user (should be denied access to purchase endpoints)
        self.sales_user = User.objects.create_user(
            username="salesperson", password="testpass123"
        )
        self.sales_user.groups.add(self.sales_group)

        self.client = APIClient()

    def _auth_as(self, user):
        self.client.force_authenticate(user=user)

    def _create_vendor(self, **overrides):
        data = {
            "vendorName": "Test Vendor",
            "phone": "03001234567",
            "email": "vendor@example.com",
            "address": "123 Test St",
            "taxNumber": "TAX-001",
            "openingPayable": "500.00",
            "openingNote": "Opening balance note",
        }
        data.update(overrides)
        return self.client.post("/api/purchase/vendors/", data, format="json")


class VendorCreateTests(VendorAPITestMixin, TestCase):
    """Test vendor creation via API."""

    def test_create_vendor_with_all_fields(self):
        """Creating a vendor with all fields succeeds, vendor_id auto-generated."""
        self._auth_as(self.purchase_user)
        resp = self._create_vendor()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()["vendorId"], VENDOR_ID_START)
        self.assertEqual(resp.json()["vendorName"], "Test Vendor")
        self.assertEqual(resp.json()["phone"], "03001234567")
        self.assertEqual(resp.json()["email"], "vendor@example.com")
        self.assertEqual(resp.json()["openingPayable"], "500.00")

    def test_vendor_id_auto_increments_via_api(self):
        """Sequential API creates get incrementing vendor_ids."""
        self._auth_as(self.purchase_user)
        r1 = self._create_vendor(vendor_name="V1", phone="111")
        r2 = self._create_vendor(vendor_name="V2", phone="222")
        self.assertEqual(r1.json()["vendorId"], 5000)
        self.assertEqual(r2.json()["vendorId"], 5001)


class VendorNullPhoneTests(VendorAPITestMixin, TestCase):
    """Regression tests for the NULL-not-empty-string phone fix."""

    def test_two_vendors_with_empty_phone_succeeds(self):
        """
        Critical regression test: creating two vendors with empty-string
        phone from API input should succeed for both (serializer converts
        '' → None, and NULLs don't collide on unique constraint).
        """
        self._auth_as(self.purchase_user)
        r1 = self._create_vendor(vendor_name="No Phone 1", phone="")
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(r1.json()["phone"])

        r2 = self._create_vendor(vendor_name="No Phone 2", phone="")
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(r2.json()["phone"])

    def test_whitespace_only_phone_treated_as_null(self):
        """Whitespace-only phone input should be converted to None."""
        self._auth_as(self.purchase_user)
        resp = self._create_vendor(vendor_name="Whitespace Phone", phone="   ")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(resp.json()["phone"])

    def test_duplicate_non_empty_phone_returns_400(self):
        """
        Creating a vendor with a phone that already exists should return
        a clean 400 validation error, not a raw 500 IntegrityError.
        """
        self._auth_as(self.purchase_user)
        self._create_vendor(vendor_name="V1", phone="03009999999")
        resp = self._create_vendor(vendor_name="V2", phone="03009999999")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone", resp.json())


class VendorListSerializerTests(VendorAPITestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self._auth_as(self.admin_user)
        self.vendor = Vendor.objects.create(
            vendor_name="List Vendor",
            phone="999888777",
            opening_payable=Decimal("0.00"),
            payable_balance=Decimal("0.00"),
            advance_balance=Decimal("0.00")
        )
        self.vendor_id = self.vendor.vendor_id
        
        from purchase.models import PurchaseInvoice
        self.inv1 = PurchaseInvoice.objects.create(vendor=self.vendor, date="2026-07-01", payment_term="Credit", status="Draft", paid_amount=Decimal("10.00"))
        self.inv2 = PurchaseInvoice.objects.create(vendor=self.vendor, date="2026-07-02", payment_term="Credit", status="Saved", paid_amount=Decimal("20.00"))
        self.inv3 = PurchaseInvoice.objects.create(vendor=self.vendor, date="2026-07-03", payment_term="Cash", status="Saved", paid_amount=Decimal("30.00"))
        
        self.inv_deleted = PurchaseInvoice.objects.create(vendor=self.vendor, date="2026-07-04", payment_term="Credit", status="Saved", paid_amount=Decimal("40.00"), is_deleted=True)

    def test_list_returns_camelcase_with_invoices(self):
        resp = self.client.get("/api/purchase/vendors/")
        self.assertEqual(resp.status_code, 200)
        
        vendor_data = next((v for v in resp.json()["results"] if v["vendorId"] == self.vendor_id), None)
        self.assertIsNotNone(vendor_data)
        
        self.assertIn("vendorName", vendor_data)
        self.assertIn("openingPayable", vendor_data)
        self.assertIn("payableBalance", vendor_data)
        self.assertIn("totalPaid", vendor_data)
        self.assertIn("invoices", vendor_data)
        
        self.assertEqual(vendor_data["totalPaid"], "60.00")
        
        self.assertEqual(len(vendor_data["invoices"]), 3)
        self.assertEqual(vendor_data["invoices"][0]["invoiceNumber"], self.inv3.invoice_number)
        
        self.assertIn("invoiceNumber", vendor_data["invoices"][0])
        self.assertNotIn("invoice_number", vendor_data["invoices"][0])

    def test_vendor_zero_invoices(self):
        v0 = Vendor.objects.create(vendor_name="Zero Vendor")
        resp = self.client.get(f"/api/purchase/vendors/?name=Zero")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["results"]), 1)
        vendor_data = resp.json()["results"][0]
        
        self.assertEqual(vendor_data["invoices"], [])
        self.assertEqual(vendor_data["totalPaid"], "0.00")

    def test_detail_endpoint_is_camel_case(self):
        resp = self.client.get(f"/api/purchase/vendors/{self.vendor_id}/")
        self.assertEqual(resp.status_code, 200)
        
        self.assertIn("vendorName", resp.json())
        self.assertNotIn("vendor_name", resp.json())
        self.assertNotIn("invoices", resp.json())

    def test_query_count_for_list(self):
        Vendor.objects.create(vendor_name="Another Vendor")
        with self.assertNumQueries(5):
            # 1 auth, 1 count, 1 select vendors, 1 prefetch active invoices, 1 prefetch items
            resp = self.client.get("/api/purchase/vendors/")
            self.assertEqual(resp.status_code, 200)


class VendorSoftDeleteTests(VendorAPITestMixin, TestCase):
    """Test soft-delete (trash), restore, and permanent-delete flows."""

    def _get_vendor_id(self):
        """Helper: create a vendor and return its vendor_id."""
        self._auth_as(self.admin_user)
        resp = self._create_vendor()
        return resp.json()["vendorId"]

    def test_destroy_soft_deletes(self):
        """DELETE on vendor moves it to trash (soft-delete), not hard delete."""
        vid = self._get_vendor_id()
        self._auth_as(self.admin_user)
        resp = self.client.delete(f"/api/purchase/vendors/{vid}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Not in default list
        list_resp = self.client.get("/api/purchase/vendors/")
        vendor_ids = [v["vendorId"] for v in list_resp.json()["results"]]
        self.assertNotIn(vid, vendor_ids)
        # Still in all_objects
        self.assertTrue(Vendor.all_objects.filter(vendor_id=vid).exists())

    def test_trash_list_shows_deleted_vendors(self):
        """Trash endpoint lists soft-deleted vendors."""
        vid = self._get_vendor_id()
        self._auth_as(self.admin_user)
        self.client.delete(f"/api/purchase/vendors/{vid}/")
        resp = self.client.get("/api/purchase/vendors/trash/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        vendor_ids = [v["vendorId"] for v in resp.json()["results"]]
        self.assertIn(vid, vendor_ids)

    def test_restore_brings_back_vendor(self):
        """Restoring a trashed vendor returns it to the default queryset."""
        vid = self._get_vendor_id()
        self._auth_as(self.admin_user)
        self.client.delete(f"/api/purchase/vendors/{vid}/")
        # Restore
        resp = self.client.post(f"/api/purchase/vendors/{vid}/restore/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Now visible in default list
        list_resp = self.client.get("/api/purchase/vendors/")
        vendor_ids = [v["vendorId"] for v in list_resp.json()["results"]]
        self.assertIn(vid, vendor_ids)

    def test_permanent_delete_requires_superuser(self):
        """Non-superuser (even Admin group) gets 403 on permanent-delete."""
        vid = self._get_vendor_id()
        self._auth_as(self.admin_user)
        self.client.delete(f"/api/purchase/vendors/{vid}/")

        # Admin group user — not superuser
        resp = self.client.delete(f"/api/purchase/vendors/{vid}/permanent-delete/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # Superuser succeeds
        self._auth_as(self.superuser)
        resp = self.client.delete(f"/api/purchase/vendors/{vid}/permanent-delete/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(Vendor.all_objects.filter(vendor_id=vid).exists())


class VendorPermissionTests(VendorAPITestMixin, TestCase):
    """Test RBAC enforcement on vendor endpoints."""

    def test_non_purchase_role_gets_403(self):
        """Sales-only user should be denied access to all vendor endpoints."""
        self._auth_as(self.sales_user)

        # List
        resp = self.client.get("/api/purchase/vendors/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # Create
        resp = self._create_vendor()
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_gets_401_or_403(self):
        """Unauthenticated requests are denied."""
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/purchase/vendors/")
        self.assertIn(resp.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_purchase_user_cannot_delete(self):
        """Purchase user (non-admin) should get 403 on DELETE (OnlyAdminCanDelete)."""
        # Create as admin
        self._auth_as(self.admin_user)
        resp = self._create_vendor()
        vid = resp.json()["vendorId"]

        # Try delete as purchase user
        self._auth_as(self.purchase_user)
        resp = self.client.delete(f"/api/purchase/vendors/{vid}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class VendorReadOnlyFieldTests(VendorAPITestMixin, TestCase):
    """Test that read-only fields cannot be set via API."""

    def test_payable_balance_ignored_on_create(self):
        """POSTing payable_balance should be silently ignored (read-only)."""
        self._auth_as(self.purchase_user)
        resp = self._create_vendor(payable_balance="999.99")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # Should be the default 0.00, not 999.99
        self.assertEqual(resp.json()["payableBalance"], "0.00")

    def test_advance_balance_ignored_on_create(self):
        """POSTing advance_balance should be silently ignored (read-only)."""
        self._auth_as(self.purchase_user)
        resp = self._create_vendor(advance_balance="555.00")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()["advanceBalance"], "0.00")

    def test_vendor_id_ignored_on_create(self):
        """POSTing vendor_id should be silently ignored (auto-generated)."""
        self._auth_as(self.purchase_user)
        resp = self._create_vendor(vendor_id=9999)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()["vendorId"], VENDOR_ID_START)


class PurchaseInvoiceAPITestMixin(VendorAPITestMixin):
    def setUp(self):
        super().setUp()
        self.vendor = Vendor.objects.create(
            vendor_name="Test Vendor",
            phone="03001111111",
            advance_balance=Decimal("0.00"),
            payable_balance=Decimal("0.00"),
        )
        self.vendor_id = self.vendor.vendor_id
        
        self.valid_invoice_data = {
            "vendor": {
                "vendorId": self.vendor_id,
                "vendorName": "Test Vendor",
                "phone": "03001111111"
            },
            "date": "2026-07-13",
            "paymentTerm": "Cash",
            "status": "Draft",
            "items": [
                {
                    "productName": "Item A",
                    "quantity": "2.00",
                    "purchasePrice": "100.00",
                    "discount": "0.00"
                }
            ],
            "paidAmount": "200.00"
        }

    def _create_invoice(self, **overrides):
        data = dict(self.valid_invoice_data)
        data.update(overrides)
        return self.client.post("/api/purchase/invoices/", data, format="json")


class PurchaseInvoiceTests(PurchaseInvoiceAPITestMixin, TestCase):
    
    def test_invoice_calculation(self):
        """Test subtotal/tax/net_total/balance_due logic."""
        self._auth_as(self.purchase_user)
        data = dict(self.valid_invoice_data)
        data["items"] = [
            {"productName": "A", "quantity": "0.10", "purchasePrice": "0.20", "discount": "0.00"}
        ]
        data["paidAmount"] = "0.00"
        data["paymentTerm"] = "Credit"
        data["vat_percentage"] = "10.00" # subtotal: 0.02, tax: 0.002 -> 0.00 ? wait, 0.02 * 10% = 0.002 
        
        # let's use bigger values
        data["items"] = [
            {"productName": "A", "quantity": "10.00", "purchasePrice": "10.00", "discount": "10.00"} # 90
        ]
        data["vat_percentage"] = "5.00" # 90 * 5% = 4.5
        data["invoiceDiscount"] = "4.50" # net = 90 + 4.5 - 4.5 = 90
        
        resp = self._create_invoice(**data)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()["subtotal"], "100.00")
        self.assertEqual(resp.json()["totalLineDiscount"], "10.00")
        self.assertEqual(resp.json()["taxAmount"], "4.50")
        self.assertEqual(resp.json()["netTotal"], "90.00")
        self.assertEqual(resp.json()["balanceDue"], "90.00")

    def test_invoice_number_generation(self):
        self._auth_as(self.purchase_user)
        r1 = self._create_invoice()
        r2 = self._create_invoice(paid_amount="0.00", payment_term="Credit")
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        self.assertTrue(r1.json()["invoiceNumber"].startswith("PI-"))
        self.assertTrue(r1.json()["invoiceNumber"].endswith("-00001"))
        self.assertTrue(r2.json()["invoiceNumber"].endswith("-00002"))

    def test_bill_number_not_unique(self):
        self._auth_as(self.purchase_user)
        r1 = self._create_invoice(bill_number="BILL-123")
        r2 = self._create_invoice(bill_number="BILL-123", paid_amount="0.00", payment_term="Credit")
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)
        
    def test_missing_vendor_returns_400(self):
        self._auth_as(self.purchase_user)
        resp = self._create_invoice(vendor={"vendorId": 99999, "vendorName": "Test Vendor", "phone": "03001111111"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Vendor not found", str(resp.json()))

    def test_vendor_validation_success(self):
        self._auth_as(self.purchase_user)
        resp = self._create_invoice()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()["vendor"]["vendorId"], self.vendor_id)
        self.assertEqual(resp.json()["vendor"]["vendorName"], "Test Vendor")

    def test_vendor_validation_mismatch(self):
        self._auth_as(self.purchase_user)
        resp1 = self._create_invoice(vendor={"vendorId": self.vendor_id, "vendorName": "Wrong Name", "phone": "03001111111"})
        self.assertEqual(resp1.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Vendor details do not match", str(resp1.json()))

        resp2 = self._create_invoice(vendor={"vendorId": self.vendor_id, "vendorName": "Test Vendor", "phone": "wrong"})
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Vendor details do not match", str(resp2.json()))

    def test_payment_status_logic(self):
        self._auth_as(self.purchase_user)
        # 1. Unpaid: paid_amount = 0, advance = 0. net = 200
        r1 = self._create_invoice(paid_amount="0.00", payment_term="Credit")
        self.assertEqual(r1.json()["paymentStatus"], "Unpaid")

        # 2. Partial: paid_amount = 100, advance = 0. net = 200
        r2 = self._create_invoice(paid_amount="100.00", payment_term="Credit")
        self.assertEqual(r2.json()["paymentStatus"], "Partial")

        # 3. Paid: paid_amount = 200, advance = 0. net = 200
        r3 = self._create_invoice(paid_amount="200.00", payment_term="Cash")
        self.assertEqual(r3.json()["paymentStatus"], "Paid")

        # 4. Advance: paid_amount = 250, advance = 0. net = 200
        r4 = self._create_invoice(paid_amount="250.00", payment_term="Cash")
        self.assertEqual(r4.json()["paymentStatus"], "Advance")

        # 5. payment_status is read-only
        r5 = self._create_invoice(payment_status="Paid", paid_amount="0.00", payment_term="Credit")
        self.assertEqual(r5.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r5.json()["paymentStatus"], "Unpaid")

    def test_payment_term_validation(self):
        self._auth_as(self.purchase_user)
        # Net total 200, paid 100, term Cash -> rejected
        r1 = self._create_invoice(paid_amount="100.00", payment_term="Cash")
        self.assertEqual(r1.status_code, status.HTTP_400_BAD_REQUEST)
        
        # Net total 200, paid 200, term Credit -> rejected
        r2 = self._create_invoice(paid_amount="200.00", payment_term="Credit")
        self.assertEqual(r2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_advance_consumption_on_save(self):
        self.vendor.advance_balance = Decimal("150.00")
        self.vendor.save()
        
        self._auth_as(self.purchase_user)
        # Net total 200. Paid 0. Term Credit.
        # Advance 150 -> balance_due 50. Term must be Credit.
        resp = self._create_invoice(paid_amount="0.00", payment_term="Credit", status="Saved")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()["advanceApplied"], "150.00")
        self.assertEqual(resp.json()["balanceDue"], "50.00")
        
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.advance_balance, Decimal("0.00"))
        self.assertEqual(self.vendor.payable_balance, Decimal("50.00"))

    def test_credit_increment_on_save(self):
        self._auth_as(self.purchase_user)
        resp = self._create_invoice(paid_amount="50.00", payment_term="Credit", status="Saved")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()["balanceDue"], "150.00")
        
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, Decimal("150.00"))

    def test_lock_error_on_saved_invoice(self):
        self._auth_as(self.purchase_user)
        resp = self._create_invoice(paid_amount="200.00", payment_term="Cash", status="Saved")
        invoice_id = resp.json()["id"]
        
        # Try to modify
        patch_resp = self.client.patch(f"/api/purchase/invoices/{invoice_id}/", {"notes": "test"}, format="json")
        self.assertEqual(patch_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Saved invoices are locked", str(patch_resp.json()))

    def test_trash_restore_reverses_balances(self):
        self._auth_as(self.admin_user)
        self.vendor.advance_balance = Decimal("100.00")
        self.vendor.save()
        
        # Net 200, paid 0, advance 100 -> due 100, credit +100
        resp = self._create_invoice(paid_amount="0.00", payment_term="Credit", status="Saved")
        invoice_id = resp.json()["id"]
        
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.advance_balance, Decimal("0.00"))
        self.assertEqual(self.vendor.payable_balance, Decimal("100.00"))
        
        # Trash
        self.client.delete(f"/api/purchase/invoices/{invoice_id}/")
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.advance_balance, Decimal("100.00"))
        self.assertEqual(self.vendor.payable_balance, Decimal("0.00"))
        
        # Restore
        self.client.post(f"/api/purchase/invoices/{invoice_id}/restore/")
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.advance_balance, Decimal("0.00"))
        self.assertEqual(self.vendor.payable_balance, Decimal("100.00"))

    def test_invoice_discount_exceeds_net(self):
        self._auth_as(self.purchase_user)
        # Net total 200, invoice_discount 201 -> rejected
        resp = self._create_invoice(invoice_discount="201.00", paid_amount="0.00", payment_term="Credit")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_purchase_role(self):
        self._auth_as(self.sales_user)
        resp = self._create_invoice()
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_endpoint_shape(self):
        """Test the list endpoint returns exactly the requested shape."""
        self._auth_as(self.purchase_user)
        # Create a couple of invoices with different payment statuses
        i1 = self._create_invoice(paid_amount="0.00", payment_term="Credit", status="Saved")
        i2 = self._create_invoice(paid_amount="200.00", payment_term="Cash", status="Saved", bill_number="B-123")
        
        self.assertEqual(i1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(i2.status_code, status.HTTP_201_CREATED)

        resp = self.client.get("/api/purchase/invoices/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        
        results = resp.json().get("results", [])
        self.assertGreaterEqual(len(results), 2)
        
        for item in results:
            # Check for correct keys
            self.assertIn("id", item)
            self.assertIn("invoiceNumber", item)
            self.assertIn("billNumber", item)
            self.assertIn("date", item)
            self.assertIn("paymentTerm", item)
            self.assertIn("invoiceStatus", item)
            self.assertIn("paymentStatus", item)
            self.assertIn("subtotal", item)
            self.assertIn("netTotal", item)
            self.assertIn("balanceDue", item)
            
            # Check vendor object
            self.assertIn("vendor", item)
            vendor = item["vendor"]
            self.assertIn("vendorId", vendor)
            self.assertIn("vendorName", vendor)
            self.assertIn("phone", vendor)
            
            # Check absence of wrong keys
            self.assertNotIn("total", item)
            self.assertNotIn("paid", item)
            self.assertNotIn("pending", item)
            self.assertNotIn("status", item) # We renamed it to invoiceStatus
            
        # Verify payment statuses in list
        payment_statuses = [item["paymentStatus"] for item in results]
        self.assertIn("Unpaid", payment_statuses)
        self.assertIn("Paid", payment_statuses)


class VendorPaymentTests(PurchaseInvoiceAPITestMixin, TestCase):
    """Tests for VendorPayment and Expense endpoints and business logic."""
    
    def setUp(self):
        super().setUp()
        # Create an invoice with net=200, paid=0 -> balance_due=200
        # This will increment vendor.payable_balance by 200
        self._auth_as(self.purchase_user)
        resp = self._create_invoice(paid_amount="0.00", payment_term="Credit", status="Saved")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.invoice_number = resp.json()["invoiceNumber"]
        
        self.vendor.refresh_from_db()
        # payable_balance should now be 200
        self.assertEqual(self.vendor.payable_balance, Decimal("200.00"))

    def _create_payment(self, amount, invoice_number=None, vendor_id=None, vendor_name=None, phone=None):
        data = {
            "vendor": {
                "vendorId": vendor_id or self.vendor_id,
                "vendorName": vendor_name or "Test Vendor",
                "phone": phone if phone is not None else "03001111111"
            },
            "amountPaid": str(amount),
            "date": "2026-07-14",
            "method": "Bank Transfer",
            "notes": "Test payment"
        }
        if invoice_number:
            data["invoice"] = invoice_number
        return self.client.post("/api/purchase/vendor-payments/", data, format="json")

    def test_payment_targeting_invoice_only(self):
        """Payment targeting invoice reduces balance_due and increments applied_to_invoice."""
        resp = self._create_payment("100.00", invoice_number=self.invoice_number)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()["appliedToInvoice"], "100.00")
        self.assertEqual(resp.json()["appliedToPayable"], "0.00")
        self.assertEqual(resp.json()["appliedToAdvance"], "0.00")
        
        from purchase.models import PurchaseInvoice
        invoice = PurchaseInvoice.objects.get(invoice_number=self.invoice_number)
        self.assertEqual(invoice.paid_amount, Decimal("100.00"))
        self.assertEqual(invoice.balance_due, Decimal("100.00"))

    def test_payment_overflows_all_three_tiers(self):
        """Payment amount > invoice due overflows to payable_balance, then advance_balance."""
        # Due is 200. Let's add more payable_balance without an invoice (manual test setup)
        self.vendor.payable_balance = Decimal("300.00") # 200 from invoice + 100 extra
        self.vendor.save()
        
        # Pay 450. 
        # 1. 200 goes to invoice (invoice due is 200) -> leftover 250
        # 2. 250 goes to payable_balance (which is 300) -> leftover 0
        resp = self._create_payment("450.00", invoice_number=self.invoice_number)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()["appliedToInvoice"], "200.00")
        self.assertEqual(resp.json()["appliedToPayable"], "100.00")
        self.assertEqual(resp.json()["appliedToAdvance"], "150.00")
        
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, Decimal("0.00"))
        self.assertEqual(self.vendor.advance_balance, Decimal("150.00"))
        
        # Now pay 100. 
        # Invoice due is 0.
        # Payable is 0.
        # 100 goes to advance.
        resp2 = self._create_payment("100.00", invoice_number=self.invoice_number)
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp2.json()["appliedToInvoice"], "0.00")
        self.assertEqual(resp2.json()["appliedToPayable"], "0.00")
        self.assertEqual(resp2.json()["appliedToAdvance"], "100.00")
        
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, Decimal("0.00"))
        self.assertEqual(self.vendor.advance_balance, Decimal("250.00"))

    def test_general_ledger_payment(self):
        """Payment without invoice applies directly to payable_balance then advance_balance."""
        self.vendor.payable_balance = Decimal("100.00")
        self.vendor.save()
        
        resp = self._create_payment("150.00")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()["appliedToInvoice"], "0.00")
        self.assertEqual(resp.json()["appliedToPayable"], "100.00")
        self.assertEqual(resp.json()["appliedToAdvance"], "50.00")
        
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, Decimal("0.00"))
        self.assertEqual(self.vendor.advance_balance, Decimal("50.00"))

    def test_payment_number_auto_generates(self):
        resp1 = self._create_payment("10.00")
        resp2 = self._create_payment("10.00")
        self.assertTrue(resp1.json()["paymentNumber"].startswith("SP-"))
        self.assertTrue(resp1.json()["paymentNumber"].endswith("-00001"))
        self.assertTrue(resp2.json()["paymentNumber"].endswith("-00002"))

    def test_vendor_invoice_mismatch(self):
        """Vendor/invoice mismatch in payment payload returns 400."""
        # Create another vendor
        v2_resp = self.client.post("/api/purchase/vendors/", {
            "vendorName": "Vendor 2", "phone": "03002222222", "openingPayable": "0.00"
        }, format="json")
        v2_id = v2_resp.json()["vendorId"]
        
        # Try paying vendor 2's money against vendor 1's invoice
        resp = self._create_payment("50.00", invoice_number=self.invoice_number, vendor_id=v2_id, vendor_name="Vendor 2", phone="03002222222")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("does not belong to this vendor", str(resp.json()))

    def test_trash_restore_reverses_balance_effects(self):
        self.vendor.payable_balance = Decimal("100.00")
        self.vendor.save()
        
        resp = self._create_payment("150.00") # 100 payable, 50 advance
        pid = resp.json()["id"]
        
        self._auth_as(self.admin_user)
        # Trash it
        self.client.delete(f"/api/purchase/vendor-payments/{pid}/")
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, Decimal("100.00"))
        self.assertEqual(self.vendor.advance_balance, Decimal("0.00"))
        
        # Restore it
        self.client.post(f"/api/purchase/vendor-payments/{pid}/restore/")
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, Decimal("0.00"))
        self.assertEqual(self.vendor.advance_balance, Decimal("50.00"))

    def test_expense_creation(self):
        self._auth_as(self.purchase_user)
        for cat in ["Salary", "Utilities", "Rent"]:
            resp = self.client.post("/api/purchase/expenses/", {
                "category": cat,
                "amount": "100.00",
                "paymentMethod": "Cash",
                "date": "2026-07-13",
                "notes": "Test"
            }, format="json")
            self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
            self.assertEqual(resp.json()["category"], cat)

    def test_expense_number_generation_independent(self):
        self._auth_as(self.purchase_user)
        # We already created an invoice (PI-xxxx-00001) in setUp
        # Create a payment (SP-xxxx-00001)
        p1 = self._create_payment("10.00")
        # Create an expense (EXP-xxxx-00001)
        e1 = self.client.post("/api/purchase/expenses/", {
            "category": "Salary", "amount": "50.00"
        }, format="json")
        
        # Create another of each
        i2 = self._create_invoice(paid_amount="0.00", payment_term="Credit", status="Saved")
        p2 = self._create_payment("10.00")
        e2 = self.client.post("/api/purchase/expenses/", {
            "category": "Rent", "amount": "100.00"
        }, format="json")
        
        self.assertTrue(i2.json()["invoiceNumber"].endswith("-00002"))
        self.assertTrue(p2.json()["paymentNumber"].endswith("-00002"))
        self.assertTrue(e2.json()["expenseNumber"].endswith("-00002"))

    def test_auto_generate_payment_from_invoice(self):
        self._auth_as(self.purchase_user)
        # Advance is 0. Payable is 0 (from fresh vendor perspective, let's reset it)
        self.vendor.payable_balance = Decimal("0.00")
        self.vendor.save()
        
        # Create invoice with net 200, term Credit, paid 50
        # Since paid > 0, should auto-create VendorPayment
        resp = self._create_invoice(paid_amount="50.00", payment_term="Credit", status="Saved")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        
        self.vendor.refresh_from_db()
        
        # Expected balances:
        # Invoice was for 200. Term Credit -> adds 200 to payable_balance.
        # Payment of 50 -> reduces payable_balance? No, payment applies to invoice first!
        # So Invoice balance_due = 150. paid_amount = 50.
        # Vendor payable_balance = 150. Advance = 0.
        self.assertEqual(self.vendor.payable_balance, Decimal("150.00"))
        self.assertEqual(self.vendor.advance_balance, Decimal("0.00"))
        
        from purchase.models import PurchaseInvoice
        invoice = PurchaseInvoice.objects.get(id=resp.json()["id"])
        self.assertEqual(invoice.paid_amount, Decimal("50.00"))
        self.assertEqual(invoice.balance_due, Decimal("150.00"))
        
        # Verify VendorPayment record
        from purchase.models import VendorPayment
        payment = VendorPayment.objects.get(invoice=invoice)
        self.assertEqual(payment.amount_paid, Decimal("50.00"))
        self.assertEqual(payment.applied_to_invoice, Decimal("50.00"))
        self.assertEqual(payment.applied_to_payable, Decimal("0.00"))

    def test_auto_generate_payment_overpayment_cash(self):
        self._auth_as(self.purchase_user)
        # Make a walkin-like or cash purchase with overpayment
        # Net = 200. Paid = 250. Cash term.
        # Expected:
        # Invoice balance_due = 0.
        # Advance balance = 50.
        # Payable balance = 0.
        self.vendor.payable_balance = Decimal("0.00")
        self.vendor.save()
        
        resp = self._create_invoice(paid_amount="250.00", payment_term="Cash", status="Saved")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, Decimal("0.00"))
        self.assertEqual(self.vendor.advance_balance, Decimal("50.00"))
        
        from purchase.models import PurchaseInvoice, VendorPayment
        invoice = PurchaseInvoice.objects.get(id=resp.json()["id"])
        self.assertEqual(invoice.paid_amount, Decimal("200.00")) # Capped at net_total
        self.assertEqual(invoice.balance_due, Decimal("0.00"))
        
        payment = VendorPayment.objects.get(invoice=invoice)
        self.assertEqual(payment.amount_paid, Decimal("250.00"))
        self.assertEqual(payment.applied_to_invoice, Decimal("200.00"))
        self.assertEqual(payment.applied_to_payable, Decimal("0.00"))
        self.assertEqual(payment.applied_to_advance, Decimal("50.00"))

    def test_non_purchase_role_denied(self):
        self._auth_as(self.sales_user)
        resp1 = self.client.get("/api/purchase/vendor-payments/")
        resp2 = self.client.get("/api/purchase/expenses/")
        self.assertEqual(resp1.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp2.status_code, status.HTTP_403_FORBIDDEN)


class VendorLedgerTests(PurchaseInvoiceAPITestMixin, TestCase):
    """Tests for Vendor ledger endpoint."""
    
    def test_ledger_no_invoices_returns_opening(self):
        self.vendor.opening_payable = Decimal("100.00")
        self.vendor.save()
        self._auth_as(self.purchase_user)
        
        resp = self.client.get(f"/api/purchase/vendors/{self.vendor_id}/ledger/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        
        data = resp.json()
        self.assertEqual(data["vendor"]["vendorId"], self.vendor_id)
        
        # Summary
        self.assertEqual(data["summary"]["openingPayable"], 100.0)
        self.assertEqual(data["summary"]["creditPurchases"], 0.0)
        
        # Ledger entries
        self.assertEqual(len(data["ledger"]), 1)
        self.assertEqual(data["ledger"][0]["voucher"], "OPENING")
        self.assertEqual(data["ledger"][0]["credit"], 100.0)
        self.assertEqual(data["ledger"][0]["debit"], 0.0)
        self.assertEqual(data["ledger"][0]["balance"], 100.0)

    def test_ledger_with_invoices_and_payments_order_and_balance(self):
        self._auth_as(self.purchase_user)
        
        self.vendor.opening_payable = Decimal("50.00")
        self.vendor.save()
        
        # Day 1: Invoice 1: 100 Credit
        # Balance becomes 150
        data1 = dict(self.valid_invoice_data)
        data1["date"] = "2026-07-01"
        data1["paymentTerm"] = "Credit"
        data1["paidAmount"] = "0.00"
        data1["status"] = "Saved"
        data1["items"] = [{"productName": "A", "quantity": "1.00", "purchasePrice": "100.00", "discount": "0.00"}]
        resp1 = self.client.post("/api/purchase/invoices/", data1, format="json")
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        
        # Day 2: Payment of 70
        # Balance becomes 80
        resp2 = self.client.post("/api/purchase/vendor-payments/", {
            "vendor": {"vendorId": self.vendor_id, "vendorName": "Test Vendor", "phone": "03001111111"},
            "amountPaid": "70.00",
            "date": "2026-07-02",
            "method": "Cash"
        }, format="json")
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        
        # Day 3: Invoice 2: 200 Cash with 200 paid
        # Balance becomes 80 (since cash invoice paid immediately, or increases to 280 then drops to 80)
        data3 = dict(self.valid_invoice_data)
        data3["date"] = "2026-07-03"
        data3["paymentTerm"] = "Cash"
        data3["paidAmount"] = "200.00"
        data3["status"] = "Saved"
        data3["items"] = [{"productName": "B", "quantity": "1.00", "purchasePrice": "200.00", "discount": "0.00"}]
        resp3 = self.client.post("/api/purchase/invoices/", data3, format="json")
        self.assertEqual(resp3.status_code, status.HTTP_201_CREATED)
        
        resp = self.client.get(f"/api/purchase/vendors/{self.vendor_id}/ledger/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        
        ledger = resp.json()["ledger"]
        # Expected:
        # 1. Opening: Credit 50 -> Balance 50
        # 2. Inv1: Credit 100 -> Balance 150
        # 3. Pay1: Debit 70 -> Balance 80
        # 4. Inv2: Credit 200 -> Balance 280
        # 5. Pay2 (auto from Inv2): Debit 200 -> Balance 80
        
        self.assertEqual(len(ledger), 5)
        
        self.assertEqual(ledger[0]["voucher"], "OPENING")
        self.assertEqual(ledger[0]["credit"], 50.0)
        self.assertEqual(ledger[0]["balance"], 50.0)
        
        self.assertTrue(ledger[1]["voucher"].startswith("PI-"))
        self.assertEqual(ledger[1]["credit"], 100.0)
        self.assertEqual(ledger[1]["balance"], 150.0)
        
        self.assertTrue(ledger[2]["voucher"].startswith("SP-"))
        self.assertEqual(ledger[2]["debit"], 70.0)
        self.assertEqual(ledger[2]["balance"], 80.0)
        
        self.assertTrue(ledger[3]["voucher"].startswith("PI-"))
        self.assertEqual(ledger[3]["credit"], 200.0)
        self.assertEqual(ledger[3]["balance"], 280.0)
        
        self.assertTrue(ledger[4]["voucher"].startswith("SP-"))
        self.assertEqual(ledger[4]["debit"], 200.0)
        self.assertEqual(ledger[4]["balance"], 80.0)
        
        # Verify running balance matches final payable_balance
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, Decimal("80.00"))
        
        summary = resp.json()["summary"]
        self.assertEqual(summary["closingBalance"], 80.0)

    def test_ledger_excludes_draft_and_trashed(self):
        self._auth_as(self.purchase_user)
        # Draft invoice
        data1 = dict(self.valid_invoice_data)
        data1["date"] = "2026-07-01"
        data1["paymentTerm"] = "Credit"
        data1["paidAmount"] = "0.00"
        data1["status"] = "Draft"
        data1["items"] = [{"productName": "A", "quantity": "1.00", "purchasePrice": "100.00", "discount": "0.00"}]
        self.client.post("/api/purchase/invoices/", data1, format="json")
        
        # Saved invoice but trashed later
        data2 = dict(self.valid_invoice_data)
        data2["date"] = "2026-07-02"
        data2["paymentTerm"] = "Credit"
        data2["paidAmount"] = "0.00"
        data2["status"] = "Saved"
        data2["items"] = [{"productName": "B", "quantity": "1.00", "purchasePrice": "50.00", "discount": "0.00"}]
        resp2 = self.client.post("/api/purchase/invoices/", data2, format="json")
        
        # Trash it
        self.client.delete(f"/api/purchase/invoices/{resp2.json()['id']}/")
        
        resp = self.client.get(f"/api/purchase/vendors/{self.vendor_id}/ledger/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        
        ledger = resp.json()["ledger"]
        self.assertEqual(len(ledger), 0)

    def test_ledger_date_range_balance_brought_forward(self):
        self._auth_as(self.purchase_user)
        self.vendor.opening_payable = Decimal("10.00")
        self.vendor.save()
        
        # Prior to range (Date: 2026-06-01)
        data1 = dict(self.valid_invoice_data)
        data1["date"] = "2026-06-01"
        data1["paymentTerm"] = "Credit"
        data1["paidAmount"] = "0.00"
        data1["status"] = "Saved"
        data1["items"] = [{"productName": "A", "quantity": "1.00", "purchasePrice": "100.00", "discount": "0.00"}]
        self.client.post("/api/purchase/invoices/", data1, format="json") # Balance = 110
        
        self.client.post("/api/purchase/vendor-payments/", {
            "vendor": {"vendorId": self.vendor_id, "vendorName": "Test Vendor", "phone": "03001111111"},
            "amountPaid": "20.00",
            "date": "2026-06-15",
            "method": "Cash"
        }, format="json") # Balance = 90
        
        # Within range (Date: 2026-07-01)
        data2 = dict(self.valid_invoice_data)
        data2["date"] = "2026-07-01"
        data2["paymentTerm"] = "Credit"
        data2["paidAmount"] = "0.00"
        data2["status"] = "Saved"
        data2["items"] = [{"productName": "B", "quantity": "1.00", "purchasePrice": "50.00", "discount": "0.00"}]
        self.client.post("/api/purchase/invoices/", data2, format="json")
        
        # Query with range
        resp = self.client.get(f"/api/purchase/vendors/{self.vendor_id}/ledger/?from=2026-07-01&to=2026-07-31")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        
        ledger = resp.json()["ledger"]
        self.assertEqual(len(ledger), 2)
        
        self.assertEqual(ledger[0]["description"], "Balance Brought Forward")
        self.assertEqual(ledger[0]["credit"], 90.0)
        self.assertEqual(ledger[0]["balance"], 90.0)
        
        self.assertTrue(ledger[1]["voucher"].startswith("PI-"))
        self.assertEqual(ledger[1]["credit"], 50.0)
        self.assertEqual(ledger[1]["balance"], 140.0)

    def test_ledger_invalid_vendor(self):
        self._auth_as(self.purchase_user)
        resp = self.client.get("/api/purchase/vendors/999999/ledger/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_ledger_sales_user_denied(self):
        self._auth_as(self.sales_user)
        resp = self.client.get(f"/api/purchase/vendors/{self.vendor_id}/ledger/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

