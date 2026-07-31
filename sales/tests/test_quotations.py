from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from erp_backend.models import User
from sales.models import Customer, Quotation, QuotationItem, SalesInvoice, SalesItem


class QuotationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        # Assume there's a user factory or we can just create a superuser for tests
        self.user = User.objects.create_superuser(
            username='testadmin',
            email='admin@test.com',
            password='password123'
        )
        self.client.force_authenticate(user=self.user)
        
        self.customer_data = {
            "customer_name": "John Doe",
            "phone": "03001234567",
            "customer_type": "walkin"
        }

    def test_create_quotation(self):
        payload = {
            "customer_data": self.customer_data,
            "valid_days": 15,
            "payment_term": "cash",
            "discount_percentage": "10.00",
            "vat_percentage": "15.00",
            "items": [
                {
                    "item_name": "Item A",
                    "unit": "pcs",
                    "qty": "10.00",
                    "rate": "100.00",
                    "discount": "5.00"
                },
                {
                    "item_name": "Item B",
                    "unit": "pcs",
                    "qty": "5.00",
                    "rate": "200.00",
                    "discount": "0.00"
                }
            ]
        }
        
        response = self.client.post('/api/sales/quotations/', payload, format='json')
        self.assertEqual(response.status_code, 201)
        
        quotation = Quotation.objects.get(id=response.data['id'])
        
        # Verify subtotal, discounts, and total
        # Item 1: (10 * 100) - 5% = 950
        # Item 2: (5 * 200) - 0% = 1000
        # Subtotal: 1950
        self.assertEqual(quotation.subtotal, Decimal('1950.00'))
        
        # Header discount 10%: 1950 * 0.1 = 195
        self.assertEqual(quotation.discount_amount, Decimal('195.00'))
        
        # VAT 15% on (1950 - 195) = 1755 * 0.15 = 263.25
        self.assertEqual(quotation.vat_amount, Decimal('263.25'))
        
        # Total: 1950 - 195 + 263.25 = 2018.25
        self.assertEqual(quotation.total, Decimal('2018.25'))
        
        # Verify validity date logic
        expected_valid_until = timezone.localdate() + timedelta(days=15)
        self.assertEqual(quotation.valid_until, expected_valid_until)

    def test_convert_to_invoice(self):
        # Create a quotation first
        quotation = Quotation.objects.create(
            customer_data=self.customer_data,
            discount_percentage=Decimal("10.00"),
            vat_percentage=Decimal("15.00")
        )
        QuotationItem.objects.create(
            quotation=quotation,
            item_name="Test Item",
            unit="kg",
            qty=Decimal("2.00"),
            rate=Decimal("50.00"),
            discount=Decimal("0.00")
        )
        
        response = self.client.post(f'/api/sales/quotations/{quotation.id}/convert/')
        self.assertEqual(response.status_code, 201)
        
        # Check that the quotation is converted
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, 'converted')
        self.assertIsNotNone(quotation.converted_invoice)
        
        # Check that an invoice was created correctly
        invoice = quotation.converted_invoice
        self.assertEqual(invoice.status, 'Draft')
        self.assertEqual(invoice.invoice_discount, Decimal("10.00"))
        self.assertEqual(invoice.vat_percentage, Decimal("15.00"))
        
        # Check that customer was generated
        self.assertEqual(invoice.customer.phone, "03001234567")
        self.assertEqual(invoice.customer.customer_name, "John Doe")
        self.assertEqual(invoice.customer.customer_type, "walkin")
        
        # Check items were mapped correctly
        self.assertEqual(invoice.items.count(), 1)
        invoice_item = invoice.items.first()
        self.assertEqual(invoice_item.item_name, "Test Item")
        self.assertEqual(invoice_item.units, "kg")
        self.assertEqual(invoice_item.quantity, Decimal("2.00"))

    def test_convert_blocked_on_already_converted(self):
        quotation = Quotation.objects.create(
            customer_data=self.customer_data,
            status='converted'
        )
        response = self.client.post(f'/api/sales/quotations/{quotation.id}/convert/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], "Quotation is already converted.")

    def test_convert_blocked_on_expired(self):
        past_date = timezone.localdate() - timedelta(days=10)
        quotation = Quotation.objects.create(
            customer_data=self.customer_data,
            date=past_date,
            valid_days=5,
            status='draft'
        )
        response = self.client.post(f'/api/sales/quotations/{quotation.id}/convert/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], "This quotation has expired and cannot be converted.")

    def test_soft_delete_blocked_on_converted(self):
        quotation = Quotation.objects.create(
            customer_data=self.customer_data,
            status='converted'
        )
        response = self.client.delete(f'/api/sales/quotations/{quotation.id}/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], "Converted quotations cannot be deleted.")

    def test_validity_display(self):
        today = timezone.localdate()
        
        # 1. No Expiry
        q1 = Quotation(valid_days=None)
        self.assertEqual(q1.validity_display, "No Expiry")
        
        # 2. Expired
        q2 = Quotation(valid_until=today - timedelta(days=5), status='draft')
        self.assertEqual(q2.validity_display, "Expired")
        
        # 3. Expires today
        q3 = Quotation(valid_until=today, status='draft')
        self.assertEqual(q3.validity_display, "Expires today")
        
        # 4. N days left
        q4 = Quotation(valid_until=today + timedelta(days=3), status='draft')
        self.assertEqual(q4.validity_display, "3 days left")
