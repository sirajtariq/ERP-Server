from decimal import Decimal
from datetime import date
from django.test import TestCase
from rest_framework.test import APIClient

from erp_backend.models import User
from sales.models import Customer, SalesInvoice, SalesItem, PaymentReceived
from sales.serializers import PaymentReceivedSerializer


class DashboardCardsAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username='dashboardadmin',
            email='admin@dashboard.com',
            password='password123',
        )
        self.client.force_authenticate(user=self.user)

    def test_dashboard_customer_advance_and_receivable_realtime(self):
        """Verify dynamic customer_advance and receivable calculations on Dashboard Cards API."""
        customer = Customer.objects.create(
            customer_name="Dashboard Test Customer",
            customer_type="permanent",
            phone="03001112233",
        )

        # 1. Add payment of 80,480 -> advance = 80,480
        pay_serializer = PaymentReceivedSerializer(data={
            "customer": customer.id,
            "amount_received": "80480.00",
            "method": "Bank Transfer",
        })
        self.assertTrue(pay_serializer.is_valid(), pay_serializer.errors)
        pay_serializer.save()

        # Fetch dashboard metrics
        resp = self.client.get('/api/dashboard/cards/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['customer_advance'], 80480.0)
        self.assertEqual(resp.data['receivable'], 0.0)

        # 2. Add invoice of 7,680 (using 7,680 advance) -> advance becomes 72,800
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
            item_name="Item 1",
            quantity=Decimal("1"),
            rate=Decimal("7680.00"),
            discount=Decimal("0.00"),
        )

        # Refresh dashboard metrics
        resp = self.client.get('/api/dashboard/cards/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['customer_advance'], 72800.0)
        self.assertEqual(resp.data['receivable'], 0.0)

        # 3. Add second invoice of 72,800 (using remaining advance) -> advance becomes 0.0
        invoice2 = SalesInvoice.objects.create(
            customer=customer,
            payment_term="Credit",
            paid_amount=Decimal("0.00"),
            advance_applied=Decimal("72800.00"),
            status="Saved",
            date=date.today(),
        )
        SalesItem.objects.create(
            invoice=invoice2,
            item_name="Item 2",
            quantity=Decimal("1"),
            rate=Decimal("72800.00"),
            discount=Decimal("0.00"),
        )

        # Refresh dashboard metrics -> customer_advance MUST immediately reflect 0.0
        resp = self.client.get('/api/dashboard/cards/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['customer_advance'], 0.0)
        self.assertEqual(resp.data['receivable'], 0.0)

