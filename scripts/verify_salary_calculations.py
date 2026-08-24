import os
import django
import sys
from decimal import Decimal
import datetime

# Set up Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "erp_backend.settings")
django.setup()

from employees.models import Employee, Attendance, EmployeeSalary, SalaryPayment
from employees.services import (
    record_salary_payment,
    get_employee_360_overview,
    get_salary_balance_remaining,
)
from erp_backend.models import BusinessSettings

def run_tests():
    print("============================================================")
    print("           SALARY CALCULATION VERIFICATION SCRIPT           ")
    print("============================================================")

    # 1. Setup 3 Test Employees
    print("\n[1] Setting up 3 Test Employees...")
    emp_a, _ = Employee.objects.get_or_create(
        emp_no="EMP-TEST-01",
        defaults={
            "name": "Test Emp Alpha",
            "email": "alpha@test.com",
            "basic_salary": Decimal("30000.00"),
            "current_salary": Decimal("30000.00"),
            "status": "active",
        }
    )
    emp_b, _ = Employee.objects.get_or_create(
        emp_no="EMP-TEST-02",
        defaults={
            "name": "Test Emp Beta",
            "email": "beta@test.com",
            "basic_salary": Decimal("60000.00"),
            "current_salary": Decimal("60000.00"),
            "status": "active",
        }
    )
    emp_c, _ = Employee.objects.get_or_create(
        emp_no="EMP-TEST-03",
        defaults={
            "name": "Test Emp Gamma",
            "email": "gamma@test.com",
            "basic_salary": Decimal("20000.00"),
            "current_salary": Decimal("20000.00"),
            "status": "active",
        }
    )
    print("Test employees created/retrieved successfully.")

    # 2. Seed Attendance Data
    print("\n[2] Seeding Attendance Data...")
    
    # Employee A: 31 days present in May 2026
    for day in range(1, 32):
        Attendance.objects.update_or_create(
            employee=emp_a,
            date=datetime.date(2026, 5, day),
            defaults={"status": "present"}
        )
    
    # Employee B: 15 present, 2 half_day in May 2026
    for day in range(1, 16):
        Attendance.objects.update_or_create(
            employee=emp_b,
            date=datetime.date(2026, 5, day),
            defaults={"status": "present"}
        )
    for day in range(16, 18):
        Attendance.objects.update_or_create(
            employee=emp_b,
            date=datetime.date(2026, 5, day),
            defaults={"status": "half_paid"}
        )
    for day in range(18, 32):
         Attendance.objects.update_or_create(
            employee=emp_b,
            date=datetime.date(2026, 5, day),
            defaults={"status": "absent"}
        )

    # Employee C: 8 days present in August 2026 (Live month)
    # Using timezone.now() for August 2026 since we need to test live mid-month settlement 
    # which depends on timezone.now() in get_employee_360_overview
    from django.utils import timezone
    now = timezone.now()
    
    # Mark 8 days as present in current month
    for day in range(1, 9):
        Attendance.objects.update_or_create(
            employee=emp_c,
            date=datetime.date(now.year, now.month, day),
            defaults={"status": "present"}
        )
    print("Attendance data seeded successfully.")

    # 3. Test Scenario 1: `month_days` Setting (Employee A)
    print("\n[3] Test Scenario 1: `month_days` Setting (Employee A)")
    biz_settings = BusinessSettings.get_solo()
    biz_settings.salary_calculation_basis = 'month_days'
    biz_settings.save()

    payload_a = {
        "month": 5,
        "year": 2026,
        "bonus": 2000,
        "deductions": 500,
        "amount": 31500,
    }
    salary_a = record_salary_payment(emp_a, payload_a)
    
    balance_a = get_salary_balance_remaining(salary_a)
    expected_balance_a = Decimal("0.00")
    pass_a = (balance_a == expected_balance_a and salary_a.status == 'paid')

    print(f"  Divisor: {salary_a.month_days}")
    print(f"  Earned: {salary_a.net_salary}")
    print(f"  Balance: {balance_a}, Status: {salary_a.status}")


    # 4. Test Scenario 2: `fixed_30` Setting & Multi-Installment Payments (Employee B)
    print("\n[4] Test Scenario 2: `fixed_30` Setting & Multi-Installment Payments (Employee B)")
    biz_settings.salary_calculation_basis = 'fixed_30'
    biz_settings.save()

    payload_b_1 = {
        "month": 5,
        "year": 2026,
        "amount": 10000,
    }
    
    # Reset existing payments for emp_b for clean test if it was run before
    b_salary_qs = EmployeeSalary.objects.filter(employee=emp_b, month=5, year=2026)
    if b_salary_qs.exists():
        SalaryPayment.objects.filter(salary=b_salary_qs.first()).delete()
        # Need to recalculate amount_paid on the salary obj
        s_b = b_salary_qs.first()
        s_b.amount_paid = Decimal("0.00")
        s_b.status = "pending"
        s_b.save()
    
    salary_b = record_salary_payment(emp_b, payload_b_1)
    balance_b_1 = get_salary_balance_remaining(salary_b)
    expected_b_1 = Decimal("22000.00")
    pass_b_1 = (balance_b_1 == expected_b_1 and salary_b.status == 'partial')
    print(f"  Payment 1: Amount = 10000.00")
    print(f"    Expected Balance: {expected_b_1} | Actual: {balance_b_1} | Status: {salary_b.status}")

    # Payment 2
    payload_b_2 = {
        "month": 5,
        "year": 2026,
        "amount": 15000,
    }
    salary_b = record_salary_payment(emp_b, payload_b_2)
    balance_b_2 = get_salary_balance_remaining(salary_b)
    expected_b_2 = Decimal("7000.00")
    pass_b_2 = (balance_b_2 == expected_b_2 and salary_b.status == 'partial')
    print(f"  Payment 2: Amount = 15000.00")
    print(f"    Expected Balance: {expected_b_2} | Actual: {balance_b_2} | Status: {salary_b.status}")

    # Payment 3
    payload_b_3 = {
        "month": 5,
        "year": 2026,
        "amount": 7000,
    }
    salary_b = record_salary_payment(emp_b, payload_b_3)
    balance_b_3 = get_salary_balance_remaining(salary_b)
    expected_b_3 = Decimal("0.00")
    pass_b_3 = (balance_b_3 == expected_b_3 and salary_b.status == 'paid')
    print(f"  Payment 3: Amount = 7000.00")
    print(f"    Expected Balance: {expected_b_3} | Actual: {balance_b_3} | Status: {salary_b.status}")
    
    pass_b = pass_b_1 and pass_b_2 and pass_b_3


    # 5. Test Scenario 3: `working_days` Setting & Live Settlement Check (Employee C)
    print("\n[5] Test Scenario 3: `working_days` Setting & Live Settlement Check (Employee C)")
    biz_settings.salary_calculation_basis = 'working_days'
    biz_settings.standard_working_days = 26
    biz_settings.save()

    overview_c = get_employee_360_overview(emp_c)
    salary_info_c = overview_c.get("salaryInfo", {})
    
    expected_days_c = 8.0
    daily_rate_c = Decimal("20000.00") / Decimal("26")
    expected_accrued_c = float(round(Decimal("8") * daily_rate_c, 2))
    
    actual_days_c = salary_info_c.get("currentMonthDaysWorked")
    actual_accrued_c = salary_info_c.get("currentMonthAccrued")
    
    pass_c = (actual_days_c == expected_days_c and actual_accrued_c == expected_accrued_c)

    print(f"  Live Month Days Worked: Expected: {expected_days_c} | Actual: {actual_days_c}")
    print(f"  Live Month Accrued: Expected: {expected_accrued_c} | Actual: {actual_accrued_c}")

    # 6. Output Summary
    print("\n============================================================")
    print("                    TEST SUMMARY RESULTS                    ")
    print("============================================================")
    
    print(f"{'Employee Name':<20} | {'Setting Mode':<15} | {'Status':<10}")
    print("-" * 50)
    print(f"{'Test Emp Alpha':<20} | {'month_days':<15} | {'PASS' if pass_a else 'FAIL':<10}")
    print(f"{'Test Emp Beta':<20} | {'fixed_30':<15} | {'PASS' if pass_b else 'FAIL':<10}")
    print(f"{'Test Emp Gamma':<20} | {'working_days':<15} | {'PASS' if pass_c else 'FAIL':<10}")
    
    print("\nAll tests executed. Test data remains preserved in the database for manual UI/Postman inspection.")
    print("============================================================")


if __name__ == "__main__":
    run_tests()
