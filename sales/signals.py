from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from sales.models import SalesInvoice, SalesItem

@receiver(post_save, sender=SalesInvoice)
@receiver(post_delete, sender=SalesInvoice)
def handle_invoice_change(sender, instance, **kwargs):
    if instance.customer_id:
        try:
            customer = instance.customer
            if customer:
                customer.save()
        except Exception:
            pass

@receiver(post_save, sender=SalesItem)
@receiver(post_delete, sender=SalesItem)
def handle_item_change(sender, instance, **kwargs):
    try:
        invoice = instance.invoice
        if invoice and invoice.customer_id:
            customer = invoice.customer
            if customer:
                customer.save()
    except Exception:
        pass
