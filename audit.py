import json
import random
import urllib.request
import urllib.error
from decimal import Decimal

BASE_URL = "http://localhost:8000"
headers = {"Content-Type": "application/json"}

report = {
    "endpoints_tested": 0,
    "failed_endpoints": [],
    "passed_endpoints": [],
    "findings": []
}

def log_success(method, url):
    report["endpoints_tested"] += 1
    report["passed_endpoints"].append(f"{method} {url}")

def log_error(method, url, payload, status_code, response_text, cause):
    report["endpoints_tested"] += 1
    report["failed_endpoints"].append({
        "method": method,
        "url": url,
        "payload": payload,
        "status_code": status_code,
        "response": response_text,
        "cause": cause
    })
    print(f"FAILED: {method} {url} - {status_code} - {cause}")

def make_request(method, endpoint, payload=None):
    url = f"{BASE_URL}{endpoint}"
    data = None
    if payload:
        data = json.dumps(payload).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            res_text = response.read().decode('utf-8')
            status = response.status
            log_success(method, endpoint)
            if status != 204 and res_text:
                return json.loads(res_text)
            return True
    except urllib.error.HTTPError as e:
        res_text = e.read().decode('utf-8')
        log_error(method, endpoint, payload, e.code, res_text, "HTTP Error")
        return None
    except Exception as e:
        log_error(method, endpoint, payload, 0, str(e), "Exception")
        return None

print("Phase 2: Authentication")
auth_payload = {"username": "admin", "password": "admin"}
auth_res = make_request("POST", "/api/auth/login/", auth_payload)

if auth_res and "access" in auth_res:
    headers["Authorization"] = f"Bearer {auth_res['access']}"
    print("Authentication successful.")
else:
    print("Authentication failed.")
    exit(1)

print("\nPhase 3: Testing Endpoints")

# 1. User Me
make_request("GET", "/api/auth/me/")

# 2. Settings & Backup
make_request("GET", "/api/settings/backup/")
backup_payload = {"directory": "/tmp/backup", "frequency": "daily", "retention_days": 7}
make_request("PUT", "/api/settings/backup/", backup_payload)
make_request("POST", "/api/settings/backup/trigger/")

# 3. Sales & Customers
customer_phone = f"999888{random.randint(1000, 9999)}"
customer_payload = {
    "customerName": "Test QA Customer",
    "customerType": "permanent",
    "phone": customer_phone,
    "openingCredit": "100.00",
}
customer = make_request("POST", "/api/sales/customers/", customer_payload)

if customer:
    customer_id = customer['customerId']
    customer_pk = customer['id']
    make_request("GET", f"/api/sales/customers/{customer_id}/")
    make_request("GET", "/api/sales/customers/")
    
    # Create Invoice Draft
    invoice_payload = {
        "customer_data": {"phone": customer_phone, "customer_name": "Test QA Customer", "customer_type": "permanent"},
        "payment_term": "Credit",
        "items": [
            {"item_name": "Test Item 1", "quantity": "2", "rate": "50.00", "discount": "0"}
        ]
    }
    invoice = make_request("POST", "/api/sales/invoices/", invoice_payload)
    if invoice:
        invoice_id = invoice['id']
        make_request("GET", f"/api/sales/invoices/{invoice_id}/")
        
        # Upgrade to Saved by paying
        payment_payload = {
            "customer": customer_pk,
            "invoice": invoice_id,
            "amount_received": "100.00",
            "method": "Cash"
        }
        make_request("POST", "/api/sales/payments/", payment_payload)
        
        # Fetch Ledger
        ledger = make_request("GET", f"/api/sales/customers/{customer_id}/ledger/")
        if ledger:
            # Audit ledger for duplicates or FIFO calculation errors
            entries = ledger.get("results", [])
            print(f"Customer ledger entries: {len(entries)}")
            
# 4. Purchase & Vendors
vendor_phone = f"111222{random.randint(1000, 9999)}"
vendor_payload = {
    "vendorName": "Test QA Vendor",
    "phone": vendor_phone,
    "openingPayable": "200.00"
}
vendor = make_request("POST", "/api/purchase/vendors/", vendor_payload)
if vendor:
    vendor_id = vendor['vendorId']
    vendor_pk = vendor['id']
    make_request("GET", f"/api/purchase/vendors/{vendor_id}/")
    
    pinvoice_payload = {
        "vendor": vendor_pk,
        "payment_term": "Credit",
        "items": [
            {"product_name": "Test PItem 1", "quantity": "5", "purchase_price": "20.00"}
        ]
    }
    pinv = make_request("POST", "/api/purchase/invoices/", pinvoice_payload)
    if pinv:
        pinv_pk = pinv['id']
        
        vpayment_payload = {
            "vendor": vendor_pk,
            "invoice": pinv.get('invoiceNumber'),
            "amount_paid": "50.00",
            "method": "Cash"
        }
        make_request("POST", "/api/purchase/vendor-payments/", vpayment_payload)
        
        vledger = make_request("GET", f"/api/purchase/vendors/{vendor_id}/ledger/")
        if vledger:
            ventries = vledger.get("results", [])
            print(f"Vendor ledger entries: {len(ventries)}")

# 5. Expenses
expense_payload = {
    "category": "Office Supplies",
    "amount": "15.00",
    "payment_method": "Cash"
}
expense = make_request("POST", "/api/purchase/expenses/", expense_payload)
make_request("GET", "/api/purchase/expenses/")
make_request("GET", "/api/purchase/daily-outflows/")

with open("audit_results.json", "w") as f:
    json.dump(report, f, indent=4)

print("Audit script finished.")
