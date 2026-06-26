"""Django admin registrations for sales models."""

from django.contrib import admin

from sales.models import Customer, SalesInvoice, SalesItem


class SalesItemInline(admin.TabularInline):
    model = SalesItem
    extra = 1


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone")
    search_fields = ("name", "phone")


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "customer", "date", "total_amount")
    list_filter = ("date", "customer")
    search_fields = ("invoice_number", "customer__name")
    inlines = [SalesItemInline]


@admin.register(SalesItem)
class SalesItemAdmin(admin.ModelAdmin):
    list_display = ("product_name", "invoice", "quantity", "sale_price")
    list_filter = ("invoice",)
