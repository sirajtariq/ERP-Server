"""
Unit test suite verifying fixes for all 4 reported issues:
1. Customer ledger payment non-double counting.
2. Vendor opening_payable & Customer opening_credit PATCH difference updates.
3. Vendor & Customer general payment FIFO invoice clearing.
4. Non-negative floor for payable/credit balances with overflow into advance balance.
"""

from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase
from rest_framework.test import APIClient

from erp_backend.models import User
from sales.models import Customer, SalesInvoice, SalesItem
from sales.serializers import CustomerSerializer, PaymentReceivedSerializer
from purchase.models import Vendor, PurchaseInvoice, PurchaseItem
from purchase.serializers import VendorSerializer, VendorPaymentSerializer


class ReportedIssuesFixesTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username='testadmin',
            email='admin@test.com',
            password='password123',
        )
        self.client.force_authenticate(user=self.user)

    def test_01_sales_ledger_does_not_double_count_payment(self):
        """Issue 01: Customer ledger should only count PaymentReceived once."""
        customer = Customer.objects.create(
            customer_name="Ledger Test Customer",
            customer_type="permanent",
            phone="03009998877",
            credit_balance=Decimal("1000.00"),
        )
        invoice = SalesInvoice.objects.create(
            customer=customer,
            payment_term="Credit",
            paid_amount=Decimal("0.00"),
            status="Saved",
            date=date.today(),
        )
        SalesItem.objects.create(
            invoice=invoice,
            item_name="Item 1",
            quantity=Decimal("10.00"),
            rate=Decimal("100.00"),
            discount=Decimal("0.00"),
        )

        # Apply payment via serializer (which creates PaymentReceived)
        pay_serializer = PaymentReceivedSerializer(data={
            "customer": customer.id,
            "amount_received": "500.00",
            "method": "Cash",
        })
        self.assertTrue(pay_serializer.is_valid(), pay_serializer.errors)
        pay_serializer.save()

        # Fetch customer ledger
        url = f"/api/sales/customers/{customer.customer_id}/ledger/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        data = response.data
        ledger_rows = data.get("ledger", [])

        # Count payment rows
        payment_rows = [r for r in ledger_rows if r.get("referenceType") == "payment"]
        invoice_payment_rows = [r for r in ledger_rows if r.get("voucher", "").startswith("PAY-")]

        self.assertEqual(len(payment_rows), 1, "Should have exactly 1 payment ledger row")
        self.assertEqual(len(invoice_payment_rows), 0, "Should NOT have duplicate PAY- invoice payment rows")
        self.assertEqual(Decimal(str(data["summary"]["totalCollected"])), Decimal("500.00"))

    def test_02_vendor_patch_opening_payable_updates_balance_by_diff(self):
        """Issue 02: Updating Vendor opening_payable via PATCH updates payable_balance by diff."""
        v_serializer = VendorSerializer(data={
            "vendor_name": "Test Vendor",
            "phone": "03001234567",
            "opening_payable": "500.00",
        })
        self.assertTrue(v_serializer.is_valid(), v_serializer.errors)
        vendor = v_serializer.save()

        self.assertEqual(vendor.payable_balance, Decimal("500.00"))

        # PATCH to edit opening_payable to 1000.00
        patch_serializer = VendorSerializer(instance=vendor, data={"opening_payable": "1000.00"}, partial=True)
        self.assertTrue(patch_serializer.is_valid(), patch_serializer.errors)
        patch_serializer.save()

        vendor.refresh_from_db()
        self.assertEqual(vendor.opening_payable, Decimal("1000.00"))
        self.assertEqual(vendor.payable_balance, Decimal("1000.00"))

    def test_02_customer_patch_opening_credit_updates_balance_by_diff(self):
        """Issue 02: Updating Customer openingCredit via PATCH updates credit_balance by diff."""
        c_serializer = CustomerSerializer(data={
            "customerName": "Test Customer",
            "customerType": "permanent",
            "Phone": "03007654321",
            "openingCredit": "300.00",
        })
        self.assertTrue(c_serializer.is_valid(), c_serializer.errors)
        customer = c_serializer.save()

        self.assertEqual(customer.credit_balance, Decimal("300.00"))

        # PATCH to edit openingCredit to 700.00
        patch_serializer = CustomerSerializer(instance=customer, data={"openingCredit": "700.00"}, partial=True)
        self.assertTrue(patch_serializer.is_valid(), patch_serializer.errors)
        patch_serializer.save()

        customer.refresh_from_db()
        self.assertEqual(customer.opening_credit, Decimal("700.00"))
        self.assertEqual(customer.credit_balance, Decimal("700.00"))

    def test_03_vendor_general_payment_clears_invoices_fifo(self):
        """Issue 03: Vendor general payment clears pending invoices in FIFO order."""
        vendor = Vendor.objects.create(
            vendor_name="FIFO Vendor",
            payable_balance=Decimal("800.00"),
        )
        inv1 = PurchaseInvoice.objects.create(
            vendor=vendor,
            payment_term="Credit",
            paid_amount=Decimal("0.00"),
            status="Saved",
            date=date.today() - timedelta(days=5),
        )
        PurchaseItem.objects.create(
            invoice=inv1,
            product_name="Product A",
            quantity=Decimal("3"),
            purchase_price=Decimal("100.00"),
            discount=Decimal("0.00"),
        )

        inv2 = PurchaseInvoice.objects.create(
            vendor=vendor,
            payment_term="Credit",
            paid_amount=Decimal("0.00"),
            status="Saved",
            date=date.today(),
        )
        PurchaseItem.objects.create(
            invoice=inv2,
            product_name="Product B",
            quantity=Decimal("5"),
            purchase_price=Decimal("100.00"),
            discount=Decimal("0.00"),
        )

        # General payment of 450
        vp_serializer = VendorPaymentSerializer(data={
            "vendor": {
                "id": vendor.id,
                "vendor_name": vendor.vendor_name,
                "phone": vendor.phone or "",
            },
            "amount_paid": "450.00",
            "method": "Cash",
        })
        self.assertTrue(vp_serializer.is_valid(), vp_serializer.errors)
        vp_serializer.save()

        inv1.refresh_from_db()
        inv2.refresh_from_db()
        vendor.refresh_from_db()

        # Inv1 (300 due) should be fully cleared
        self.assertEqual(inv1.paid_amount, Decimal("300.00"))
        # Inv2 (500 due) should have 150 paid
        self.assertEqual(inv2.paid_amount, Decimal("150.00"))
        # Remaining vendor payable balance should be 800 - 450 = 350
        self.assertEqual(vendor.payable_balance, Decimal("350.00"))
        self.assertEqual(vendor.advance_balance, Decimal("0.00"))

    def test_03_customer_general_payment_clears_invoices_fifo_and_overflows_advance(self):
        """Issue 03 & 04: Customer general payment clears invoices FIFO and overflows into advance."""
        customer = Customer.objects.create(
            customer_name="FIFO Customer",
            customer_type="permanent",
            phone="03001122334",
            credit_balance=Decimal("600.00"),
        )
        inv1 = SalesInvoice.objects.create(
            customer=customer,
            payment_term="Credit",
            paid_amount=Decimal("0.00"),
            status="Saved",
            date=date.today() - timedelta(days=3),
        )
        SalesItem.objects.create(
            invoice=inv1,
            item_name="Item A",
            quantity=Decimal("2"),
            rate=Decimal("100.00"),
            discount=Decimal("0.00"),
        )

        inv2 = SalesInvoice.objects.create(
            customer=customer,
            payment_term="Credit",
            paid_amount=Decimal("0.00"),
            status="Saved",
            date=date.today(),
        )
        SalesItem.objects.create(
            invoice=inv2,
            item_name="Item B",
            quantity=Decimal("4"),
            rate=Decimal("100.00"),
            discount=Decimal("0.00"),
        )

        # General payment of 750 (exceeds total invoice & credit balance of 600)
        pay_serializer = PaymentReceivedSerializer(data={
            "customer": customer.id,
            "amount_received": "750.00",
            "method": "Cash",
        })
        self.assertTrue(pay_serializer.is_valid(), pay_serializer.errors)
        pay_serializer.save()

        inv1.refresh_from_db()
        inv2.refresh_from_db()
        customer.refresh_from_db()

        self.assertEqual(inv1.paid_amount, Decimal("200.00"))
        self.assertEqual(inv2.paid_amount, Decimal("400.00"))
        # Credit balance reaches 0.00 (never negative)
        self.assertEqual(customer.credit_balance, Decimal("0.00"))
        # Excess (150.00) goes to advance balance
        self.assertEqual(customer.advance_balance, Decimal("150.00"))

    def test_customer_ledger_advance_formulas_and_no_duplicate_voucher(self):
        """Verify advance double-counting fix and correct availableAdvance formula."""
        customer = Customer.objects.create(
            customer_name="Advance Test Customer",
            customer_type="permanent",
            phone="03009988776",
        )
        # Create payment of 80,480
        pay_serializer = PaymentReceivedSerializer(data={
            "customer": customer.id,
            "amount_received": "80480.00",
            "method": "Bank Transfer",
        })
        self.assertTrue(pay_serializer.is_valid(), pay_serializer.errors)
        pay_serializer.save()

        # Create invoice with advance applied of 7,680
        invoice = SalesInvoice.objects.create(
            customer=customer,
            payment_term="Credit",
            paid_amount=Decimal("0.00"),
            advance_applied=Decimal("7680.00"),
            status="Saved",
            date=date.today(),
        )
        SalesItem.objects.create(
            invoice=invoice,
            item_name="Item Advance Test",
            quantity=Decimal("1"),
            rate=Decimal("7680.00"),
            discount=Decimal("0.00"),
        )

        url = f"/api/sales/customers/{customer.customer_id}/ledger/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        data = response.data
        ledger_rows = data.get("ledger", [])

        # 1. Verify NO ADV- voucher entry exists
        adv_rows = [r for r in ledger_rows if str(r.get("voucher", "")).startswith("ADV-")]
        self.assertEqual(len(adv_rows), 0, "No duplicate ADV- voucher row should exist in ledger")

        # 2. Verify summary.availableAdvance formula: max(0, totalCollected - (openingBalance + totalPurchases))
        # 80,480 - (0 + 7,680) = 72,800.0
        self.assertEqual(Decimal(str(data["summary"]["availableAdvance"])), Decimal("72800.00"))
        self.assertEqual(Decimal(str(data["summary"]["remainingBalance"])), Decimal("0.00"))

        # 3. Verify finalPaymentDetails.availableAdvance
        self.assertEqual(Decimal(str(data["finalPaymentDetails"]["availableAdvance"])), Decimal("72800.00"))
        self.assertEqual(Decimal(str(data["finalPaymentDetails"]["remainingBalance"])), Decimal("0.00"))

        # 4. Verify Customer API detail response returns updated advanceBalance (72,800.00)
        cust_url = f"/api/sales/customers/{customer.customer_id}/"
        cust_resp = self.client.get(cust_url)
        self.assertEqual(cust_resp.status_code, 200)
        self.assertEqual(Decimal(str(cust_resp.data["advanceBalance"])), Decimal("72800.00"))
        self.assertEqual(Decimal(str(cust_resp.data["creditBalance"])), Decimal("0.00"))

    def test_sales_invoice_auto_status_transition_and_draft_locking(self):
        """Test automatic transition from Draft to Saved when payment/receipt is added, and locking from revert/delete."""
        customer = Customer.objects.create(
            customer_name="Draft Lock Test Customer",
            customer_type="permanent",
            phone="03007776655",
        )
        invoice = SalesInvoice.objects.create(
            customer=customer,
            payment_term="Credit",
            paid_amount=Decimal("0.00"),
            status="Draft",
            date=date.today(),
        )
        SalesItem.objects.create(
            invoice=invoice,
            item_name="Draft Item",
            quantity=Decimal("1"),
            rate=Decimal("500.00"),
            discount=Decimal("0.00"),
        )
        self.assertEqual(invoice.status, "Draft")

        # 1. Apply payment targeting the Draft invoice
        pay_serializer = PaymentReceivedSerializer(data={
            "customer": customer.id,
            "invoice": invoice.id,
            "amount_received": "200.00",
            "method": "Cash",
        })
        self.assertTrue(pay_serializer.is_valid(), pay_serializer.errors)
        pay_serializer.save()

        # 2. Check invoice status automatically transitioned to Saved
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "Saved")

        # 3. Attempting to revert status back to Draft should fail with validation error
        patch_resp = self.client.patch(
            f"/api/sales/invoices/{invoice.id}/",
            data={"invoiceStatus": "Draft"},
            format="json"
        )
        self.assertEqual(patch_resp.status_code, 400)
        self.assertIn("Cannot revert or delete Sales Invoice because payments/advances are attached to it", str(patch_resp.data))

        # 4. Attempting to delete the invoice should fail with 400 Bad Request
        del_resp = self.client.delete(f"/api/sales/invoices/{invoice.id}/")
        self.assertEqual(del_resp.status_code, 400)
        self.assertIn("Cannot revert or delete Sales Invoice because payments/advances are attached to it", str(del_resp.data))



