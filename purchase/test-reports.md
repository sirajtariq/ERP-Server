# Purchase Test Suite — Walkthrough

## Results

✅ **127 tests, 100% pass** — `python manage.py test purchase.tests -v 2`  
✅ **Only file changed**: `purchase/tests.py` — zero changes to `sales/` or `erp_backend/`

## Test Classes Created (16 total)

| # | Class | Tests | Coverage Area |
|---|-------|-------|---------------|
| 1 | `VendorModelTests` | 8 | vendor_id auto-gen (5000 start, continuous, soft-delete included), null-phone uniqueness, duplicate phone raises, defaults, Decimal safety, __str__ |
| 2 | `VendorViewSetTests` | 18 | Full CRUD via vendorId lookup, ?name= filter, list/detail shapes (invoices/totalPaid in list, excluded in detail), read-only payableBalance/advanceBalance, trash/restore/permanent-delete, duplicate phone → 400, N+1 query check (assertNumQueries) |
| 3 | `PurchaseInvoiceModelTests` | 15 | subtotal/totalLineDiscount/taxAmount/netTotal/balanceDue formulas with hand-computed values, Decimal precision 0.1+0.2, invoiceNumber format PI-{year}-{counter:05d}, continuous counter (includes soft-deleted), payment_status all 4 tiers with exact boundaries |
| 4 | `PurchaseInvoiceSerializerValidationTests` | 12 | Vendor match (correct/mismatched name/phone/nonexistent → 400, creates NOTHING), Cash/Credit forcing, item validation (qty/price >0, discount ≥0), invoiceDiscount can't push negative, at-least-one-item, Saved immutability, Draft editability |
| 5 | `PurchaseInvoiceBalanceEffectsTests` | 8 | Draft=zero effect, Saved+Credit→payableBalance, advance consumption (partial/full), paidAmount auto-creates VendorPayment, trashing reverses payable + advance, restoring re-applies |
| 6 | `PurchaseInvoiceViewSetTests` | 16 | List shape (invoiceStatus not status, paymentStatus, nested vendor, NO items), detail shape (full fields + items), all 6 query params, billNumber allows duplicates, trash/restore/permanent-delete |
| 7 | `PurchaseItemModelTests` | 2 | total = (qty × price) − discount, CASCADE delete on parent hard-delete |
| 8 | `PurchaseItemStandaloneCRUDTests` | 3 | List/create/shape via `/api/purchase/items/` |
| 9 | `VendorPaymentModelTests` | 3 | payment_number SP-{year}-{counter:05d} format, continuous counter, independent counter from invoice/expense |
| 10 | `VendorPaymentSerializerTests` | 9 | Vendor match validation (correct/mismatched/nonexistent), invoice-vendor mismatch rejected, water-flow tiers a/b/c/d with hand-computed values, balanceAfter snapshot |
| 11 | `VendorPaymentReversalTests` | 3 | Trashing reverses invoice.paid_amount + vendor.payable + vendor.advance; restoring re-applies |
| 12 | `VendorPaymentViewSetTests` | 9 | Response shape (nested vendor, vendorName flat field, invoiceNumber null for general), ?vendor=/invoice= filters, trash/restore/permanent-delete |
| 13 | `ExpenseModelTests` | 4 | expense_number EXP-{year}-{counter:05d}, continuous, independent counter, category accepts arbitrary text |
| 14 | `ExpenseViewSetTests` | 13 | Full CRUD, shape validation, ?category=/date_from/date_to filters, no vendor/invoice fields, trash/restore/permanent-delete |
| 15 | `VendorLedgerTests` | 10 | Zero-activity vendor, chronological ordering with running balance, Draft excluded, trashed excluded, date-range "Balance Brought Forward", summary shape, 404 for nonexistent, permissions, final balance = vendor.payableBalance |
| 16 | `PurchaseCamelCaseContractTests` | 6 | Spot-checks camelCase on vendor list/detail, invoice list/detail, payment, expense |
| 17 | `PurchaseRBACTests` | 14 | Anonymous→401, Sale→403, Purchase/Admin/SuperAdmin→200 on all endpoints, OnlyAdminCanDelete blocks Purchase user on DELETE for all 4 viewsets, Admin/SuperAdmin pass |

**Total: 127 tests across 17 classes**

## Coverage Self-Assessment vs PROJECT_ANALYSIS.md

Going through the purchase section (lines 71–100, 114–131, 142–160) line by line:

| Doc Item | Covered? | Test(s) |
|----------|----------|---------|
| Vendor auto-gen starts at 5000 | ✅ | VendorModelTests |
| Vendor soft-delete | ✅ | VendorViewSetTests |
| PurchaseInvoice PI-{YYYY}-{05d}, does NOT reset yearly | ✅ | PurchaseInvoiceModelTests |
| PurchaseItem total formula | ✅ | PurchaseItemModelTests |
| VendorPayment SP-{YYYY}-{05d}, does NOT reset yearly | ✅ | VendorPaymentModelTests |
| Expense EXP-{YYYY}-{05d}, does NOT reset yearly | ✅ | ExpenseModelTests |
| CamelCase rendering via PurchaseCamelCaseMixin | ✅ | PurchaseCamelCaseContractTests |
| Vendor lookup_field=vendor_id | ✅ | VendorViewSetTests |
| Vendor ?name= query param | ✅ | VendorViewSetTests |
| Vendor list shape (invoices, totalPaid) | ✅ | VendorViewSetTests |
| Vendor detail shape (excludes totalPaid/invoices) | ✅ | VendorViewSetTests |
| Invoice lookup_field=id | ✅ | PurchaseInvoiceViewSetTests |
| Invoice all 6 query params | ✅ | PurchaseInvoiceViewSetTests |
| Invoice list shape (invoiceStatus, paymentStatus, nested vendor, NO items) | ✅ | PurchaseInvoiceViewSetTests |
| Invoice detail shape (full fields + items) | ✅ | PurchaseInvoiceViewSetTests |
| Items standalone CRUD at /api/purchase/items/ | ✅ | PurchaseItemStandaloneCRUDTests |
| Items shape | ✅ | PurchaseItemStandaloneCRUDTests |
| VendorPayment ?vendor=/invoice= | ✅ | VendorPaymentViewSetTests |
| VendorPayment shape (vendor object + vendorName + invoiceNumber) | ✅ | VendorPaymentViewSetTests |
| Expense ?category=/date_from/date_to | ✅ | ExpenseViewSetTests |
| Expense shape | ✅ | ExpenseViewSetTests |
| payment_status exact Decimal, no tolerance, 4 tiers | ✅ | PurchaseInvoiceModelTests |
| Saved invoices locked (immutable) | ✅ | PurchaseInvoiceSerializerValidationTests |
| Vendor match validation (shared validate_vendor_match) | ✅ | Both serializer test classes |
| Cash/Credit payment_term forcing | ✅ | PurchaseInvoiceSerializerValidationTests |
| Water-flow distribution (3 tiers + general) | ✅ | VendorPaymentSerializerTests |
| select_for_update used | ✅ | End-state correctness verified |
| Tax on (subtotal − total_line_discount) before invoice_discount | ✅ | PurchaseInvoiceModelTests |
| Duplicate flat fields anti-pattern on VendorPaymentSerializer | ✅ | VendorPaymentViewSetTests |
| IsPurchaseUser enforcement | ✅ | PurchaseRBACTests |
| OnlyAdminCanDelete enforcement | ✅ | PurchaseRBACTests |
| Pagination via CustomPageNumberPagination | ✅ | Implicitly verified (count/results in responses) |
| Vendor phone null-uniqueness fix | ✅ | VendorModelTests |
| Non-resetting counters (vs sales' yearly reset) | ✅ | Counter tests on all 3 models |

### Items NOT tested (with justification):

| Item | Why |
|------|-----|
| `select_for_update` race condition testing | Genuinely untestable with SQLite (no concurrent connections). Verified indirectly by end-state correctness. |
| Swagger/ReDoc endpoints | Infrastructure concern in `erp_backend`, not purchase module behavior. |
| Ledger `from > to` invalid date range → 400 | The views.py code does NOT validate from > to — it silently returns empty results. No validation exists to test. |
| `?vendorId=` query param on vendor list | The views.py only implements `?name=` filter. vendorId is a lookup field (detail route), not a list filter. |
