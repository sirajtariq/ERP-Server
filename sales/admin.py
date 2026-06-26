"""Django admin registrations for sales models."""

from django.contrib import admin

from sales.models import Customer, SalesInvoice, SalesItem


class SalesItemInline(admin.TabularInline):
    model = SalesItem
    extra = 1


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("customer_id", "customer_name", "phone")
    search_fields = ("customer_name", "phone", "customer_id")


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "customer", "walk_in_customer_name", "payment_term", "payment_method", "date", "net_total", "status")
    list_filter = ("date", "payment_term", "status", "customer")
    search_fields = ("invoice_number", "customer__customer_name", "walk_in_customer_name")
    inlines = [SalesItemInline]


@admin.register(SalesItem)
class SalesItemAdmin(admin.ModelAdmin):
    list_display = ("item_name", "invoice", "quantity", "rate", "total")
    list_filter = ("invoice",)
