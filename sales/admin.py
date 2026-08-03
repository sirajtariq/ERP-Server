"""Django admin registrations for sales models."""

from django.contrib import admin

from sales.models import Customer, SalesInvoice, SalesItem, SalesReturn, SalesReturnItem


class SalesItemInline(admin.TabularInline):
    model = SalesItem
    extra = 1


class SalesReturnItemInline(admin.TabularInline):
    model = SalesReturnItem
    extra = 1


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("customer_id", "customer_name", "customer_type", "phone")
    search_fields = ("customer_name", "phone", "customer_id")


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "customer", "payment_term", "payment_method", "date", "net_total", "status")
    list_filter = ("date", "payment_term", "status", "customer")
    search_fields = ("invoice_number", "customer__customer_name")
    inlines = [SalesItemInline]


@admin.register(SalesItem)
class SalesItemAdmin(admin.ModelAdmin):
    list_display = ("item_name", "invoice", "quantity", "rate", "total")
    list_filter = ("invoice",)


@admin.register(SalesReturn)
class SalesReturnAdmin(admin.ModelAdmin):
    list_display = ("return_number", "invoice", "customer", "status", "return_date", "net_return_amount")
    list_filter = ("status", "return_date", "customer")
    search_fields = ("return_number", "invoice__invoice_number", "customer__customer_name")
    inlines = [SalesReturnItemInline]


@admin.register(SalesReturnItem)
class SalesReturnItemAdmin(admin.ModelAdmin):
    list_display = ("item_name", "sales_return", "quantity", "rate", "total")
    list_filter = ("sales_return",)

