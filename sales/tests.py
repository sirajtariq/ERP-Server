from decimal import Decimal
from django.contrib.auth.models import User, Group
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from erp_backend.permissions import SALES_GROUP
from sales.models import Customer, SalesInvoice, SalesItem

class SalesModuleTests(APITestCase):
    def setUp(self):
        # Create Sales group and a Sales person
        self.sales_group, _ = Group.objects.get_or_create(name=SALES_GROUP)
        self.sales_user = User.objects.create_user(
            username='sales_person',
            email='sales_person@example.com',
            password='password123'
        )
        self.sales_user.groups.add(self.sales_group)
        self.client.force_authenticate(user=self.sales_user)

        # Create a standard customer
        self.customer = Customer.objects.create(
            customer_name="John Doe",
            phone="03001234567",
            opening_credit=Decimal('100.00'),
            opening_note="Initial debt"
        )

    def test_invoice_calculations(self):
        """Test properties like subtotal, tax_amount, net_total, and balance_due on SalesInvoice."""
        invoice = SalesInvoice.objects.create(
            customer=self.customer,
            payment_term='Credit',
            vat_percentage=Decimal('10.00'),
            invoice_discount=Decimal('50.00'),
            paid_amount=Decimal('100.00'),
            status='Saved'
        )

        item1 = SalesItem.objects.create(
            invoice=invoice,
            item_name="Item 1",
            quantity=Decimal('2.00'),
            rate=Decimal('200.00'),
            discount=Decimal('10.00')
        )  # total = 2*200 - 10 = 390.00

        item2 = SalesItem.objects.create(
            invoice=invoice,
            item_name="Item 2",
            quantity=Decimal('1.00'),
            rate=Decimal('150.00'),
            discount=Decimal('0.00')
        )  # total = 150.00

        # subtotal = 390.00 + 150.00 = 540.00
        self.assertEqual(invoice.subtotal, Decimal('540.00'))
        
        # total_line_discount = 10.00
        self.assertEqual(invoice.total_line_discount, Decimal('10.00'))

        # tax_amount = (540.00 - 50.00) * 10% = 490.00 * 0.10 = 49.00
        self.assertEqual(invoice.tax_amount, Decimal('49.00'))

        # net_total = (540.00 - 50.00) + 49.00 = 539.00
        self.assertEqual(invoice.net_total, Decimal('539.00'))

        # balance_due = 539.00 - 100.00 = 439.00
        self.assertEqual(invoice.balance_due, Decimal('439.00'))

    def test_walk_in_customer_validation(self):
        """Verify the validation checks in the SalesInvoiceSerializer for walk-in vs. normal customers."""
        url = reverse('sales-invoice-list')

        # 1. Walk-in customer with Credit payment term should fail
        data = {
            "customer": None,
            "walk_in_customer_name": "Walk-in Buyer",
            "payment_term": "Credit",
            "vat_percentage": "0.00",
            "invoice_discount": "0.00",
            "paid_amount": "0.00",
            "status": "Saved",
            "items": [{"item_name": "USB Drive", "quantity": "2", "rate": "15"}]
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Walk-in customers can only pay via Cash.", response.data['non_field_errors'][0])

        # 2. Walk-in customer with empty name and customer=None should fail
        data["payment_term"] = "Cash"
        data["walk_in_customer_name"] = ""
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Either a customer or walk-in name is required.", response.data['non_field_errors'][0])

        # 3. Providing both customer and walk_in_customer_name should fail
        data["customer"] = self.customer.id
        data["walk_in_customer_name"] = "Walk-in Buyer"
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Provide either a customer or walk-in name, not both.", response.data['non_field_errors'][0])

        # 4. Valid walk-in cash customer should succeed
        data["customer"] = None
        data["walk_in_customer_name"] = "Walk-in Buyer"
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_customer_balance_lifecycle(self):
        """Verify that customer balances (credit & advance) update correctly through the CRUD lifecycle of invoices and items."""
        # Refresh and check initial balance (which should match opening_credit)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('100.00'))
        self.assertEqual(self.customer.advance_balance, Decimal('0.00'))

        # 1. Create a Draft Credit invoice. Stored balance should NOT change.
        invoice1 = SalesInvoice.objects.create(
            customer=self.customer,
            payment_term='Credit',
            paid_amount=Decimal('50.00'),
            status='Draft'
        )
        SalesItem.objects.create(invoice=invoice1, item_name="Widgets", quantity=Decimal('10'), rate=Decimal('15.00'))
        # net_total = 150.00, balance_due = 100.00
        
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('100.00')) # Unchanged because status='Draft'

        # 2. Update status to Saved. Balance should update.
        invoice1.status = 'Saved'
        invoice1.save()
        
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('200.00')) # 100 opening + 100 balance_due

        # 3. Create another Saved Credit invoice. Balance should update.
        invoice2 = SalesInvoice.objects.create(
            customer=self.customer,
            payment_term='Credit',
            paid_amount=Decimal('0.00'),
            status='Saved'
        )
        SalesItem.objects.create(invoice=invoice2, item_name="Gizmos", quantity=Decimal('5'), rate=Decimal('20.00'))
        # net_total = 100.00, balance_due = 100.00
        
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('300.00')) # 200 + 100

        # 4. Modify line item of invoice2. Balance should update.
        item = invoice2.items.first()
        item.rate = Decimal('30.00')
        item.save() # net_total changes to 150.00, balance_due to 150.00
        
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('350.00')) # 200 + 150

        # 5. Overpay invoice2 to test advance balance.
        invoice2.paid_amount = Decimal('500.00') # net_total 150.00, paid 500.00 -> balance_due -350.00
        invoice2.save()

        self.customer.refresh_from_db()
        # total outstanding = 100 opening + 100 (invoice1 due) - 350 (invoice2 overpaid) = -150.00
        self.assertEqual(self.customer.credit_balance, Decimal('0.00'))
        self.assertEqual(self.customer.advance_balance, Decimal('150.00'))

        # 6. Delete invoice2. Balances should reset back to invoice1 + opening.
        invoice2.delete()
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal('200.00'))
        self.assertEqual(self.customer.advance_balance, Decimal('0.00'))

    def test_customer_ledger_api(self):
        """Test the customer ledger endpoint response and balances."""
        invoice = SalesInvoice.objects.create(
            customer=self.customer,
            payment_term='Credit',
            paid_amount=Decimal('40.00'),
            status='Saved'
        )
        SalesItem.objects.create(invoice=invoice, item_name="Service Call", quantity=1, rate=Decimal('100.00'))
        # net = 100.00, paid = 40.00, balance_due = 60.00

        url = reverse('customer-ledger', kwargs={'customer_id': self.customer.customer_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        summary = data['summary']
        ledger = data['ledger']
        final_details = data['finalPaymentDetails']

        # Assert balances in response are correct and match database
        self.customer.refresh_from_db()
        self.assertEqual(summary['remainingBalance'], float(self.customer.credit_balance))
        self.assertEqual(summary['closingBalance'], float(self.customer.credit_balance))
        self.assertEqual(final_details['remainingBalance'], float(self.customer.credit_balance))

        # Check ledger entries (opening balance + invoice debit + payment credit)
        self.assertEqual(len(ledger), 3)
        self.assertEqual(ledger[0]['voucher'], 'OPENING')
        self.assertEqual(ledger[0]['debit'], 100.00)
        self.assertEqual(ledger[0]['balance'], 100.00)

        # Invoice debit
        self.assertEqual(ledger[1]['debit'], 100.00)
        self.assertEqual(ledger[1]['balance'], 200.00)

        # Payment credit
        self.assertEqual(ledger[2]['credit'], 40.00)
        self.assertEqual(ledger[2]['balance'], 160.00)
