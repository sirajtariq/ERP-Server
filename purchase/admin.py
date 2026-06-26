"""Django admin registrations for purchase models."""

from django.contrib import admin

from purchase.models import PurchaseInvoice, PurchaseItem, Vendor


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "phone")
    search_fields = ("name", "phone")


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "vendor", "date", "total_amount")
    list_filter = ("date", "vendor")
    search_fields = ("invoice_number", "vendor__name")
    inlines = [PurchaseItemInline]


@admin.register(PurchaseItem)
class PurchaseItemAdmin(admin.ModelAdmin):
    list_display = ("product_name", "invoice", "quantity", "purchase_price")
    list_filter = ("invoice",)
