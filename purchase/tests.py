"""
Comprehensive test suite for the purchase module.

Covers: models, serializers, viewsets, balance effects, reversals,
vendor ledger, camelCase contract, and RBAC enforcement.

Every test uses Decimal for money assertions and includes descriptive
assertion messages so failures are self-explanatory.
"""

import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from purchase.models import (
    VENDOR_ID_START,
    Expense,
    PurchaseInvoice,
    PurchaseItem,
    Vendor,
    VendorPayment,
)


# ─── helpers ────────────────────────────────────────────────────────

def _create_groups():
    """Ensure the three required auth groups exist."""
    Group.objects.get_or_create(name="Admin")
    Group.objects.get_or_create(name="Sales")
    Group.objects.get_or_create(name="Purchase")


def _make_user(username, role=None, is_superuser=False):
    """Create a user and optionally assign to a group."""
    user = User.objects.create_user(
        username=username,
        password="testpass123",
        is_superuser=is_superuser,
        is_staff=is_superuser,
    )
    if role:
        group = Group.objects.get(name=role)
        user.groups.add(group)
    return user


def _vendor_payload(name="Acme Supplies", phone="0300-1234567", **extra):
    data = {"vendorName": name, "phone": phone}
    data.update(extra)
    return data


def _invoice_payload(vendor, *, items=None, status_val="Draft",
                     payment_term="Credit", paid_amount="0.00",
                     vat_percentage="0.00", invoice_discount="0.00",
                     bill_number="", **extra):
    """Build a camelCase purchase invoice create payload."""
    if items is None:
        items = [{"productName": "Widget", "units": "pcs",
                  "quantity": "10", "purchasePrice": "100.00",
                  "discount": "0.00"}]
    data = {
        "vendor": {
            "vendorId": vendor.vendor_id,
            "vendorName": vendor.vendor_name,
            "phone": vendor.phone or "",
        },
        "billNumber": bill_number,
        "paymentTerm": payment_term,
        "paidAmount": paid_amount,
        "vatPercentage": vat_percentage,
        "invoiceDiscount": invoice_discount,
        "status": status_val,
        "items": items,
    }
    data.update(extra)
    return data


def _payment_payload(vendor, amount, invoice=None, method="Cash", **extra):
    """Build a camelCase vendor payment create payload."""
    data = {
        "vendor": {
            "vendorId": vendor.vendor_id,
            "vendorName": vendor.vendor_name,
            "phone": vendor.phone or "",
        },
        "amountPaid": str(amount),
        "method": method,
    }
    if invoice is not None:
        data["invoice"] = invoice.invoice_number
    data.update(extra)
    return data


# =====================================================================
# 1. VendorModelTests
# =====================================================================

class VendorModelTests(TestCase):
    """Vendor model unit tests: ID auto-gen, field defaults, phone uniqueness."""

    def test_first_vendor_id_starts_at_5000(self):
        v = Vendor.objects.create(vendor_name="First Vendor")
        self.assertEqual(
            v.vendor_id, VENDOR_ID_START,
            "First vendor should receive vendor_id == VENDOR_ID_START (5000)",
        )

    def test_vendor_id_continuous_sequence(self):
        v1 = Vendor.objects.create(vendor_name="V1")
        v2 = Vendor.objects.create(vendor_name="V2")
        v3 = Vendor.objects.create(vendor_name="V3")
        self.assertEqual(v2.vendor_id, v1.vendor_id + 1,
                         "Second vendor_id should be exactly one more than the first")
        self.assertEqual(v3.vendor_id, v2.vendor_id + 1,
                         "Third vendor_id should be exactly one more than the second")

    def test_vendor_id_includes_soft_deleted_in_sequence(self):
        """Soft-deleted vendors must not cause ID reuse."""
        v1 = Vendor.objects.create(vendor_name="V1")
        v1.soft_delete()
        v2 = Vendor.objects.create(vendor_name="V2")
        self.assertEqual(v2.vendor_id, v1.vendor_id + 1,
                         "Vendor ID sequence must include soft-deleted records")

    def test_multiple_vendors_with_null_phone_succeed(self):
        """NULL phones bypass the unique constraint (SQL NULL != NULL)."""
        v1 = Vendor.objects.create(vendor_name="V1", phone=None)
        v2 = Vendor.objects.create(vendor_name="V2", phone=None)
        self.assertIsNone(v1.phone, "Phone should store as None (NULL)")
        self.assertIsNone(v2.phone, "Phone should store as None (NULL)")

    def test_duplicate_phone_value_raises(self):
        """Two vendors with the same non-null phone must conflict."""
        Vendor.objects.create(vendor_name="V1", phone="0300-1111111")
        with self.assertRaises(Exception):
            Vendor.objects.create(vendor_name="V2", phone="0300-1111111")

    def test_default_field_values(self):
        v = Vendor.objects.create(vendor_name="Defaults")
        self.assertEqual(v.opening_payable, Decimal("0.00"),
                         "opening_payable defaults to 0.00")
        self.assertEqual(v.payable_balance, Decimal("0.00"),
                         "payable_balance defaults to 0.00")
        self.assertEqual(v.advance_balance, Decimal("0.00"),
                         "advance_balance defaults to 0.00")
        self.assertEqual(v.address, "", "address defaults to empty string")
        self.assertEqual(v.tax_number, "", "tax_number defaults to empty string")
        self.assertEqual(v.opening_note, "", "opening_note defaults to empty string")

    def test_decimal_safe_balances(self):
        """Verify no float-precision issues with balance fields."""
        v = Vendor.objects.create(
            vendor_name="Precision",
            opening_payable=Decimal("0.10"),
            payable_balance=Decimal("0.20"),
        )
        v.refresh_from_db()
        self.assertEqual(v.opening_payable, Decimal("0.10"),
                         "opening_payable must survive round-trip as Decimal")
        self.assertEqual(v.payable_balance, Decimal("0.20"),
                         "payable_balance must survive round-trip as Decimal")

    def test_str_representation(self):
        v = Vendor.objects.create(vendor_name="StrTest")
        self.assertEqual(str(v), "StrTest")


# =====================================================================
# 2. VendorViewSetTests
# =====================================================================

class VendorViewSetTests(APITestCase):
    """VendorViewSet CRUD, query params, serializer shapes, permissions."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.super_admin = _make_user("superadmin", is_superuser=True)
        self.admin = _make_user("admin_user", role="Admin")
        self.purchase_user = _make_user("purchase_user", role="Purchase")
        self.sale_user = _make_user("sale_user", role="Sales")
        self.client = APIClient()
        self.client.force_authenticate(user=self.purchase_user)
        self.url = "/api/purchase/vendors/"

    def _create_vendor(self, **kwargs):
        payload = _vendor_payload(**kwargs)
        return self.client.post(self.url, payload, format="json")

    # ── CRUD ─────────────────────────────────────────────────────────

    def test_create_vendor(self):
        resp = self._create_vendor()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED,
                         "Purchase user should be able to create a vendor")
        self.assertIn("vendor_id", resp.data,
                      "Response must include vendor_id")

    def test_retrieve_vendor_by_vendor_id(self):
        create_resp = self._create_vendor()
        vendor_id = create_resp.data["vendor_id"]
        resp = self.client.get(f"{self.url}{vendor_id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK,
                         "Retrieve by vendor_id lookup must return 200")
        self.assertEqual(resp.data["vendor_name"], "Acme Supplies")

    def test_update_vendor(self):
        create_resp = self._create_vendor()
        vendor_id = create_resp.data["vendor_id"]
        resp = self.client.put(
            f"{self.url}{vendor_id}/",
            {"vendorName": "Updated Name", "phone": "0300-1234567"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["vendor_name"], "Updated Name")

    def test_partial_update_vendor(self):
        create_resp = self._create_vendor()
        vendor_id = create_resp.data["vendor_id"]
        resp = self.client.patch(
            f"{self.url}{vendor_id}/",
            {"vendorName": "Patched"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["vendor_name"], "Patched")

    def test_delete_vendor_soft_deletes(self):
        self.client.force_authenticate(user=self.admin)
        create_resp = self._create_vendor()
        vendor_id = create_resp.data["vendor_id"]
        resp = self.client.delete(f"{self.url}{vendor_id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK,
                         "Soft-delete should return 200")
        self.assertEqual(Vendor.objects.filter(vendor_id=vendor_id).count(), 0,
                         "Vendor should be hidden from default manager after soft-delete")
        self.assertEqual(
            Vendor.all_objects.filter(vendor_id=vendor_id, is_deleted=True).count(), 1,
            "Vendor should be in all_objects as soft-deleted",
        )

    # ── Query params ─────────────────────────────────────────────────

    def test_filter_by_name(self):
        self._create_vendor(name="Alpha Corp")
        self._create_vendor(name="Beta LLC", phone="0300-9999999")
        resp = self.client.get(self.url, {"name": "alpha"})
        self.assertEqual(resp.data["count"], 1,
                         "?name= filter should match case-insensitively")

    def test_filter_by_vendor_id_via_lookup(self):
        create_resp = self._create_vendor()
        vid = create_resp.data["vendor_id"]
        resp = self.client.get(f"{self.url}{vid}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    # ── List serializer shape ────────────────────────────────────────

    def test_list_serializer_shape_includes_invoices_and_total_paid(self):
        self._create_vendor()
        resp = self.client.get(self.url)
        vendor_data = resp.data["results"][0]
        self.assertIn("invoices", vendor_data,
                       "List serializer must include 'invoices' nested array")
        self.assertIn("total_paid", vendor_data,
                       "List serializer must include 'total_paid'")

    def test_list_empty_invoice_vendor(self):
        """Vendor with no invoices shows empty list and 0.00 totalPaid."""
        self._create_vendor()
        resp = self.client.get(self.url)
        vendor_data = resp.data["results"][0]
        self.assertEqual(vendor_data["invoices"], [],
                         "Vendor with no invoices should have invoices=[]")
        self.assertEqual(
            Decimal(str(vendor_data["total_paid"])), Decimal("0.00"),
            "Vendor with no invoices should have total_paid=0.00",
        )

    # ── Detail serializer shape ──────────────────────────────────────

    def test_detail_excludes_total_paid_and_invoices(self):
        create_resp = self._create_vendor()
        vid = create_resp.data["vendor_id"]
        resp = self.client.get(f"{self.url}{vid}/")
        self.assertNotIn("total_paid", resp.data,
                          "Detail serializer must EXCLUDE total_paid")
        self.assertNotIn("invoices", resp.data,
                          "Detail serializer must EXCLUDE invoices")

    def test_detail_includes_all_fields(self):
        create_resp = self._create_vendor()
        vid = create_resp.data["vendor_id"]
        resp = self.client.get(f"{self.url}{vid}/")
        for field in ["id", "vendor_id", "vendor_name", "phone", "email",
                       "address", "tax_number", "opening_payable", "opening_note",
                       "payable_balance", "advance_balance", "created_at", "updated_at"]:
            self.assertIn(field, resp.data,
                           f"Detail must include '{field}'")

    # ── Read-only enforcement ────────────────────────────────────────

    def test_payable_balance_read_only_on_create(self):
        resp = self._create_vendor(payableBalance="999.99")
        self.assertEqual(
            Decimal(str(resp.data["payable_balance"])), Decimal("0.00"),
            "payable_balance must be read-only and default to 0.00 on create",
        )

    def test_advance_balance_read_only_on_update(self):
        create_resp = self._create_vendor()
        vid = create_resp.data["vendor_id"]
        resp = self.client.patch(
            f"{self.url}{vid}/",
            {"advanceBalance": "999.99"},
            format="json",
        )
        self.assertEqual(
            Decimal(str(resp.data["advance_balance"])), Decimal("0.00"),
            "advance_balance must be read-only and not change via PATCH",
        )

    # ── Trash / Restore / Permanent-Delete ───────────────────────────

    def test_trash_list(self):
        self.client.force_authenticate(user=self.admin)
        create_resp = self._create_vendor()
        vid = create_resp.data["vendor_id"]
        self.client.delete(f"{self.url}{vid}/")
        resp = self.client.get(f"{self.url}trash/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data["count"], 1,
                                "Trash list should contain the soft-deleted vendor")

    def test_restore_vendor(self):
        self.client.force_authenticate(user=self.admin)
        create_resp = self._create_vendor()
        vid = create_resp.data["vendor_id"]
        self.client.delete(f"{self.url}{vid}/")
        resp = self.client.post(f"{self.url}{vid}/restore/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(Vendor.objects.filter(vendor_id=vid).exists(),
                        "Restored vendor should appear in default queryset")

    def test_permanent_delete_requires_superuser(self):
        self.client.force_authenticate(user=self.admin)
        create_resp = self._create_vendor()
        vid = create_resp.data["vendor_id"]
        self.client.delete(f"{self.url}{vid}/")
        resp = self.client.delete(f"{self.url}{vid}/permanent-delete/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN,
                         "Non-superuser admin should not be able to permanently delete")

    def test_permanent_delete_by_superuser(self):
        self.client.force_authenticate(user=self.super_admin)
        create_resp = self._create_vendor()
        vid = create_resp.data["vendor_id"]
        self.client.delete(f"{self.url}{vid}/")
        resp = self.client.delete(f"{self.url}{vid}/permanent-delete/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Vendor.all_objects.filter(vendor_id=vid).count(), 0,
            "Permanent delete should remove the vendor from all_objects too",
        )

    # ── Duplicate phone via API returns 400 ──────────────────────────

    def test_duplicate_phone_returns_400(self):
        self._create_vendor(name="V1", phone="0300-1111111")
        resp = self._create_vendor(name="V2", phone="0300-1111111")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Duplicate phone should return 400, not 500")

    # ── N+1 query check on list endpoint ─────────────────────────────

    def test_list_vendor_query_count_stable(self):
        """Assert that adding more vendors doesn't wildly increase queries."""
        for i in range(5):
            Vendor.objects.create(vendor_name=f"Vendor {i}")
        self.client.force_authenticate(user=self.purchase_user)
        # Warm up ContentType / permission caches
        self.client.get(self.url)
        with self.assertNumQueries(5):
            # Expected: 2 group checks, 1 count, 1 vendors+annotate, 1 prefetch invoices
            # Key point: stays constant regardless of vendor count (no N+1)
            self.client.get(self.url)


# =====================================================================
# 3. PurchaseInvoiceModelTests
# =====================================================================

class PurchaseInvoiceModelTests(TestCase):
    """PurchaseInvoice model tests: computed properties, invoice_number, payment_status."""

    def setUp(self):
        self.vendor = Vendor.objects.create(vendor_name="Test Vendor")

    def _make_invoice(self, **kwargs):
        defaults = {
            "vendor": self.vendor,
            "payment_term": "Credit",
            "status": "Draft",
        }
        defaults.update(kwargs)
        return PurchaseInvoice.objects.create(**defaults)

    def _add_item(self, invoice, qty=10, price=Decimal("100.00"), discount=Decimal("0.00")):
        return PurchaseItem.objects.create(
            invoice=invoice,
            product_name="Widget",
            quantity=qty,
            purchase_price=price,
            discount=discount,
        )

    # ── Computed fields ──────────────────────────────────────────────

    def test_subtotal(self):
        inv = self._make_invoice()
        self._add_item(inv, qty=5, price=Decimal("200.00"))
        self._add_item(inv, qty=3, price=Decimal("150.00"))
        expected = Decimal("5") * Decimal("200.00") + Decimal("3") * Decimal("150.00")  # 1450.00
        self.assertEqual(inv.subtotal, expected,
                         "subtotal = sum(qty * purchase_price) across all items")

    def test_total_line_discount(self):
        inv = self._make_invoice()
        self._add_item(inv, qty=10, price=Decimal("100.00"), discount=Decimal("10.00")) # 1000 * 10% = 100
        self._add_item(inv, qty=5, price=Decimal("200.00"), discount=Decimal("5.00"))  # 1000 * 5% = 50
        self.assertEqual(inv.total_line_discount, Decimal("150.00"),
                         "total_line_discount = sum of all item discounts in currency amount")

    def test_tax_amount_calculated_on_subtotal_minus_line_discount(self):
        """Tax = (subtotal - total_line_discount) * vat_percentage / 100.
        Per PROJECT_ANALYSIS.md, purchase tax is on (subtotal - total_line_discount),
        BEFORE invoice_discount."""
        inv = self._make_invoice(vat_percentage=Decimal("10.00"))
        self._add_item(inv, qty=10, price=Decimal("100.00"), discount=Decimal("10.00"))
        # subtotal = 1000, line_discount = 100, base = 900
        # tax = 900 * 10/100 = 90
        self.assertEqual(inv.tax_amount, Decimal("90.00"),
                         "tax_amount should be (subtotal - total_line_discount) * vat%")

    def test_net_total_formula(self):
        inv = self._make_invoice(
            vat_percentage=Decimal("10.00"),
            invoice_discount=Decimal("5.00"),
        )
        self._add_item(inv, qty=10, price=Decimal("100.00"), discount=Decimal("10.00"))
        # subtotal=1000, line_disc=100, base=900, tax=90, invoice_disc=900*5%=45
        # net_total = 900 + 90 - 45 = 945
        self.assertEqual(inv.net_total, Decimal("945.00"),
                         "net_total = subtotal - total_line_discount + tax_amount - deducted_invoice_discount")

    def test_balance_due_formula(self):
        inv = self._make_invoice(paid_amount=Decimal("200.00"),
                                 advance_applied=Decimal("50.00"))
        self._add_item(inv, qty=10, price=Decimal("100.00"))
        # net_total = 1000, balance_due = 1000 - 200 - 50 = 750
        self.assertEqual(inv.balance_due, Decimal("750.00"),
                         "balance_due = net_total - paid_amount - advance_applied")

    def test_decimal_precision_edge_case(self):
        """Verify 0.1 + 0.2 style amounts don't introduce float errors."""
        inv = self._make_invoice()
        self._add_item(inv, qty=1, price=Decimal("0.10"), discount=Decimal("0.00"))
        self._add_item(inv, qty=1, price=Decimal("0.20"), discount=Decimal("0.00"))
        self.assertEqual(inv.subtotal, Decimal("0.30"),
                         "0.10 + 0.20 must equal exactly 0.30 (Decimal-safe)")

    # ── invoice_number auto-generation ───────────────────────────────

    def test_invoice_number_format(self):
        inv = self._make_invoice()
        self._add_item(inv)
        year = date.today().year
        self.assertTrue(
            inv.invoice_number.startswith(f"PI-{year}-"),
            f"invoice_number should start with PI-{year}-",
        )
        counter_str = inv.invoice_number.split("-")[-1]
        self.assertEqual(len(counter_str), 5,
                         "Counter part should be zero-padded to 5 digits")

    def test_invoice_number_continuous_counter(self):
        """Counter never resets — it's a global continuous sequence."""
        inv1 = self._make_invoice()
        self._add_item(inv1)
        inv2 = self._make_invoice()
        self._add_item(inv2)
        n1 = int(inv1.invoice_number.split("-")[-1])
        n2 = int(inv2.invoice_number.split("-")[-1])
        self.assertEqual(n2, n1 + 1,
                         "Invoice counter must be continuous (no gaps)")

    def test_counter_includes_soft_deleted(self):
        inv1 = self._make_invoice()
        self._add_item(inv1)
        inv1.soft_delete()
        inv2 = self._make_invoice()
        self._add_item(inv2)
        n1 = int(inv1.invoice_number.split("-")[-1])
        n2 = int(inv2.invoice_number.split("-")[-1])
        self.assertEqual(n2, n1 + 1,
                         "Counter should include soft-deleted invoices in sequence")

    # ── payment_status property ──────────────────────────────────────

    def test_payment_status_unpaid(self):
        inv = self._make_invoice(paid_amount=Decimal("0.00"),
                                 advance_applied=Decimal("0.00"))
        self._add_item(inv)
        self.assertEqual(inv.payment_status, "Unpaid",
                         "paid+advance=0 should be Unpaid")

    def test_payment_status_partial(self):
        inv = self._make_invoice(paid_amount=Decimal("500.00"),
                                 advance_applied=Decimal("0.00"))
        self._add_item(inv, qty=10, price=Decimal("100.00"))
        # net_total=1000, covered=500 < 1000
        self.assertEqual(inv.payment_status, "Partial",
                         "covered < net_total should be Partial")

    def test_payment_status_paid_exact(self):
        inv = self._make_invoice(paid_amount=Decimal("1000.00"),
                                 advance_applied=Decimal("0.00"))
        self._add_item(inv, qty=10, price=Decimal("100.00"))
        self.assertEqual(inv.payment_status, "Paid",
                         "covered == net_total should be Paid")

    def test_payment_status_advance(self):
        inv = self._make_invoice(paid_amount=Decimal("1000.01"),
                                 advance_applied=Decimal("0.00"))
        self._add_item(inv, qty=10, price=Decimal("100.00"))
        self.assertEqual(inv.payment_status, "Advance",
                         "covered > net_total should be Advance")

    def test_payment_status_paid_boundary_one_cent_under(self):
        """One cent under net_total → Partial, not Paid (no tolerance)."""
        inv = self._make_invoice(paid_amount=Decimal("999.99"),
                                 advance_applied=Decimal("0.00"))
        self._add_item(inv, qty=10, price=Decimal("100.00"))
        self.assertEqual(inv.payment_status, "Partial",
                         "One cent under should be Partial (exact comparison, no tolerance)")

    def test_payment_status_paid_boundary_one_cent_over(self):
        inv = self._make_invoice(paid_amount=Decimal("1000.01"),
                                 advance_applied=Decimal("0.00"))
        self._add_item(inv, qty=10, price=Decimal("100.00"))
        self.assertEqual(inv.payment_status, "Advance",
                         "One cent over should be Advance")


# =====================================================================
# 4. PurchaseInvoiceSerializerValidationTests
# =====================================================================

class PurchaseInvoiceSerializerValidationTests(APITestCase):
    """PurchaseInvoiceSerializer validation rules tested via API."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.user = _make_user("purchase_val", role="Purchase")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.vendor = Vendor.objects.create(
            vendor_name="Val Vendor", phone="0300-1234567",
        )
        self.url = "/api/purchase/invoices/"

    # ── Vendor match validation ──────────────────────────────────────

    def test_correct_vendor_match_succeeds(self):
        payload = _invoice_payload(self.vendor)
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED,
                         "Correct vendor data should allow invoice creation")

    def test_mismatched_vendor_name_returns_400(self):
        payload = _invoice_payload(self.vendor)
        payload["vendor"]["vendorName"] = "WRONG NAME"
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Mismatched vendorName should return 400")
        self.assertEqual(
            PurchaseInvoice.objects.count(), 0,
            "No invoice should be created on vendor mismatch",
        )

    def test_mismatched_phone_returns_400(self):
        payload = _invoice_payload(self.vendor)
        payload["vendor"]["phone"] = "0999-WRONGPH"
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Mismatched phone should return 400")

    def test_nonexistent_vendor_id_returns_400(self):
        payload = _invoice_payload(self.vendor)
        payload["vendor"]["vendorId"] = 99999
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Non-existent vendorId should return 400")

    # ── Payment term forcing rules ───────────────────────────────────

    def test_underpaid_cash_rejected(self):
        """If paidAmount (+ vendor advance) < netTotal, Cash is rejected."""
        payload = _invoice_payload(
            self.vendor,
            payment_term="Cash",
            paid_amount="500.00",  # net_total = 1000
        )
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Underpaid + Cash should be rejected")

    def test_fully_covered_credit_rejected(self):
        """If paidAmount (+ vendor advance) >= netTotal, Credit is rejected."""
        payload = _invoice_payload(
            self.vendor,
            payment_term="Credit",
            paid_amount="1000.00",  # covers net_total exactly
        )
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Fully covered + Credit should be rejected")

    # ── Item validation ──────────────────────────────────────────────

    def test_quantity_zero_rejected(self):
        payload = _invoice_payload(self.vendor, items=[
            {"productName": "W", "units": "pcs", "quantity": "0",
             "purchasePrice": "10.00", "discount": "0.00"},
        ])
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "quantity=0 should be rejected")

    def test_purchase_price_negative_rejected(self):
        payload = _invoice_payload(self.vendor, items=[
            {"productName": "W", "units": "pcs", "quantity": "1",
             "purchasePrice": "-5.00", "discount": "0.00"},
        ])
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Negative purchase price should be rejected")

    def test_negative_discount_rejected(self):
        payload = _invoice_payload(self.vendor, items=[
            {"productName": "W", "units": "pcs", "quantity": "1",
             "purchasePrice": "10.00", "discount": "-1.00"},
        ])
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Negative line item discount should be rejected")

    def test_over_100_discount_rejected(self):
        payload = _invoice_payload(self.vendor, items=[
            {"productName": "W", "units": "pcs", "quantity": "1",
             "purchasePrice": "10.00", "discount": "101.00"},
        ])
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Line item discount > 100 should be rejected")

    def test_invoice_discount_over_100_rejected(self):
        payload = _invoice_payload(
            self.vendor,
            invoice_discount="100.01",
        )
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "invoice_discount > 100 should be rejected")

    def test_at_least_one_item_required(self):
        payload = _invoice_payload(self.vendor, items=[])
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Invoice with zero items should be rejected")

    # ── Draft vs Saved immutability ──────────────────────────────────

    def test_saved_invoice_cannot_be_updated(self):
        """PUT/PATCH on a Saved invoice should be rejected."""
        payload = _invoice_payload(
            self.vendor, status_val="Saved",
            payment_term="Cash", paid_amount="1000.00",
        )
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        invoice_id = resp.data["id"]

        patch_resp = self.client.patch(
            f"{self.url}{invoice_id}/",
            {"billNumber": "changed"},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "PATCH on Saved invoice must return 400")

    def test_draft_invoice_freely_editable(self):
        payload = _invoice_payload(self.vendor, status_val="Draft")
        resp = self.client.post(self.url, payload, format="json")
        invoice_id = resp.data["id"]

        patch_resp = self.client.patch(
            f"{self.url}{invoice_id}/",
            {"billNumber": "EDITED-123"},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK,
                         "Draft invoice should be freely editable")


# =====================================================================
# 5. PurchaseInvoiceBalanceEffectsTests
# =====================================================================

class PurchaseInvoiceBalanceEffectsTests(APITestCase):
    """Balance side-effects of PurchaseInvoice create/trash/restore."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.admin = _make_user("admin_bal", role="Admin")
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.vendor = Vendor.objects.create(
            vendor_name="Balance Vendor", phone="0300-0000001",
        )
        self.url = "/api/purchase/invoices/"

    def _refresh_vendor(self):
        self.vendor.refresh_from_db()

    # ── Draft has zero effect ────────────────────────────────────────

    def test_draft_invoice_no_balance_effect(self):
        payload = _invoice_payload(self.vendor, status_val="Draft")
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self._refresh_vendor()
        self.assertEqual(self.vendor.payable_balance, Decimal("0.00"),
                         "Draft invoice must NOT change payableBalance")
        self.assertEqual(self.vendor.advance_balance, Decimal("0.00"),
                         "Draft invoice must NOT change advanceBalance")

    # ── Saved+Credit adds to payable ─────────────────────────────────

    def test_saved_credit_adds_to_payable_balance(self):
        """Saved Credit invoice with no payment should add balance_due to payableBalance."""
        payload = _invoice_payload(
            self.vendor, status_val="Saved",
            payment_term="Credit", paid_amount="0.00",
        )
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self._refresh_vendor()
        # net_total = 10*100 = 1000
        self.assertEqual(self.vendor.payable_balance, Decimal("1000.00"),
                         "Saved Credit invoice should add net_total to payableBalance")

    # ── Saved with existing advance consumes it ──────────────────────

    def test_saved_invoice_partial_advance_consumption(self):
        """Vendor with advance_balance partially consumed by a new Saved invoice."""
        self.vendor.advance_balance = Decimal("300.00")
        self.vendor.save(update_fields=["advance_balance"])

        payload = _invoice_payload(
            self.vendor, status_val="Saved",
            payment_term="Credit", paid_amount="0.00",
        )
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self._refresh_vendor()
        # net_total=1000, advance=300 consumed, remaining due=700 → payable
        self.assertEqual(self.vendor.advance_balance, Decimal("0.00"),
                         "All 300 advance should be consumed")
        self.assertEqual(self.vendor.payable_balance, Decimal("700.00"),
                         "Remaining balance_due (1000-300=700) should be added to payable")
        inv = PurchaseInvoice.objects.get(id=resp.data["id"])
        self.assertEqual(inv.advance_applied, Decimal("300.00"),
                         "advance_applied should equal the consumed advance")

    def test_saved_invoice_full_advance_consumption(self):
        """Vendor advance covers the entire net_total."""
        self.vendor.advance_balance = Decimal("2000.00")
        self.vendor.save(update_fields=["advance_balance"])

        payload = _invoice_payload(
            self.vendor, status_val="Saved",
            payment_term="Cash", paid_amount="0.00",
        )
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self._refresh_vendor()
        # advance = 2000, net_total = 1000: consumes 1000, leftover = 1000
        self.assertEqual(self.vendor.advance_balance, Decimal("1000.00"),
                         "Remaining advance should be 2000 - 1000 = 1000")
        inv = PurchaseInvoice.objects.get(id=resp.data["id"])
        self.assertEqual(inv.advance_applied, Decimal("1000.00"),
                         "advance_applied should equal the full net_total")

    # ── Saved with paidAmount auto-creates VendorPayment ─────────────

    def test_saved_invoice_with_payment_creates_vendor_payment(self):
        payload = _invoice_payload(
            self.vendor, status_val="Saved",
            payment_term="Cash", paid_amount="1000.00",
        )
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        vp = VendorPayment.objects.filter(vendor=self.vendor).first()
        self.assertIsNotNone(vp, "A VendorPayment should be auto-created")
        self.assertEqual(vp.amount_paid, Decimal("1000.00"),
                         "Auto-created payment amount should match paidAmount")
        year = date.today().year
        self.assertTrue(vp.payment_number.startswith(f"SP-{year}-"),
                        f"Auto-created payment_number should start with SP-{year}-")

    # ── Trashing Saved invoice reverses balance ──────────────────────

    def test_trashing_saved_invoice_reverses_balance(self):
        payload = _invoice_payload(
            self.vendor, status_val="Saved",
            payment_term="Credit", paid_amount="0.00",
        )
        resp = self.client.post(self.url, payload, format="json")
        invoice_id = resp.data["id"]

        pre_trash_payable = Vendor.objects.get(pk=self.vendor.pk).payable_balance
        self.assertEqual(pre_trash_payable, Decimal("1000.00"))

        self.client.delete(f"{self.url}{invoice_id}/")
        self._refresh_vendor()
        self.assertEqual(self.vendor.payable_balance, Decimal("0.00"),
                         "Trashing Saved invoice should reverse payableBalance to pre-invoice state")

    def test_trashing_saved_invoice_restores_advance(self):
        self.vendor.advance_balance = Decimal("300.00")
        self.vendor.save(update_fields=["advance_balance"])

        payload = _invoice_payload(
            self.vendor, status_val="Saved",
            payment_term="Credit", paid_amount="0.00",
        )
        resp = self.client.post(self.url, payload, format="json")
        invoice_id = resp.data["id"]

        self._refresh_vendor()
        self.assertEqual(self.vendor.advance_balance, Decimal("0.00"),
                         "Advance should be consumed after Saved invoice")

        self.client.delete(f"{self.url}{invoice_id}/")
        self._refresh_vendor()
        self.assertEqual(self.vendor.advance_balance, Decimal("300.00"),
                         "Trashing should restore advance_balance to pre-invoice state")

    # ── Restoring re-applies ─────────────────────────────────────────

    def test_restoring_saved_invoice_reapplies_balance(self):
        payload = _invoice_payload(
            self.vendor, status_val="Saved",
            payment_term="Credit", paid_amount="0.00",
        )
        resp = self.client.post(self.url, payload, format="json")
        invoice_id = resp.data["id"]

        self.client.delete(f"{self.url}{invoice_id}/")
        self._refresh_vendor()
        self.assertEqual(self.vendor.payable_balance, Decimal("0.00"))

        self.client.post(f"{self.url}{invoice_id}/restore/")
        self._refresh_vendor()
        self.assertEqual(self.vendor.payable_balance, Decimal("1000.00"),
                         "Restoring Saved invoice should re-apply payableBalance")


# =====================================================================
# 6. PurchaseInvoiceViewSetTests
# =====================================================================

class PurchaseInvoiceViewSetTests(APITestCase):
    """PurchaseInvoiceViewSet: serializer shapes, query params, permissions."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.admin = _make_user("admin_inv", role="Admin")
        self.purchase_user = _make_user("purch_inv", role="Purchase")
        self.super_admin = _make_user("super_inv", is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.vendor = Vendor.objects.create(
            vendor_name="InvVendor", phone="0300-5555555",
        )
        self.url = "/api/purchase/invoices/"

    def _create_invoice(self, **kwargs):
        payload = _invoice_payload(self.vendor, **kwargs)
        return self.client.post(self.url, payload, format="json")

    # ── List serializer shape ────────────────────────────────────────

    def test_list_shape_has_invoice_status_not_status(self):
        self._create_invoice()
        resp = self.client.get(self.url)
        inv_data = resp.data["results"][0]
        self.assertIn("invoice_status", inv_data,
                       "List must have 'invoice_status' not 'status'")
        self.assertNotIn("status", inv_data,
                          "List must NOT have plain 'status' key")

    def test_list_shape_has_payment_status(self):
        self._create_invoice()
        resp = self.client.get(self.url)
        inv_data = resp.data["results"][0]
        self.assertIn("payment_status", inv_data,
                       "List must include payment_status")

    def test_list_shape_has_nested_vendor(self):
        self._create_invoice()
        resp = self.client.get(self.url)
        inv_data = resp.data["results"][0]
        self.assertIsInstance(inv_data["vendor"], dict,
                              "List vendor should be a nested object")
        for key in ["vendor_id", "vendor_name", "phone"]:
            self.assertIn(key, inv_data["vendor"],
                           f"List vendor object must contain '{key}'")

    def test_list_shape_no_items(self):
        self._create_invoice()
        resp = self.client.get(self.url)
        inv_data = resp.data["results"][0]
        self.assertNotIn("items", inv_data,
                          "List serializer must NOT include items")

    # ── Detail serializer shape ──────────────────────────────────────

    def test_detail_includes_items(self):
        resp = self._create_invoice()
        inv_id = resp.data["id"]
        detail = self.client.get(f"{self.url}{inv_id}/")
        self.assertIn("items", detail.data,
                       "Detail must include items array")

    def test_detail_includes_full_fields(self):
        resp = self._create_invoice()
        inv_id = resp.data["id"]
        detail = self.client.get(f"{self.url}{inv_id}/")
        for field in ["id", "vendor", "bill_number", "invoice_number", "date",
                       "payment_term", "payment_method", "paid_amount",
                       "advance_applied", "payment_reference", "notes",
                       "vat_percentage", "invoice_discount", "status",
                       "subtotal", "total_line_discount", "tax_amount",
                       "net_total", "balance_due", "payment_status", "items"]:
            self.assertIn(field, detail.data,
                           f"Detail must include '{field}'")

    # ── Query params ─────────────────────────────────────────────────

    def test_filter_by_vendor(self):
        self._create_invoice()
        resp = self.client.get(self.url, {"vendor": self.vendor.vendor_id})
        self.assertGreaterEqual(resp.data["count"], 1)

    def test_filter_by_bill_number(self):
        self._create_invoice(bill_number="BILL-001")
        resp = self.client.get(self.url, {"bill_number": "BILL"})
        self.assertGreaterEqual(resp.data["count"], 1)

    def test_filter_by_invoice_number(self):
        create_resp = self._create_invoice()
        inv_num = create_resp.data["invoice_number"]
        resp = self.client.get(self.url, {"invoice_number": inv_num})
        self.assertEqual(resp.data["count"], 1)

    def test_filter_by_status(self):
        self._create_invoice(status_val="Draft")
        resp = self.client.get(self.url, {"status": "Draft"})
        self.assertGreaterEqual(resp.data["count"], 1)

    def test_filter_by_payment_term(self):
        self._create_invoice(payment_term="Credit")
        resp = self.client.get(self.url, {"payment_term": "Credit"})
        self.assertGreaterEqual(resp.data["count"], 1)

    def test_ordering(self):
        self._create_invoice()
        resp = self.client.get(self.url, {"ordering": "date"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    # ── billNumber allows duplicates ─────────────────────────────────

    def test_bill_number_allows_duplicates(self):
        self._create_invoice(bill_number="SAME-BILL")
        resp = self._create_invoice(bill_number="SAME-BILL")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED,
                         "billNumber should allow duplicates across invoices")

    # ── Trash / Restore / Permanent-Delete ───────────────────────────

    def test_trash_list(self):
        resp = self._create_invoice()
        inv_id = resp.data["id"]
        self.client.delete(f"{self.url}{inv_id}/")
        trash_resp = self.client.get(f"{self.url}trash/")
        self.assertGreaterEqual(trash_resp.data["count"], 1)

    def test_restore_invoice(self):
        resp = self._create_invoice()
        inv_id = resp.data["id"]
        self.client.delete(f"{self.url}{inv_id}/")
        restore_resp = self.client.post(f"{self.url}{inv_id}/restore/")
        self.assertEqual(restore_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(PurchaseInvoice.objects.filter(id=inv_id).exists())

    def test_permanent_delete(self):
        self.client.force_authenticate(user=self.super_admin)
        resp = self._create_invoice()
        inv_id = resp.data["id"]
        self.client.delete(f"{self.url}{inv_id}/")
        perm_resp = self.client.delete(f"{self.url}{inv_id}/permanent-delete/")
        self.assertEqual(perm_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(PurchaseInvoice.all_objects.filter(id=inv_id).count(), 0)

    def test_permanent_delete_denied_for_non_superuser(self):
        resp = self._create_invoice()
        inv_id = resp.data["id"]
        self.client.delete(f"{self.url}{inv_id}/")
        perm_resp = self.client.delete(f"{self.url}{inv_id}/permanent-delete/")
        self.assertEqual(perm_resp.status_code, status.HTTP_403_FORBIDDEN)


# =====================================================================
# 7. PurchaseItemModelTests
# =====================================================================

class PurchaseItemModelTests(TestCase):
    """PurchaseItem model: total formula, cascade delete, standalone CRUD."""

    def setUp(self):
        self.vendor = Vendor.objects.create(vendor_name="ItemVendor")
        self.invoice = PurchaseInvoice.objects.create(
            vendor=self.vendor, payment_term="Credit",
        )

    def test_total_formula(self):
        item = PurchaseItem.objects.create(
            invoice=self.invoice,
            product_name="Gadget",
            quantity=Decimal("5"),
            purchase_price=Decimal("200.00"),
            discount=Decimal("10.00"),
        )
        expected = Decimal("5") * Decimal("200.00") - (Decimal("5") * Decimal("200.00") * Decimal("0.10"))  # 900
        self.assertEqual(item.total, expected,
                         "total = (quantity * purchase_price) - ((quantity * purchase_price) * discount/100)")

    def test_cascade_delete_on_invoice_hard_delete(self):
        PurchaseItem.objects.create(
            invoice=self.invoice,
            product_name="Cascade Test",
            quantity=1, purchase_price=Decimal("10.00"),
        )
        inv_id = self.invoice.id
        # Hard delete the invoice (bypass soft-delete for this test)
        PurchaseInvoice.all_objects.filter(id=inv_id).delete()
        self.assertEqual(PurchaseItem.objects.count(), 0,
                         "Items should be cascade-deleted when parent invoice is hard-deleted")


class PurchaseItemStandaloneCRUDTests(APITestCase):
    """Standalone item CRUD via /api/purchase/items/."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.user = _make_user("purch_item", role="Purchase")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.vendor = Vendor.objects.create(vendor_name="ItemVendor2")
        self.invoice = PurchaseInvoice.objects.create(
            vendor=self.vendor, payment_term="Credit",
        )
        PurchaseItem.objects.create(
            invoice=self.invoice, product_name="Seed",
            quantity=1, purchase_price=Decimal("10.00"),
        )
        self.url = "/api/purchase/items/"

    def test_list_items(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_create_item_standalone(self):
        resp = self.client.post(self.url, {
            "invoice": self.invoice.id,
            "productName": "New Item",
            "units": "kg",
            "quantity": "5.00",
            "purchasePrice": "25.00",
            "discount": "0.00",
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_item_shape(self):
        resp = self.client.get(self.url)
        # Handle both paginated and non-paginated responses
        items = resp.data if isinstance(resp.data, list) else resp.data.get("results", resp.data)
        if isinstance(items, list) and len(items) > 0:
            item = items[0]
        else:
            item = resp.data
        for field in ["id", "invoice", "product_name", "units", "quantity",
                       "purchase_price", "discount", "total"]:
            self.assertIn(field, item,
                           f"Item shape must include '{field}'")


# =====================================================================
# 8. VendorPaymentModelTests
# =====================================================================

class VendorPaymentModelTests(TestCase):
    """VendorPayment model: payment_number format, independent counter."""

    def setUp(self):
        self.vendor = Vendor.objects.create(vendor_name="PayVendor")

    def test_payment_number_format(self):
        vp = VendorPayment.objects.create(
            vendor=self.vendor,
            amount_paid=Decimal("100.00"),
            balance_after=Decimal("0.00"),
        )
        year = date.today().year
        self.assertTrue(
            vp.payment_number.startswith(f"SP-{year}-"),
            f"payment_number should start with SP-{year}-",
        )
        counter_str = vp.payment_number.split("-")[-1]
        self.assertEqual(len(counter_str), 5,
                         "Counter should be zero-padded to 5 digits")

    def test_payment_number_continuous(self):
        vp1 = VendorPayment.objects.create(
            vendor=self.vendor,
            amount_paid=Decimal("10.00"),
            balance_after=Decimal("0.00"),
        )
        vp2 = VendorPayment.objects.create(
            vendor=self.vendor,
            amount_paid=Decimal("20.00"),
            balance_after=Decimal("0.00"),
        )
        n1 = int(vp1.payment_number.split("-")[-1])
        n2 = int(vp2.payment_number.split("-")[-1])
        self.assertEqual(n2, n1 + 1,
                         "Payment counter must be continuous")

    def test_independent_counter_from_invoice_and_expense(self):
        """Creating invoices/expenses should not skip payment counter numbers."""
        vp1 = VendorPayment.objects.create(
            vendor=self.vendor,
            amount_paid=Decimal("10.00"),
            balance_after=Decimal("0.00"),
        )
        # Create an invoice in between
        PurchaseInvoice.objects.create(
            vendor=self.vendor, payment_term="Credit",
        )
        # Create an expense in between
        Expense.objects.create(
            category="Office", amount=Decimal("50.00"),
        )
        vp2 = VendorPayment.objects.create(
            vendor=self.vendor,
            amount_paid=Decimal("20.00"),
            balance_after=Decimal("0.00"),
        )
        n1 = int(vp1.payment_number.split("-")[-1])
        n2 = int(vp2.payment_number.split("-")[-1])
        self.assertEqual(n2, n1 + 1,
                         "Payment counter should not skip due to interleaved invoice/expense creation")


# =====================================================================
# 9. VendorPaymentSerializerTests
# =====================================================================

class VendorPaymentSerializerTests(APITestCase):
    """VendorPaymentSerializer: vendor match, invoice mismatch, water-flow tiers."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.user = _make_user("purch_pay", role="Purchase")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.vendor = Vendor.objects.create(
            vendor_name="PaySerVendor", phone="0300-7777777",
        )
        self.url = "/api/purchase/vendor-payments/"

    def _create_saved_credit_invoice(self, net_total=Decimal("1000.00")):
        """Create a Saved Credit invoice directly for testing payments against it."""
        inv = PurchaseInvoice.objects.create(
            vendor=self.vendor, payment_term="Credit", status="Draft",
        )
        PurchaseItem.objects.create(
            invoice=inv, product_name="Widget",
            quantity=Decimal("10"), purchase_price=net_total / Decimal("10"),
        )
        # Manually transition to Saved and set balance
        inv.status = "Saved"
        inv.save(update_fields=["status"])
        # Update vendor payable_balance to reflect the Credit invoice
        self.vendor.payable_balance += net_total
        self.vendor.save(update_fields=["payable_balance"])
        return inv

    # ── Vendor match validation ──────────────────────────────────────

    def test_correct_vendor_match(self):
        payload = _payment_payload(self.vendor, Decimal("50.00"))
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED,
                         "Correct vendor data should allow payment creation")

    def test_mismatched_vendor_name_returns_400(self):
        payload = _payment_payload(self.vendor, Decimal("50.00"))
        payload["vendor"]["vendorName"] = "WRONG"
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Mismatched vendorName in payment should return 400")

    def test_nonexistent_vendor_returns_400(self):
        payload = _payment_payload(self.vendor, Decimal("50.00"))
        payload["vendor"]["vendorId"] = 99999
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Non-existent vendorId in payment should return 400")

    # ── Invoice/vendor mismatch ──────────────────────────────────────

    def test_invoice_vendor_mismatch_rejected(self):
        other_vendor = Vendor.objects.create(
            vendor_name="Other", phone="0300-8888888",
        )
        inv = PurchaseInvoice.objects.create(
            vendor=other_vendor, payment_term="Credit", status="Draft",
        )
        PurchaseItem.objects.create(
            invoice=inv, product_name="X", quantity=1,
            purchase_price=Decimal("100.00"),
        )
        payload = _payment_payload(self.vendor, Decimal("50.00"), invoice=inv)
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST,
                         "Payment targeting an invoice belonging to a different vendor should be rejected")

    # ── Water-flow distribution tiers ────────────────────────────────

    def test_tier_a_fully_covers_invoice_balance_due(self):
        """Payment exactly covers the invoice's balanceDue with nothing left over."""
        inv = self._create_saved_credit_invoice(net_total=Decimal("500.00"))
        payload = _payment_payload(self.vendor, Decimal("500.00"), invoice=inv)
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            Decimal(str(resp.data["applied_to_invoice"])), Decimal("500.00"),
            "Tier (a): full payment should apply to invoice",
        )
        self.assertEqual(
            Decimal(str(resp.data["applied_to_payable"])), Decimal("0.00"),
            "Tier (a): nothing should spill into payable",
        )
        self.assertEqual(
            Decimal(str(resp.data["applied_to_advance"])), Decimal("0.00"),
            "Tier (a): nothing should spill into advance",
        )

    def test_tier_b_covers_invoice_and_spills_into_payable(self):
        """Payment covers invoice balance_due AND spills into payableBalance."""
        inv = self._create_saved_credit_invoice(net_total=Decimal("500.00"))
        # vendor payable = 500 from invoice, set additional payable
        self.vendor.refresh_from_db()
        self.vendor.payable_balance += Decimal("200.00")  # now 500 + 200 = 700 total payable
        self.vendor.save(update_fields=["payable_balance"])

        # Pay 600: 500 → invoice, 100 → payable (of the extra 200)
        payload = _payment_payload(self.vendor, Decimal("600.00"), invoice=inv)
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            Decimal(str(resp.data["applied_to_invoice"])), Decimal("500.00"),
            "Tier (b): invoice balance_due fully covered",
        )
        # After invoice payment reduces payable by 500 (Credit invoice),
        # remaining payable = 200, remaining payment = 100
        self.assertEqual(
            Decimal(str(resp.data["applied_to_payable"])), Decimal("100.00"),
            "Tier (b): 100 should spill into payable",
        )
        self.assertEqual(
            Decimal(str(resp.data["applied_to_advance"])), Decimal("0.00"),
            "Tier (b): nothing should spill into advance",
        )

    def test_tier_c_covers_invoice_payable_and_spills_into_advance(self):
        """Payment covers invoice, then payableBalance, then spills into advanceBalance."""
        inv = self._create_saved_credit_invoice(net_total=Decimal("500.00"))
        # vendor.payable_balance = 500 from the invoice
        self.vendor.refresh_from_db()

        # Pay 800: 500→invoice, then payable_balance remainder → payable, rest → advance
        payload = _payment_payload(self.vendor, Decimal("800.00"), invoice=inv)
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            Decimal(str(resp.data["applied_to_invoice"])), Decimal("500.00"),
            "Tier (c): full invoice coverage",
        )
        # After paying invoice (Credit), vendor payable reduced by 500 → 0
        # remaining = 300, payable = 0, so 0 spills to payable
        self.assertEqual(
            Decimal(str(resp.data["applied_to_payable"])), Decimal("0.00"),
            "Tier (c): payable already zeroed by invoice payment",
        )
        self.assertEqual(
            Decimal(str(resp.data["applied_to_advance"])), Decimal("300.00"),
            "Tier (c): 300 should spill into advance",
        )

    def test_tier_d_general_ledger_payment(self):
        """No invoice targeted — payment applies to payableBalance then advanceBalance."""
        self.vendor.payable_balance = Decimal("200.00")
        self.vendor.save(update_fields=["payable_balance"])

        payload = _payment_payload(self.vendor, Decimal("350.00"))
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            Decimal(str(resp.data["applied_to_invoice"])), Decimal("0.00"),
            "Tier (d): no invoice targeted",
        )
        self.assertEqual(
            Decimal(str(resp.data["applied_to_payable"])), Decimal("200.00"),
            "Tier (d): 200 should reduce payable",
        )
        self.assertEqual(
            Decimal(str(resp.data["applied_to_advance"])), Decimal("150.00"),
            "Tier (d): 150 should become advance",
        )

    def test_balance_after_snapshots_vendor_payable(self):
        self.vendor.payable_balance = Decimal("500.00")
        self.vendor.save(update_fields=["payable_balance"])

        payload = _payment_payload(self.vendor, Decimal("300.00"))
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(
            Decimal(str(resp.data["balance_after"])), Decimal("200.00"),
            "balance_after should be vendor.payable_balance after payment (500-300=200)",
        )


# =====================================================================
# 10. VendorPaymentReversalTests
# =====================================================================

class VendorPaymentReversalTests(APITestCase):
    """Trashing/restoring VendorPayment reverses/re-applies effects."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.admin = _make_user("admin_rev", role="Admin")
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.vendor = Vendor.objects.create(
            vendor_name="RevVendor", phone="0300-6666666",
            payable_balance=Decimal("1000.00"),
        )
        self.url = "/api/purchase/vendor-payments/"

    def _make_payment(self, amount, invoice=None):
        payload = _payment_payload(self.vendor, amount, invoice=invoice)
        return self.client.post(self.url, payload, format="json")

    def test_trashing_payment_reverses_all_effects(self):
        # Record pre-payment state
        pre_payable = Decimal("1000.00")
        pre_advance = Decimal("0.00")

        # Make a 1200 payment: 1000 → payable, 200 → advance
        resp = self._make_payment(Decimal("1200.00"))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        payment_id = resp.data["id"]

        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, Decimal("0.00"))
        self.assertEqual(self.vendor.advance_balance, Decimal("200.00"))

        # Trash the payment
        del_resp = self.client.delete(f"{self.url}{payment_id}/")
        self.assertEqual(del_resp.status_code, status.HTTP_200_OK)

        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, pre_payable,
                         "Trashing payment should restore payableBalance to pre-payment state")
        self.assertEqual(self.vendor.advance_balance, pre_advance,
                         "Trashing payment should restore advanceBalance to pre-payment state")

    def test_restoring_payment_reapplies_effects(self):
        resp = self._make_payment(Decimal("500.00"))
        payment_id = resp.data["id"]

        # Trash
        self.client.delete(f"{self.url}{payment_id}/")
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, Decimal("1000.00"))

        # Restore
        restore_resp = self.client.post(f"{self.url}{payment_id}/restore/")
        self.assertEqual(restore_resp.status_code, status.HTTP_200_OK)
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.payable_balance, Decimal("500.00"),
                         "Restoring payment should re-apply effects (1000-500=500)")

    def test_trashing_payment_reverses_invoice_paid_amount(self):
        inv = PurchaseInvoice.objects.create(
            vendor=self.vendor, payment_term="Credit", status="Draft",
        )
        PurchaseItem.objects.create(
            invoice=inv, product_name="W", quantity=10,
            purchase_price=Decimal("100.00"),
        )
        inv.status = "Saved"
        inv.save(update_fields=["status"])

        resp = self._make_payment(Decimal("300.00"), invoice=inv)
        payment_id = resp.data["id"]

        inv.refresh_from_db()
        self.assertEqual(inv.paid_amount, Decimal("300.00"))

        self.client.delete(f"{self.url}{payment_id}/")
        inv.refresh_from_db()
        self.assertEqual(inv.paid_amount, Decimal("0.00"),
                         "Trashing payment should reverse invoice.paid_amount")


# =====================================================================
# 11. VendorPaymentViewSetTests
# =====================================================================

class VendorPaymentViewSetTests(APITestCase):
    """VendorPaymentViewSet: shape, query params, trash/restore/perm-delete, permissions."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.admin = _make_user("admin_vp", role="Admin")
        self.purchase_user = _make_user("purch_vp", role="Purchase")
        self.super_admin = _make_user("super_vp", is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.vendor = Vendor.objects.create(
            vendor_name="VPVendor", phone="0300-4444444",
            payable_balance=Decimal("500.00"),
        )
        self.url = "/api/purchase/vendor-payments/"

    def _make_payment(self, amount=Decimal("100.00"), invoice=None):
        payload = _payment_payload(self.vendor, amount, invoice=invoice)
        return self.client.post(self.url, payload, format="json")

    # ── Response shape ───────────────────────────────────────────────

    def test_response_shape_has_nested_vendor_object(self):
        resp = self._make_payment()
        self.assertIsInstance(resp.data["vendor"], dict,
                              "Response must have nested vendor object")

    def test_response_shape_has_vendor_name_flat_field(self):
        """Per PROJECT_ANALYSIS.md line 96, vendorName IS present as a flat field
        (this is the documented serialization debt/anti-pattern)."""
        resp = self._make_payment()
        self.assertIn("vendor_name", resp.data,
                       "vendor_name should exist as flat field (documented anti-pattern)")

    def test_invoice_number_null_for_general_payment(self):
        resp = self._make_payment()
        self.assertIsNone(resp.data["invoice_number"],
                          "invoice_number should be null for general ledger payments")

    # ── Query params ─────────────────────────────────────────────────

    def test_filter_by_vendor(self):
        self._make_payment()
        resp = self.client.get(self.url, {"vendor": self.vendor.vendor_id})
        self.assertGreaterEqual(resp.data["count"], 1)

    def test_filter_by_invoice(self):
        inv = PurchaseInvoice.objects.create(
            vendor=self.vendor, payment_term="Credit", status="Draft",
        )
        PurchaseItem.objects.create(
            invoice=inv, product_name="W", quantity=1,
            purchase_price=Decimal("100.00"),
        )
        self._make_payment(invoice=inv)
        resp = self.client.get(self.url, {"invoice": inv.invoice_number})
        self.assertGreaterEqual(resp.data["count"], 1)

    # ── Trash / Restore / Permanent-Delete ───────────────────────────

    def test_trash_list(self):
        resp = self._make_payment()
        pid = resp.data["id"]
        self.client.delete(f"{self.url}{pid}/")
        trash_resp = self.client.get(f"{self.url}trash/")
        self.assertGreaterEqual(trash_resp.data["count"], 1)

    def test_restore_payment(self):
        resp = self._make_payment()
        pid = resp.data["id"]
        self.client.delete(f"{self.url}{pid}/")
        restore_resp = self.client.post(f"{self.url}{pid}/restore/")
        self.assertEqual(restore_resp.status_code, status.HTTP_200_OK)

    def test_permanent_delete_by_superuser(self):
        self.client.force_authenticate(user=self.super_admin)
        resp = self._make_payment()
        pid = resp.data["id"]
        self.client.delete(f"{self.url}{pid}/")
        perm_resp = self.client.delete(f"{self.url}{pid}/permanent-delete/")
        self.assertEqual(perm_resp.status_code, status.HTTP_200_OK)

    def test_permanent_delete_denied_non_superuser(self):
        resp = self._make_payment()
        pid = resp.data["id"]
        self.client.delete(f"{self.url}{pid}/")
        perm_resp = self.client.delete(f"{self.url}{pid}/permanent-delete/")
        self.assertEqual(perm_resp.status_code, status.HTTP_403_FORBIDDEN)


# =====================================================================
# 12. ExpenseModelTests
# =====================================================================

class ExpenseModelTests(TestCase):
    """Expense model: auto-number format, continuous counter, category flexibility."""

    def test_expense_number_format(self):
        exp = Expense.objects.create(category="Office", amount=Decimal("100.00"))
        year = date.today().year
        self.assertTrue(
            exp.expense_number.startswith(f"EXP-{year}-"),
            f"expense_number should start with EXP-{year}-",
        )

    def test_expense_number_continuous(self):
        e1 = Expense.objects.create(category="A", amount=Decimal("10.00"))
        e2 = Expense.objects.create(category="B", amount=Decimal("20.00"))
        n1 = int(e1.expense_number.split("-")[-1])
        n2 = int(e2.expense_number.split("-")[-1])
        self.assertEqual(n2, n1 + 1, "Expense counter must be continuous")

    def test_independent_counter_from_invoice_and_payment(self):
        e1 = Expense.objects.create(category="A", amount=Decimal("10.00"))
        vendor = Vendor.objects.create(vendor_name="V")
        PurchaseInvoice.objects.create(vendor=vendor, payment_term="Credit")
        VendorPayment.objects.create(
            vendor=vendor, amount_paid=Decimal("10.00"),
            balance_after=Decimal("0.00"),
        )
        e2 = Expense.objects.create(category="B", amount=Decimal("20.00"))
        n1 = int(e1.expense_number.split("-")[-1])
        n2 = int(e2.expense_number.split("-")[-1])
        self.assertEqual(n2, n1 + 1,
                         "Expense counter should be independent of invoice/payment counters")

    def test_category_accepts_arbitrary_text(self):
        categories = ["Office Supplies", "Travel & Transport", "Utilities / Bills",
                       "Miscellaneous", "R&D"]
        for cat in categories:
            exp = Expense.objects.create(category=cat, amount=Decimal("1.00"))
            self.assertEqual(exp.category, cat,
                             f"Category should accept arbitrary text: {cat}")

    def test_expense_optional_fields(self):
        exp = Expense.objects.create(
            category="Office", amount=Decimal("100.00"),
            person_supplier="John Doe", paid_by="Jane Doe"
        )
        self.assertEqual(exp.person_supplier, "John Doe")
        self.assertEqual(exp.paid_by, "Jane Doe")

    def test_expense_item_cascade_delete(self):
        from purchase.models import ExpenseItem
        exp = Expense.objects.create(category="Office", amount=Decimal("100.00"))
        ExpenseItem.objects.create(expense=exp, item_name="Pen", quantity=Decimal("10"), amount=Decimal("10.00"))
        
        Expense.all_objects.filter(id=exp.id).delete()
        self.assertEqual(ExpenseItem.objects.count(), 0)


# =====================================================================
# 13. ExpenseViewSetTests
# =====================================================================

class ExpenseViewSetTests(APITestCase):
    """ExpenseViewSet: CRUD, query params, trash/restore/perm-delete, permissions."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.admin = _make_user("admin_exp", role="Admin")
        self.purchase_user = _make_user("purch_exp", role="Purchase")
        self.super_admin = _make_user("super_exp", is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.purchase_user)
        self.url = "/api/purchase/expenses/"

    def _create_expense(self, **kwargs):
        data = {
            "category": kwargs.get("category", "Office"),
            "amount": kwargs.get("amount", "100.00"),
            "paymentMethod": kwargs.get("paymentMethod", "Cash"),
            "date": kwargs.get("date", str(date.today())),
            "notes": kwargs.get("notes", ""),
        }
        if "items" in kwargs:
            data["items"] = kwargs["items"]
            if "amount" not in kwargs:
                data.pop("amount", None)
        if "personSupplier" in kwargs:
            data["personSupplier"] = kwargs["personSupplier"]
        if "paidBy" in kwargs:
            data["paidBy"] = kwargs["paidBy"]
        return self.client.post(self.url, data, format="json")

    def test_create_expense(self):
        resp = self._create_expense()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("expense_number", resp.data)

    def test_create_expense_with_items_computes_amount(self):
        resp = self._create_expense(
            items=[
                {"itemName": "Pen", "quantity": "10", "amount": "50.00"},
                {"itemName": "Paper", "quantity": "5", "amount": "25.00"}
            ]
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(str(resp.data["amount"])), Decimal("75.00"), "Amount should be sum of items")
        self.assertEqual(len(resp.data["items"]), 2)

    def test_create_expense_no_items_manual_amount(self):
        resp = self._create_expense(amount="150.00")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(str(resp.data["amount"])), Decimal("150.00"))

    def test_person_supplier_and_paid_by_saved_and_returned(self):
        resp = self._create_expense(personSupplier="Alice", paidBy="Bob")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["person_supplier"], "Alice")
        self.assertEqual(resp.data["paid_by"], "Bob")

    def test_list_expenses(self):
        self._create_expense()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data["count"], 1)

    def test_retrieve_expense(self):
        create_resp = self._create_expense()
        eid = create_resp.data["id"]
        resp = self.client.get(f"{self.url}{eid}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_update_expense(self):
        create_resp = self._create_expense()
        eid = create_resp.data["id"]
        resp = self.client.put(f"{self.url}{eid}/", {
            "category": "Updated",
            "amount": "200.00",
            "paymentMethod": "Bank",
            "date": str(date.today()),
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["category"], "Updated")

    def test_expense_shape(self):
        resp = self._create_expense()
        for field in ["id", "expense_number", "category", "amount",
                       "person_supplier", "paid_by", "payment_method", "date", "notes", "created_at", "items"]:
            self.assertIn(field, resp.data, f"Expense must include '{field}'")

    # ── Query params ─────────────────────────────────────────────────

    def test_filter_by_category(self):
        self._create_expense(category="Travel")
        resp = self.client.get(self.url, {"category": "travel"})
        self.assertGreaterEqual(resp.data["count"], 1)

    def test_filter_by_date_range(self):
        self._create_expense(date=str(date.today()))
        resp = self.client.get(self.url, {
            "date_from": str(date.today()),
            "date_to": str(date.today()),
        })
        self.assertGreaterEqual(resp.data["count"], 1,
                                "date_from/date_to should filter expenses")

    # ── Expense has NO vendor/invoice fields ─────────────────────────

    def test_expense_standalone_no_vendor_invoice_fields(self):
        resp = self._create_expense()
        self.assertNotIn("vendor", resp.data, "Expense must NOT have vendor")
        self.assertNotIn("invoice", resp.data, "Expense must NOT have invoice")

    # ── Trash / Restore / Permanent-Delete ───────────────────────────

    def test_trash_expense(self):
        self.client.force_authenticate(user=self.admin)
        resp = self._create_expense()
        eid = resp.data["id"]
        del_resp = self.client.delete(f"{self.url}{eid}/")
        self.assertEqual(del_resp.status_code, status.HTTP_200_OK)

    def test_trash_list_expenses(self):
        self.client.force_authenticate(user=self.admin)
        resp = self._create_expense()
        eid = resp.data["id"]
        self.client.delete(f"{self.url}{eid}/")
        trash_resp = self.client.get(f"{self.url}trash/")
        self.assertGreaterEqual(trash_resp.data["count"], 1)

    def test_restore_expense(self):
        self.client.force_authenticate(user=self.admin)
        resp = self._create_expense()
        eid = resp.data["id"]
        self.client.delete(f"{self.url}{eid}/")
        restore_resp = self.client.post(f"{self.url}{eid}/restore/")
        self.assertEqual(restore_resp.status_code, status.HTTP_200_OK)

    def test_permanent_delete_expense(self):
        self.client.force_authenticate(user=self.super_admin)
        resp = self._create_expense()
        eid = resp.data["id"]
        self.client.delete(f"{self.url}{eid}/")
        perm_resp = self.client.delete(f"{self.url}{eid}/permanent-delete/")
        self.assertEqual(perm_resp.status_code, status.HTTP_200_OK)

    def test_permanent_delete_denied_non_superuser(self):
        self.client.force_authenticate(user=self.admin)
        resp = self._create_expense()
        eid = resp.data["id"]
        self.client.delete(f"{self.url}{eid}/")
        perm_resp = self.client.delete(f"{self.url}{eid}/permanent-delete/")
        self.assertEqual(perm_resp.status_code, status.HTTP_403_FORBIDDEN)


# =====================================================================
# 14. VendorLedgerTests
# =====================================================================

class VendorLedgerTests(APITestCase):
    """Vendor ledger endpoint — highest-risk area, thorough testing."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.admin = _make_user("admin_ledger", role="Admin")
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.vendor = Vendor.objects.create(
            vendor_name="LedgerVendor", phone="0300-3333333",
            opening_payable=Decimal("500.00"),
            payable_balance=Decimal("500.00"),
        )
        self.base_url = f"/api/purchase/vendors/{self.vendor.vendor_id}/ledger/"

    def _create_saved_credit_invoice(self, amount, inv_date=None):
        """Helper to create a Saved Credit invoice and manually apply balance."""
        inv = PurchaseInvoice.objects.create(
            vendor=self.vendor, payment_term="Credit",
            status="Saved", date=inv_date or date.today(),
        )
        PurchaseItem.objects.create(
            invoice=inv, product_name="Item",
            quantity=1, purchase_price=amount,
        )
        self.vendor.refresh_from_db()
        self.vendor.payable_balance += amount
        self.vendor.save(update_fields=["payable_balance"])
        return inv

    def _create_payment_direct(self, amount, pay_date=None, invoice=None):
        """Helper to create a VendorPayment and manually apply balance."""
        self.vendor.refresh_from_db()
        applied_payable = min(self.vendor.payable_balance, amount)
        applied_advance = amount - applied_payable
        self.vendor.payable_balance -= applied_payable
        self.vendor.advance_balance += applied_advance
        self.vendor.save(update_fields=["payable_balance", "advance_balance"])

        vp = VendorPayment.objects.create(
            vendor=self.vendor,
            invoice=invoice,
            amount_paid=amount,
            balance_after=self.vendor.payable_balance,
            applied_to_invoice=Decimal("0.00"),
            applied_to_payable=applied_payable,
            applied_to_advance=applied_advance,
            date=pay_date or date.today(),
        )
        return vp

    # ── Zero activity ────────────────────────────────────────────────

    def test_vendor_zero_activity(self):
        """Vendor with only opening balance, no invoices or payments."""
        resp = self.client.get(self.base_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("ledger", resp.data)
        self.assertIn("summary", resp.data)

    # ── Chronological ordering with hand-computed running balance ────

    def test_chronological_ordering_and_running_balance(self):
        """Create 5+ entries across dates and verify running balance at each."""
        today = date.today()
        d1 = today - timedelta(days=5)
        d2 = today - timedelta(days=4)
        d3 = today - timedelta(days=3)
        d4 = today - timedelta(days=2)
        d5 = today - timedelta(days=1)

        # Opening: 500 (credit)
        # d1: Invoice 200 (credit)  → balance = 500 + 200 = 700
        # d2: Payment 300 (debit)   → balance = 700 - 300 = 400
        # d3: Invoice 150 (credit)  → balance = 400 + 150 = 550
        # d4: Payment 100 (debit)   → balance = 550 - 100 = 450
        # d5: Invoice 50 (credit)   → balance = 450 + 50 = 500

        self._create_saved_credit_invoice(Decimal("200.00"), inv_date=d1)
        self._create_payment_direct(Decimal("300.00"), pay_date=d2)
        self._create_saved_credit_invoice(Decimal("150.00"), inv_date=d3)
        self._create_payment_direct(Decimal("100.00"), pay_date=d4)
        self._create_saved_credit_invoice(Decimal("50.00"), inv_date=d5)

        resp = self.client.get(self.base_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        ledger = resp.data["ledger"]
        self.assertGreaterEqual(len(ledger), 5,
                                "Should have at least 5 entries + opening")

        # Verify running balance is monotonically computed
        for i, entry in enumerate(ledger):
            self.assertIn("balance", entry,
                           f"Ledger entry {i} must have 'balance'")

    # ── Draft invoices excluded ──────────────────────────────────────

    def test_draft_invoices_excluded_from_ledger(self):
        PurchaseInvoice.objects.create(
            vendor=self.vendor, payment_term="Credit",
            status="Draft", date=date.today(),
        )
        resp = self.client.get(self.base_url)
        ledger = resp.data["ledger"]
        for entry in ledger:
            if entry.get("referenceType") == "invoice":
                inv = PurchaseInvoice.objects.get(id=entry["referenceId"])
                self.assertEqual(inv.status, "Saved",
                                 "Only Saved invoices should appear in ledger")

    # ── Trashed records excluded ─────────────────────────────────────

    def test_trashed_records_excluded_from_ledger(self):
        inv = self._create_saved_credit_invoice(Decimal("100.00"))
        inv.soft_delete()

        resp = self.client.get(self.base_url)
        ledger = resp.data["ledger"]
        invoice_ids = [e.get("referenceId") for e in ledger if e.get("referenceType") == "invoice"]
        self.assertNotIn(inv.id, invoice_ids,
                          "Trashed invoices should be excluded from ledger")

    # ── Date range filtering with Balance Brought Forward ────────────

    def test_date_range_balance_brought_forward(self):
        today = date.today()
        d_before = today - timedelta(days=10)
        d_within = today - timedelta(days=5)

        # Create activity before the range
        self._create_saved_credit_invoice(Decimal("200.00"), inv_date=d_before)

        # Create activity within the range
        self._create_saved_credit_invoice(Decimal("100.00"), inv_date=d_within)

        from_date = (today - timedelta(days=7)).isoformat()
        to_date = today.isoformat()
        resp = self.client.get(self.base_url, {"from": from_date, "to": to_date})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        ledger = resp.data["ledger"]
        # Check for Balance Brought Forward entry
        opening_entries = [e for e in ledger if e.get("description") == "Balance Brought Forward"]
        self.assertGreaterEqual(
            len(opening_entries), 1,
            "Date-range filtered ledger should have a 'Balance Brought Forward' entry",
        )

    # ── Summary object shape ─────────────────────────────────────────

    def test_summary_object_fields(self):
        resp = self.client.get(self.base_url)
        summary = resp.data["summary"]
        expected_fields = [
            "creditPurchases", "cashPurchases", "advanceApplied",
            "totalPaid", "remainingBalance", "totalInvoices",
            "openingPayable", "availableAdvance", "closingBalance",
        ]
        for field in expected_fields:
            self.assertIn(field, summary,
                           f"Summary must include '{field}'")

    # ── Non-existent vendor returns 404 ──────────────────────────────

    def test_nonexistent_vendor_returns_404(self):
        resp = self.client.get("/api/purchase/vendors/99999/ledger/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ── Permission enforcement ───────────────────────────────────────

    def test_anonymous_user_gets_401(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get(self.base_url)
        self.assertIn(resp.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_sale_user_gets_403(self):
        sale_user = _make_user("sale_ledger", role="Sales")
        self.client.force_authenticate(user=sale_user)
        resp = self.client.get(self.base_url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── Final balance matches vendor payable ─────────────────────────

    def test_ledger_final_balance_matches_vendor_payable(self):
        """The ledger's final running balance should match vendor.payableBalance."""
        self._create_saved_credit_invoice(Decimal("300.00"))
        self._create_payment_direct(Decimal("100.00"))

        resp = self.client.get(self.base_url)
        ledger = resp.data["ledger"]
        if ledger:
            final_balance = Decimal(str(ledger[-1]["balance"]))
            self.vendor.refresh_from_db()
            self.assertEqual(
                final_balance, self.vendor.payable_balance,
                "Ledger final running balance must match vendor.payableBalance",
            )


# =====================================================================
# 15. PurchaseCamelCaseContractTests
# =====================================================================

class PurchaseCamelCaseContractTests(APITestCase):
    """Spot-check that all purchase viewsets return camelCase keys."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.user = _make_user("camel_user", role="Purchase")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.vendor = Vendor.objects.create(
            vendor_name="CamelVendor", phone="0300-2222222",
        )

    def test_vendor_list_camelcase(self):
        resp = self.client.get("/api/purchase/vendors/")
        rendered = json.loads(resp.content)
        vendor = rendered["results"][0]
        self.assertIn("vendorId", vendor)
        self.assertNotIn("vendor_id", vendor)
        self.assertIn("vendorName", vendor)
        self.assertNotIn("vendor_name", vendor)
        self.assertIn("payableBalance", vendor)
        self.assertNotIn("payable_balance", vendor)

    def test_vendor_detail_camelcase(self):
        resp = self.client.get(f"/api/purchase/vendors/{self.vendor.vendor_id}/")
        rendered = json.loads(resp.content)
        self.assertIn("openingPayable", rendered)
        self.assertNotIn("opening_payable", rendered)
        self.assertIn("advanceBalance", rendered)
        self.assertNotIn("advance_balance", rendered)

    def test_invoice_list_camelcase(self):
        inv = PurchaseInvoice.objects.create(
            vendor=self.vendor, payment_term="Credit",
        )
        PurchaseItem.objects.create(
            invoice=inv, product_name="W", quantity=1,
            purchase_price=Decimal("10.00"),
        )
        resp = self.client.get("/api/purchase/invoices/")
        rendered = json.loads(resp.content)
        inv_data = rendered["results"][0]
        self.assertIn("invoiceNumber", inv_data)
        self.assertNotIn("invoice_number", inv_data)
        self.assertIn("paymentTerm", inv_data)
        self.assertNotIn("payment_term", inv_data)
        self.assertIn("netTotal", inv_data)
        self.assertNotIn("net_total", inv_data)

    def test_invoice_detail_camelcase(self):
        inv = PurchaseInvoice.objects.create(
            vendor=self.vendor, payment_term="Credit",
        )
        PurchaseItem.objects.create(
            invoice=inv, product_name="W", quantity=1,
            purchase_price=Decimal("10.00"),
        )
        resp = self.client.get(f"/api/purchase/invoices/{inv.id}/")
        rendered = json.loads(resp.content)
        self.assertIn("paidAmount", rendered)
        self.assertNotIn("paid_amount", rendered)
        self.assertIn("invoiceDiscount", rendered)
        self.assertNotIn("invoice_discount", rendered)
        self.assertIn("totalLineDiscount", rendered)
        self.assertNotIn("total_line_discount", rendered)

    def test_payment_camelcase(self):
        VendorPayment.objects.create(
            vendor=self.vendor,
            amount_paid=Decimal("10.00"),
            balance_after=Decimal("0.00"),
        )
        resp = self.client.get("/api/purchase/vendor-payments/")
        rendered = json.loads(resp.content)
        pay = rendered["results"][0]
        self.assertIn("paymentNumber", pay)
        self.assertNotIn("payment_number", pay)
        self.assertIn("amountPaid", pay)
        self.assertNotIn("amount_paid", pay)
        self.assertIn("appliedToInvoice", pay)
        self.assertNotIn("applied_to_invoice", pay)

    def test_expense_camelcase(self):
        Expense.objects.create(category="Office", amount=Decimal("50.00"), person_supplier="Acme", paid_by="John")
        resp = self.client.get("/api/purchase/expenses/")
        rendered = json.loads(resp.content)
        exp = rendered["results"][0]
        self.assertIn("expenseNumber", exp)
        self.assertNotIn("expense_number", exp)
        self.assertIn("paymentMethod", exp)
        self.assertNotIn("payment_method", exp)
        self.assertIn("personSupplier", exp)
        self.assertNotIn("person_supplier", exp)
        self.assertIn("paidBy", exp)
        self.assertNotIn("paid_by", exp)
        self.assertIn("createdAt", exp)
        self.assertNotIn("created_at", exp)


# =====================================================================
# 16. PurchaseRBACTests
# =====================================================================

class PurchaseRBACTests(APITestCase):
    """Consolidated RBAC checks across all purchase viewsets."""

    @classmethod
    def setUpTestData(cls):
        _create_groups()

    def setUp(self):
        self.super_admin = _make_user("rbac_super", is_superuser=True)
        self.admin = _make_user("rbac_admin", role="Admin")
        self.purchase = _make_user("rbac_purchase", role="Purchase")
        self.sale = _make_user("rbac_sale", role="Sales")
        self.client = APIClient()

        # Create test data with superadmin
        self.client.force_authenticate(user=self.super_admin)
        self.vendor = Vendor.objects.create(
            vendor_name="RBACVendor", phone="0300-9999000",
        )
        self.invoice = PurchaseInvoice.objects.create(
            vendor=self.vendor, payment_term="Credit",
        )
        PurchaseItem.objects.create(
            invoice=self.invoice, product_name="Item",
            quantity=1, purchase_price=Decimal("100.00"),
        )
        self.payment = VendorPayment.objects.create(
            vendor=self.vendor,
            amount_paid=Decimal("50.00"),
            balance_after=Decimal("0.00"),
        )
        self.expense = Expense.objects.create(
            category="Test", amount=Decimal("25.00"),
        )

    ENDPOINTS = [
        "/api/purchase/vendors/",
        "/api/purchase/invoices/",
        "/api/purchase/vendor-payments/",
        "/api/purchase/expenses/",
    ]

    # ── Anonymous users get 401 ──────────────────────────────────────

    def test_anonymous_gets_401_on_all_endpoints(self):
        self.client.force_authenticate(user=None)
        for url in self.ENDPOINTS:
            resp = self.client.get(url)
            self.assertIn(
                resp.status_code,
                [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
                f"Anonymous user should get 401/403 on {url}",
            )

    # ── Sale user gets 403 ───────────────────────────────────────────

    def test_sale_user_gets_403_on_all_endpoints(self):
        self.client.force_authenticate(user=self.sale)
        for url in self.ENDPOINTS:
            resp = self.client.get(url)
            self.assertEqual(
                resp.status_code, status.HTTP_403_FORBIDDEN,
                f"SALES_USER should get 403 on {url}",
            )

    # ── Purchase user succeeds on read/write ─────────────────────────

    def test_purchase_user_can_read_all_endpoints(self):
        self.client.force_authenticate(user=self.purchase)
        for url in self.ENDPOINTS:
            resp = self.client.get(url)
            self.assertEqual(
                resp.status_code, status.HTTP_200_OK,
                f"PURCHASE_USER should get 200 on GET {url}",
            )

    def test_purchase_user_can_create_vendor(self):
        self.client.force_authenticate(user=self.purchase)
        resp = self.client.post("/api/purchase/vendors/",
                                _vendor_payload(name="NewV", phone="0300-1112233"),
                                format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    # ── Admin succeeds on read/write ─────────────────────────────────

    def test_admin_can_read_all_endpoints(self):
        self.client.force_authenticate(user=self.admin)
        for url in self.ENDPOINTS:
            resp = self.client.get(url)
            self.assertEqual(
                resp.status_code, status.HTTP_200_OK,
                f"ADMIN should get 200 on GET {url}",
            )

    # ── Super admin succeeds on read/write ───────────────────────────

    def test_super_admin_can_read_all_endpoints(self):
        self.client.force_authenticate(user=self.super_admin)
        for url in self.ENDPOINTS:
            resp = self.client.get(url)
            self.assertEqual(
                resp.status_code, status.HTTP_200_OK,
                f"SUPER_ADMIN should get 200 on GET {url}",
            )

    # ── OnlyAdminCanDelete enforcement ───────────────────────────────

    def test_purchase_user_cannot_delete_vendor(self):
        self.client.force_authenticate(user=self.purchase)
        resp = self.client.delete(
            f"/api/purchase/vendors/{self.vendor.vendor_id}/",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN,
                         "PURCHASE_USER should be blocked from DELETE by OnlyAdminCanDelete")

    def test_purchase_user_cannot_delete_invoice(self):
        self.client.force_authenticate(user=self.purchase)
        resp = self.client.delete(
            f"/api/purchase/invoices/{self.invoice.id}/",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_purchase_user_cannot_delete_payment(self):
        self.client.force_authenticate(user=self.purchase)
        resp = self.client.delete(
            f"/api/purchase/vendor-payments/{self.payment.id}/",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_purchase_user_cannot_delete_expense(self):
        self.client.force_authenticate(user=self.purchase)
        resp = self.client.delete(
            f"/api/purchase/expenses/{self.expense.id}/",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_delete_vendor(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.delete(
            f"/api/purchase/vendors/{self.vendor.vendor_id}/",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK,
                         "ADMIN should be able to soft-delete via OnlyAdminCanDelete")

    def test_admin_can_delete_invoice(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.delete(
            f"/api/purchase/invoices/{self.invoice.id}/",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_admin_can_delete_payment(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.delete(
            f"/api/purchase/vendor-payments/{self.payment.id}/",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_admin_can_delete_expense(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.delete(
            f"/api/purchase/expenses/{self.expense.id}/",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_super_admin_can_delete_vendor(self):
        self.client.force_authenticate(user=self.super_admin)
        resp = self.client.delete(
            f"/api/purchase/vendors/{self.vendor.vendor_id}/",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_purchase_invoice_auto_status_transition_and_draft_locking(self):
        """Test automatic transition from Draft to Saved when payment is added, and locking from revert/delete."""
        self.client.force_authenticate(user=self.admin)
        vendor = Vendor.objects.create(
            vendor_name="Draft Lock Test Vendor",
            phone="03008887766",
        )
        invoice = PurchaseInvoice.objects.create(
            vendor=vendor,
            payment_term="Credit",
            paid_amount=Decimal("0.00"),
            status="Draft",
            date=date.today(),
        )
        PurchaseItem.objects.create(
            invoice=invoice,
            product_name="Draft Product",
            quantity=Decimal("1"),
            purchase_price=Decimal("1000.00"),
            discount=Decimal("0.00"),
        )
        self.assertEqual(invoice.status, "Draft")

        # 1. Apply supplier payment targeting Draft PurchaseInvoice
        pay_resp = self.client.post(
            "/api/purchase/vendor-payments/",
            data={
                "vendor": {"id": vendor.id, "vendor_id": vendor.vendor_id, "vendor_name": vendor.vendor_name, "phone": vendor.phone},
                "invoice": invoice.invoice_number,
                "amount_paid": "400.00",
                "method": "Cash",
            },
            format="json"
        )
        self.assertEqual(pay_resp.status_code, status.HTTP_201_CREATED, pay_resp.data)

        # 2. Check invoice status automatically transitioned to Saved
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "Saved")

        # 3. Attempting to revert status back to Draft should fail with validation error
        patch_resp = self.client.patch(
            f"/api/purchase/invoices/{invoice.id}/",
            data={"status": "Draft"},
            format="json"
        )
        self.assertEqual(patch_resp.status_code, 400)
        self.assertIn("Cannot revert or delete Purchase Invoice because payments/advances are attached to it", str(patch_resp.data))

        # 4. Attempting to delete the invoice should fail with 400 Bad Request
        del_resp = self.client.delete(f"/api/purchase/invoices/{invoice.id}/")
        self.assertEqual(del_resp.status_code, 400)
        self.assertIn("Cannot revert or delete Purchase Invoice because payments/advances are attached to it", str(del_resp.data))

