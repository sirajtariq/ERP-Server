# Dashboard API Documentation

This document explains the available API endpoints in the `dashboard` module, the data they return, and the calculation logic behind each metric.

## 1. Dashboard Cards API
**Endpoint:** `GET /api/dashboard/cards/`

This API returns the aggregated metrics for the dashboard summary cards.

### Query Parameters
- `from_date` (optional): Start date in `YYYY-MM-DD` format.
- `to_date` (optional): End date in `YYYY-MM-DD` format.

> **Note:** If no dates are provided, the API calculates metrics across the entire database history. 
> The `receivable` and `supplier_payable` metrics always represent the **current overall outstanding balance** and are immune to the date filters (as they are balance sheet items).

### Returned Metrics & Calculations
| Metric | Description / Calculation |
| :--- | :--- |
| `total_sales` | Sum of `net_total` for all **Saved** `SalesInvoice` records in the given date range. |
| `receivable` | Total `credit_balance` across all `Customer` records (Current outstanding total). |
| `profit` | Calculated dynamically as: `(Total Sales) - (Total Purchases + Outgoing Expenses)` within the date range. |
| `cash_sales` | Sum of `net_total` for **Saved** `SalesInvoice` records where `payment_term` is **'Cash'**. |
| `credit_sales` | Sum of `net_total` for **Saved** `SalesInvoice` records where `payment_term` is **'Credit'**. |
| `outgoing_expense` | Sum of `amount` for all `Expense` records in the given date range. |
| `supplier_payable`| Total `payable_balance` across all `Vendor` (Supplier) records (Current outstanding total). |
| `supplier_paid` | Sum of `amount_paid` from all `VendorPayment` records in the given date range. |
| `incoming_cash` | Sum of `amount_received` from all `PaymentReceived` records in the given date range. |

---

## 2. Dashboard Charts API
**Endpoint:** `GET /api/dashboard/charts/`

This API generates monthly aggregated data specifically structured for the Income vs Expense and Monthly Breakdown charts on the dashboard.

### Query Parameters
- `from_date` (optional): Start date in `YYYY-MM-DD` format.
- `to_date` (optional): End date in `YYYY-MM-DD` format.

> **Note:** If `from_date` is not provided, the system intelligently scans `SalesInvoice`, `PurchaseInvoice`, and `Expense` records to find the **absolute earliest transaction date**. It then generates the monthly breakdown from that starting month all the way up to `to_date` (or today).

### Response Structure & Calculations
Returns an array of objects, each representing a single month:

```json
[
  {
    "month": "Jan",
    "income": 45000.0,
    "expense": 18500.0
  },
  {
    "month": "Feb",
    "income": 50000.0,
    "expense": 22000.0
  }
]
```

| Field | Description / Calculation |
| :--- | :--- |
| `month` | The abbreviated month name (e.g., "Jan", "Feb"). |
| `income` | Sum of `net_total` for all **Saved** `SalesInvoice` records belonging to that specific month. |
| `expense` | Sum of `net_total` for all **Saved** `PurchaseInvoice` records **PLUS** the sum of `amount` for all `Expense` records belonging to that specific month. |
