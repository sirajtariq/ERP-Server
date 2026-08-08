# Backend API Audit Report

## 1. Executive Summary
- **Total Endpoints Discovered:** ~35+ (across Sales, Purchase, Settings, Auth, and Dashboard modules)
- **Endpoints Tested Dynamically:** 13
- **HTTP Success Rate (Dynamic):** 76.9% (10 passed, 3 failed)
- **Overall Backend Stability Rating:** Moderate. While core CRUD operations like customer and expense creation function properly, there are critical serialization inconsistencies that block major workflows (e.g., invoice and vendor creation).

## 2. Failed & Buggy Endpoints

### 2.1. Vendor Creation Fails (400 Bad Request)
- **Method & URL:** `POST /api/purchase/vendors/`
- **Request Payload:** `{"vendorName": "Test QA Vendor", "phone": "1112223334", "openingPayable": "200.00"}`
- **Error Response:** `{"vendorName": ["This field is required."]}`
- **Root Cause:** A double-conversion bug caused by mixing DRF parser plugins and explicit serializer aliases. The `PurchaseCamelCaseMixin` uses `djangorestframework-camel-case` which automatically converts incoming `vendorName` JSON keys to `vendor_name` Python dict keys before passing data to the serializer. However, `VendorSerializer` explicitly defines `vendorName = serializers.CharField(source="vendor_name")`, which expects a key literally named `vendorName` in the parsed data. Since the parser already converted it, the serializer throws a "required" error.

### 2.2. Sales Invoice Creation Fails (400 Bad Request)
- **Method & URL:** `POST /api/sales/invoices/`
- **Request Payload:** Valid invoice payload with items and customer data.
- **Error Response:** `{"invoiceStatus": ["This field is required."]}`
- **Root Cause:** In `SalesInvoiceSerializer`, the `status` field is explicitly mapped as `invoiceStatus = serializers.ChoiceField(source='status', choices=SalesInvoice.STATUS_CHOICES)`. By default, DRF makes defined serializer fields required unless `required=False` is passed. This overrides the model's `default='Draft'` behavior and forces the client to send `invoiceStatus` on creation.

### 2.3. Customer Retrieval Fails (404 Not Found)
- **Method & URL:** `GET /api/sales/customers/4/`
- **Root Cause:** `CustomerViewSet` uses `lookup_field = "customer_id"` (e.g., `PR-00001`), but many other views and generic relationships expect the primary key (`id`). This creates an inconsistency in the API design where a newly created customer object returns an integer ID, but fetching it via the REST detail route requires the string `customer_id`.

## 3. Accounting & Business Logic Findings

- **Ledger Generation Engine:** The ledger generation in both `sales/views.py` and `purchase/views.py` processes invoices as Debits and payments as Credits correctly. It avoids generating duplicate `PAY-INV` rows because advance consumption correctly manipulates `invoice.paid_amount` without creating redundant `PaymentReceived` records that would bloat the ledger.
- **FIFO Payment Allocation (N+1 Issue):** The FIFO engine (`Customer.apply_payment` and `Vendor.apply_payment`) iterates through unpaid invoices and calls `inv.save(update_fields=['paid_amount'])` inside a loop. While logically sound, this will cause an N+1 query performance bottleneck if a customer has hundreds of small unpaid invoices.
- **Credit vs Cash Auto-Validation logic:** The business rule enforcing that full payments must be marked as "Cash" and partial as "Credit" works correctly inside the `SalesInvoiceSerializer.validate()` method.

## 4. Passed Endpoints
The following endpoints were verified as working correctly during the dynamic test phase:
- `POST /api/auth/login/`
- `GET /api/auth/me/`
- `GET /api/settings/backup/`
- `PUT /api/settings/backup/`
- `POST /api/settings/backup/trigger/`
- `POST /api/sales/customers/`
- `GET /api/sales/customers/`
- `POST /api/purchase/expenses/`
- `GET /api/purchase/expenses/`
- `GET /api/purchase/daily-outflows/`

## 5. Recommended Fixes (Actionable Code Adjustments)

1. **Remove Manual CamelCase aliases in Purchase Module:**
   - Modify `VendorSerializer`, `PurchaseInvoiceSerializer`, etc., in `purchase/serializers.py`. Instead of explicitly aliasing fields like `vendorName = serializers.CharField(source="vendor_name")`, rely entirely on standard snake_case field definitions (e.g., `vendor_name = serializers.CharField()`). The `PurchaseCamelCaseMixin` will handle the translation automatically.
2. **Fix Required Aliased Fields in Sales:**
   - In `SalesInvoiceSerializer`, update the status field: `invoiceStatus = serializers.ChoiceField(source='status', choices=SalesInvoice.STATUS_CHOICES, required=False)`.
3. **Fix Dead Code / Crashing Fields in `CustomerSerializer`:**
   - **Bug:** `CustomerSerializer` has `totalPaid = serializers.DecimalField(...)` but defines a `get_totalPaid` method. DRF will try to read `totalPaid` directly from the `Customer` model and crash because it is not a `SerializerMethodField`.
   - **Fix:** Change `totalPaid = serializers.DecimalField(...)` to `totalPaid = serializers.SerializerMethodField()`.
   - **Bug:** `CustomerListSerializer` has a dead `get_totalDue` method because `totalDue` is defined as a `DecimalField(source="credit_balance")`. Remove the dead method.
4. **Standardize Lookup Fields:**
   - Consider reverting `lookup_field` to the default `pk` (`id`) in ViewSets (like `CustomerViewSet` and `VendorViewSet`) to maintain REST consistency, and implement a separate search/filter mechanism for `customer_id` strings.
