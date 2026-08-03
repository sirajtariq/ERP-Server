"""
Management command to seed sales returns / credit notes.

Picks existing Saved invoices and creates partial returns with a mix of
refund types (50% CASH, 50% STORE_CREDIT) and statuses (30% Draft, 70% Saved).
Uses serializers to trigger proper ledger and balance updates.

Usage:
    uv run python manage.py seed_returns
    uv run python manage.py seed_returns --count 15
    uv run python manage.py seed_returns --clear
"""

import random
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from sales.models import Customer, SalesInvoice, SalesItem, SalesReturn, SalesReturnItem

RETURN_REASONS = [
    "Defective product received",
    "Wrong item shipped",
    "Customer changed mind",
    "Product damaged during delivery",
    "Quality not as expected",
    "Duplicate order",
    "Size/specification mismatch",
    "Expired product",
]

RETURN_NOTES = [
    "Customer brought items back in original packaging",
    "Partial return — remaining items accepted",
    "Replacement will be issued separately",
    "Refund processed at counter",
    "Credit applied to next order",
    "",
    "",
    "",
]


class Command(BaseCommand):
    help = "Seed the database with sales return / credit note mock data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=12,
            help="Number of returns to create (default: 12)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing returns and return items before seeding",
        )

    def handle(self, *args, **options):
        count = options["count"]
        clear = options["clear"]

        if clear:
            items_deleted = SalesReturnItem.all_objects.all().delete()[0]
            returns_deleted = SalesReturn.all_objects.all().delete()[0]
            self.stdout.write(
                self.style.WARNING(
                    f"Deleted {returns_deleted} returns and {items_deleted} return items."
                )
            )

        existing = SalesReturn.objects.count()
        if existing >= 5 and not clear:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Already {existing} returns in DB. Skipping seed. "
                    f"Use --clear to reset."
                )
            )
            return

        # Only pick Saved, non-deleted invoices that have items
        saved_invoices = list(
            SalesInvoice.objects.filter(status='Saved')
            .prefetch_related('items')
            .order_by('?')
        )

        if not saved_invoices:
            self.stdout.write(
                self.style.ERROR(
                    "No Saved invoices found. Run 'seed_invoices' first."
                )
            )
            return

        # Track previously returned quantities per sales_item to respect caps
        returned_qty_map = {}  # sales_item.id -> Decimal

        created_returns = 0
        created_items = 0

        for i in range(count):
            invoice = random.choice(saved_invoices)
            invoice_items = list(invoice.items.all())

            if not invoice_items:
                continue

            # Pick a random subset of items to return (1 to all)
            num_return_items = random.randint(1, min(3, len(invoice_items)))
            chosen_items = random.sample(invoice_items, num_return_items)

            # Determine refund_type: 50/50 CASH vs STORE_CREDIT
            refund_type = random.choice(['CASH', 'STORE_CREDIT'])

            # Determine status: 30% Draft, 70% Saved
            status = 'Draft' if random.random() < 0.30 else 'Saved'

            reason = random.choice(RETURN_REASONS)
            notes = random.choice(RETURN_NOTES)

            try:
                with transaction.atomic():
                    # Build return items with quantity caps
                    return_items_data = []
                    valid_return = True

                    for sales_item in chosen_items:
                        # Calculate available quantity
                        prev_returned_db = Decimal('0.00')
                        existing_returns = SalesReturnItem.objects.filter(
                            sales_item=sales_item,
                            sales_return__status='Saved',
                            sales_return__is_deleted=False,
                        ).values_list('quantity', flat=True)
                        for q in existing_returns:
                            prev_returned_db += q

                        # Also account for returns we've created in this seed run
                        prev_in_run = returned_qty_map.get(sales_item.id, Decimal('0.00'))
                        total_prev = prev_returned_db + prev_in_run

                        available = sales_item.quantity - total_prev
                        if available <= 0:
                            continue

                        # Return between 1 and available quantity
                        max_ret = min(int(available), 5)
                        if max_ret < 1:
                            continue

                        return_qty = Decimal(str(random.randint(1, max_ret)))

                        return_items_data.append({
                            'sales_item': sales_item,
                            'item_name': sales_item.item_name,
                            'quantity': return_qty,
                            'rate': sales_item.rate,
                            'discount': sales_item.discount,
                        })

                    if not return_items_data:
                        continue

                    # Create the SalesReturn
                    sales_return = SalesReturn(
                        invoice=invoice,
                        customer=invoice.customer,
                        status='Draft',  # Start as Draft, transition to Saved below if needed
                        refund_type=refund_type,
                        reason=reason,
                        notes=notes,
                    )
                    sales_return.save()

                    for item_data in return_items_data:
                        SalesReturnItem.objects.create(
                            sales_return=sales_return,
                            **item_data,
                        )
                        created_items += 1

                    # If status should be Saved, transition and apply balance effects
                    if status == 'Saved':
                        sales_return.status = 'Saved'
                        sales_return.save(update_fields=['status'])
                        sales_return.refresh_from_db()

                        # Apply balance effects using the serializer logic
                        if refund_type == 'STORE_CREDIT' and invoice.customer:
                            customer = Customer.objects.select_for_update().get(
                                pk=invoice.customer_id
                            )
                            net_amount = sales_return.net_return_amount
                            remaining = net_amount

                            applied_to_credit = Decimal('0.00')
                            applied_to_advance = Decimal('0.00')

                            if customer.credit_balance > 0 and remaining > 0:
                                applied_to_credit = min(customer.credit_balance, remaining)
                                customer.credit_balance -= applied_to_credit
                                remaining -= applied_to_credit

                            if remaining > 0:
                                applied_to_advance = remaining
                                customer.advance_balance += applied_to_advance

                            customer.save(update_fields=['credit_balance', 'advance_balance'])

                            sales_return.applied_to_credit = applied_to_credit
                            sales_return.applied_to_advance = applied_to_advance
                            sales_return.save(update_fields=['applied_to_credit', 'applied_to_advance'])

                        # Track returned quantities for cap enforcement
                        for item_data in return_items_data:
                            si_id = item_data['sales_item'].id
                            returned_qty_map[si_id] = (
                                returned_qty_map.get(si_id, Decimal('0.00'))
                                + item_data['quantity']
                            )

                    created_returns += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"Error creating return #{i + 1}: {e}")
                )

        total_returns = SalesReturn.objects.count()
        total_items = SalesReturnItem.objects.count()
        draft_count = SalesReturn.objects.filter(status='Draft').count()
        saved_count = SalesReturn.objects.filter(status='Saved').count()
        cash_count = SalesReturn.objects.filter(refund_type='CASH').count()
        credit_count = SalesReturn.objects.filter(refund_type='STORE_CREDIT').count()

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {created_returns} returns with {created_items} line items.\n"
                f"Total in DB: {total_returns} returns, {total_items} items.\n"
                f"Status: {draft_count} Draft, {saved_count} Saved.\n"
                f"Refund types: {cash_count} Cash, {credit_count} Store Credit."
            )
        )
