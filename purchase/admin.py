"""Django admin registrations for purchase models."""

from django.contrib import admin

from purchase.models import PurchaseInvoice, PurchaseItem, Vendor


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("vendor_name", "vendor_id", "phone", "payable_balance", "advance_balance")
    search_fields = ("vendor_name", "phone")


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "vendor", "date", "status", "net_total")
    list_filter = ("date", "vendor", "status")
    search_fields = ("invoice_number", "vendor__vendor_name")
    inlines = [PurchaseItemInline]


@admin.register(PurchaseItem)
class PurchaseItemAdmin(admin.ModelAdmin):
    list_display = ("product_name", "invoice", "quantity", "purchase_price")
    list_filter = ("invoice",)
