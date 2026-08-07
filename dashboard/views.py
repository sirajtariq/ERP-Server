import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from sales.models import SalesInvoice, Customer, PaymentReceived, SalesReturn
from purchase.models import PurchaseInvoice, Vendor, VendorPayment, Expense
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

date_params = [
    openapi.Parameter('from_date', openapi.IN_QUERY, description="Start date (YYYY-MM-DD)", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE),
    openapi.Parameter('to_date', openapi.IN_QUERY, description="End date (YYYY-MM-DD)", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE),
]


class DashboardCardsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get Dashboard Cards Metrics",
        manual_parameters=date_params
    )
    def get(self, request):
        from_date = request.query_params.get('from_date')
        to_date = request.query_params.get('to_date')

        # Base querysets
        sales_qs = SalesInvoice.objects.filter(status='Saved')
        purchase_qs = PurchaseInvoice.objects.filter(status='Saved')
        expense_qs = Expense.objects.all()
        payment_received_qs = PaymentReceived.objects.all()
        vendor_payment_qs = VendorPayment.objects.all()
        sales_return_qs = SalesReturn.objects.filter(status='Saved')

        if from_date:
            sales_qs = sales_qs.filter(date__gte=from_date)
            purchase_qs = purchase_qs.filter(date__gte=from_date)
            expense_qs = expense_qs.filter(date__gte=from_date)
            payment_received_qs = payment_received_qs.filter(date__gte=from_date)
            vendor_payment_qs = vendor_payment_qs.filter(date__gte=from_date)
            sales_return_qs = sales_return_qs.filter(return_date__gte=from_date)
            
        if to_date:
            sales_qs = sales_qs.filter(date__lte=to_date)
            purchase_qs = purchase_qs.filter(date__lte=to_date)
            expense_qs = expense_qs.filter(date__lte=to_date)
            payment_received_qs = payment_received_qs.filter(date__lte=to_date)
            vendor_payment_qs = vendor_payment_qs.filter(date__lte=to_date)
            sales_return_qs = sales_return_qs.filter(return_date__lte=to_date)

        # Calculate properties in python (since net_total is a property)
        # For a robust enterprise app with 1M+ rows we would annotate DB directly, 
        # but for this structure utilizing properties is accurate and suitable.
        sales_invoices = list(sales_qs.prefetch_related('items'))
        purchase_invoices = list(purchase_qs.prefetch_related('items'))
        sales_returns = list(sales_return_qs.prefetch_related('items'))

        total_sales = sum((inv.net_total for inv in sales_invoices), Decimal('0.00'))
        
        cash_sales = sum((inv.net_total for inv in sales_invoices if inv.payment_term == 'Cash'), Decimal('0.00'))
        credit_sales = sum((inv.net_total for inv in sales_invoices if inv.payment_term == 'Credit'), Decimal('0.00'))

        total_purchases = sum((inv.net_total for inv in purchase_invoices), Decimal('0.00'))
        
        total_sales_returns = sum((ret.net_return_amount for ret in sales_returns), Decimal('0.00'))

        outgoing_expense = expense_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        profit = (total_sales - total_sales_returns) - (total_purchases + outgoing_expense)

        incoming_cash = payment_received_qs.aggregate(total=Sum('amount_received'))['total'] or Decimal('0.00')

        supplier_paid = vendor_payment_qs.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')

        # Receivable & Payable (Current total balance, irrespective of dates)
        receivable = Customer.objects.aggregate(total=Sum('credit_balance'))['total'] or Decimal('0.00')
        customer_advance = Customer.objects.aggregate(total=Sum('advance_balance'))['total'] or Decimal('0.00')
        
        supplier_payable = Vendor.objects.aggregate(total=Sum('payable_balance'))['total'] or Decimal('0.00')
        vendor_advance = Vendor.objects.aggregate(total=Sum('advance_balance'))['total'] or Decimal('0.00')

        return Response({
            'total_sales': float(total_sales),
            'total_purchases': float(total_purchases),
            'receivable': float(receivable),
            'customer_advance': float(customer_advance),
            'profit': float(profit),
            'cash_sales': float(cash_sales),
            'credit_sales': float(credit_sales),
            'total_sales_returns': float(total_sales_returns),
            'outgoing_expense': float(outgoing_expense),
            'total_expenses': float(outgoing_expense),  # Added alias for clarity
            'supplier_payable': float(supplier_payable),
            'vendor_advance': float(vendor_advance),
            'supplier_paid': float(supplier_paid),
            'total_vendor_payments': float(supplier_paid),
            'total_cash_outflow': float(outgoing_expense + supplier_paid),
            'incoming_cash': float(incoming_cash),
        })


class DashboardChartsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get Dashboard Charts Data",
        manual_parameters=date_params
    )
    def get(self, request):
        from_date_str = request.query_params.get('from_date')
        to_date_str = request.query_params.get('to_date')
        
        today = datetime.date.today()
        
        # If no dates provided, use the earliest record date to get overall data
        if not from_date_str:
            earliest_sales = SalesInvoice.objects.order_by('date').first()
            earliest_purchase = PurchaseInvoice.objects.order_by('date').first()
            earliest_expense = Expense.objects.order_by('date').first()
            
            dates = []
            if earliest_sales and earliest_sales.date: dates.append(earliest_sales.date)
            if earliest_purchase and earliest_purchase.date: dates.append(earliest_purchase.date)
            if earliest_expense and earliest_expense.date: dates.append(earliest_expense.date)
            
            if dates:
                start_date = min(dates).replace(day=1)
            else:
                start_date = today.replace(day=1)
        else:
            try:
                start_date = datetime.datetime.strptime(from_date_str, '%Y-%m-%d').date()
                start_date = start_date.replace(day=1) # normalize to month start
            except ValueError:
                start_date = today.replace(day=1)
            
        if not to_date_str:
            end_date = today
        else:
            try:
                end_date = datetime.datetime.strptime(to_date_str, '%Y-%m-%d').date()
            except ValueError:
                end_date = today

        sales_qs = SalesInvoice.objects.filter(status='Saved', date__gte=start_date, date__lte=end_date).prefetch_related('items')
        purchase_qs = PurchaseInvoice.objects.filter(status='Saved', date__gte=start_date, date__lte=end_date).prefetch_related('items')
        expense_qs = Expense.objects.filter(date__gte=start_date, date__lte=end_date)

        monthly_data = defaultdict(lambda: {'income': Decimal('0.0'), 'expense': Decimal('0.0')})
        
        for inv in sales_qs:
            sort_key = inv.date.strftime('%Y-%m')
            monthly_data[sort_key]['income'] += inv.net_total
            
        for inv in purchase_qs:
            sort_key = inv.date.strftime('%Y-%m')
            monthly_data[sort_key]['expense'] += inv.net_total
            
        for exp in expense_qs:
            sort_key = exp.date.strftime('%Y-%m')
            monthly_data[sort_key]['expense'] += exp.amount
            
        result = []
        current = start_date.replace(day=1)
        
        # Determine the last month's start date
        end_month_start = end_date.replace(day=1)
        
        while current <= end_month_start:
            sort_key = current.strftime('%Y-%m')
            month_key = current.strftime('%b') # e.g. "Jan", "Feb" like in the screenshot
            
            data = monthly_data.get(sort_key, {'income': Decimal('0.0'), 'expense': Decimal('0.0')})
            result.append({
                'month': month_key,
                'income': float(data['income']),
                'expense': float(data['expense'])
            })
            
            # Add one month safely
            next_month = current.month + 1 if current.month < 12 else 1
            next_year = current.year + (1 if current.month == 12 else 0)
            current = current.replace(year=next_year, month=next_month)
            
        return Response(result)
