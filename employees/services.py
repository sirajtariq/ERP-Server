import datetime
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Sum, Avg, Q, F
from django.utils import timezone

from employees.models import (
    Employee,
    Attendance,
    EmployeeIncrement,
    SalaryAdvance,
    EmployeeSalary,
    SalaryPayment,
)


def _quantize_decimal(value: Decimal) -> Decimal:
    """Helper to round decimal values to 2 decimal places using ROUND_HALF_UP."""
    if value is None:
        return Decimal("0.00")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def num_to_words(number) -> str:
    """Helper function to convert monetary decimal to English words for payslips."""
    try:
        val = int(Decimal(str(number)))
    except (ValueError, TypeError):
        return "Zero Dollars"

    if val == 0:
        return "Zero Dollars"

    units = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
             "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def _convert_below_thousand(n):
        if n == 0:
            return ""
        elif n < 20:
            return units[n] + " "
        elif n < 100:
            return tens[n // 10] + (" " + units[n % 10] if n % 10 != 0 else "") + " "
        else:
            return units[n // 100] + " Hundred " + (_convert_below_thousand(n % 100) if n % 100 != 0 else "")

    if val < 0:
        return "Minus " + num_to_words(abs(val))

    words = ""
    billions = val // 1000000000
    millions = (val % 1000000000) // 1000000
    thousands = (val % 1000000) // 1000
    remainder = val % 1000

    if billions > 0:
        words += _convert_below_thousand(billions) + "Billion "
    if millions > 0:
        words += _convert_below_thousand(millions) + "Million "
    if thousands > 0:
        words += _convert_below_thousand(thousands) + "Thousand "
    if remainder > 0:
        words += _convert_below_thousand(remainder)

    return words.strip() + " Only"


def calculate_payroll_global_kpis() -> dict:
    """
    Computes global KPI card summaries for active non-deleted employees:
    - activeEmployees: count of active employees
    - monthlyPayroll: total current_salary of active employees
    - averageSalary: average current_salary of active employees
    - advanceOutstanding: total unrecovered advance balance across all employees
    """
    active_qs = Employee.objects.filter(status="active", is_deleted=False)
    active_count = active_qs.count()

    monthly_payroll_agg = active_qs.aggregate(total=Sum("current_salary"))["total"] or Decimal("0.00")
    monthly_payroll = _quantize_decimal(monthly_payroll_agg)

    if active_count > 0:
        avg_salary = _quantize_decimal(monthly_payroll / Decimal(active_count))
    else:
        avg_salary = Decimal("0.00")

    # Advance outstanding
    unrecovered_advances = SalaryAdvance.objects.filter(
        employee__is_deleted=False
    ).exclude(status="recovered")

    advance_out = Decimal("0.00")
    for adv in unrecovered_advances:
        balance = adv.amount - adv.recovered_amount
        if balance > Decimal("0.00"):
            advance_out += balance

    advance_outstanding = _quantize_decimal(advance_out)

    return {
        "activeEmployees": active_count,
        "monthlyPayroll": monthly_payroll,
        "averageSalary": avg_salary,
        "advanceOutstanding": advance_outstanding,
    }


def calculate_employee_advance_balance(employee: Employee) -> Decimal:
    """
    Computes the total outstanding unrecovered salary advances for an employee.
    """
    advances = SalaryAdvance.objects.filter(
        employee=employee,
        status__in=["pending", "partial"]
    )
    balance = Decimal("0.00")
    for adv in advances:
        diff = adv.amount - adv.recovered_amount
        if diff > Decimal("0.00"):
            balance += diff
    return _quantize_decimal(balance)


def calculate_month_attendance_summary(employee: Employee, month: int, year: int) -> dict:
    """
    Analyzes attendance records for an employee for the given month and year:
    - totalWorkingDays (default 30)
    - presentDays, absentDays, halfDays, paidLeaves, unpaidLeaves
    - perDayRate = current_salary / totalWorkingDays
    - attendanceDeduction = (absentDays * perDayRate) + (halfDays * perDayRate / 2)
    """
    total_working_days = 30
    records = Attendance.objects.filter(
        employee=employee,
        date__year=year,
        date__month=month
    )

    present_days = records.filter(status="present").count()
    absent_days = records.filter(status__in=["absent", "unpaid_leave"]).count()
    half_days = records.filter(status="half_day").count()
    paid_leaves = records.filter(status="paid_leave").count()

    per_day_rate = _quantize_decimal(employee.current_salary / Decimal(total_working_days))
    
    # attendanceDeduction = (absent_days * per_day_rate) + (half_days * per_day_rate / 2)
    absent_deduction = Decimal(absent_days) * per_day_rate
    half_day_deduction = Decimal(half_days) * (per_day_rate / Decimal(2))
    attendance_deduction = _quantize_decimal(absent_deduction + half_day_deduction)

    effective_absent_days = _quantize_decimal(Decimal(absent_days) + (Decimal(half_days) * Decimal("0.5")))

    return {
        "totalWorkingDays": total_working_days,
        "presentDays": present_days,
        "absentDays": effective_absent_days,
        "rawAbsentDays": absent_days,
        "halfDays": half_days,
        "paidLeaves": paid_leaves,
        "perDayRate": per_day_rate,
        "attendanceDeduction": attendance_deduction,
    }


@transaction.atomic
def process_fifo_advance_recovery(employee: Employee, deduction_amount: Decimal):
    """
    Loops through pending/partial SalaryAdvance records for the employee ordered by date, id ASC.
    Allocates deduction_amount sequentially to each advance record until fully recovered.
    """
    deduction_amount = _quantize_decimal(Decimal(str(deduction_amount)))
    if deduction_amount <= Decimal("0.00"):
        return

    advances = SalaryAdvance.objects.filter(
        employee=employee,
        status__in=["pending", "partial"]
    ).select_for_update().order_by("date", "id")

    remaining_to_deduct = deduction_amount

    for adv in advances:
        unrecovered = adv.amount - adv.recovered_amount
        if unrecovered <= Decimal("0.00"):
            adv.status = "recovered"
            adv.save()
            continue

        if remaining_to_deduct >= unrecovered:
            adv.recovered_amount = adv.amount
            adv.status = "recovered"
            remaining_to_deduct -= unrecovered
        else:
            adv.recovered_amount = _quantize_decimal(adv.recovered_amount + remaining_to_deduct)
            adv.status = "partial"
            remaining_to_deduct = Decimal("0.00")

        adv.save()

        if remaining_to_deduct <= Decimal("0.00"):
            break


@transaction.atomic
def record_salary_payment(employee: Employee, payload: dict) -> EmployeeSalary:
    """
    Records or updates a month's salary for an employee and optionally logs an installment payment.
    Calculates net_salary = current_salary + bonus - deductions - advance_deduction - attendance_deduction.
    Executes inside transaction.atomic for concurrency safety.
    """
    employee = Employee.objects.select_for_update().get(id=employee.id)

    month = int(payload.get("month"))
    year = int(payload.get("year"))
    working_days = int(payload.get("workingDays", 30))

    bonus = _quantize_decimal(Decimal(str(payload.get("bonus", "0.00"))))
    deductions = _quantize_decimal(Decimal(str(payload.get("deductions", "0.00"))))
    advance_deduction = _quantize_decimal(Decimal(str(payload.get("advanceDeduction", "0.00"))))

    # Calculate attendance deduction from summary if absent_days or attendance_deduction is omitted
    att_summary = calculate_month_attendance_summary(employee, month, year)
    
    if "attendanceDeduction" in payload and payload["attendanceDeduction"] is not None:
        attendance_deduction = _quantize_decimal(Decimal(str(payload["attendanceDeduction"])))
        absent_days = Decimal(str(payload.get("absentDays", att_summary["absentDays"])))
    else:
        attendance_deduction = att_summary["attendanceDeduction"]
        absent_days = att_summary["absentDays"]

    # Calculate net salary
    net_salary = _quantize_decimal(
        employee.current_salary + bonus - deductions - advance_deduction - attendance_deduction
    )

    slip_no = payload.get("slipNo")
    if not slip_no:
        slip_no = f"PS-{year}{month:02d}-{employee.id:04d}"

    salary_obj, created = EmployeeSalary.objects.select_for_update().get_or_create(
        employee=employee,
        month=month,
        year=year,
        defaults={
            "slip_no": slip_no,
            "basic_salary": employee.current_salary,
            "working_days": working_days,
            "absent_days": absent_days,
            "attendance_deduction": attendance_deduction,
            "bonus": bonus,
            "deductions": deductions,
            "advance_deduction": advance_deduction,
            "net_salary": net_salary,
            "status": "pending",
        }
    )

    if not created:
        salary_obj.basic_salary = employee.current_salary
        salary_obj.working_days = working_days
        salary_obj.absent_days = absent_days
        salary_obj.attendance_deduction = attendance_deduction
        salary_obj.bonus = bonus
        salary_obj.deductions = deductions
        salary_obj.advance_deduction = advance_deduction
        salary_obj.net_salary = net_salary
        salary_obj.save()

    # Process FIFO advance recovery if advance_deduction > 0
    if advance_deduction > Decimal("0.00"):
        process_fifo_advance_recovery(employee, advance_deduction)

    # Process Payment Installment if amount provided
    payment_amount = _quantize_decimal(Decimal(str(payload.get("amount", "0.00"))))
    if payment_amount > Decimal("0.00"):
        payment_date = payload.get("paymentDate") or timezone.now().date()
        payment_method = payload.get("paymentMethod", "Cash")
        paid_by = payload.get("paidBy", "Finance Manager")
        remarks = payload.get("remarks", "")

        SalaryPayment.objects.create(
            salary=salary_obj,
            payment_date=payment_date,
            amount=payment_amount,
            payment_method=payment_method,
            paid_by=paid_by,
            remarks=remarks,
        )

    # Re-evaluate status based on total payments
    total_paid_agg = salary_obj.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    total_paid = _quantize_decimal(total_paid_agg)

    if total_paid >= salary_obj.net_salary and salary_obj.net_salary > Decimal("0.00"):
        salary_obj.status = "paid"
    elif total_paid > Decimal("0.00"):
        salary_obj.status = "partial"
    else:
        salary_obj.status = "pending"

    salary_obj.save()
    return salary_obj


@transaction.atomic
def process_salary_increment(employee: Employee, payload: dict) -> EmployeeIncrement:
    """
    Saves an increment record and updates employee's current_salary inside transaction.atomic.
    """
    employee = Employee.objects.select_for_update().get(id=employee.id)

    effective_date = payload.get("effectiveDate") or timezone.now().date()
    increment_amount = _quantize_decimal(Decimal(str(payload["incrementAmount"])))
    previous_salary = employee.current_salary
    new_salary = _quantize_decimal(previous_salary + increment_amount)
    approved_by = payload.get("approvedBy", "")
    reason = payload.get("reason", "Performance Increment")

    increment = EmployeeIncrement.objects.create(
        employee=employee,
        effective_date=effective_date,
        previous_salary=previous_salary,
        increment_amount=increment_amount,
        new_salary=new_salary,
        approved_by=approved_by,
        reason=reason,
    )

    employee.current_salary = new_salary
    employee.save()

    return increment


@transaction.atomic
def issue_salary_advance(employee: Employee, payload: dict) -> SalaryAdvance:
    """
    Issues a salary advance record inside transaction.atomic.
    """
    advance_date = payload.get("date") or timezone.now().date()
    amount = _quantize_decimal(Decimal(str(payload["amount"])))
    payment_method = payload.get("paymentMethod", "Cash")
    reason = payload.get("reason", "Personal Advance")

    advance = SalaryAdvance.objects.create(
        employee=employee,
        date=advance_date,
        amount=amount,
        recovered_amount=Decimal("0.00"),
        payment_method=payment_method,
        reason=reason,
        status="pending",
    )
    return advance


@transaction.atomic
def toggle_employee_status(employee: Employee, payload: dict) -> Employee:
    """
    Toggles employee status between 'active' and 'inactive'.
    Optionally sets leaving_date or rejoining_date.
    """
    employee = Employee.objects.select_for_update().get(id=employee.id)
    new_status = payload.get("status")
    
    if new_status not in ["active", "inactive"]:
        raise ValueError("Status must be either 'active' or 'inactive'.")

    employee.status = new_status

    if new_status == "inactive":
        employee.leaving_date = payload.get("leavingDate") or timezone.now().date()
    elif new_status == "active":
        employee.rejoining_date = payload.get("rejoiningDate") or timezone.now().date()

    employee.save()
    return employee


def generate_payslip_data(salary_instance: EmployeeSalary) -> dict:
    """
    Produces formatted printable JSON payload for a payslip document.
    Includes Company Info, Employee Meta, Attendance breakdown, Earnings, Deductions, Net Salary, Amount in Words.
    """
    employee = salary_instance.employee
    total_paid_agg = salary_instance.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    paid_amount = _quantize_decimal(total_paid_agg)
    balance_remaining = _quantize_decimal(salary_instance.net_salary - paid_amount)

    basic = _quantize_decimal(salary_instance.basic_salary)
    bonus = _quantize_decimal(salary_instance.bonus)
    total_earnings = _quantize_decimal(basic + bonus)

    att_ded = _quantize_decimal(salary_instance.attendance_deduction)
    adv_ded = _quantize_decimal(salary_instance.advance_deduction)
    other_ded = _quantize_decimal(salary_instance.deductions)
    total_deductions = _quantize_decimal(att_ded + adv_ded + other_ded)

    net_salary = _quantize_decimal(salary_instance.net_salary)
    amount_in_words = num_to_words(net_salary)

    return {
        "company": {
            "name": "LenDen ERP Systems",
            "address": "Headquarters, Industrial Zone, Pakistan",
            "phone": "+92-42-111-222-333",
            "email": "payroll@lenden.erp",
        },
        "employee": {
            "id": employee.id,
            "empNo": employee.emp_no,
            "name": employee.name,
            "designation": employee.designation,
            "department": employee.department,
            "joiningDate": employee.joining_date,
            "cnic": employee.cnic,
            "phone": employee.phone,
        },
        "payslip": {
            "slipNo": salary_instance.slip_no,
            "month": salary_instance.month,
            "year": salary_instance.year,
            "issueDate": salary_instance.created_at.date(),
            "status": salary_instance.status,
        },
        "attendance": {
            "workingDays": salary_instance.working_days,
            "absentDays": salary_instance.absent_days,
            "attendanceDeduction": att_ded,
        },
        "earnings": {
            "basicSalary": basic,
            "bonus": bonus,
            "totalEarnings": total_earnings,
        },
        "deductions": {
            "attendanceDeduction": att_ded,
            "advanceDeduction": adv_ded,
            "otherDeductions": other_ded,
            "totalDeductions": total_deductions,
        },
        "summary": {
            "netSalary": net_salary,
            "paidAmount": paid_amount,
            "balanceRemaining": balance_remaining,
            "amountInWords": amount_in_words,
        },
        "payments": [
            {
                "id": p.id,
                "paymentDate": p.payment_date,
                "amount": _quantize_decimal(p.amount),
                "paymentMethod": p.payment_method,
                "paidBy": p.paid_by,
                "remarks": p.remarks,
            }
            for p in salary_instance.payments.all()
        ]
    }


def generate_employee_360_timeline(employee: Employee) -> dict:
    """
    Generates a full 360-degree timeline and aggregated detail object for an employee.
    Contains personal details, salary statistics, advance balance, history tabs, and event stream.
    """
    advance_balance = calculate_employee_advance_balance(employee)

    # Salaries history
    salaries = EmployeeSalary.objects.filter(employee=employee).order_by("-year", "-month")
    salary_list = []
    total_salaries_paid = Decimal("0.00")

    for s in salaries:
        paid_agg = s.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        paid_amt = _quantize_decimal(paid_agg)
        total_salaries_paid += paid_amt
        salary_list.append({
            "id": s.id,
            "slipNo": s.slip_no,
            "month": s.month,
            "year": s.year,
            "basicSalary": _quantize_decimal(s.basic_salary),
            "workingDays": s.working_days,
            "absentDays": s.absent_days,
            "attendanceDeduction": _quantize_decimal(s.attendance_deduction),
            "bonus": _quantize_decimal(s.bonus),
            "deductions": _quantize_decimal(s.deductions),
            "advanceDeduction": _quantize_decimal(s.advance_deduction),
            "netSalary": _quantize_decimal(s.net_salary),
            "paidAmount": paid_amt,
            "balanceRemaining": _quantize_decimal(s.net_salary - paid_amt),
            "status": s.status,
            "createdAt": s.created_at,
        })

    # Increments history
    increments = EmployeeIncrement.objects.filter(employee=employee).order_by("-effective_date")
    increment_list = [
        {
            "id": inc.id,
            "effectiveDate": inc.effective_date,
            "previousSalary": _quantize_decimal(inc.previous_salary),
            "incrementAmount": _quantize_decimal(inc.increment_amount),
            "newSalary": _quantize_decimal(inc.new_salary),
            "approvedBy": inc.approved_by,
            "reason": inc.reason,
            "createdAt": inc.created_at,
        }
        for inc in increments
    ]

    # Advances history
    advances = SalaryAdvance.objects.filter(employee=employee).order_by("-date")
    advance_list = [
        {
            "id": adv.id,
            "date": adv.date,
            "amount": _quantize_decimal(adv.amount),
            "recoveredAmount": _quantize_decimal(adv.recovered_amount),
            "remainingAmount": _quantize_decimal(adv.amount - adv.recovered_amount),
            "paymentMethod": adv.payment_method,
            "reason": adv.reason,
            "status": adv.status,
            "createdAt": adv.created_at,
        }
        for adv in advances
    ]

    # Recent attendances
    recent_attendances = Attendance.objects.filter(employee=employee).order_by("-date")[:30]
    attendance_list = [
        {
            "id": att.id,
            "date": att.date,
            "status": att.status,
            "checkIn": att.check_in,
            "checkOut": att.check_out,
            "remarks": att.remarks,
        }
        for att in recent_attendances
    ]

    # Construct chronological 360 event stream timeline
    timeline = []
    # Joining event
    if employee.joining_date:
        timeline.append({
            "type": "joining",
            "date": employee.joining_date,
            "title": "Employee Joined",
            "description": f"Joined as {employee.designation} in {employee.department} department with basic salary of {_quantize_decimal(employee.basic_salary)}.",
        })
    # Increment events
    for inc in increments:
        timeline.append({
            "type": "increment",
            "date": inc.effective_date,
            "title": f"Salary Increment: +{_quantize_decimal(inc.increment_amount)}",
            "description": f"Salary increased from {_quantize_decimal(inc.previous_salary)} to {_quantize_decimal(inc.new_salary)}. Reason: {inc.reason}",
        })
    # Advance events
    for adv in advances:
        timeline.append({
            "type": "advance",
            "date": adv.date,
            "title": f"Salary Advance Taken: {_quantize_decimal(adv.amount)}",
            "description": f"Advance of {_quantize_decimal(adv.amount)} issued via {adv.payment_method}. Reason: {adv.reason}",
        })
    # Salary events
    for s in salaries:
        timeline.append({
            "type": "salary",
            "date": datetime.date(s.year, s.month, 1),
            "title": f"Salary Generated for {s.month}/{s.year}",
            "description": f"Net salary: {_quantize_decimal(s.net_salary)} (Status: {s.status.title()})",
        })

    # Sort timeline descending by date
    timeline.sort(key=lambda x: str(x["date"]), reverse=True)

    return {
        "employee": {
            "id": employee.id,
            "empNo": employee.emp_no,
            "name": employee.name,
            "designation": employee.designation,
            "department": employee.department,
            "joiningDate": employee.joining_date,
            "leavingDate": employee.leaving_date,
            "rejoiningDate": employee.rejoining_date,
            "basicSalary": _quantize_decimal(employee.basic_salary),
            "currentSalary": _quantize_decimal(employee.current_salary),
            "phone": employee.phone,
            "email": employee.email,
            "cnic": employee.cnic,
            "address": employee.address,
            "status": employee.status,
            "isDeleted": employee.is_deleted,
            "createdAt": employee.created_at,
            "updatedAt": employee.updated_at,
        },
        "advanceBalance": advance_balance,
        "totalSalariesPaid": _quantize_decimal(total_salaries_paid),
        "salaries": salary_list,
        "increments": increment_list,
        "advances": advance_list,
        "attendances": attendance_list,
        "timeline": timeline,
    }
