# Project Architecture Analysis - Django ERP Backend

This document provides a comprehensive architectural analysis of the Django ERP Backend project, detailing its overall structure, app-by-App breakdown, business logic mapping, test suite status, and current technical debt.

## 1. Project Architecture Overview

The project is built as a RESTful API serving a desktop ERP client, using the **Django REST Framework (DRF)**.

*   **Database:** SQLite (`db.sqlite3`) is configured as the default database.
*   **Authentication:** JWT token-based authentication using `rest_framework_simplejwt`. The token generation (`CustomTokenObtainPairSerializer`) is heavily customized to inject user roles (`SUPER_ADMIN`, `ADMIN`, `SALE_PERSON`, `PURCHASE_PERSON`) directly into the JWT payload.
*   **Role-Based Access Control (RBAC):** Permissions are strictly enforced at the ViewSet level using custom permission classes:
    *   `IsAdminUser`: Superusers or members of the "Admin" group.
    *   `IsSalesUser`: Members of "Sales", "Admin", or superusers.
    *   `IsPurchaseUser`: Members of "Purchase", "Admin", or superusers.
    *   `OnlyAdminCanDelete`: Only Admins or Superusers can execute `DELETE` requests (moving items to trash).
*   **Design Patterns:**
    *   **Fat Serializers:** Most of the heavy business logic, validations, and accounting side-effects reside in the Serializers rather than the Models.
    *   **Soft Delete:** Implemented in the `sales` module via a custom `SoftDeleteModel` abstract class, which flags records with `is_deleted` and `deleted_at` instead of hard deleting them. Custom managers (`SoftDeleteManager`, `AllRecordsManager`) filter active versus trashed records.
*   **API Documentation:** Interactive Swagger UI and ReDoc are integrated via `drf-yasg`.
*   **CORS:** Configured to allow all origins (`CORS_ALLOW_ALL_ORIGINS = True`), intended for local desktop client access.

---

## 2. Comprehensive App-by-App Breakdown

### A. `erp_backend` (Core & Auth)
Manages project configuration, JWT logic, and extended user profiles.
*   **Models:**
    *   `UserProfile`: Extends the default Django `User` model via a OneToOneField. Adds fields: `fullname`, `phone`, `cnic`, `address`, `designation`, `dateofjoining`, `employmenttype` (fulltime, parttime, contract), `basicsalary`, `salarytype` (monthly, daily, perjob).
*   **Routes (`erp_backend/urls.py`):**
    *   `POST /api/auth/login/`: Token generation (`CustomTokenObtainPairView`).
    *   `POST /api/auth/login/refresh/`: Token refresh.
    *   `POST /api/auth/logout/`: Token blacklist.
    *   `PATCH /api/auth/password/change/`: Password changes with role-based logic.
    *   `GET /api/auth/me/`: Current user profile information.
    *   `CRUD /api/users/`: Managed via `UserViewSet` (Admin access only).

### B. `sales` (Sales Module)
The core operational module, fully implementing the Soft Delete pattern and comprehensive ledger logic.
*   **Models (All inheriting from `SoftDeleteModel` except `SalesItem`):**
    *   `Customer`: `customer_id` (Integer, custom sequence generator), `customer_name`, `customer_type` (permanent, walkin), `phone` (Unique), `email`, `address`, `opening_credit`, `opening_note`, `tax_number`, `credit_balance`, `advance_balance`.
    *   `SalesInvoice`: `customer` (FK), `payment_term` (Cash, Credit), `payment_method`, `paid_amount`, `advance_applied`, `payment_reference`, `notes`, `vat_percentage`, `invoice_discount`, `status` (Draft, Saved), `invoice_number` (String, auto-generated sequence). Includes properties: `subtotal`, `total_line_discount`, `tax_amount`, `net_total`, `balance_due`.
    *   `SalesItem`: `invoice` (FK), `item_name`, `units`, `quantity`, `rate`, `discount`. (Cascades on invoice deletion).
    *   `PaymentReceived`: `customer` (FK), `invoice` (FK, nullable), `receipt_number`, `amount_received`, `balance_after`, `method`, `notes`, `date`, `applied_to_invoice`, `applied_to_credit`, `applied_to_advance`.
*   **Routes (`sales/urls.py` & `sales/views.py`):**
    *   `CRUD /api/sales/customers/`: `CustomerViewSet`. Custom actions: `trash`, `restore`, `permanent-delete`, `ledger`, `convert-to-permanent`.
    *   `CRUD /api/sales/invoices/`: `SalesInvoiceViewSet`. Custom actions: `trash`, `restore`, `permanent-delete`, `all-with-items`.
    *   `CRUD /api/sales/items/`: `SalesItemViewSet`.
    *   `CRUD /api/sales/payments/`: `PaymentReceivedViewSet`. Custom actions: `trash`, `restore`.
*   **Permissions:** `IsSalesUser`, `OnlyAdminCanDelete`.
*   **Pagination:** Custom page size limits via `CustomPageNumberPagination`.

### C. `purchase` (Purchase Module)
A simplified, rudimentary module handling vendor purchases. Lacks the advanced ledger, validations, and soft-delete features of the Sales module.
*   **Models:**
    *   `Vendor`: `name`, `phone`, `address`.
    *   `PurchaseInvoice`: `vendor` (FK), `invoice_number`, `date`, `total_amount`.
    *   `PurchaseItem`: `invoice` (FK), `product_name`, `quantity`, `purchase_price`.
*   **Routes (`purchase/urls.py`):**
    *   `CRUD /api/purchase/vendors/`: `VendorViewSet`.
    *   `CRUD /api/purchase/invoices/`: `PurchaseInvoiceViewSet` (supports nested items on creation).
    *   `CRUD /api/purchase/items/`: `PurchaseItemViewSet`.
*   **Permissions:** `IsPurchaseUser`.

---

## 3. Business Logic & Validations Mapping

The core complexity of the application resides in the `sales` serializers, dictating how money moves and how invoices behave.

### A. Invoice Lifecycle & Validation (`SalesInvoiceSerializer`)
*   **Absolute Locks:** If an invoice's status is `'Saved'`, any `PUT`/`PATCH` requests are blocked entirely. "Saved invoices are locked and cannot be modified." Users must delete (move to trash) and recreate.
*   **Customer Handling (`CustomerDataField`):** Invoices accept a nested `customer_data` payload. It looks up customers strictly by `phone` number. If a match is found (even deleted ones, which it restores), it links to the invoice. If no match is found, it dynamically provisions a new `walkin` customer.
*   **Walk-in vs. Permanent Rules:**
    *   *Walk-in Customers* must pay via `'Cash'` only. The `paid_amount` must equal the exact `net_total` (no credit allowed).
    *   *Permanent Customers*:
        *   If `paid_amount` + `advance_balance` < `net_total`, the `payment_term` MUST be `'Credit'`.
        *   If `paid_amount` + `advance_balance` >= `net_total`, the `payment_term` MUST be `'Cash'` (preventing unnecessary credit records when fully covered).
*   **Mathematical Validations:** Quantity and rate must be > 0. Line discounts and invoice discounts cannot be negative. Invoice discount cannot exceed the base amount + tax (preventing negative `net_total`).

### B. Accounting Side-Effects & Ledger Logic
*   **Advance Consumption (`_apply_invoice_balance_effects`):** When an invoice transitions to `'Saved'`, the system checks if the customer has an `advance_balance`. If so, it automatically consumes the advance to pay down the invoice's `balance_due`.
*   **Credit Balance Updates:** If the `payment_term` is `'Credit'`, any remaining `balance_due` is explicitly added to the customer's `credit_balance`.
*   **Auto-Payment Generation:** If `paid_amount` > 0 during invoice creation, the serializer automatically generates a corresponding `PaymentReceived` record.
*   **Payment Routing (`PaymentReceivedSerializer._apply_payment`):** Payments cascade through balances in strict order:
    1.  Applies against a specific invoice's `balance_due` (capped at the invoice's net total).
    2.  Applies against the customer's general `credit_balance` (outstanding debt).
    3.  Any leftover amount becomes an overpayment and is routed to the customer's `advance_balance`.
*   **Trash/Restore Reversal:** Moving an invoice or payment to the trash explicitly triggers reverse accounting logic (`_reverse_payment`) to undo balances. Restoring re-applies the logic.
*   **Ledger Compilation:** The `/api/sales/customers/{id}/ledger/` endpoint manually aggregates Opening Balances, Saved Invoices, and Payments. It dynamically computes chronologically sorted running balances (`balance`), summarizing credit sales, cash returns, and available advances.

---

## 4. Test Suite Analysis

The project contains tests targeting the Core and Sales modules, but coverage is asymmetric.

*   **`erp_backend/tests.py`:**
    *   Tests the `/api/auth/me/` endpoint.
    *   Ensures unauthenticated requests fail.
    *   Verifies that the `role` is correctly mapped for Admin, Sales, and Superusers within the JWT claims and profile responses.
*   **`sales/tests.py`:**
    *   Tests core `SalesInvoice` properties (subtotal, tax, net_total).
    *   Verifies serializer validation rules (Walk-in payment term rejections, partial payment logic).
    *   Validates the customer balance lifecycle (Credit vs. Advance) during invoice Draft -> Saved -> Payment -> Delete phases.
    *   Basic API structural tests for the Ledger endpoint.
*   **`sales/tests/test_ledger_calculations.py`:**
    *   A massive (~130,000 bytes, 3,250+ lines) independently verified test suite.
    *   It bypasses application helpers and manually computes expected values using Python's `Decimal` to ensure no silent bugs exist in ledger math.
    *   Tests intricate scenarios: single/multiple invoices, partial payments, general overpayments routing to advance, chronological mixed ordering, and edge cases.
*   **`purchase/tests.py`:**
    *   **Blank.** No tests currently exist for the purchase module.

---

## 5. Technical Debt, Gaps & Architectural State

Based on the architectural review, the following structural inconsistencies and gaps were identified:

1.  **Asymmetric Module Maturity:**
    The `sales` module is highly developed with robust RBAC, soft-deletes, trash/restore queues, and strict financial ledgers. The `purchase` module is rudimentary, lacking tests, soft-delete functionality, payment tracking, and ledger integrations entirely.
2.  **Fat Serializers vs. Model Methods:**
    The majority of complex accounting side-effects (e.g., `_apply_payment`, `_apply_invoice_balance_effects`) are tightly coupled within DRF Serializers. As highlighted in the test suite, creating models directly via the ORM bypasses advance consumption and payment generation. Moving this logic to Model `save()` overrides or Service layer classes would improve reliability and testability.
3.  **Hardcoded Configurations & Magic Strings:**
    *   Customer ID sequence starting points are hardcoded (8000 for walk-in, 4000 for permanent).
    *   Prefix strings for sequence generation (`INV-`, `REC-`) are hardcoded directly within model `save()` methods.
    *   Error handling relies on fragile string matching (`if 'customer_id' in str(e)` or `if 'invoice_number' in str(e)`) for IntegrityErrors.
4.  **Concurrency & Race Conditions:**
    While `transaction.atomic()` is used effectively for sequence generation (along with `select_for_update()` in places like Customer conversion and Payment generation), the updating of `Customer.credit_balance` and `advance_balance` inside the `SalesInvoiceSerializer` and `SalesInvoiceViewSet.destroy` lacks `select_for_update()`. Under high concurrency, simultaneous invoices or payments could cause race conditions yielding corrupted customer balances.
5.  **Soft Delete Cascading:**
    The `SoftDeleteModel.soft_delete()` method does not cascade. Soft deleting a `Customer` does not automatically soft delete their related `SalesInvoices` or `Payments`, potentially leaving orphaned active financial records attached to a deleted customer profile.
6.  **Database Precision Constraints:**
    The application relies entirely on SQLite. SQLite does not natively enforce the `Decimal` precision specified in Django models, treating them as floating-point approximations. Heavy reliance on exact Decimal calculations (with `0.01` tolerances in code) may lead to rounding inconsistencies unless migrated to PostgreSQL or MySQL.
