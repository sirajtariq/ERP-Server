# ERP Backend Project Analysis

This document is a comprehensive, line-by-line analysis of the ERP backend project. It covers the architecture, models, endpoints, business rules, ledgers, cross-cutting concerns, and known gaps, providing an exhaustive reference for test suite creation.

## 1. Project Architecture Overview

*   **Database**: Relational SQL database managed via Django ORM. Soft-delete pattern (`SoftDeleteModel`) is applied globally.
*   **Auth Mechanism**: JSON Web Tokens (JWT) via `rest_framework_simplejwt`. The token contains custom claims (`username` and `role`).
*   **Roles & Groups**: Four primary roles exist:
    *   `SUPER_ADMIN` (Superuser)
    *   `ADMIN` (Admin group)
    *   `SALE_PERSON` (Sales group)
    *   `PURCHASE_PERSON` (Purchase group)
*   **RBAC (Role-Based Access Control)**: Enforced via custom permission classes in `erp_backend/permissions.py`:
    *   `IsAdminUser`: Admin/Superuser only.
    *   `IsSalesUser`: Sales/Admin/Superuser.
    *   `IsPurchaseUser`: Purchase/Admin/Superuser.
    *   `OnlyAdminCanDelete`: Restricts `DELETE` HTTP methods to Admin/Superuser globally across the sales and purchase apps.
*   **Design Patterns**: 
    *   **Fat Serializers**: All critical accounting, validation, and balance-updating side effects live inside serializer `create()`, `update()`, and `validate()` methods.
    *   **Soft Delete**: Records are flagged `is_deleted=True` rather than hard deleted. Queries use `.objects` (active) and `.all_objects` (including trashed).
*   **CamelCase Rendering**: The `purchase` module explicitly uses `PurchaseCamelCaseMixin` (utilizing `CamelCaseJSONRenderer` and `CamelCaseJSONParser`). All request/response payloads in `purchase` use `camelCase`. The `sales` module does **not** use this mixin and relies on default DRF snake_case, except where serializers explicitly define camelCase field names (e.g., `customerName`, `creditBalance`).
*   **Pagination**: Handled by `CustomPageNumberPagination` (default 10 results per page, max 100).
*   **API Docs**: Swagger UI (`/swagger/`) and ReDoc (`/redoc/`) are auto-generated via `drf_yasg`.

---

## 2. App-by-App Breakdown

### A. `erp_backend` (Core & Auth)

**Models**:
1.  **UserProfile**:
    *   Fields: `user` (OneToOne), `fullname` (Char), `phone` (Char), `cnic` (Char), `address` (Text, optional), `designation` (Char), `dateofjoining` (Date), `employmenttype` (Choices), `basicsalary` (Decimal, optional), `salarytype` (Choices).

**Endpoints**:
*   `POST /api/auth/login/`: Returns `{"access": "...", "refresh": "...", "role": "...", "username": "..."}`
*   `POST /api/auth/login/refresh/`, `POST /api/auth/logout/`, `PATCH /api/auth/password/change/`
*   `GET /api/auth/me/`: Returns User object with nested profile.
*   `GET/POST/PUT/PATCH/DELETE /api/users/`: CRUD for users & nested profiles. `lookup_field` = `id` (default).

### B. `sales` Module

**Models**:
1.  **Customer**:
    *   Soft-delete: Yes. Auto-gen: `customer_id` (`walkin` starts at 8000; `permanent` starts at 4000).
2.  **SalesInvoice**:
    *   Soft-delete: Yes. Auto-gen: `invoice_number` (`INV-{YYYY}-{05d}`). **Resets yearly**.
3.  **SalesItem**: Line items. `total = (quantity * rate) - discount`.
4.  **PaymentReceived**:
    *   Soft-delete: Yes. Auto-gen: `receipt_number` (`REC-{YYYY}-{05d}`). **Resets yearly**.

**Endpoints** (All require `IsSalesUser`):
*   `/api/sales/customers/`: 
    *   **Lookup Field**: `customer_id`
    *   **List Query Params**: `?name=`, `?type=`, `?ordering=`, `?page=`, `?page_size=`
    *   **Shape (List)**: `{"id", "customerId", "customerName", "customerType", "Phone", "creditBalance", "advanceBalance", "totalPaid", "totalDue"}`
    *   **Shape (Detail/Create)**: Adds `email`, `Address`, `openingCredit`, `openingNote`, `taxNumber`, `createdAt`, `updatedAt`, `invoices` (nested array).
*   `/api/sales/invoices/`: 
    *   **Lookup Field**: `id`
    *   **List Query Params**: `?name=`, `?invoice_number=`, `?customer_id=`, `?type=`, `?ordering=`, `?page=`, `?page_size=`
    *   **Shape (List)**: `{"id", "invoiceNumber", "customerName", "total", "paid", "pending", "paymentStatus", "date"}`
    *   **Shape (Detail/Create)**: `{"id", "invoice_number", "date", "customer_data" (object), "payment_term", "payment_method", "paid_amount", "payment_reference", "notes", "vat_percentage", "invoice_discount", "invoiceStatus", "paymentStatus", "items" (array), "subtotal", "total_line_discount", "tax_amount", "net_total", "balance_due", "advance_applied"}`
*   `/api/sales/items/`: Standalone line-item CRUD. `lookup_field` = `id`.
    *   **Shape**: `{"id", "invoice", "item_name", "units", "quantity", "rate", "discount", "total"}`
*   `/api/sales/payments/`:
    *   **Lookup Field**: `id`
    *   **List Query Params**: `?from=`, `?to=`, `?customer=`, `?ordering=`, `?page=`, `?page_size=`
    *   **Shape**: `{"id", "receipt_number", "date", "customer" (ID), "customerName", "invoice" (ID), "invoiceNumber", "amount_received", "balance_after", "method", "notes", "applied_to_invoice", "applied_to_credit", "applied_to_advance"}`

### C. `purchase` Module (CamelCase Payloads)

**Models**:
1.  **Vendor**: Auto-gen: `vendor_id` (starts at 5000). Soft-delete: Yes.
2.  **PurchaseInvoice**: Auto-gen: `invoice_number` (`PI-{YYYY}-{05d}`). **Does NOT reset yearly**. Soft-delete: Yes.
3.  **PurchaseItem**: `total = (quantity * purchasePrice) - discount`.
4.  **VendorPayment**: Auto-gen: `payment_number` (`SP-{YYYY}-{05d}`). **Does NOT reset yearly**.
5.  **Expense**: Auto-gen: `expense_number` (`EXP-{YYYY}-{05d}`). **Does NOT reset yearly**.

**Endpoints** (All require `IsPurchaseUser`):
*   `/api/purchase/vendors/`:
    *   **Lookup Field**: `vendor_id`
    *   **List Query Params**: `?name=`, `?ordering=`, `?page=`, `?page_size=`
    *   **Shape (List)**: `{"id", "vendorId", "vendorName", "phone", "email", "address", "taxNumber", "openingPayable", "openingNote", "payableBalance", "advanceBalance", "totalPaid", "createdAt", "updatedAt", "invoices" (array)}`
    *   **Shape (Detail/Create)**: Same as list, but EXCLUDES `totalPaid` and `invoices`.
*   `/api/purchase/invoices/`:
    *   **Lookup Field**: `id`
    *   **List Query Params**: `?vendor=`, `?bill_number=`, `?invoice_number=`, `?status=`, `?payment_term=`, `?ordering=`, `?page=`, `?page_size=`
    *   **Shape (List)**: `{"id", "invoiceNumber", "billNumber", "date", "paymentTerm", "invoiceStatus", "paymentStatus", "subtotal", "netTotal", "balanceDue", "vendor" (object)}`
    *   **Shape (Detail/Create)**: `{"id", "vendor" (object), "billNumber", "invoiceNumber", "date", "paymentTerm", "paymentMethod", "paidAmount", "advanceApplied", "paymentReference", "notes", "vatPercentage", "invoiceDiscount", "status", "subtotal", "totalLineDiscount", "taxAmount", "netTotal", "balanceDue", "paymentStatus", "items" (array)}`
*   `/api/purchase/items/`: `lookup_field` = `id`.
    *   **Shape**: `{"id", "invoice", "productName", "units", "quantity", "purchasePrice", "discount", "total"}`
*   `/api/purchase/vendor-payments/`:
    *   **Lookup Field**: `id`
    *   **List Query Params**: `?vendor=`, `?invoice=`, `?ordering=`, `?page=`, `?page_size=`
    *   **Shape**: `{"id", "paymentNumber", "date", "vendor" (object), "vendorName", "invoice" (string identifier), "invoiceNumber", "amountPaid", "balanceAfter", "method", "notes", "appliedToInvoice", "appliedToPayable", "appliedToAdvance"}`
*   `/api/purchase/expenses/`:
    *   **Lookup Field**: `id`
    *   **List Query Params**: `?category=`, `?date_from=`, `?date_to=`, `?ordering=`, `?page=`, `?page_size=`
    *   **Shape**: `{"id", "expenseNumber", "category", "amount", "paymentMethod", "date", "notes", "createdAt"}`

---

## 3. Business Logic & Validation Mapping

### `paymentStatus` Logic
*   **SalesInvoice**: Computed via `compute_payment_status(invoice)` in `sales/serializers.py`.
    *   **Tolerance**: Yes, uses a `Decimal('0.01')` tolerance.
    *   **Values**: 
        *   `Unpaid`: if `balance_due > 0.01` and `paid_amount == 0`.
        *   `Partial`: if `balance_due > 0.01` and `paid_amount > 0`.
        *   `Advance`: if `customer.advance_balance > 0` (and not caught by unpaid/partial).
        *   `Paid`: Otherwise (balance_due <= 0.01).
*   **PurchaseInvoice**: Computed via `payment_status` property on the `PurchaseInvoice` model.
    *   **Tolerance**: **None**. Uses exact `Decimal` comparisons.
    *   **Values**:
        *   `Unpaid`: if `paid_amount + advance_applied <= 0.00`.
        *   `Partial`: if `paid_amount + advance_applied < net_total`.
        *   `Paid`: if `paid_amount + advance_applied == net_total`.
        *   `Advance`: if `paid_amount + advance_applied > net_total`.

### `SalesInvoiceSerializer` & `PurchaseInvoiceSerializer` Common Rules
*   **Immutability**: If `status == 'Saved'`, any `update` raises a ValidationError.
*   **Validation Rules**: Line amounts must be positive, discounts cannot be negative, invoice discount cannot push net total below 0. Walk-in (sales) requires Cash + full payment. Credit terms are forced if underpaid, Cash is forced if fully paid.
*   **Vendor Matching (Purchase only)**: `PurchaseInvoiceSerializer` and `VendorPaymentSerializer` **both** share identical strict vendor matching logic via `validate_vendor_match()`. They check that the incoming payload's `vendor_id`, `vendor_name`, and `phone` identically match the DB row. (400 validation error on mismatch). Sales dynamically provisions walk-in customers instead.

### `PaymentReceivedSerializer` & `VendorPaymentSerializer`
*   **Water-flow Distribution (`_apply_payment`)**:
    1.  Target the linked invoice's `balance_due`.
    2.  Spill over to the global `credit_balance` (Sales) or `payable_balance` (Purchase).
    3.  Remaining amount becomes an overpayment and drops into `advance_balance`.

---

## 4. Ledger Endpoints (Sales & Purchase)
[Logic remains unchanged: dynamic from/to filtering, Running Balance generation, and output shapes `summary`/`ledger`/`finalPaymentDetails`]

---

## 5. Cross-Cutting Concerns

*   **Concurrency (`select_for_update`)**: 
    *   Used in `VendorPaymentSerializer` and `PurchaseInvoiceSerializer` to safely lock `Vendor` rows.
    *   **GAP:** `SalesInvoiceSerializer` and `PaymentReceivedSerializer` do **not** use `select_for_update` when manipulating `Customer` balances, risking race conditions.
*   **Tax Calculation Asymmetry**: 
    *   Sales: Tax is calculated on `(subtotal - invoice_discount)`.
    *   Purchase: Tax is calculated on `(subtotal - total_line_discount)`, *before* `invoice_discount` is applied.

---

## 6. Current Known Gaps / TODOs

*   **Duplicate Flat + Nested Fields (Serialization Debt)**:
    *   `PaymentReceivedSerializer` (Sales) and `VendorPaymentSerializer` (Purchase) both suffer from a redundant-field anti-pattern where they serialize the parent entity twice: once as a nested slug/object (e.g., `customer` slug or `vendor` object), and immediately again as flat string fields (`customerName`, `invoiceNumber`, `vendorName`, `invoiceNumber`).
    *   **Check Results**: `PurchaseInvoiceSerializer` and `ExpenseSerializer` were manually audited for this. They are **clean** (no redundant flat fields).
*   **`purchase/views.py:198`**: Missing guard on `VendorViewSet` permanent delete to prevent deletion of vendors with related records.
*   **Fragile IntegrityError Matching**: `if 'vendor_id' in str(e)` in models.py is tech debt.
*   **Phone Number Uniqueness Bug**: The `sales` `Customer` model uses `phone = models.CharField(unique=True, blank=True, null=True)`. The serializer validator expects unique, but does not explicitly cast empty strings `""` to `None` like `Vendor` does, leading to potential IntegrityErrors.
*   **Inconsistent Sequence Resets**: `SalesInvoice` and `PaymentReceived` reset their counters to 1 every calendar year because their `.filter()` queries include the year in the prefix. `PurchaseInvoice`, `VendorPayment`, and `Expense` do **not** reset yearly because they omit the year from their `startswith=` filter prefix.
