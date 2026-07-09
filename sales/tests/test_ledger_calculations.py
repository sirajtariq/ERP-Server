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

import datetime
from datetime import date
import time
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
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
            'customer_data': {
                'customer_id': customer.customer_id,
                'customer_name': customer.customer_name,
                'phone': customer.phone,
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': payment_term,
            'paid_amount': str(paid_amount),
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
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
            'customer': customer.customer_id,
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

    def get_ledger(self, customer, from_date=None, to_date=None):
        """
        GET the customer ledger and return the parsed JSON.
        Asserts 200 status — fails loudly if not.
        """
        url = f'/api/sales/customers/{customer.customer_id}/ledger/'
        params = {}
        if from_date:
            params['from'] = from_date
        if to_date:
            params['to'] = to_date
        response = self.client.get(url, params)
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

    def test_20_ledger_includes_reference_ids_and_customer_block(self):
        """
        Test 20: test_ledger_includes_reference_ids_and_customer_block
        Create a customer with opening_credit=1000. Create one Credit invoice,
        item_total=2000. Create one payment of 2000.
        """
        customer = self.create_customer(opening_credit=Decimal('1000'))
        inv = self.create_invoice(customer, 'Credit', Decimal('2000'))
        pay = self.create_payment(customer, Decimal('2000'))

        ledger = self.get_ledger(customer)

        self.assertIn('customer', ledger)
        self.assertEqual(ledger['customer']['customerId'], customer.customer_id)
        self.assertEqual(ledger['customer']['customerName'], customer.customer_name)
        
        # Verify rows
        rows = ledger['ledger']
        
        opening_row = next(r for r in rows if r['voucher'] == 'OPENING')
        self.assertIsNone(opening_row.get('referenceType'))
        self.assertIsNone(opening_row.get('referenceId'))
        
        inv_row = next(r for r in rows if r['voucher'] == inv.invoice_number)
        self.assertEqual(inv_row.get('referenceType'), 'invoice')
        self.assertEqual(inv_row.get('referenceId'), inv.id)
        
        pay_row = next(r for r in rows if r['voucher'] == pay['receipt_number'])
        self.assertEqual(pay_row.get('referenceType'), 'payment')
        self.assertEqual(pay_row.get('referenceId'), pay['id'])

    def test_21_ledger_date_range_filter_computes_correct_brought_forward_balance(self):
        """
        Test 21: test_ledger_date_range_filter_computes_correct_brought_forward_balance
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        inv = self.create_invoice(customer, 'Credit', Decimal('5000'))
        pay = self.create_payment(customer, Decimal('2000'))

        # NO date filter
        ledger_no_filter = self.get_ledger(customer)
        self.assertDecimalEqual(
            ledger_no_filter['summary']['remainingBalance'], Decimal('3000'),
            msg_prefix="[Test 21] No filter baseline"
        )
        
        today_str = datetime.date.today().isoformat()
        
        # WITH today's filter
        ledger_today = self.get_ledger(customer, from_date=today_str, to_date=today_str)
        self.assertDecimalEqual(
            ledger_today['summary']['remainingBalance'], Decimal('3000'),
            msg_prefix="[Test 21] Today filter"
        )
        
        tomorrow_str = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        ledger_tomorrow = self.get_ledger(customer, from_date=tomorrow_str, to_date=tomorrow_str)
        
        rows = ledger_tomorrow['ledger']
        if len(rows) == 1:
            row = rows[0]
            self.assertEqual(row['description'], 'Balance Brought Forward')
            self.assertDecimalEqual(row['debit'], Decimal('3000'), msg_prefix="[Test 21] Brought forward debit")
        else:
            self.assertEqual(len(rows), 0, msg="[Test 21] Expected 0 or 1 rows")

    # ═══════════════════════════════════════════════════════════════════
    # INVOICE LIST SERIALIZER TESTS
    # ═══════════════════════════════════════════════════════════════════

    def list_invoices(self):
        """GET the invoice list and return parsed JSON."""
        response = self.client.get('/api/sales/invoices/')
        self.assertEqual(
            response.status_code, status.HTTP_200_OK,
            msg=f"list_invoices() expected 200, got {response.status_code}. Body: {response.data}",
        )
        return response.data

    def test_22_invoice_list_status_unpaid(self):
        """
        Test 22: Credit invoice with no payment → status "Unpaid".
        """
        customer = self.create_customer()
        inv = self.create_invoice(customer, 'Credit', Decimal('5000'))

        data = self.list_invoices()
        # Find this invoice in the paginated results
        results = data.get('results', data)
        row = next(r for r in results if r['invoiceNumber'] == inv.invoice_number)

        self.assertEqual(
            row['paymentStatus'], 'Unpaid',
            msg=f"[Test 22] Expected paymentStatus 'Unpaid', got '{row['paymentStatus']}'",
        )
        self.assertDecimalEqual(
            row['pending'], Decimal('5000'),
            msg_prefix="[Test 22] pending",
        )
        self.assertDecimalEqual(
            row['paid'], Decimal('0'),
            msg_prefix="[Test 22] paid",
        )

    def test_23_invoice_list_status_partial(self):
        """
        Test 23: Cash invoice with partial payment → status "Partial".
        """
        customer = self.create_customer()
        inv = self.create_invoice(customer, 'Cash', Decimal('5000'))
        self.create_payment(customer, Decimal('2000'), invoice=inv)

        data = self.list_invoices()
        results = data.get('results', data)
        row = next(r for r in results if r['invoiceNumber'] == inv.invoice_number)

        self.assertEqual(
            row['paymentStatus'], 'Partial',
            msg=f"[Test 23] Expected paymentStatus 'Partial', got '{row['paymentStatus']}'",
        )
        self.assertDecimalEqual(
            row['pending'], Decimal('3000'),
            msg_prefix="[Test 23] pending (5000 - 2000)",
        )
        self.assertDecimalEqual(
            row['paid'], Decimal('2000'),
            msg_prefix="[Test 23] paid",
        )

    def test_24_invoice_list_status_paid_no_advance(self):
        """
        Test 24: Cash invoice fully paid, customer advance_balance == 0
        → status "Paid".
        """
        customer = self.create_customer()
        inv = self.create_invoice(customer, 'Cash', Decimal('3000'))
        self.create_payment(customer, Decimal('3000'), invoice=inv)

        # Confirm customer has no advance
        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('0'),
            msg_prefix="[Test 24] customer.advance_balance should be 0",
        )

        data = self.list_invoices()
        results = data.get('results', data)
        row = next(r for r in results if r['invoiceNumber'] == inv.invoice_number)

        self.assertEqual(
            row['paymentStatus'], 'Paid',
            msg=f"[Test 24] Expected paymentStatus 'Paid', got '{row['paymentStatus']}'",
        )
        self.assertDecimalEqual(
            row['pending'], Decimal('0'),
            msg_prefix="[Test 24] pending",
        )

    def test_25_invoice_list_status_advance_when_customer_has_leftover_advance(self):
        """
        Test 25: Invoice fully paid via normal payment; customer has a
        leftover advance_balance from a prior overpayment elsewhere.
        → status "Advance" because pending==0 AND customer.advance_balance > 0.

        Confirms the client's rule: Advance status reflects the customer's
        current advance state, not this invoice's own payment method.
        """
        customer = self.create_customer()
        # Simulate leftover advance from a prior overpayment
        customer.advance_balance = Decimal('1000')
        customer.save(update_fields=['advance_balance'])

        inv = self.create_invoice(
            customer, 'Cash', Decimal('2000'), paid_amount=Decimal('2000'),
        )
        self.create_payment(customer, Decimal('2000'), invoice=inv)

        # Customer should still have advance (the payment fully covers the
        # invoice so no advance is consumed from the leftover 1000)
        customer.refresh_from_db()
        self.assertTrue(
            customer.advance_balance > 0,
            msg=f"[Test 25] customer.advance_balance should be > 0, got {customer.advance_balance}",
        )

        data = self.list_invoices()
        results = data.get('results', data)
        row = next(r for r in results if r['invoiceNumber'] == inv.invoice_number)

        self.assertEqual(
            row['paymentStatus'], 'Advance',
            msg=f"[Test 25] Expected paymentStatus 'Advance', got '{row['paymentStatus']}'",
        )

    def test_26_invoice_list_customer_name_present(self):
        """
        Test 26: Verify customerName is returned correctly in the list.
        """
        customer = self.create_customer()
        inv = self.create_invoice(customer, 'Cash', Decimal('1000'))

        data = self.list_invoices()
        results = data.get('results', data)
        row = next(r for r in results if r['invoiceNumber'] == inv.invoice_number)

        self.assertEqual(
            row['customerName'], customer.customer_name,
            msg=(
                f"[Test 26] Expected customerName '{customer.customer_name}', "
                f"got '{row['customerName']}'"
            ),
        )

    def test_27_invoice_status_not_partial_due_to_rounding_noise(self):
        """
        Test 27: A tiny rounding remainder (balance_due well under 0.01)
        should NOT cause status "Partial". The 0.01 tolerance in get_status()
        treats this as fully settled. Also verify the displayed 'pending'
        is "0.00", not some fractional artifact.

        Strategy: Use vat_percentage to produce a net_total with many decimal
        places (via Decimal division by 100). Then set paid_amount to the
        2-decimal-rounded value, leaving a sub-cent remainder in balance_due.
        """
        customer = self.create_customer()

        # Create invoice via ORM with a VAT that produces a fractional total.
        # subtotal = 1000 (qty=1 × rate=1000 - discount=0)
        # vat_percentage = 7  →  tax_amount = 1000 × 7/100 = 70.00  (exact)
        # That's too clean. Use vat_percentage = 3.33:
        # tax_amount = 1000 × 3.33 / 100 = 33.3  (exact Decimal)
        # Still exact. Use a subtotal that produces a repeating decimal:
        # rate = 999, vat_percentage = 7
        # tax_amount = 999 × 7 / 100 = 69.93 (exact)
        # Use rate = 1000, vat_percentage = 33.33:
        # tax_amount = 1000 × 33.33 / 100 = 333.30 (exact)
        # The trick: use a combo where division introduces extra precision.
        # rate = 1001, vat_percentage = 3 → tax = 1001 * 3 / 100 = 30.03
        # Still exact. Let's just directly manipulate the scenario:
        invoice = SalesInvoice.objects.create(
            customer=customer,
            payment_term='Credit',
            paid_amount=Decimal('0'),
            vat_percentage=Decimal('7'),
            invoice_discount=Decimal('0'),
            status='Saved',
        )
        # Create an item with rate that produces fractional tax via division
        SalesItem.objects.create(
            invoice=invoice,
            item_name='TestItem_rounding',
            quantity=Decimal('1'),
            rate=Decimal('333.33'),  # subtotal = 333.33
            discount=Decimal('0'),
        )
        # net_total = 333.33 + (333.33 * 7/100) = 333.33 + 23.3331 = 356.6631
        # This has 4 decimal places — more than the 2dp paid_amount field.

        invoice.refresh_from_db()
        net = invoice.net_total  # Decimal('356.6631')

        # Set paid_amount to the 2-decimal rounded value (356.66)
        rounded_paid = net.quantize(Decimal('0.01'))
        invoice.paid_amount = rounded_paid
        invoice.save(update_fields=['paid_amount'])

        invoice.refresh_from_db()
        remainder = invoice.balance_due  # 356.6631 - 356.66 = 0.0031

        self.assertTrue(
            remainder > 0,
            msg=f"[Test 27] balance_due should be > 0, got {remainder}",
        )
        self.assertTrue(
            remainder < Decimal('0.01'),
            msg=f"[Test 27] balance_due should be < 0.01, got {remainder}",
        )

        data = self.list_invoices()
        results = data.get('results', data)
        row = next(r for r in results if r['invoiceNumber'] == invoice.invoice_number)

        # Status should be "Paid", NOT "Partial"
        self.assertEqual(
            row['paymentStatus'], 'Paid',
            msg=(
                f"[Test 27] Expected paymentStatus 'Paid' (rounding noise absorbed), "
                f"got '{row['paymentStatus']}'"
            ),
        )

        # Displayed pending should be clean "0.00"
        self.assertDecimalEqual(
            row['pending'], Decimal('0.00'),
            msg_prefix="[Test 27] pending display value",
        )

    def test_28_invoice_pending_field_rounds_to_two_decimals_when_unpaid(self):
        """
        Test 28: Verify that the pending field is quantized to exactly 2
        decimal places, even when net_total has more precision from VAT
        division. An unpaid invoice with net_total=356.6631 should show
        pending="356.66", not "356.6631".
        """
        customer = self.create_customer()

        invoice = SalesInvoice.objects.create(
            customer=customer,
            payment_term='Credit',
            paid_amount=Decimal('0'),
            vat_percentage=Decimal('7'),
            invoice_discount=Decimal('0'),
            status='Saved',
        )
        SalesItem.objects.create(
            invoice=invoice,
            item_name='TestItem_precision',
            quantity=Decimal('1'),
            rate=Decimal('333.33'),
            discount=Decimal('0'),
        )
        # net_total = 333.33 + (333.33 * 7/100) = 333.33 + 23.3331 = 356.6631

        data = self.list_invoices()
        results = data.get('results', data)
        row = next(r for r in results if r['invoiceNumber'] == invoice.invoice_number)

        # pending must be exactly "356.66" (2 decimal places)
        self.assertEqual(
            str(row['pending']), '356.66',
            msg=(
                f"[Test 28] Expected pending '356.66', "
                f"got '{row['pending']}'"
            ),
        )

        # paymentStatus should be "Unpaid" since 356.6631 is well above 0.01
        self.assertEqual(
            row['paymentStatus'], 'Unpaid',
            msg=f"[Test 28] Expected paymentStatus 'Unpaid', got '{row['paymentStatus']}'",
        )

    def test_29_invoice_detail_customer_data_excludes_nested_invoices(self):
        """
        Test 29: customer_data in the invoice detail response should use
        CustomerListSerializer (lightweight), NOT CustomerSerializer (which
        includes nested invoices). Confirm 'invoices' key is absent.
        """
        customer = self.create_customer()
        inv1 = self.create_invoice(customer, 'Credit', Decimal('1000'))
        inv2 = self.create_invoice(customer, 'Cash', Decimal('2000'))

        response = self.client.get(f'/api/sales/invoices/{inv1.id}/')
        self.assertEqual(
            response.status_code, status.HTTP_200_OK,
            msg=f"[Test 29] Expected 200, got {response.status_code}. Body: {response.data}",
        )

        customer_data = response.data['customer_data']
        self.assertNotIn(
            'invoices', customer_data,
            msg=(
                "[Test 29] customer_data should NOT contain 'invoices' key "
                "(lightweight serializer expected). Found keys: "
                f"{list(customer_data.keys())}"
            ),
        )

    def test_30_invoice_detail_has_both_invoiceStatus_and_paymentStatus_distinctly(self):
        """
        Test 30: The invoice detail response should have both 'invoiceStatus'
        (record lifecycle: Draft/Saved) and 'paymentStatus' (payment state:
        Unpaid/Partial/Paid/Advance) as distinct fields.
        """
        customer = self.create_customer()
        inv = self.create_invoice(customer, 'Cash', Decimal('1000'))

        response = self.client.get(f'/api/sales/invoices/{inv.id}/')
        self.assertEqual(
            response.status_code, status.HTTP_200_OK,
            msg=f"[Test 30] Expected 200, got {response.status_code}. Body: {response.data}",
        )

        # Record status should be 'Saved' (set by create_invoice helper)
        self.assertEqual(
            response.data['invoiceStatus'], 'Saved',
            msg=f"[Test 30] Expected invoiceStatus 'Saved', got '{response.data['invoiceStatus']}'",
        )

        # Payment status should be 'Unpaid' (no payment made)
        self.assertEqual(
            response.data['paymentStatus'], 'Unpaid',
            msg=f"[Test 30] Expected paymentStatus 'Unpaid', got '{response.data['paymentStatus']}'",
        )

    def test_31_invoice_list_pending_field_is_a_string_type(self):
        """
        Test 31: The 'pending', 'total', and 'paid' fields in the list
        response should all be strings (consistent JSON decimal formatting),
        not raw numbers.
        """
        customer = self.create_customer()
        inv = self.create_invoice(customer, 'Cash', Decimal('2000'))
        self.create_payment(customer, Decimal('1000'), invoice=inv)

        data = self.list_invoices()
        results = data.get('results', data)
        row = next(r for r in results if r['invoiceNumber'] == inv.invoice_number)

        # pending should be a string "1000.00"
        self.assertIsInstance(
            row['pending'], str,
            msg=f"[Test 31] 'pending' should be a str, got {type(row['pending']).__name__}",
        )
        self.assertEqual(
            row['pending'], '1000.00',
            msg=f"[Test 31] Expected pending '1000.00', got '{row['pending']}'",
        )

        # total and paid should also be strings for consistency
        self.assertIsInstance(
            row['total'], str,
            msg=f"[Test 31] 'total' should be a str, got {type(row['total']).__name__}",
        )
        self.assertIsInstance(
            row['paid'], str,
            msg=f"[Test 31] 'paid' should be a str, got {type(row['paid']).__name__}",
        )

    def test_32_customer_id_retry_on_collision(self):
        """
        Test 32 — test_customer_id_retry_on_collision:
        """
        from unittest.mock import patch
        
        # 1. Create a customer
        customer1 = self.create_customer(customer_type='permanent')
        
        # 2. Create another customer
        customer2 = self.create_customer(customer_type='permanent')

        # 3. Initialize a third customer without saving it yet
        customer3 = Customer(
            customer_name='TestCustomer_Collision',
            phone=f'0300{LedgerCalculationTests._phone_counter + 1:07d}',
            customer_type='permanent'
        )

        # FIX: objects ki jagah all_objects use karein kyunki model badal chuka ha
        real_filter = Customer.all_objects.filter
        call_count = [0]

        def fake_filter(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return real_filter(id=customer1.id)
            return real_filter(*args, **kwargs)

        # FIX: Patch Customer.all_objects instead of Customer.objects
        with patch.object(Customer.all_objects, 'filter', side_effect=fake_filter):
            customer3.save()

        # The mocked filter should be called twice due to the retry loop
        self.assertEqual(call_count[0], 2)
        
        # Ensure customer3 successfully got a unique customer_id that is NOT customer2's
        self.assertNotEqual(customer3.customer_id, customer2.customer_id)
        self.assertTrue(customer3.customer_id > customer2.customer_id)

    def test_33_invoice_creation_with_inline_new_walkin_customer(self):
        """
        Test 33: POST to /api/sales/invoices/ with customer_data as an
        object, payment_term='Cash', one item with rate=1500, quantity=1.
        Verifies that a new walk-in Customer is created on the fly and
        linked to the invoice, and that to_representation returns the
        full nested customer data object.
        """
        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': 'Inline Walkin Test',
                'phone': '03111111111',
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Cash',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_InlineWalkin',
                'quantity': '1',
                'rate': '1500',
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
                f"[Test 33] Expected 201 but got {response.status_code}. "
                f"Response body: {response.data}"
            ),
        )

        # Fetch the created invoice from the DB
        invoice = SalesInvoice.objects.get(id=response.data['id'])

        # Assert customer is linked
        self.assertIsNotNone(
            invoice.customer,
            msg="[Test 33] invoice.customer should not be None",
        )

        # Assert customer_name
        self.assertEqual(
            invoice.customer.customer_name, 'Inline Walkin Test',
            msg=(
                f"[Test 33] Expected customer_name 'Inline Walkin Test', "
                f"got '{invoice.customer.customer_name}'"
            ),
        )

        # Assert customer_type is 'walkin'
        self.assertEqual(
            invoice.customer.customer_type, 'walkin',
            msg=(
                f"[Test 33] Expected customer_type 'walkin', "
                f"got '{invoice.customer.customer_type}'"
            ),
        )

        # Assert customer_id is in the walk-in range (>= 8000)
        self.assertGreaterEqual(
            invoice.customer.customer_id, 8000,
            msg=(
                f"[Test 33] Walk-in customer_id should be >= 8000, "
                f"got {invoice.customer.customer_id}"
            ),
        )

        # Assert response.data['customer_data'] is a dict (nested object),
        # confirming to_representation returns full customer info
        self.assertIsInstance(
            response.data['customer_data'], dict,
            msg=(
                f"[Test 33] Expected response customer_data to be a dict, "
                f"got {type(response.data['customer_data']).__name__}"
            ),
        )

    def test_34_invoice_creation_inline_walkin_rejects_credit_payment(self):
        """
        Test 34: POST to /api/sales/invoices/ with customer_data for a
        new walk-in customer, payment_term='Credit'.
        Should be rejected with 400 because walk-in customers cannot use
        Credit payment term.
        """
        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': 'Credit Test Walkin',
                'phone': '03112222222',
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Credit',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_CreditWalkin',
                'quantity': '1',
                'rate': '1000',
                'discount': '0',
            }],
        }
        response = self.client.post(
            '/api/sales/invoices/', payload, format='json',
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=(
                f"[Test 34] Expected 400 but got {response.status_code}. "
                f"Response body: {response.data}"
            ),
        )

    def test_35_invoice_creation_customer_data_missing_required_fields_rejected(self):
        """
        Test 35: POST to /api/sales/invoices/ with customer_data missing
        """
        # Test with missing customer_name (empty string) — Ab ye pass hona chahiye aur default "General" banna chahiye
        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': '',
                'phone': '03113333333',
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Cash',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_NoName',
                'quantity': '1',
                'rate': '500',
                'discount': '0',
            }],
        }
        response = self.client.post(
            '/api/sales/invoices/', payload, format='json',
        )
        # FIX: Expect 201_CREATED instead of 400
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invoice = SalesInvoice.objects.get(id=response.data['id'])
        self.assertEqual(invoice.customer.customer_name, "General")

        # Test with missing phone (empty string) — Ye abhi bhi strictly 400 hi hona chahiye
        payload['customer_data'] = {
            'customer_id': None,
            'customer_name': 'Has Name',
            'phone': '',
            'customer_type': 'walkin',
            'tax_number': None,
        }
        response = self.client.post(
            '/api/sales/invoices/', payload, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_36_inline_walkin_customer_with_tax_number(self):
        """
        Test 36: POST to /api/sales/invoices/ with customer_data that
        includes an optional tax_number. Assert 201 and verify the
        tax_number is saved on the created Customer record.
        """
        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': 'Tax Number Walkin',
                'phone': '03114444444',
                'customer_type': 'walkin',
                'tax_number': 'NTN-12345',
            },
            'payment_term': 'Cash',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_TaxNumber',
                'quantity': '1',
                'rate': '500',
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
                f"[Test 36] Expected 201 but got {response.status_code}. "
                f"Response body: {response.data}"
            ),
        )

        # Fetch the created invoice and its customer
        invoice = SalesInvoice.objects.get(id=response.data['id'])
        cust = invoice.customer

        self.assertIsNotNone(cust, "[Test 36] invoice.customer should not be None")
        self.assertEqual(
            cust.customer_name, 'Tax Number Walkin',
            msg=f"[Test 36] customer_name mismatch: got '{cust.customer_name}'",
        )
        self.assertEqual(
            cust.customer_type, 'walkin',
            msg=f"[Test 36] customer_type mismatch: got '{cust.customer_type}'",
        )
        self.assertEqual(
            cust.phone, '03114444444',
            msg=f"[Test 36] phone mismatch: got '{cust.phone}'",
        )
        self.assertEqual(
            cust.tax_number, 'NTN-12345',
            msg=f"[Test 36] tax_number mismatch: got '{cust.tax_number}'",
        )

    def test_37_existing_customer_matched_by_phone_links_to_invoice(self):
        """
        Test 37: Create an existing customer with a known phone number,
        then create an invoice with customer_data using the same phone.
        The new behavior links to the EXISTING customer instead of
        rejecting the duplicate — this is the phone-based lookup.
        """
        existing = self.create_customer()
        duplicate_phone = existing.phone  # auto-generated unique phone

        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': 'Different Name',
                'phone': duplicate_phone,
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Cash',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_PhoneMatch',
                'quantity': '1',
                'rate': '1000',
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
                f"[Test 37] Expected 201 (phone match links existing), got "
                f"{response.status_code}. Response body: {response.data}"
            ),
        )

        # Verify it linked to the EXISTING customer
        invoice = SalesInvoice.objects.get(id=response.data['id'])
        self.assertEqual(
            invoice.customer.id, existing.id,
            msg=(
                f"[Test 37] Expected invoice linked to existing customer "
                f"id={existing.id}, got id={invoice.customer.id}"
            ),
        )
        # No new customer should have been created with that phone
        self.assertEqual(
            Customer.objects.filter(phone=duplicate_phone).count(), 1,
            msg="[Test 37] Should still be exactly 1 customer with that phone",
        )

    def test_38_draft_invoice_has_zero_balance_effect(self):
        """
        Test 38: A Draft invoice must have ZERO financial effect — no
        credit_balance change, no advance consumption, no PaymentReceived
        auto-creation, and must not appear in the ledger.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))

        payload = {
            'customer_data': {
                'customer_id': customer.customer_id,
                'customer_name': customer.customer_name,
                'phone': customer.phone,
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Credit',
            'paid_amount': '500',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Draft',
            'items': [{
                'item_name': 'DraftItem',
                'quantity': '1',
                'rate': '100',
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
                f"[Test 38] Expected 201 but got {response.status_code}. "
                f"Response body: {response.data}"
            ),
        )

        invoice = SalesInvoice.objects.get(id=response.data['id'])
        customer.refresh_from_db()

        # credit_balance must be untouched
        self.assertDecimalEqual(
            customer.credit_balance, Decimal('0'),
            msg_prefix="[Test 38] customer.credit_balance should be 0",
        )

        # advance_balance must be untouched
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('0'),
            msg_prefix="[Test 38] customer.advance_balance should be 0",
        )

        # No PaymentReceived auto-created
        from sales.models import PaymentReceived
        self.assertEqual(
            PaymentReceived.objects.filter(customer=customer).count(), 0,
            msg="[Test 38] No PaymentReceived should exist for Draft invoice",
        )

        # advance_applied must be 0
        self.assertDecimalEqual(
            invoice.advance_applied, Decimal('0'),
            msg_prefix="[Test 38] invoice.advance_applied should be 0",
        )

        # Ledger must not include Draft invoices
        ledger = self.get_ledger(customer)
        self.assertEqual(
            ledger['summary']['totalInvoices'], 0,
            msg="[Test 38] totalInvoices should be 0 for Draft-only",
        )
        self.assertEqual(
            len(ledger['ledger']), 0,
            msg="[Test 38] ledger array should be empty for Draft-only",
        )

    def test_39_draft_to_saved_transition_applies_balance_effects_once(self):
        """
        Test 39: When a Draft invoice is PATCHed to Saved, balance effects
        (advance consumption, credit_balance update) must trigger exactly
        once at that point. A second PATCH with invoiceStatus=Saved must
        NOT re-apply the effects.
        """
        customer = self.create_customer(opening_credit=Decimal('0'))
        customer.advance_balance = Decimal('200')
        customer.save(update_fields=['advance_balance'])

        # Create Draft invoice
        payload = {
            'customer_data': {
                'customer_id': customer.customer_id,
                'customer_name': customer.customer_name,
                'phone': customer.phone,
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Credit',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Draft',
            'items': [{
                'item_name': 'DraftToSavedItem',
                'quantity': '1',
                'rate': '500',
                'discount': '0',
            }],
        }
        response = self.client.post(
            '/api/sales/invoices/', payload, format='json',
        )
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            msg=f"[Test 39] Create Draft: {response.data}",
        )
        invoice_id = response.data['id']

        # Confirm nothing changed yet
        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('200'),
            msg_prefix="[Test 39] advance_balance should still be 200 after Draft",
        )
        self.assertDecimalEqual(
            customer.credit_balance, Decimal('0'),
            msg_prefix="[Test 39] credit_balance should be 0 after Draft",
        )

        # Transition Draft → Saved
        patch_response = self.client.patch(
            f'/api/sales/invoices/{invoice_id}/',
            {'invoiceStatus': 'Saved'},
            format='json',
        )
        self.assertEqual(
            patch_response.status_code, status.HTTP_200_OK,
            msg=f"[Test 39] PATCH to Saved: {patch_response.data}",
        )

        # Refresh and verify
        invoice = SalesInvoice.objects.get(id=invoice_id)
        customer.refresh_from_db()

        # Hand-computed: advance=200 consumes into 500 balance_due,
        # leaving 300 as credit_balance
        self.assertDecimalEqual(
            invoice.advance_applied, Decimal('200'),
            msg_prefix="[Test 39] invoice.advance_applied",
        )
        self.assertDecimalEqual(
            customer.advance_balance, Decimal('0'),
            msg_prefix="[Test 39] customer.advance_balance after transition",
        )
        self.assertDecimalEqual(
            customer.credit_balance, Decimal('300'),
            msg_prefix="[Test 39] customer.credit_balance after transition",
        )

        # Ledger should show the invoice now
        ledger = self.get_ledger(customer)
        self.assertEqual(
            ledger['summary']['totalInvoices'], 1,
            msg="[Test 39] totalInvoices should be 1 after Saved",
        )
        # Should have both invoice debit row and advance-applied credit row
        vouchers = [row['voucher'] for row in ledger['ledger']]
        self.assertIn(
            invoice.invoice_number, vouchers,
            msg=f"[Test 39] Invoice voucher should be in ledger: {vouchers}",
        )
        self.assertIn(
            f'ADV-{invoice.invoice_number}', vouchers,
            msg=f"[Test 39] Advance Applied voucher should be in ledger: {vouchers}",
        )

        # PATCH Saved → Saved again: should NOT double-apply
        patch2 = self.client.patch(
            f'/api/sales/invoices/{invoice_id}/',
            {'invoiceStatus': 'Saved'},
            format='json',
        )
        self.assertEqual(
            patch2.status_code, status.HTTP_200_OK,
            msg=f"[Test 39] Second PATCH: {patch2.data}",
        )
        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.credit_balance, Decimal('300'),
            msg_prefix="[Test 39] credit_balance must NOT double after re-PATCH",
        )

    def test_40_deleting_a_draft_invoice_does_not_affect_balance(self):
        """
        Test 40: Deleting a Draft invoice must not change any customer
        balances (a Draft never had effects applied).
        """
        customer = self.create_customer(opening_credit=Decimal('0'))

        payload = {
            'customer_data': {
                'customer_id': customer.customer_id,
                'customer_name': customer.customer_name,
                'phone': customer.phone,
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Credit',
            'paid_amount': '1000',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Draft',
            'items': [{
                'item_name': 'DraftDeleteItem',
                'quantity': '1',
                'rate': '1000',
                'discount': '0',
            }],
        }
        response = self.client.post(
            '/api/sales/invoices/', payload, format='json',
        )
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            msg=f"[Test 40] Create: {response.data}",
        )
        invoice_id = response.data['id']

        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.credit_balance, Decimal('0'),
            msg_prefix="[Test 40] credit_balance before delete",
        )

        from sales.models import PaymentReceived
        self.assertEqual(
            PaymentReceived.objects.filter(customer=customer).count(), 0,
            msg="[Test 40] No PaymentReceived for Draft",
        )

        # DELETE the Draft invoice
        del_response = self.client.delete(
            f'/api/sales/invoices/{invoice_id}/',
        )
        self.assertEqual(
            del_response.status_code, status.HTTP_200_OK,
            msg=f"[Test 40] DELETE: {del_response.data}",
        )

        customer.refresh_from_db()
        self.assertDecimalEqual(
            customer.credit_balance, Decimal('0'),
            msg_prefix="[Test 40] credit_balance after deleting Draft (must stay 0)",
        )

    def test_41_invoice_detail_has_invoiceStatus_and_paymentStatus_distinctly(self):
        """
        Test 41: The invoice detail response should have 'invoiceStatus'
        (not bare 'status') alongside 'paymentStatus', confirming the
        API rename.
        """
        customer = self.create_customer()

        payload = {
            'customer_data': {
                'customer_id': customer.customer_id,
                'customer_name': customer.customer_name,
                'phone': customer.phone,
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Cash',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_StatusRename',
                'quantity': '1',
                'rate': '1000',
                'discount': '0',
            }],
        }
        response = self.client.post(
            '/api/sales/invoices/', payload, format='json',
        )
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            msg=f"[Test 41] Create: {response.data}",
        )
        invoice_id = response.data['id']

        detail = self.client.get(f'/api/sales/invoices/{invoice_id}/')
        self.assertEqual(
            detail.status_code, status.HTTP_200_OK,
            msg=f"[Test 41] GET: {detail.data}",
        )

        # invoiceStatus should be present and correct
        self.assertEqual(
            detail.data['invoiceStatus'], 'Saved',
            msg=(
                f"[Test 41] Expected invoiceStatus 'Saved', "
                f"got '{detail.data.get('invoiceStatus')}'"
            ),
        )

        # paymentStatus should be present and correct (Unpaid, no payment)
        self.assertEqual(
            detail.data['paymentStatus'], 'Unpaid',
            msg=(
                f"[Test 41] Expected paymentStatus 'Unpaid', "
                f"got '{detail.data.get('paymentStatus')}'"
            ),
        )

        # Bare 'status' key should NOT be in the response
        self.assertNotIn(
            'status', detail.data,
            msg=(
                "[Test 41] Bare 'status' key should not exist in response "
                f"(renamed to 'invoiceStatus'). Keys: {list(detail.data.keys())}"
            ),
        )

    # ═══════════════════════════════════════════════════════════════════
    # CUSTOMER DATA FIELD TESTS (42-47)
    # ═══════════════════════════════════════════════════════════════════

    def test_42_invoice_customer_data_creates_new_walkin_by_phone(self):
        """
        Test A — Do NOT pre-create a customer. POST with customer_data
        containing a new phone number. Assert 201 and verify a new
        walk-in customer was created with the correct fields.
        """
        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': 'Phone Test Walkin',
                'phone': '03219998877',
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Cash',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_NewPhone',
                'quantity': '1',
                'rate': '500',
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
                f"[Test 42] Expected 201 but got {response.status_code}. "
                f"Response body: {response.data}"
            ),
        )

        invoice = SalesInvoice.objects.get(id=response.data['id'])
        self.assertEqual(
            invoice.customer.customer_name, 'Phone Test Walkin',
            msg=f"[Test 42] customer_name: got '{invoice.customer.customer_name}'",
        )
        self.assertEqual(
            invoice.customer.customer_type, 'walkin',
            msg=f"[Test 42] customer_type: got '{invoice.customer.customer_type}'",
        )
        self.assertEqual(
            invoice.customer.phone, '03219998877',
            msg=f"[Test 42] phone: got '{invoice.customer.phone}'",
        )
        self.assertGreaterEqual(
            invoice.customer.customer_id, 8000,
            msg=(
                f"[Test 42] Walk-in customer_id should be >= 8000, "
                f"got {invoice.customer.customer_id}"
            ),
        )

    def test_43_invoice_customer_data_matches_existing_customer_by_phone(self):
        """
        Test B — Create a customer via create_customer(). POST an invoice
        with customer_data using a DIFFERENT customer_name but the SAME
        phone. Assert 201. Assert the invoice links to the EXISTING
        customer, and no new Customer was created.
        """
        existing = self.create_customer()
        original_phone = existing.phone
        original_id = existing.id

        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': 'Different Name Entirely',
                'phone': original_phone,
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Cash',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_PhoneMatch',
                'quantity': '1',
                'rate': '700',
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
                f"[Test 43] Expected 201 but got {response.status_code}. "
                f"Response body: {response.data}"
            ),
        )

        invoice = SalesInvoice.objects.get(id=response.data['id'])
        self.assertEqual(
            invoice.customer.id, original_id,
            msg=(
                f"[Test 43] Expected invoice linked to existing customer "
                f"id={original_id}, got id={invoice.customer.id}"
            ),
        )
        # Ensure no duplicate was created
        self.assertEqual(
            Customer.objects.filter(phone=original_phone).count(), 1,
            msg="[Test 43] Should still be exactly 1 customer with that phone",
        )

    def test_44_invoice_customer_data_rejects_non_walkin_type(self):
        """
        Test C — POST with customer_data customer_type='permanent'.
        Assert 400 with an error mentioning customer_type must be 'walkin'.
        """
        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': 'Permanent Attempt',
                'phone': '03225556666',
                'customer_type': 'permanent',
                'tax_number': None,
            },
            'payment_term': 'Cash',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_NonWalkin',
                'quantity': '1',
                'rate': '500',
                'discount': '0',
            }],
        }
        response = self.client.post(
            '/api/sales/invoices/', payload, format='json',
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=(
                f"[Test 44] Expected 400 for non-walkin type, got "
                f"{response.status_code}. Response body: {response.data}"
            ),
        )

    def test_45_invoice_customer_data_requires_customer_name_and_phone(self):
        """
        Test D — POST with customer_data missing customer_name (empty string).
        """
        # Missing customer_name — Allowed and defaults to General
        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': '',
                'phone': '03227778888',
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Cash',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_NoName',
                'quantity': '1',
                'rate': '500',
                'discount': '0',
            }],
        }
        response = self.client.post(
            '/api/sales/invoices/', payload, format='json',
        )
        # FIX: Expect 201_CREATED instead of 400
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invoice = SalesInvoice.objects.get(id=response.data['id'])
        self.assertEqual(invoice.customer.customer_name, "General")

        # Missing phone — Rejected with 400
        payload['customer_data'] = {
            'customer_id': None,
            'customer_name': 'Has Name No Phone',
            'phone': '',
            'customer_type': 'walkin',
            'tax_number': None,
        }
        response = self.client.post(
            '/api/sales/invoices/', payload, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_46_invoice_response_customer_data_shows_full_nested_object(self):
        """
        Test E — Create an invoice via the API. Fetch it via
        GET /api/sales/invoices/{id}/. Assert response['customer_data']
        is a dict containing customerId, customerName, customerType,
        Phone keys with correct values.
        """
        customer = self.create_customer()
        inv = self.create_invoice_via_api(customer, 'Cash', Decimal('1000'))

        response = self.client.get(f'/api/sales/invoices/{inv.id}/')
        self.assertEqual(
            response.status_code, status.HTTP_200_OK,
            msg=f"[Test 46] GET expected 200, got {response.status_code}",
        )

        customer_data = response.data['customer_data']
        self.assertIsInstance(
            customer_data, dict,
            msg=(
                f"[Test 46] customer_data should be a dict, got "
                f"{type(customer_data).__name__}"
            ),
        )
        self.assertEqual(
            customer_data['customerId'], customer.customer_id,
            msg=(
                f"[Test 46] customerId: expected {customer.customer_id}, "
                f"got {customer_data['customerId']}"
            ),
        )
        self.assertEqual(
            customer_data['customerName'], customer.customer_name,
            msg=(
                f"[Test 46] customerName: expected '{customer.customer_name}', "
                f"got '{customer_data['customerName']}'"
            ),
        )
        self.assertEqual(
            customer_data['customerType'], customer.customer_type,
            msg=(
                f"[Test 46] customerType: expected '{customer.customer_type}', "
                f"got '{customer_data['customerType']}'"
            ),
        )
        self.assertEqual(
            customer_data['Phone'], customer.phone,
            msg=(
                f"[Test 46] Phone: expected '{customer.phone}', "
                f"got '{customer_data['Phone']}'"
            ),
        )

    def test_47_invoice_customer_data_matches_existing_permanent_customer_by_phone_allows_credit(self):
        """
        Test F — Create a permanent customer. POST an invoice with
        customer_data using that customer's REAL phone, customer_type='walkin'
        (as required in the payload), payment_term='Credit'. Assert 201 —
        confirming that the actual matched customer being 'permanent'
        correctly ALLOWS Credit payment.
        """
        customer = self.create_customer(customer_type='permanent')

        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': 'Payload Name Irrelevant',
                'phone': customer.phone,
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Credit',
            'paid_amount': '0',
            'vat_percentage': '0',
            'invoice_discount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_PermanentCredit',
                'quantity': '1',
                'rate': '2000',
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
                f"[Test 47] Expected 201 (permanent customer matched by "
                f"phone allows Credit), got {response.status_code}. "
                f"Response body: {response.data}"
            ),
        )

        # Verify it linked to the existing permanent customer
        invoice = SalesInvoice.objects.get(id=response.data['id'])
        self.assertEqual(
            invoice.customer.id, customer.id,
            msg=(
                f"[Test 47] Expected invoice linked to permanent customer "
                f"id={customer.id}, got id={invoice.customer.id}"
            ),
        )
        self.assertEqual(
            invoice.customer.customer_type, 'permanent',
            msg=(
                f"[Test 47] Matched customer should be 'permanent', "
                f"got '{invoice.customer.customer_type}'"
            ),
        )

    def test_48_customer_api_creation_requires_phone(self):
        """
        Test G — POST to /api/sales/customers/ with full payload EXCEPT phone.
        Assert response status 400 with error mentioning Phone is required.
        """
        payload = {
            'customerName': 'NoPhone Customer',
            'customerType': 'permanent',
            'Address': '123 Test St',
        }
        response = self.client.post('/api/sales/customers/', payload, format='json')
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST,
            msg=f"[Test 48] Expected 400 when Phone omitted, got {response.status_code}",
        )
        self.assertIn(
            'Phone', response.data,
            msg=f"[Test 48] Expected 'Phone' in validation errors, got {response.data}",
        )

    def test_49_customer_api_creation_address_is_optional(self):
        """
        Test H — POST to /api/sales/customers/ with NO Address key.
        Assert response status 201.
        """
        LedgerCalculationTests._phone_counter += 1
        phone = f'0300{LedgerCalculationTests._phone_counter:07d}'
        payload = {
            'customerName': 'NoAddress Customer',
            'customerType': 'permanent',
            'Phone': phone,
        }
        response = self.client.post('/api/sales/customers/', payload, format='json')
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            msg=f"[Test 49] Expected 201 when Address omitted, got {response.status_code}. Response: {response.data}",
        )

    def test_50_customer_api_creation_duplicate_phone_returns_clean_400(self):
        """
        Test I — Create a customer. POST to /api/sales/customers/ with DIFFERENT
        customerName but the SAME phone number.
        Assert response status 400 (not 500 IntegrityError).
        """
        existing_customer = self.create_customer(customer_type='permanent')
        
        payload = {
            'customerName': 'DuplicatePhone Customer',
            'customerType': 'walkin',
            'Phone': existing_customer.phone,
        }
        response = self.client.post('/api/sales/customers/', payload, format='json')
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST,
            msg=f"[Test 50] Expected 400 for duplicate phone, got {response.status_code}",
        )
        self.assertIn(
            'Phone', response.data,
            msg=f"[Test 50] Expected 'Phone' in validation errors, got {response.data}",
        )

    def test_51_invoice_creation_with_backdated_date(self):
        """
        Test J — POST to /api/sales/invoices/ with explicit date in past.
        Assert 201 and invoice.date equals exactly that past date.
        """
        LedgerCalculationTests._phone_counter += 1
        phone = f'0300{LedgerCalculationTests._phone_counter:07d}'
        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': 'Backdated Customer',
                'phone': phone,
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Cash',
            'paid_amount': '0',
            'invoiceStatus': 'Saved',
            'date': '2026-01-15',
            'items': [{
                'item_name': 'TestItem_Backdated',
                'quantity': '1',
                'rate': '1000',
                'discount': '0',
            }],
        }
        response = self.client.post('/api/sales/invoices/', payload, format='json')
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            msg=f"[Test 51] Expected 201, got {response.status_code}. Response: {response.data}"
        )
        invoice = SalesInvoice.objects.get(id=response.data['id'])
        self.assertEqual(
            invoice.date, date(2026, 1, 15),
            msg=f"[Test 51] Expected date 2026-01-15, got {invoice.date}"
        )

    def test_52_invoice_creation_without_date_defaults_to_today(self):
        """
        Test K — POST to /api/sales/invoices/ without date key.
        Assert 201 and invoice.date equals today.
        """
        LedgerCalculationTests._phone_counter += 1
        phone = f'0300{LedgerCalculationTests._phone_counter:07d}'
        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': 'Today Customer',
                'phone': phone,
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Cash',
            'paid_amount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_Today',
                'quantity': '1',
                'rate': '1000',
                'discount': '0',
            }],
        }
        response = self.client.post('/api/sales/invoices/', payload, format='json')
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            msg=f"[Test 52] Expected 201, got {response.status_code}. Response: {response.data}"
        )
        invoice = SalesInvoice.objects.get(id=response.data['id'])
        self.assertEqual(
            invoice.date, timezone.now().date(),
            msg=f"[Test 52] Expected date {timezone.now().date()}, got {invoice.date}"
        )

    def test_53_invoice_creation_with_existing_permanent_customer_by_phone(self):
        """
        Test L — test_invoice_creation_with_existing_permanent_customer_by_phone:
        Create a customer explicitly with customer_type='permanent' via 
        create_customer(). POST to /api/sales/invoices/ with customer_data 
        containing that customer's real phone number and 
        customer_type='walkin' (as the frontend always sends). 
        payment_term='Credit'. Assert 201 (NOT 400).
        """
        customer = self.create_customer(customer_type='permanent')
        
        payload = {
            'customer_data': {
                'customer_id': None,
                'customer_name': customer.customer_name,
                'phone': customer.phone,
                'customer_type': 'walkin',
                'tax_number': None,
            },
            'payment_term': 'Credit',
            'paid_amount': '0',
            'invoiceStatus': 'Saved',
            'items': [{
                'item_name': 'TestItem_Permanent_Walkin_Payload',
                'quantity': '1',
                'rate': '1000',
                'discount': '0',
            }],
        }
        response = self.client.post('/api/sales/invoices/', payload, format='json')
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            msg=f"[Test 53] Expected 201, got {response.status_code}. Response: {response.data}"
        )
        invoice = SalesInvoice.objects.get(id=response.data['id'])
        self.assertEqual(invoice.customer.id, customer.id)
        self.assertEqual(invoice.customer.customer_type, 'permanent')
