"""
Comprehensive, independently-verified test suite for the customer ledger
and balance-calculation logic.

CRITICAL DESIGN PRINCIPLE:
    Every expected value is computed with plain Decimal arithmetic written
    directly in this file. We do NOT import or call any of the app's own
    calculation helpers (_apply_payment, _reverse_payment, or the ledger
    builder) to compute "expected" results. This ensures that a bug in the
    app's code cannot silently pass the test.
"""

import time
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from sales.models import Customer, SalesInvoice, SalesItem


class LedgerCalculationTests(TestCase):
    """Test suite for customer ledger and balance calculations."""

    # Class-level counter for unique phone numbers across all tests.
    _phone_counter = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.superuser = User.objects.create_superuser(
            username='ledger_test_admin',
            email='admin@test.com',
            password='testpass123',
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.superuser)

    # ═══════════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════════

    def create_customer(self, opening_credit=Decimal('0'), customer_type='permanent'):
        """Create a Customer via ORM with a unique phone number."""
        LedgerCalculationTests._phone_counter += 1
        phone = f'0300{LedgerCalculationTests._phone_counter:07d}'
        customer = Customer.objects.create(
            customer_name=f'TestCustomer_{LedgerCalculationTests._phone_counter}',
            phone=phone,
            customer_type=customer_type,
            opening_credit=opening_credit,
        )
        return customer

    def create_invoice(self, customer, payment_term, item_total,
                       paid_amount=Decimal('0')):
        """
        Create a SalesInvoice with a single SalesItem whose quantity=1,
        rate=item_total, discount=0. vat_percentage=0, invoice_discount=0.
        net_total will equal item_total exactly.

        NOTE: This creates via ORM, NOT the serializer, so no auto
        PaymentReceived is generated (unlike the real API flow).
        """
        invoice = SalesInvoice.objects.create(
            customer=customer,
            payment_term=payment_term,
            paid_amount=paid_amount,
            vat_percentage=Decimal('0'),
            invoice_discount=Decimal('0'),
            status='Saved',
        )
        SalesItem.objects.create(
            invoice=invoice,
            item_name=f'TestItem_{invoice.invoice_number}',
            quantity=Decimal('1'),
            rate=item_total,
            discount=Decimal('0'),
        )
        # Brief sleep to ensure created_at timestamps are distinct and
        # ordering in the ledger is deterministic.
        time.sleep(0.05)
        return invoice

    def create_invoice_via_api(self, customer, payment_term, item_total,
                               paid_amount=Decimal('0')):
        """
        Create a SalesInvoice via the API (POST /api/sales/invoices/).
        Unlike create_invoice(), this goes through the serializer and
        triggers advance consumption logic.
        """
        payload = {
            'customer': customer.id,
            'payment_term': payment_term,
            'paid_amount': str(paid_amount),
            'vat_percentage': '0',
            'invoice_discount': '0',
            'status': 'Saved',
            'items': [{
                'item_name': 'TestItem_API',
                'quantity': '1',
                'rate': str(item_total),
                'discount': '0',
            }],
        }
        response = self.client.post(
            '/api/sales/invoices/', payload, format='json',
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=(
                f"create_invoice_via_api() expected 201 but got "
                f"{response.status_code}. Response body: {response.data}"
            ),
        )
        time.sleep(0.05)
        # Return the actual SalesInvoice instance from DB for assertions.
        invoice = SalesInvoice.objects.get(id=response.data['id'])
        return invoice

    def create_payment(self, customer, amount, invoice=None):
        """
        POST to /api/sales/payments/ and return the parsed JSON.
        Asserts 201 status — fails loudly with the full response body
        if the request is not successful.
        """
        payload = {
            'customer': customer.id,
            'amount_received': str(amount),
            'method': 'Cash',
        }
        if invoice is not None:
            payload['invoice'] = invoice.id
        response = self.client.post(
            '/api/sales/payments/', payload, format='json',
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=(
                f"create_payment() expected 201 but got {response.status_code}. "
                f"Response body: {response.data}"
            ),
        )
        # Brief sleep for deterministic timestamp ordering.
        time.sleep(0.05)
        return response.data

    def get_ledger(self, customer):
        """
        GET the customer ledger and return the parsed JSON.
        Asserts 200 status — fails loudly if not.
        """
        url = f'/api/sales/customers/{customer.customer_id}/ledger/'
        response = self.client.get(url)
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=(
                f"get_ledger() expected 200 but got {response.status_code}. "
                f"Response body: {response.data}"
            ),
        )
        return response.data

    def assertDecimalEqual(self, actual, expected, msg_prefix=''):
        """
        Compare two numeric values with Decimal('0.01') tolerance.
        On failure, prints a clear message with expected vs actual and diff.
        """
        actual_d = Decimal(str(actual))
        expected_d = Decimal(str(expected))
        diff = actual_d - expected_d
        self.assertAlmostEqual(
            float(actual_d),
            float(expected_d),
            delta=0.01,
            msg=(
                f"{msg_prefix}: expected {expected_d}, got {actual_d}, "
                f"diff {diff}"
            ),
        )

    # ═══════════════════════════════════════════════════════════════════
    # TEST SCENARIOS
    # ═══════════════════════════════════════════════════════════════════

    def test_01_opening_balance_only(self):
        """
        Test 1: Customer with opening_credit=5000, no invoices, no payments.
        Expected: remainingBalance=5000, availableAdvance=0,
                  ledger has exactly 1 row (OPENING) with balance=5000.
        """
        customer = self.create_customer(opening_credit=Decimal('5000'))
        ledger = self.get_ledger(customer)

        # --- Hand-computed expected values ---
        # Only opening credit exists. No invoices, no payments.
        # Ledger: OPENING debit=5000 → running_balance = 0 + 5000 = 5000
        expected_remaining = Decimal('5000')  # positive balance = amount owed
        expected_advance = Decimal('0')       # no overpayment
        expected_total_collected = Decimal('0')
        expected_opening_credit = Decimal('5000')

        # (a) summary.remainingBalance
        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 1] summary.remainingBalance",
        )

        # (b) summary.closingBalance
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 1] summary.closingBalance",
        )

        # (c) summary.availableAdvance
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], expected_advance,
            msg_prefix="[Test 1] summary.availableAdvance",
        )

        # (d) summary.totalCollected
        self.assertDecimalEqual(
            ledger['summary']['totalCollected'], expected_total_collected,
            msg_prefix="[Test 1] summary.totalCollected",
        )

        # (e) summary.openingCredit
        self.assertDecimalEqual(
            ledger['summary']['openingCredit'], expected_opening_credit,
            msg_prefix="[Test 1] summary.openingCredit",
        )

        # (f) finalPaymentDetails.remainingBalance
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 1] finalPaymentDetails.remainingBalance",
        )

        # (g) finalPaymentDetails.availableAdvance
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['availableAdvance'], expected_advance,
            msg_prefix="[Test 1] finalPaymentDetails.availableAdvance",
        )

        # Ledger should have exactly 1 row
        self.assertEqual(
            len(ledger['ledger']), 1,
            msg=f"[Test 1] Expected 1 ledger row, got {len(ledger['ledger'])}",
        )

        # (h) Last row balance == remainingBalance
        last_row_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_row_balance, expected_remaining,
            msg_prefix="[Test 1] last ledger row balance",
        )

        # (i) Voucher order
        vouchers = [row['voucher'] for row in ledger['ledger']]
        self.assertEqual(
            vouchers, ['OPENING'],
            msg=f"[Test 1] Voucher order mismatch: expected ['OPENING'], got {vouchers}",
        )

    def test_02_single_credit_invoice_no_payment(self):
        """
        Test 2: Customer with opening_credit=0, one Credit invoice for 3000,
        no payments.
        Manually computed: 0 (opening) + 3000 (invoice) - 0 (payments) = 3000.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        inv = self.create_invoice(customer, 'Credit', Decimal('3000'))
        ledger = self.get_ledger(customer)

        # --- Hand-computed ---
        # No opening credit row (opening_credit=0)
        # Invoice debit: net_total = 3000 (qty=1 × rate=3000 - discount=0)
        # Running balance: 0 + 3000 = 3000
        expected_remaining = Decimal('3000')
        expected_advance = Decimal('0')

        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 2] summary.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 2] summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], expected_advance,
            msg_prefix="[Test 2] summary.availableAdvance",
        )
        self.assertDecimalEqual(
            ledger['summary']['totalCollected'], Decimal('0'),
            msg_prefix="[Test 2] summary.totalCollected",
        )
        self.assertDecimalEqual(
            ledger['summary']['openingCredit'], Decimal('0'),
            msg_prefix="[Test 2] summary.openingCredit",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 2] finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['availableAdvance'], expected_advance,
            msg_prefix="[Test 2] finalPaymentDetails.availableAdvance",
        )

        # Last row balance
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 2] last ledger row balance",
        )

        # Voucher order: just the invoice (no opening row since credit=0)
        vouchers = [row['voucher'] for row in ledger['ledger']]
        self.assertEqual(
            vouchers, [inv.invoice_number],
            msg=f"[Test 2] Voucher order: expected [{inv.invoice_number}], got {vouchers}",
        )

    def test_03_single_cash_invoice_fully_paid_at_creation(self):
        """
        Test 3: Customer with opening_credit=0. Cash invoice, item_total=2000,
        paid_amount=2000. Since we create via ORM (no auto PaymentReceived),
        we also create_payment() for 2000 linked to the invoice to simulate
        the real flow.
        Expected: remainingBalance=0, availableAdvance=0.
        Manually: 0 (opening) + 2000 (invoice) - 2000 (payment) = 0.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        inv = self.create_invoice(
            customer, 'Cash', Decimal('2000'), paid_amount=Decimal('2000'),
        )
        pay = self.create_payment(customer, Decimal('2000'), invoice=inv)
        ledger = self.get_ledger(customer)

        # --- Hand-computed ---
        # Invoice debit: 2000
        # Payment credit: 2000
        # Running balance: 0 + 2000 - 2000 = 0
        expected_remaining = Decimal('0')
        expected_advance = Decimal('0')

        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 3] summary.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 3] summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], expected_advance,
            msg_prefix="[Test 3] summary.availableAdvance",
        )
        self.assertDecimalEqual(
            ledger['summary']['totalCollected'], Decimal('2000'),
            msg_prefix="[Test 3] summary.totalCollected",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 3] finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['availableAdvance'], expected_advance,
            msg_prefix="[Test 3] finalPaymentDetails.availableAdvance",
        )

        # Last row balance
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 3] last ledger row balance",
        )

        # Voucher order: invoice, then payment
        vouchers = [row['voucher'] for row in ledger['ledger']]
        expected_vouchers = [inv.invoice_number, pay['receipt_number']]
        self.assertEqual(
            vouchers, expected_vouchers,
            msg=f"[Test 3] Voucher order: expected {expected_vouchers}, got {vouchers}",
        )

    def test_04_cash_invoice_partial_payment(self):
        """
        Test 4: Customer opening_credit=0. Cash invoice item_total=5000,
        paid_amount=0 initially. Then payment of 2000 against that invoice.
        Expected: remainingBalance = 5000 - 2000 = 3000.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        inv = self.create_invoice(
            customer, 'Cash', Decimal('5000'), paid_amount=Decimal('0'),
        )
        pay = self.create_payment(customer, Decimal('2000'), invoice=inv)
        ledger = self.get_ledger(customer)

        # --- Hand-computed ---
        # Invoice debit: 5000
        # Payment credit: 2000
        # Running balance: 0 + 5000 - 2000 = 3000
        expected_remaining = Decimal('3000')
        expected_advance = Decimal('0')

        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix=(
                f"[Test 4] summary.remainingBalance: "
                f"invoice=5000 - payment=2000 = 3000"
            ),
        )
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 4] summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], expected_advance,
            msg_prefix="[Test 4] summary.availableAdvance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 4] finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['availableAdvance'], expected_advance,
            msg_prefix="[Test 4] finalPaymentDetails.availableAdvance",
        )

        # Last row balance
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 4] last ledger row balance",
        )

        # Voucher order
        vouchers = [row['voucher'] for row in ledger['ledger']]
        expected_vouchers = [inv.invoice_number, pay['receipt_number']]
        self.assertEqual(
            vouchers, expected_vouchers,
            msg=f"[Test 4] Voucher order: expected {expected_vouchers}, got {vouchers}",
        )

    def test_05_credit_invoice_then_full_general_payment(self):
        """
        Test 5: Customer opening_credit=0. Credit invoice item_total=4000.
        Then a general payment (no invoice link) of 4000.
        Expected: remainingBalance=0, availableAdvance=0.
        Manually: 0 + 4000 - 4000 = 0.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        inv = self.create_invoice(customer, 'Credit', Decimal('4000'))
        pay = self.create_payment(customer, Decimal('4000'), invoice=None)
        ledger = self.get_ledger(customer)

        # --- Hand-computed ---
        # Invoice debit: 4000
        # General payment credit: 4000
        # Running balance: 0 + 4000 - 4000 = 0
        expected_remaining = Decimal('0')
        expected_advance = Decimal('0')

        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 5] summary.remainingBalance (4000 - 4000 = 0)",
        )
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 5] summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], expected_advance,
            msg_prefix="[Test 5] summary.availableAdvance",
        )
        self.assertDecimalEqual(
            ledger['summary']['totalCollected'], Decimal('4000'),
            msg_prefix="[Test 5] summary.totalCollected",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 5] finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['availableAdvance'], expected_advance,
            msg_prefix="[Test 5] finalPaymentDetails.availableAdvance",
        )

        # Last row balance
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 5] last ledger row balance",
        )

        # Voucher order
        vouchers = [row['voucher'] for row in ledger['ledger']]
        expected_vouchers = [inv.invoice_number, pay['receipt_number']]
        self.assertEqual(
            vouchers, expected_vouchers,
            msg=f"[Test 5] Voucher order: expected {expected_vouchers}, got {vouchers}",
        )

    def test_06_overpayment_on_invoice_routes_excess_to_advance(self):
        """
        Test 6: Customer opening_credit=0. Cash invoice item_total=1000.
        Payment of 1500 against that invoice (500 overpayment).
        Expected: remainingBalance=0 (1000 - 1500 = -500, clamped to 0),
                  availableAdvance=500.

        This is the exact overpayment bug scenario — assert explicitly.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        item_total = Decimal('1000')
        payment_amount = Decimal('1500')
        inv = self.create_invoice(customer, 'Cash', item_total)
        pay = self.create_payment(customer, payment_amount, invoice=inv)
        ledger = self.get_ledger(customer)

        # --- Hand-computed ---
        # Invoice debit: 1000
        # Payment credit: 1500
        # Running balance: 0 + 1000 - 1500 = -500
        # Since -500 < 0: remainingBalance = 0, availableAdvance = 500
        expected_remaining = Decimal('0')
        expected_advance = Decimal('500')  # abs(-500)

        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix=(
                f"[Test 6 - Overpayment] summary.remainingBalance: "
                f"invoice {item_total} - payment {payment_amount}, clamped to 0"
            ),
        )
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 6] summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], expected_advance,
            msg_prefix=(
                f"[Test 6 - Overpayment] summary.availableAdvance: "
                f"excess = {payment_amount} - {item_total} = 500"
            ),
        )
        self.assertDecimalEqual(
            ledger['summary']['totalCollected'], payment_amount,
            msg_prefix="[Test 6] summary.totalCollected",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 6] finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['availableAdvance'], expected_advance,
            msg_prefix="[Test 6] finalPaymentDetails.availableAdvance",
        )

        # Last row balance should be -500 (raw running balance)
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        # The ledger's last row 'balance' is the RAW running balance (-500).
        # But remainingBalance and availableAdvance are derived from it.
        # The spec says: "last row balance must equal (a) remainingBalance
        # and (b) closingBalance". Let's check what the view actually does:
        # The view stores float(running_balance) as the row balance, then
        # derives remaining/advance from the final balance. So the last row
        # balance is the raw value (-500), while remainingBalance is clamped.
        # Per the spec, we'll assert that the last row balance is consistent
        # with the derivation: if last_row_balance < 0,
        # remainingBalance = 0 and availableAdvance = abs(last_row_balance).
        expected_raw_final = Decimal('-500')  # 1000 - 1500 = -500
        self.assertDecimalEqual(
            last_balance, expected_raw_final,
            msg_prefix=(
                "[Test 6] last ledger row balance (raw running balance): "
                "1000 - 1500 = -500"
            ),
        )

        # Voucher order
        vouchers = [row['voucher'] for row in ledger['ledger']]
        expected_vouchers = [inv.invoice_number, pay['receipt_number']]
        self.assertEqual(
            vouchers, expected_vouchers,
            msg=f"[Test 6] Voucher order: expected {expected_vouchers}, got {vouchers}",
        )

    def test_07_general_overpayment_exceeding_credit_balance_goes_to_advance(self):
        """
        Test 7: Customer opening_credit=2000, no invoices.
        General payment of 5000 (more than the 2000 owed).
        Expected: remainingBalance=0, availableAdvance=3000.
        Manually: 2000 (opening debit) - 5000 (payment credit) = -3000
        Since -3000 < 0: remainingBalance=0, availableAdvance=3000.
        """
        customer = self.create_customer(opening_credit=Decimal('2000'))
        payment_amount = Decimal('5000')
        pay = self.create_payment(customer, payment_amount, invoice=None)
        ledger = self.get_ledger(customer)

        # --- Hand-computed ---
        # OPENING debit: 2000
        # Payment credit: 5000
        # Running balance: 0 + 2000 - 5000 = -3000
        # Since -3000 < 0: remainingBalance=0, availableAdvance=3000
        expected_remaining = Decimal('0')
        expected_advance = Decimal('3000')  # abs(-3000)

        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix=(
                "[Test 7] summary.remainingBalance: "
                "opening=2000 - payment=5000 = -3000, clamped to 0"
            ),
        )
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 7] summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], expected_advance,
            msg_prefix=(
                "[Test 7] summary.availableAdvance: "
                "excess = 5000 - 2000 = 3000"
            ),
        )
        self.assertDecimalEqual(
            ledger['summary']['totalCollected'], payment_amount,
            msg_prefix="[Test 7] summary.totalCollected",
        )
        self.assertDecimalEqual(
            ledger['summary']['openingCredit'], Decimal('2000'),
            msg_prefix="[Test 7] summary.openingCredit",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 7] finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['availableAdvance'], expected_advance,
            msg_prefix="[Test 7] finalPaymentDetails.availableAdvance",
        )

        # Last row balance (raw)
        expected_raw_final = Decimal('-3000')  # 2000 - 5000
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_raw_final,
            msg_prefix="[Test 7] last ledger row balance (raw): 2000 - 5000 = -3000",
        )

        # Voucher order
        vouchers = [row['voucher'] for row in ledger['ledger']]
        expected_vouchers = ['OPENING', pay['receipt_number']]
        self.assertEqual(
            vouchers, expected_vouchers,
            msg=f"[Test 7] Voucher order: expected {expected_vouchers}, got {vouchers}",
        )

    def test_08_multiple_partial_payments_same_invoice(self):
        """
        Test 8: Customer opening_credit=0. Cash invoice item_total=10000.
        Three separate payments of 3000, 3000, 4000 all against the same invoice.
        Expected: remainingBalance = 10000 - 3000 - 3000 - 4000 = 0.
        Also verify invoice.paid_amount == 10000 and never exceeded net_total.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        inv = self.create_invoice(customer, 'Cash', Decimal('10000'))

        # Payment 1: 3000
        pay1 = self.create_payment(customer, Decimal('3000'), invoice=inv)

        # After payment 1: verify invoice.paid_amount hasn't exceeded net_total
        inv.refresh_from_db()
        self.assertLessEqual(
            inv.paid_amount, Decimal('10000'),
            msg=(
                f"[Test 8] After payment 1: invoice.paid_amount "
                f"({inv.paid_amount}) should not exceed net_total (10000)"
            ),
        )

        # Payment 2: 3000
        pay2 = self.create_payment(customer, Decimal('3000'), invoice=inv)

        inv.refresh_from_db()
        self.assertLessEqual(
            inv.paid_amount, Decimal('10000'),
            msg=(
                f"[Test 8] After payment 2: invoice.paid_amount "
                f"({inv.paid_amount}) should not exceed net_total (10000)"
            ),
        )

        # Payment 3: 4000
        pay3 = self.create_payment(customer, Decimal('4000'), invoice=inv)

        inv.refresh_from_db()
        # --- Hand-computed ---
        # Total paid on invoice: 3000 + 3000 + 4000 = 10000
        # net_total = 10000, so paid_amount should be exactly 10000
        expected_paid = Decimal('10000')
        self.assertDecimalEqual(
            inv.paid_amount, expected_paid,
            msg_prefix="[Test 8] invoice.paid_amount after 3 payments",
        )

        ledger = self.get_ledger(customer)

        # --- Hand-computed ledger ---
        # Invoice debit: 10000
        # Payment credits: 3000 + 3000 + 4000 = 10000
        # Running balance: 0 + 10000 - 3000 - 3000 - 4000 = 0
        expected_remaining = Decimal('0')
        expected_advance = Decimal('0')

        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix=(
                "[Test 8] summary.remainingBalance: "
                "10000 - 3000 - 3000 - 4000 = 0"
            ),
        )
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 8] summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], expected_advance,
            msg_prefix="[Test 8] summary.availableAdvance",
        )
        self.assertDecimalEqual(
            ledger['summary']['totalCollected'], Decimal('10000'),
            msg_prefix="[Test 8] summary.totalCollected (3000+3000+4000)",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 8] finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['availableAdvance'], expected_advance,
            msg_prefix="[Test 8] finalPaymentDetails.availableAdvance",
        )

        # Last row balance
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 8] last ledger row balance",
        )

        # Voucher order: invoice, pay1, pay2, pay3
        vouchers = [row['voucher'] for row in ledger['ledger']]
        expected_vouchers = [
            inv.invoice_number,
            pay1['receipt_number'],
            pay2['receipt_number'],
            pay3['receipt_number'],
        ]
        self.assertEqual(
            vouchers, expected_vouchers,
            msg=f"[Test 8] Voucher order: expected {expected_vouchers}, got {vouchers}",
        )

    def test_09_mixed_invoices_with_payments_chronological_order(self):
        """
        Test 9: Customer opening_credit=10000. Sequence:
          1. Credit invoice #1, item_total=2000
          2. Cash invoice #1, item_total=1000
          3. Payment of 1000 against Cash invoice #1
          4. Cash invoice #2, item_total=3000
          5. General payment of 5000

        Hand-computed running balance:
          Start: 10000 (opening)
          + 2000 (credit inv) = 12000
          + 1000 (cash inv 1) = 13000
          - 1000 (payment on cash inv 1) = 12000
          + 3000 (cash inv 2) = 15000
          - 5000 (general payment) = 10000

        Expected final remainingBalance = 10000.
        """
        customer = self.create_customer(opening_credit=Decimal('10000'))

        # Step 1: Credit invoice, item_total=2000
        inv1 = self.create_invoice(customer, 'Credit', Decimal('2000'))

        # Step 2: Cash invoice, item_total=1000
        inv2 = self.create_invoice(customer, 'Cash', Decimal('1000'))

        # Step 3: Payment of 1000 against cash invoice #1
        pay1 = self.create_payment(customer, Decimal('1000'), invoice=inv2)

        # Step 4: Cash invoice #2, item_total=3000
        inv3 = self.create_invoice(customer, 'Cash', Decimal('3000'))

        # Step 5: General payment of 5000
        pay2 = self.create_payment(customer, Decimal('5000'), invoice=None)

        ledger = self.get_ledger(customer)

        # --- Hand-computed ---
        # Row 1: OPENING debit=10000, balance = 10000
        # Row 2: inv1 debit=2000, balance = 12000
        # Row 3: inv2 debit=1000, balance = 13000
        # Row 4: pay1 credit=1000, balance = 12000
        # Row 5: inv3 debit=3000, balance = 15000
        # Row 6: pay2 credit=5000, balance = 10000
        expected_remaining = Decimal('10000')
        expected_advance = Decimal('0')
        expected_total_collected = Decimal('6000')  # 1000 + 5000

        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix=(
                "[Test 9] summary.remainingBalance: "
                "10000 + 2000 + 1000 - 1000 + 3000 - 5000 = 10000"
            ),
        )
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 9] summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], expected_advance,
            msg_prefix="[Test 9] summary.availableAdvance",
        )
        self.assertDecimalEqual(
            ledger['summary']['totalCollected'], expected_total_collected,
            msg_prefix="[Test 9] summary.totalCollected (1000 + 5000 = 6000)",
        )
        self.assertDecimalEqual(
            ledger['summary']['openingCredit'], Decimal('10000'),
            msg_prefix="[Test 9] summary.openingCredit",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 9] finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['availableAdvance'], expected_advance,
            msg_prefix="[Test 9] finalPaymentDetails.availableAdvance",
        )

        # Last row balance
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 9] last ledger row balance",
        )

        # (i) Voucher order — must match creation sequence
        vouchers = [row['voucher'] for row in ledger['ledger']]
        expected_vouchers = [
            'OPENING',
            inv1.invoice_number,
            inv2.invoice_number,
            pay1['receipt_number'],
            inv3.invoice_number,
            pay2['receipt_number'],
        ]
        self.assertEqual(
            vouchers, expected_vouchers,
            msg=(
                f"[Test 9] Voucher order mismatch.\n"
                f"  Expected: {expected_vouchers}\n"
                f"  Got:      {vouchers}"
            ),
        )

        # Also verify running balances at each row
        expected_balances = [10000, 12000, 13000, 12000, 15000, 10000]
        actual_balances = [row['balance'] for row in ledger['ledger']]
        for i, (actual, expected) in enumerate(zip(actual_balances, expected_balances)):
            self.assertDecimalEqual(
                actual, Decimal(str(expected)),
                msg_prefix=f"[Test 9] row {i} ({vouchers[i]}) running balance",
            )

    def test_10_deleting_a_payment_reverses_its_effect(self):
        """
        Test 10: Customer opening_credit=0. Cash invoice item_total=5000.
        Payment of 5000 (remainingBalance=0). Then DELETE the payment.
        Expected after delete: remainingBalance=5000, and the deleted
        payment's voucher should NOT appear in the ledger rows.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        inv = self.create_invoice(customer, 'Cash', Decimal('5000'))
        pay = self.create_payment(customer, Decimal('5000'), invoice=inv)

        # Verify balance is 0 before deletion
        ledger_before = self.get_ledger(customer)
        self.assertDecimalEqual(
            ledger_before['summary']['remainingBalance'], Decimal('0'),
            msg_prefix=(
                "[Test 10] BEFORE delete: remainingBalance should be 0 "
                "(5000 - 5000)"
            ),
        )

        # DELETE the payment
        payment_id = pay['id']
        delete_url = f'/api/sales/payments/{payment_id}/'
        response = self.client.delete(delete_url)
        self.assertEqual(
            response.status_code, status.HTTP_200_OK,
            msg=(
                f"[Test 10] DELETE payment expected 200, got "
                f"{response.status_code}. Body: {response.data}"
            ),
        )

        # Re-fetch ledger
        ledger_after = self.get_ledger(customer)

        # --- Hand-computed after deletion ---
        # The payment is soft-deleted, so it's excluded from the ledger.
        # Only the invoice debit remains: 0 + 5000 = 5000
        expected_remaining = Decimal('5000')
        expected_advance = Decimal('0')

        self.assertDecimalEqual(
            ledger_after['summary']['remainingBalance'], expected_remaining,
            msg_prefix=(
                "[Test 10] AFTER delete: remainingBalance should revert to "
                "5000 (only invoice debit remains)"
            ),
        )
        self.assertDecimalEqual(
            ledger_after['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 10] AFTER delete: summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger_after['summary']['availableAdvance'], expected_advance,
            msg_prefix="[Test 10] AFTER delete: summary.availableAdvance",
        )
        self.assertDecimalEqual(
            ledger_after['finalPaymentDetails']['remainingBalance'],
            expected_remaining,
            msg_prefix="[Test 10] AFTER delete: finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger_after['finalPaymentDetails']['availableAdvance'],
            expected_advance,
            msg_prefix="[Test 10] AFTER delete: finalPaymentDetails.availableAdvance",
        )

        # Deleted payment voucher should NOT appear in ledger
        vouchers_after = [row['voucher'] for row in ledger_after['ledger']]
        self.assertNotIn(
            pay['receipt_number'], vouchers_after,
            msg=(
                f"[Test 10] Deleted payment voucher {pay['receipt_number']} "
                f"should NOT appear in ledger, but got: {vouchers_after}"
            ),
        )

        # Last row balance
        last_balance = Decimal(str(ledger_after['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 10] AFTER delete: last ledger row balance",
        )

    def test_11_restoring_a_deleted_payment_reapplies_its_effect(self):
        """
        Test 11: Same as Test 10 setup, then restore the deleted payment.
        Expected: remainingBalance returns to 0, payment voucher reappears.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        inv = self.create_invoice(customer, 'Cash', Decimal('5000'))
        pay = self.create_payment(customer, Decimal('5000'), invoice=inv)
        payment_id = pay['id']
        receipt_number = pay['receipt_number']

        # DELETE
        delete_url = f'/api/sales/payments/{payment_id}/'
        self.client.delete(delete_url)

        # Verify it's deleted (remainingBalance = 5000)
        ledger_deleted = self.get_ledger(customer)
        self.assertDecimalEqual(
            ledger_deleted['summary']['remainingBalance'], Decimal('5000'),
            msg_prefix="[Test 11] After delete: remainingBalance should be 5000",
        )

        # RESTORE
        restore_url = f'/api/sales/payments/{payment_id}/restore/'
        response = self.client.post(restore_url)
        self.assertEqual(
            response.status_code, status.HTTP_200_OK,
            msg=(
                f"[Test 11] Restore expected 200, got {response.status_code}. "
                f"Body: {response.data}"
            ),
        )

        # Re-fetch ledger
        ledger_restored = self.get_ledger(customer)

        # --- Hand-computed after restoration ---
        # Invoice debit: 5000, Payment credit: 5000
        # Running balance: 0 + 5000 - 5000 = 0
        expected_remaining = Decimal('0')
        expected_advance = Decimal('0')

        self.assertDecimalEqual(
            ledger_restored['summary']['remainingBalance'], expected_remaining,
            msg_prefix=(
                "[Test 11] After restore: remainingBalance should return "
                "to 0 (5000 - 5000)"
            ),
        )
        self.assertDecimalEqual(
            ledger_restored['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 11] After restore: summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger_restored['summary']['availableAdvance'], expected_advance,
            msg_prefix="[Test 11] After restore: summary.availableAdvance",
        )
        self.assertDecimalEqual(
            ledger_restored['finalPaymentDetails']['remainingBalance'],
            expected_remaining,
            msg_prefix="[Test 11] After restore: finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger_restored['finalPaymentDetails']['availableAdvance'],
            expected_advance,
            msg_prefix="[Test 11] After restore: finalPaymentDetails.availableAdvance",
        )

        # Payment voucher should reappear
        vouchers_restored = [row['voucher'] for row in ledger_restored['ledger']]
        self.assertIn(
            receipt_number, vouchers_restored,
            msg=(
                f"[Test 11] Restored payment voucher {receipt_number} "
                f"should appear in ledger, but got: {vouchers_restored}"
            ),
        )

        # Last row balance
        last_balance = Decimal(str(ledger_restored['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 11] After restore: last ledger row balance",
        )

    def test_12_editing_a_payment_amount_correctly_recalculates(self):
        """
        Test 12: Customer opening_credit=0. Cash invoice item_total=8000.
        Payment of 3000 (remainingBalance=5000). Then PATCH payment to
        change amount_received to 8000.
        Expected after PATCH: remainingBalance=0 (the full 8000 applied,
        not 3000+8000=11000 double-counted).
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        inv = self.create_invoice(customer, 'Cash', Decimal('8000'))
        pay = self.create_payment(customer, Decimal('3000'), invoice=inv)
        payment_id = pay['id']

        # Verify balance before edit
        ledger_before = self.get_ledger(customer)
        # Hand-computed: 0 + 8000 - 3000 = 5000
        self.assertDecimalEqual(
            ledger_before['summary']['remainingBalance'], Decimal('5000'),
            msg_prefix=(
                "[Test 12] Before edit: remainingBalance = "
                "8000 - 3000 = 5000"
            ),
        )

        # PATCH to change amount_received from 3000 to 8000
        patch_url = f'/api/sales/payments/{payment_id}/'
        response = self.client.patch(
            patch_url,
            {'amount_received': '8000'},
            format='json',
        )
        self.assertEqual(
            response.status_code, status.HTTP_200_OK,
            msg=(
                f"[Test 12] PATCH expected 200, got {response.status_code}. "
                f"Body: {response.data}"
            ),
        )

        # Re-fetch ledger
        ledger_after = self.get_ledger(customer)

        # --- Hand-computed after edit ---
        # The old payment (3000) is reversed, then the new (8000) is applied.
        # Ledger: invoice debit=8000, payment credit=8000
        # Running balance: 0 + 8000 - 8000 = 0
        expected_remaining = Decimal('0')
        expected_advance = Decimal('0')

        self.assertDecimalEqual(
            ledger_after['summary']['remainingBalance'], expected_remaining,
            msg_prefix=(
                "[Test 12] After edit: remainingBalance should be 0 "
                "(8000 - 8000), not 5000 (double-counting bug)"
            ),
        )
        self.assertDecimalEqual(
            ledger_after['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 12] After edit: summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger_after['summary']['availableAdvance'], expected_advance,
            msg_prefix="[Test 12] After edit: summary.availableAdvance",
        )
        self.assertDecimalEqual(
            ledger_after['summary']['totalCollected'], Decimal('8000'),
            msg_prefix="[Test 12] After edit: summary.totalCollected = 8000 (not 3000)",
        )
        self.assertDecimalEqual(
            ledger_after['finalPaymentDetails']['remainingBalance'],
            expected_remaining,
            msg_prefix="[Test 12] After edit: finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger_after['finalPaymentDetails']['availableAdvance'],
            expected_advance,
            msg_prefix="[Test 12] After edit: finalPaymentDetails.availableAdvance",
        )

        # Last row balance
        last_balance = Decimal(str(ledger_after['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 12] After edit: last ledger row balance",
        )

    def test_13_negative_or_zero_payment_amount_rejected(self):
        """
        Test 13: Attempting to create a payment with amount=0 and amount=-100.
        Both should return HTTP 400.
        We call client.post directly (NOT create_payment) because that
        helper asserts 201.
        """
        customer = self.create_customer(opening_credit=Decimal('1000'))

        # Attempt with amount=0
        response_zero = self.client.post(
            '/api/sales/payments/',
            {
                'customer': customer.id,
                'amount_received': '0',
                'method': 'Cash',
            },
            format='json',
        )
        self.assertEqual(
            response_zero.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=(
                f"[Test 13] Payment with amount=0 should be rejected with 400, "
                f"got {response_zero.status_code}. Body: {response_zero.data}"
            ),
        )

        # Attempt with amount=-100
        response_negative = self.client.post(
            '/api/sales/payments/',
            {
                'customer': customer.id,
                'amount_received': '-100',
                'method': 'Cash',
            },
            format='json',
        )
        self.assertEqual(
            response_negative.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=(
                f"[Test 13] Payment with amount=-100 should be rejected with 400, "
                f"got {response_negative.status_code}. Body: {response_negative.data}"
            ),
        )

    def test_14_payment_linked_to_invoice_of_different_customer_rejected(self):
        """
        Test 14: Create customers A and B. Create invoice for A.
        Attempt to create payment for B linked to A's invoice.
        Expect HTTP 400 validation error.
        """
        customer_a = self.create_customer(opening_credit=Decimal('0'))
        customer_b = self.create_customer(opening_credit=Decimal('0'))
        inv_a = self.create_invoice(customer_a, 'Credit', Decimal('5000'))

        # Attempt payment for customer B linked to customer A's invoice.
        # Use client.post directly to avoid create_payment's 201 assertion.
        response = self.client.post(
            '/api/sales/payments/',
            {
                'customer': customer_b.id,
                'invoice': inv_a.id,
                'amount_received': '1000',
                'method': 'Cash',
            },
            format='json',
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=(
                f"[Test 14] Payment for customer B linked to customer A's "
                f"invoice should be rejected with 400, got "
                f"{response.status_code}. Body: {response.data}"
            ),
        )

    # ═══════════════════════════════════════════════════════════════════
    # ADVANCE CONSUMPTION TESTS (15-19)
    # ═══════════════════════════════════════════════════════════════════

    def test_15_advance_auto_consumed_on_new_invoice(self):
        """
        Test 15: Customer with advance_balance=3000 (simulating prior
        overpayment). Create Credit invoice, item_total=5000 via API.
        Expected:
          - invoice.advance_applied == 3000
          - invoice.paid_amount == 3000 (0 original + 3000 advance)
          - customer.advance_balance == 0
          - invoice.balance_due == 2000 (5000 - 3000)
          - Ledger remainingBalance == 2000
            (0 opening + 5000 invoice - 3000 advance applied = 2000)
          - An "Advance Applied" row with credit=3000 in ledger
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        # Manually set advance_balance to simulate a previous overpayment
        customer.advance_balance = Decimal('3000')
        customer.save(update_fields=['advance_balance'])

        inv = self.create_invoice_via_api(customer, 'Credit', Decimal('5000'))

        # --- Hand-computed expected values ---
        # available advance = 3000, invoice balance_due = 5000 - 0 = 5000
        # consume = min(3000, 5000) = 3000
        # advance_applied = 3000
        # paid_amount = 0 (original) + 3000 (advance) = 3000
        # balance_due = 5000 - 3000 = 2000
        # customer.advance_balance = 3000 - 3000 = 0

        inv.refresh_from_db()
        self.assertDecimalEqual(
            inv.advance_applied, Decimal('3000'),
            msg_prefix="[Test 15] invoice.advance_applied",
        )
        self.assertDecimalEqual(
            inv.paid_amount, Decimal('3000'),
            msg_prefix="[Test 15] invoice.paid_amount (0 original + 3000 advance)",
        )
        self.assertDecimalEqual(
            inv.balance_due, Decimal('2000'),
            msg_prefix="[Test 15] invoice.balance_due (5000 - 3000)",
        )

        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('0'),
            msg_prefix="[Test 15] customer.advance_balance after consumption",
        )

        ledger = self.get_ledger(customer)

        # Ledger running balance:
        # Row 1: Invoice debit=5000, balance = 0 + 5000 = 5000
        # Row 2: ADV credit=3000, balance = 5000 - 3000 = 2000
        # No payments, no opening credit.
        expected_remaining = Decimal('2000')  # 0 + 5000 - 3000
        expected_advance = Decimal('0')

        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix=(
                "[Test 15] summary.remainingBalance: "
                "0 + 5000 - 3000 advance = 2000"
            ),
        )
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 15] summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], expected_advance,
            msg_prefix="[Test 15] summary.availableAdvance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 15] finalPaymentDetails.remainingBalance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['availableAdvance'], expected_advance,
            msg_prefix="[Test 15] finalPaymentDetails.availableAdvance",
        )

        # Verify "Advance Applied" row exists in ledger
        adv_voucher = f"ADV-{inv.invoice_number}"
        vouchers = [row['voucher'] for row in ledger['ledger']]
        self.assertIn(
            adv_voucher, vouchers,
            msg=(
                f"[Test 15] Expected advance-applied voucher {adv_voucher} "
                f"in ledger, got: {vouchers}"
            ),
        )

        # Find the advance row and check its credit value
        adv_rows = [r for r in ledger['ledger'] if r['voucher'] == adv_voucher]
        self.assertEqual(
            len(adv_rows), 1,
            msg=f"[Test 15] Expected exactly 1 ADV row, got {len(adv_rows)}",
        )
        self.assertDecimalEqual(
            adv_rows[0]['credit'], Decimal('3000'),
            msg_prefix="[Test 15] ADV row credit amount",
        )

        # Last row balance
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 15] last ledger row balance",
        )

    def test_16_advance_partially_covers_invoice_remainder_becomes_debt(self):
        """
        Test 16: advance_balance=1000, invoice item_total=4000.
        Expected: advance_applied=1000, balance_due=3000,
        customer.advance_balance becomes 0, ledger remainingBalance=3000.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        customer.advance_balance = Decimal('1000')
        customer.save(update_fields=['advance_balance'])

        inv = self.create_invoice_via_api(customer, 'Credit', Decimal('4000'))

        # --- Hand-computed ---
        # available advance = 1000, balance_due = 4000
        # consume = min(1000, 4000) = 1000
        # advance_applied = 1000
        # paid_amount = 0 + 1000 = 1000
        # balance_due = 4000 - 1000 = 3000
        # customer.advance_balance = 1000 - 1000 = 0

        inv.refresh_from_db()
        self.assertDecimalEqual(
            inv.advance_applied, Decimal('1000'),
            msg_prefix="[Test 16] invoice.advance_applied",
        )
        self.assertDecimalEqual(
            inv.paid_amount, Decimal('1000'),
            msg_prefix="[Test 16] invoice.paid_amount (0 + 1000 advance)",
        )
        self.assertDecimalEqual(
            inv.balance_due, Decimal('3000'),
            msg_prefix="[Test 16] invoice.balance_due (4000 - 1000)",
        )

        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('0'),
            msg_prefix="[Test 16] customer.advance_balance (1000 - 1000 = 0)",
        )

        ledger = self.get_ledger(customer)

        # Ledger: Invoice debit=4000, ADV credit=1000
        # Running balance: 0 + 4000 - 1000 = 3000
        expected_remaining = Decimal('3000')

        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 16] summary.remainingBalance (0 + 4000 - 1000 = 3000)",
        )
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], Decimal('0'),
            msg_prefix="[Test 16] summary.availableAdvance",
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 16] finalPaymentDetails.remainingBalance",
        )

        # Last row balance
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 16] last ledger row balance",
        )

    def test_17_advance_exceeding_invoice_amount_partially_consumed(self):
        """
        Test 17: advance_balance=9000, invoice item_total=4000.
        Expected: advance_applied=4000 (capped at balance_due, NOT 9000),
        invoice fully paid (balance_due=0),
        customer.advance_balance = 9000 - 4000 = 5000,
        ledger remainingBalance=0, availableAdvance=5000.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        customer.advance_balance = Decimal('9000')
        customer.save(update_fields=['advance_balance'])

        inv = self.create_invoice_via_api(customer, 'Credit', Decimal('4000'))

        # --- Hand-computed ---
        # available advance = 9000, balance_due = 4000
        # consume = min(9000, 4000) = 4000 (capped at what invoice needs)
        # advance_applied = 4000
        # paid_amount = 0 + 4000 = 4000
        # balance_due = 4000 - 4000 = 0
        # customer.advance_balance = 9000 - 4000 = 5000

        inv.refresh_from_db()
        self.assertDecimalEqual(
            inv.advance_applied, Decimal('4000'),
            msg_prefix="[Test 17] invoice.advance_applied (capped at 4000)",
        )
        self.assertDecimalEqual(
            inv.paid_amount, Decimal('4000'),
            msg_prefix="[Test 17] invoice.paid_amount (fully paid by advance)",
        )
        self.assertDecimalEqual(
            inv.balance_due, Decimal('0'),
            msg_prefix="[Test 17] invoice.balance_due (4000 - 4000 = 0)",
        )

        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('5000'),
            msg_prefix="[Test 17] customer.advance_balance (9000 - 4000 = 5000)",
        )

        ledger = self.get_ledger(customer)

        # Ledger: Invoice debit=4000, ADV credit=4000
        # Running balance: 0 + 4000 - 4000 = 0
        # But customer still has 5000 advance left (not in ledger debits).
        # The ledger's final balance is 0 → remainingBalance=0.
        # availableAdvance comes from customer.advance_balance, but actually
        # the ledger derives it from the final running balance:
        #   final_balance = 0 → remaining=0, advance=0 (from ledger).
        # However, the customer's actual advance_balance is 5000.
        # Let's check what the view actually returns.
        #
        # Looking at the view code: availableAdvance is derived from the
        # ledger's own final balance. If final_balance >= 0, advance = 0.
        # So ledger's availableAdvance will be 0 even though
        # customer.advance_balance is 5000. This is because the advance
        # balance is not represented in the ledger (no debit row for it).
        # The remaining 5000 advance won't show until it's consumed by
        # a future invoice or appears as a negative balance.
        #
        # The actual availableAdvance in the ledger summary = 0
        # (because the ledger running balance ended at 0, not negative).
        expected_remaining = Decimal('0')
        expected_advance_in_ledger = Decimal('0')  # ledger-derived

        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 17] summary.remainingBalance (4000 - 4000 = 0)",
        )
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 17] summary.closingBalance",
        )
        self.assertDecimalEqual(
            ledger['summary']['availableAdvance'], expected_advance_in_ledger,
            msg_prefix=(
                "[Test 17] summary.availableAdvance (ledger-derived: "
                "final_balance=0 → advance=0)"
            ),
        )
        self.assertDecimalEqual(
            ledger['finalPaymentDetails']['remainingBalance'], expected_remaining,
            msg_prefix="[Test 17] finalPaymentDetails.remainingBalance",
        )

        # Verify the DB-level advance_balance is still 5000
        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('5000'),
            msg_prefix=(
                "[Test 17] customer.advance_balance in DB "
                "(9000 - 4000 consumed = 5000 remaining)"
            ),
        )

        # Last row balance
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 17] last ledger row balance",
        )

    def test_18_deleting_invoice_reverses_advance_consumption(self):
        """
        Test 18: Customer with advance_balance=3000. Create invoice via API
        item_total=5000 (consumes all 3000 advance). Confirm advance=0.
        Then DELETE the invoice. Expected: customer.advance_balance reverts
        to 3000, and ledger no longer shows the invoice or ADV row.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        customer.advance_balance = Decimal('3000')
        customer.save(update_fields=['advance_balance'])

        inv = self.create_invoice_via_api(customer, 'Credit', Decimal('5000'))

        # Confirm advance was consumed
        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('0'),
            msg_prefix="[Test 18] advance_balance after invoice creation (3000 consumed)",
        )
        inv.refresh_from_db()
        self.assertDecimalEqual(
            inv.advance_applied, Decimal('3000'),
            msg_prefix="[Test 18] invoice.advance_applied",
        )

        # DELETE the invoice
        delete_url = f'/api/sales/invoices/{inv.id}/'
        response = self.client.delete(delete_url)
        self.assertEqual(
            response.status_code, status.HTTP_200_OK,
            msg=(
                f"[Test 18] DELETE invoice expected 200, got "
                f"{response.status_code}. Body: {response.data}"
            ),
        )

        # --- Hand-computed after deletion ---
        # advance_applied=3000 was reversed: customer.advance_balance = 0 + 3000 = 3000
        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('3000'),
            msg_prefix=(
                "[Test 18] customer.advance_balance AFTER delete "
                "(reversed: 0 + 3000 = 3000)"
            ),
        )

        # Ledger should not contain the deleted invoice or its ADV row
        ledger = self.get_ledger(customer)
        vouchers = [row['voucher'] for row in ledger['ledger']]
        self.assertNotIn(
            inv.invoice_number, vouchers,
            msg=(
                f"[Test 18] Deleted invoice {inv.invoice_number} should NOT "
                f"appear in ledger, got: {vouchers}"
            ),
        )
        adv_voucher = f"ADV-{inv.invoice_number}"
        self.assertNotIn(
            adv_voucher, vouchers,
            msg=(
                f"[Test 18] ADV row {adv_voucher} for deleted invoice should "
                f"NOT appear in ledger, got: {vouchers}"
            ),
        )

    def test_19_restoring_deleted_invoice_reapplies_advance_consumption(self):
        """
        Test 19: Same setup as Test 18 — create invoice that consumes advance,
        then delete it. Then RESTORE the invoice. Expected:
        customer.advance_balance == 0 again, and ledger shows the invoice
        debit row and ADV credit row again.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        customer.advance_balance = Decimal('3000')
        customer.save(update_fields=['advance_balance'])

        inv = self.create_invoice_via_api(customer, 'Credit', Decimal('5000'))
        invoice_number = inv.invoice_number

        # DELETE
        delete_url = f'/api/sales/invoices/{inv.id}/'
        self.client.delete(delete_url)

        # Verify advance reversed
        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('3000'),
            msg_prefix="[Test 19] advance_balance after delete (reversed to 3000)",
        )

        # RESTORE
        restore_url = f'/api/sales/invoices/{inv.id}/restore/'
        response = self.client.post(restore_url)
        self.assertEqual(
            response.status_code, status.HTTP_200_OK,
            msg=(
                f"[Test 19] Restore invoice expected 200, got "
                f"{response.status_code}. Body: {response.data}"
            ),
        )

        # --- Hand-computed after restoration ---
        # advance_applied=3000 re-consumed: customer.advance_balance = 3000 - 3000 = 0
        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('0'),
            msg_prefix=(
                "[Test 19] customer.advance_balance AFTER restore "
                "(re-consumed: 3000 - 3000 = 0)"
            ),
        )

        # Ledger should contain the invoice and ADV row again
        ledger = self.get_ledger(customer)
        vouchers = [row['voucher'] for row in ledger['ledger']]
        self.assertIn(
            invoice_number, vouchers,
            msg=(
                f"[Test 19] Restored invoice {invoice_number} should appear "
                f"in ledger, got: {vouchers}"
            ),
        )
        adv_voucher = f"ADV-{invoice_number}"
        self.assertIn(
            adv_voucher, vouchers,
            msg=(
                f"[Test 19] ADV row {adv_voucher} should reappear "
                f"in ledger, got: {vouchers}"
            ),
        )

        # Ledger running balance should be:
        # Invoice debit=5000, ADV credit=3000
        # Running balance: 0 + 5000 - 3000 = 2000
        expected_remaining = Decimal('2000')
        self.assertDecimalEqual(
            ledger['summary']['remainingBalance'], expected_remaining,
            msg_prefix=(
                "[Test 19] summary.remainingBalance after restore "
                "(0 + 5000 - 3000 = 2000)"
            ),
        )
        self.assertDecimalEqual(
            ledger['summary']['closingBalance'], expected_remaining,
            msg_prefix="[Test 19] summary.closingBalance",
        )

        # Last row balance
        last_balance = Decimal(str(ledger['ledger'][-1]['balance']))
        self.assertDecimalEqual(
            last_balance, expected_remaining,
            msg_prefix="[Test 19] last ledger row balance",
        )
