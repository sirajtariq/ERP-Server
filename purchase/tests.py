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
        self.assertEqual(v.opening_credit, Decimal('0.00'))
        self.assertEqual(v.credit_balance, Decimal('0.00'))
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
            "vendor_name": "Test Vendor",
            "phone": "03001234567",
            "email": "vendor@example.com",
            "address": "123 Test St",
            "tax_number": "TAX-001",
            "opening_credit": "500.00",
            "opening_note": "Opening balance note",
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
        self.assertEqual(resp.data["vendor_id"], VENDOR_ID_START)
        self.assertEqual(resp.data["vendor_name"], "Test Vendor")
        self.assertEqual(resp.data["phone"], "03001234567")
        self.assertEqual(resp.data["email"], "vendor@example.com")
        self.assertEqual(resp.data["opening_credit"], "500.00")

    def test_vendor_id_auto_increments_via_api(self):
        """Sequential API creates get incrementing vendor_ids."""
        self._auth_as(self.purchase_user)
        r1 = self._create_vendor(vendor_name="V1", phone="111")
        r2 = self._create_vendor(vendor_name="V2", phone="222")
        self.assertEqual(r1.data["vendor_id"], 5000)
        self.assertEqual(r2.data["vendor_id"], 5001)


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
        self.assertIsNone(r1.data["phone"])

        r2 = self._create_vendor(vendor_name="No Phone 2", phone="")
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(r2.data["phone"])

    def test_whitespace_only_phone_treated_as_null(self):
        """Whitespace-only phone input should be converted to None."""
        self._auth_as(self.purchase_user)
        resp = self._create_vendor(vendor_name="Whitespace Phone", phone="   ")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(resp.data["phone"])

    def test_duplicate_non_empty_phone_returns_400(self):
        """
        Creating a vendor with a phone that already exists should return
        a clean 400 validation error, not a raw 500 IntegrityError.
        """
        self._auth_as(self.purchase_user)
        self._create_vendor(vendor_name="V1", phone="03009999999")
        resp = self._create_vendor(vendor_name="V2", phone="03009999999")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone", resp.data)


class VendorSoftDeleteTests(VendorAPITestMixin, TestCase):
    """Test soft-delete (trash), restore, and permanent-delete flows."""

    def _get_vendor_id(self):
        """Helper: create a vendor and return its vendor_id."""
        self._auth_as(self.admin_user)
        resp = self._create_vendor()
        return resp.data["vendor_id"]

    def test_destroy_soft_deletes(self):
        """DELETE on vendor moves it to trash (soft-delete), not hard delete."""
        vid = self._get_vendor_id()
        self._auth_as(self.admin_user)
        resp = self.client.delete(f"/api/purchase/vendors/{vid}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Not in default list
        list_resp = self.client.get("/api/purchase/vendors/")
        vendor_ids = [v["vendor_id"] for v in list_resp.data["results"]]
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
        vendor_ids = [v["vendor_id"] for v in resp.data["results"]]
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
        vendor_ids = [v["vendor_id"] for v in list_resp.data["results"]]
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
        vid = resp.data["vendor_id"]

        # Try delete as purchase user
        self._auth_as(self.purchase_user)
        resp = self.client.delete(f"/api/purchase/vendors/{vid}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class VendorReadOnlyFieldTests(VendorAPITestMixin, TestCase):
    """Test that read-only fields cannot be set via API."""

    def test_credit_balance_ignored_on_create(self):
        """POSTing credit_balance should be silently ignored (read-only)."""
        self._auth_as(self.purchase_user)
        resp = self._create_vendor(credit_balance="999.99")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # Should be the default 0.00, not 999.99
        self.assertEqual(resp.data["credit_balance"], "0.00")

    def test_advance_balance_ignored_on_create(self):
        """POSTing advance_balance should be silently ignored (read-only)."""
        self._auth_as(self.purchase_user)
        resp = self._create_vendor(advance_balance="555.00")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["advance_balance"], "0.00")

    def test_vendor_id_ignored_on_create(self):
        """POSTing vendor_id should be silently ignored (auto-generated)."""
        self._auth_as(self.purchase_user)
        resp = self._create_vendor(vendor_id=9999)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["vendor_id"], VENDOR_ID_START)
