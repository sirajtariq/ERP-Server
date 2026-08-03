"""
Comprehensive test suite for the Sales Return / Credit Note system.

Covers:
- Return number auto-generation
- Quantity cap validation (single and cumulative)
- Saved lock prevention
- Invoice validation (must be Saved, not deleted)
- Draft → Saved balance effects (credit_balance & advance_balance)
- Cash invoice returns → advance_balance
- Soft delete reversal and restore re-application
- Partial return quantities
- Customer Ledger inclusion of Credit Notes
- Dual refund modes: CASH vs STORE_CREDIT
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from erp_backend.models import User
from sales.models import Customer, SalesInvoice, SalesItem, SalesReturn, SalesReturnItem


class SalesReturnTestBase(TestCase):
    """Shared setUp for all sales return tests."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username='testadmin',
            email='admin@test.com',
            password='password123',
        )
        self.client.force_authenticate(user=self.user)

        # Create a permanent customer with known balances
        self.customer = Customer.objects.create(
            customer_name='Test Customer',
            customer_type='permanent',
            phone='03001111111',
            credit_balance=Decimal('5000.00'),
            advance_balance=Decimal('0.00'),
        )

        # Create a Saved credit invoice with items
        self.invoice = SalesInvoice(
            customer=self.customer,
            payment_term='Credit',
            status='Saved',
            paid_amount=Decimal('0.00'),
            vat_percentage=Decimal('0.00'),
            invoice_discount=Decimal('0.00'),
        )
        self.invoice.save()

        self.item_a = SalesItem.objects.create(
            invoice=self.invoice,
            item_name='Widget A',
            quantity=Decimal('10.00'),
            rate=Decimal('100.00'),
            discount=Decimal('0.00'),
        )
        self.item_b = SalesItem.objects.create(
            invoice=self.invoice,
            item_name='Widget B',
            quantity=Decimal('5.00'),
            rate=Decimal('200.00'),
            discount=Decimal('0.00'),
        )

    def _return_url(self, pk=None):
        if pk:
            return f'/api/sales/returns/{pk}/'
        return '/api/sales/returns/'


class TestReturnNumberGeneration(SalesReturnTestBase):
    """Test auto-generated CN-YYYY-NNNNN return numbers."""

    def test_return_number_auto_generation(self):
        payload = {
            'invoice': self.invoice.id,
            'status': 'Draft',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '1.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['return_number'].startswith('CN-'))
        self.assertEqual(len(response.data['return_number'].split('-')), 3)

    def test_sequential_return_numbers(self):
        for i in range(3):
            payload = {
                'invoice': self.invoice.id,
                'status': 'Draft',
                'items': [
                    {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '1.00', 'rate': '100.00', 'discount': '0.00'},
                ],
            }
            response = self.client.post(self._return_url(), payload, format='json')
            self.assertEqual(response.status_code, 201)

        returns = SalesReturn.objects.order_by('id')
        numbers = [r.return_number for r in returns]
        # Verify sequential numbering
        seqs = [int(n.split('-')[-1]) for n in numbers]
        self.assertEqual(seqs, [1, 2, 3])


class TestInvoiceValidation(SalesReturnTestBase):
    """Test that returns can only be created against valid invoices."""

    def test_return_against_draft_invoice_blocked(self):
        draft_invoice = SalesInvoice(
            customer=self.customer,
            payment_term='Credit',
            status='Draft',
        )
        draft_invoice.save()
        SalesItem.objects.create(
            invoice=draft_invoice,
            item_name='Item X',
            quantity=Decimal('5.00'),
            rate=Decimal('50.00'),
        )

        payload = {
            'invoice': draft_invoice.id,
            'status': 'Draft',
            'items': [
                {'item_name': 'Item X', 'quantity': '1.00', 'rate': '50.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('invoice', response.data)

    def test_return_against_deleted_invoice_blocked(self):
        deleted_invoice = SalesInvoice(
            customer=self.customer,
            payment_term='Credit',
            status='Saved',
        )
        deleted_invoice.save()
        SalesItem.objects.create(
            invoice=deleted_invoice,
            item_name='Item Y',
            quantity=Decimal('3.00'),
            rate=Decimal('100.00'),
        )
        deleted_invoice.soft_delete()

        payload = {
            'invoice': deleted_invoice.id,
            'status': 'Draft',
            'items': [
                {'item_name': 'Item Y', 'quantity': '1.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('invoice', response.data)


class TestQuantityCapValidation(SalesReturnTestBase):
    """Test quantity return limits against original invoice items."""

    def test_quantity_within_limit_succeeds(self):
        payload = {
            'invoice': self.invoice.id,
            'status': 'Draft',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '5.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 201)

    def test_quantity_exceeding_limit_blocked(self):
        payload = {
            'invoice': self.invoice.id,
            'status': 'Draft',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '15.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('items', response.data)

    def test_cumulative_quantity_cap_across_multiple_returns(self):
        """First return 7 of 10, second return should only allow up to 3."""
        # First return: 7 units (Saved so it counts)
        payload1 = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '7.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r1 = self.client.post(self._return_url(), payload1, format='json')
        self.assertEqual(r1.status_code, 201)

        # Second return: try 5 more (should fail, only 3 available)
        payload2 = {
            'invoice': self.invoice.id,
            'status': 'Draft',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '5.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r2 = self.client.post(self._return_url(), payload2, format='json')
        self.assertEqual(r2.status_code, 400)
        self.assertIn('items', r2.data)

        # Third return: 3 units should succeed
        payload3 = {
            'invoice': self.invoice.id,
            'status': 'Draft',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '3.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r3 = self.client.post(self._return_url(), payload3, format='json')
        self.assertEqual(r3.status_code, 201)

    def test_partial_return_quantity(self):
        """Return partial items from an invoice."""
        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '3.00', 'rate': '100.00', 'discount': '0.00'},
                {'sales_item': self.item_b.id, 'item_name': 'Widget B', 'quantity': '2.00', 'rate': '200.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 201)

        sr = SalesReturn.objects.get(id=response.data['id'])
        # 3*100 + 2*200 = 300 + 400 = 700
        self.assertEqual(sr.net_return_amount, Decimal('700.00'))


class TestSavedLock(SalesReturnTestBase):
    """Test that Saved returns are completely locked from editing."""

    def test_put_on_saved_return_blocked(self):
        # Create a Saved return
        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '1.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(r.status_code, 201)

        # Attempt PUT update
        update_payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'reason': 'Updated reason',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '1.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r2 = self.client.put(self._return_url(r.data['id']), update_payload, format='json')
        self.assertEqual(r2.status_code, 400)
        self.assertIn('status', r2.data)

    def test_patch_on_saved_return_blocked(self):
        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '1.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(r.status_code, 201)

        r2 = self.client.patch(self._return_url(r.data['id']), {'reason': 'changed'}, format='json')
        self.assertEqual(r2.status_code, 400)


class TestBalanceEffects(SalesReturnTestBase):
    """Test Draft→Saved balance transitions and accounting side-effects."""

    def test_create_draft_return_no_balance_change(self):
        original_credit = self.customer.credit_balance
        original_advance = self.customer.advance_balance

        payload = {
            'invoice': self.invoice.id,
            'status': 'Draft',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '5.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 201)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, original_credit)
        self.assertEqual(self.customer.advance_balance, original_advance)

    def test_saved_return_reduces_credit_balance(self):
        """Customer has credit_balance=5000. Return of 500 should reduce to 4500."""
        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '5.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 201)

        self.customer.refresh_from_db()
        # net_return_amount = 5 * 100 = 500
        self.assertEqual(self.customer.credit_balance, Decimal('4500.00'))
        self.assertEqual(self.customer.advance_balance, Decimal('0.00'))

        # Check stored split
        sr = SalesReturn.objects.get(id=response.data['id'])
        self.assertEqual(sr.applied_to_credit, Decimal('500.00'))
        self.assertEqual(sr.applied_to_advance, Decimal('0.00'))

    def test_saved_return_overflow_to_advance(self):
        """Return amount exceeds credit_balance → remainder goes to advance."""
        # Set credit_balance to something small
        self.customer.credit_balance = Decimal('200.00')
        self.customer.save(update_fields=['credit_balance'])

        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'items': [
                # 5 * 100 = 500 return amount; credit is only 200
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '5.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 201)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('0.00'))
        self.assertEqual(self.customer.advance_balance, Decimal('300.00'))

        sr = SalesReturn.objects.get(id=response.data['id'])
        self.assertEqual(sr.applied_to_credit, Decimal('200.00'))
        self.assertEqual(sr.applied_to_advance, Decimal('300.00'))

    def test_cash_invoice_return_goes_to_advance(self):
        """Return against Cash invoice → full amount goes to advance_balance."""
        # Create a Cash invoice
        cash_invoice = SalesInvoice(
            customer=self.customer,
            payment_term='Cash',
            status='Saved',
            paid_amount=Decimal('500.00'),
            vat_percentage=Decimal('0.00'),
            invoice_discount=Decimal('0.00'),
        )
        cash_invoice.save()
        cash_item = SalesItem.objects.create(
            invoice=cash_invoice,
            item_name='Cash Widget',
            quantity=Decimal('5.00'),
            rate=Decimal('100.00'),
            discount=Decimal('0.00'),
        )

        # Set customer credit to 0 (Cash invoice wouldn't have created credit)
        self.customer.credit_balance = Decimal('0.00')
        self.customer.save(update_fields=['credit_balance'])

        payload = {
            'invoice': cash_invoice.id,
            'status': 'Saved',
            'items': [
                {'sales_item': cash_item.id, 'item_name': 'Cash Widget', 'quantity': '3.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 201)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('0.00'))
        self.assertEqual(self.customer.advance_balance, Decimal('300.00'))


class TestSoftDeleteAndRestore(SalesReturnTestBase):
    """Test soft-delete reversal and restore re-application of balances."""

    def test_soft_delete_reverses_balances(self):
        """Trashing a Saved return should restore credit_balance."""
        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '5.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(r.status_code, 201)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('4500.00'))

        # Now trash it
        r2 = self.client.delete(self._return_url(r.data['id']))
        self.assertEqual(r2.status_code, 200)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('5000.00'))
        self.assertEqual(self.customer.advance_balance, Decimal('0.00'))

    def test_restore_reapplies_balances(self):
        """Restoring a trashed Saved return should re-apply balance effects."""
        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '5.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r = self.client.post(self._return_url(), payload, format='json')
        sr_id = r.data['id']

        # Trash it
        self.client.delete(self._return_url(sr_id))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('5000.00'))

        # Restore it
        r3 = self.client.post(f'/api/sales/returns/{sr_id}/restore/')
        self.assertEqual(r3.status_code, 200)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('4500.00'))

    def test_soft_delete_draft_no_balance_change(self):
        """Trashing a Draft return should NOT change balances."""
        original_credit = self.customer.credit_balance

        payload = {
            'invoice': self.invoice.id,
            'status': 'Draft',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '5.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(r.status_code, 201)

        r2 = self.client.delete(self._return_url(r.data['id']))
        self.assertEqual(r2.status_code, 200)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, original_credit)


class TestLedgerIntegration(SalesReturnTestBase):
    """Test that Credit Notes appear correctly in the Customer Ledger."""

    def test_ledger_includes_credit_note(self):
        # Create a Saved return
        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '2.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(r.status_code, 201)

        sr = SalesReturn.objects.get(id=r.data['id'])

        # Fetch ledger
        ledger_url = f'/api/sales/customers/{self.customer.customer_id}/ledger/'
        lr = self.client.get(ledger_url)
        self.assertEqual(lr.status_code, 200)

        ledger_rows = lr.data['ledger']

        # Find the credit note entry
        cn_rows = [row for row in ledger_rows if row['referenceType'] == 'sales_return']
        self.assertEqual(len(cn_rows), 1)
        self.assertEqual(cn_rows[0]['voucher'], sr.return_number)
        self.assertEqual(cn_rows[0]['description'], 'Sales Return / Credit Note')
        self.assertEqual(Decimal(str(cn_rows[0]['credit'])), Decimal('200.00'))
        self.assertEqual(Decimal(str(cn_rows[0]['debit'])), Decimal('0.00'))

    def test_trashed_return_excluded_from_ledger(self):
        """Deleted returns should NOT appear in the ledger."""
        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '2.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r = self.client.post(self._return_url(), payload, format='json')
        sr_id = r.data['id']

        # Trash it
        self.client.delete(self._return_url(sr_id))

        # Fetch ledger
        ledger_url = f'/api/sales/customers/{self.customer.customer_id}/ledger/'
        lr = self.client.get(ledger_url)
        self.assertEqual(lr.status_code, 200)

        cn_rows = [row for row in lr.data['ledger'] if row['referenceType'] == 'sales_return']
        self.assertEqual(len(cn_rows), 0)


class TestRefundTypes(SalesReturnTestBase):
    """Test dual refund mode behavior: CASH vs STORE_CREDIT."""

    def test_cash_refund_leaves_balances_untouched(self):
        """CASH refund should NOT alter credit_balance or advance_balance."""
        original_credit = self.customer.credit_balance
        original_advance = self.customer.advance_balance

        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'refund_type': 'CASH',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '5.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 201)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, original_credit)
        self.assertEqual(self.customer.advance_balance, original_advance)

        # Verify applied fields are zero
        sr = SalesReturn.objects.get(id=response.data['id'])
        self.assertEqual(sr.applied_to_credit, Decimal('0.00'))
        self.assertEqual(sr.applied_to_advance, Decimal('0.00'))
        self.assertEqual(sr.refund_type, 'CASH')

    def test_store_credit_refund_deducts_balances(self):
        """STORE_CREDIT refund should reduce credit_balance and/or increase advance."""
        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'refund_type': 'STORE_CREDIT',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '5.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 201)

        self.customer.refresh_from_db()
        # 5 * 100 = 500 return, credit was 5000 → should be 4500
        self.assertEqual(self.customer.credit_balance, Decimal('4500.00'))
        self.assertEqual(self.customer.advance_balance, Decimal('0.00'))

        sr = SalesReturn.objects.get(id=response.data['id'])
        self.assertEqual(sr.applied_to_credit, Decimal('500.00'))
        self.assertEqual(sr.applied_to_advance, Decimal('0.00'))
        self.assertEqual(sr.refund_type, 'STORE_CREDIT')

    def test_cash_refund_soft_delete_no_balance_change(self):
        """Trashing a CASH refund Saved return should NOT change balances."""
        original_credit = self.customer.credit_balance
        original_advance = self.customer.advance_balance

        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'refund_type': 'CASH',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '3.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(r.status_code, 201)

        # Trash it
        r2 = self.client.delete(self._return_url(r.data['id']))
        self.assertEqual(r2.status_code, 200)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, original_credit)
        self.assertEqual(self.customer.advance_balance, original_advance)

    def test_store_credit_soft_delete_reverses_balances(self):
        """Trashing a STORE_CREDIT Saved return should restore balances."""
        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'refund_type': 'STORE_CREDIT',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '5.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(r.status_code, 201)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('4500.00'))

        # Trash it — should reverse to 5000
        r2 = self.client.delete(self._return_url(r.data['id']))
        self.assertEqual(r2.status_code, 200)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('5000.00'))
        self.assertEqual(self.customer.advance_balance, Decimal('0.00'))

    def test_cash_refund_restore_no_balance_change(self):
        """Restoring a trashed CASH return should NOT change balances."""
        original_credit = self.customer.credit_balance
        original_advance = self.customer.advance_balance

        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'refund_type': 'CASH',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '2.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        r = self.client.post(self._return_url(), payload, format='json')
        sr_id = r.data['id']

        # Trash it
        self.client.delete(self._return_url(sr_id))

        # Restore it
        r3 = self.client.post(f'/api/sales/returns/{sr_id}/restore/')
        self.assertEqual(r3.status_code, 200)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, original_credit)
        self.assertEqual(self.customer.advance_balance, original_advance)

    def test_default_refund_type_is_store_credit(self):
        """When refund_type is not specified, it should default to STORE_CREDIT."""
        payload = {
            'invoice': self.invoice.id,
            'status': 'Saved',
            'items': [
                {'sales_item': self.item_a.id, 'item_name': 'Widget A', 'quantity': '1.00', 'rate': '100.00', 'discount': '0.00'},
            ],
        }
        response = self.client.post(self._return_url(), payload, format='json')
        self.assertEqual(response.status_code, 201)

        sr = SalesReturn.objects.get(id=response.data['id'])
        self.assertEqual(sr.refund_type, 'STORE_CREDIT')

        # Balance should be affected (credit reduced by 100)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('4900.00'))
