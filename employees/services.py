import calendar
import datetime
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from employees.models import (
    Employee,
    Attendance,
    EmployeeIncrement,
    SalaryAdvance,
    EmployeeSalary,
    SalaryPayment,
)


DAY_MAP = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


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


def get_configured_weekly_off_days() -> list:
    """
    Returns list of integer weekdays (0=Mon, 1=Tue, ..., 4=Fri, 5=Sat, 6=Sun) representing weekly off days.
    Checks Django settings override first, or BusinessSettings DB, defaulting to [4] (Friday).
    """
    if hasattr(settings, "WEEKLY_OFF_DAYS") and settings.WEEKLY_OFF_DAYS is not None:
        off_days = settings.WEEKLY_OFF_DAYS
    else:
        off_days = None
        try:
            from erp_backend.models import BusinessSettings
            biz = BusinessSettings.get_solo()
            if hasattr(biz, "weekly_off_days") and biz.weekly_off_days:
                off_days = biz.weekly_off_days
        except Exception:
            pass

    if not off_days:
        off_days = [4]

    if not isinstance(off_days, (list, tuple)):
        off_days = [off_days]

    result = []
    for item in off_days:
        if isinstance(item, int) and 0 <= item <= 6:
            result.append(item)
        elif isinstance(item, str):
            clean_item = item.strip().lower()
            if clean_item in DAY_MAP:
                result.append(DAY_MAP[clean_item])

    return result if result else [4]


def get_configured_salary_calculation_basis() -> str:
    """
    Returns configured salary calculation basis ('working_days', 'fixed_30', or 'month_days').
    Checks Django settings override first, or BusinessSettings DB, defaulting to 'working_days'.
    """
    if hasattr(settings, "SALARY_CALCULATION_BASIS") and settings.SALARY_CALCULATION_BASIS is not None:
        return settings.SALARY_CALCULATION_BASIS

    try:
        from erp_backend.models import BusinessSettings
        biz = BusinessSettings.get_solo()
        if hasattr(biz, "salary_calculation_basis") and biz.salary_calculation_basis:
            return biz.salary_calculation_basis
    except Exception:
        pass

    return "working_days"


def get_advance_remaining_amount(advance: SalaryAdvance) -> Decimal:
    """Zero math helper: calculates remaining balance of a SalaryAdvance instance."""
    diff = advance.amount - advance.recovered_amount
    return _quantize_decimal(diff) if diff > Decimal("0.00") else Decimal("0.00")


def get_salary_balance_remaining(salary: EmployeeSalary) -> Decimal:
    """Zero math helper: calculates remaining balance of an EmployeeSalary instance."""
    diff = salary.net_salary - salary.amount_paid
    return _quantize_decimal(diff) if diff > Decimal("0.00") else Decimal("0.00")


def calculate_payroll_global_kpis() -> dict:
    """
    Computes global KPI card summaries for active non-deleted employees:
    - activeEmployees: count of active employees
    - monthlyPayroll: total current_salary of active employees
    - averageSalary: average current_salary of active employees
    - advanceOutstanding: total unrecovered advance balance across all active non-deleted employees
    """
    active_qs = Employee.objects.filter(status="active", is_deleted=False)
    active_count = active_qs.count()

    monthly_payroll_agg = active_qs.aggregate(total=Sum("current_salary"))["total"] or Decimal("0.00")
    monthly_payroll = _quantize_decimal(monthly_payroll_agg)

    if active_count > 0:
        avg_salary = _quantize_decimal(monthly_payroll / Decimal(active_count))
    else:
        avg_salary = Decimal("0.00")

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
    Computes total outstanding unrecovered salary advances for an employee.
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


def calculate_payable_days_for_month(employee: Employee, month: int, year: int, up_to_date: datetime.date = None) -> Decimal:
    """
    Computes total payable days on a week-by-week basis.
    A weekly off is credited ONLY if all working days in that week (up to 'up_to_date') are marked.
    """
    configured_off_days = get_configured_weekly_off_days()
    weeks = calendar.monthcalendar(year, month)
    
    joining_date = employee.joining_date
    
    total_payable_days = Decimal("0.0")
    explicit_paid_days = Decimal("0.0")
    
    attendance_records = Attendance.objects.filter(
        employee=employee,
        date__year=year,
        date__month=month
    )
    att_dict = {a.date.day: a.status for a in attendance_records}
    
    for week in weeks:
        week_payable = Decimal("0.0")
        has_unmarked = False
        week_offs_count = 0
        week_working_days = 0
        
        for day in week:
            if day == 0:
                continue
                
            d = datetime.date(year, month, day)
            if up_to_date and d > up_to_date:
                continue
                
            if joining_date and d < joining_date:
                continue
                
            is_weekly_off = d.weekday() in configured_off_days
            status = att_dict.get(day)
            
            if is_weekly_off:
                week_offs_count += 1
                if status in ['present', 'leave_paid', 'paid_leave', 'half_paid', 'holiday']:
                    week_payable += Decimal("1.0")
                    explicit_paid_days += Decimal("1.0")
                    week_offs_count -= 1
                elif status in ['half_unpaid', 'half_day']:
                    week_payable += Decimal("0.5")
                    explicit_paid_days += Decimal("0.5")
                    week_offs_count -= 1
            else:
                week_working_days += 1
                if not status or status == 'not_marked':
                    has_unmarked = True
                else:
                    if status in ['present', 'leave_paid', 'paid_leave', 'half_paid', 'holiday']:
                        week_payable += Decimal("1.0")
                        explicit_paid_days += Decimal("1.0")
                    elif status in ['half_unpaid', 'half_day']:
                        week_payable += Decimal("0.5")
                        explicit_paid_days += Decimal("0.5")
                        
        if has_unmarked:
            credited_weekly_offs = 0
        else:
            credited_weekly_offs = week_offs_count
                
        week_payable += Decimal(credited_weekly_offs)
        total_payable_days += week_payable
        
    if explicit_paid_days == Decimal("0.0"):
        return Decimal("0.00")
        
    return total_payable_days


def get_salary_divisor(month: int, year: int) -> Decimal:
    """Returns the Decimal divisor for salary calculation based on settings."""
    try:
        from erp_backend.models import BusinessSettings
        biz = BusinessSettings.get_solo()
        basis = getattr(biz, 'salary_calculation_basis', 'working_days')
        standard_working_days = getattr(biz, 'standard_working_days', None) or 26
    except Exception:
        basis = 'working_days'
        standard_working_days = 26

    if basis == "fixed_30":
        return Decimal("30.0")
    elif basis == "working_days":
        return Decimal(str(standard_working_days))
    else:
        return Decimal(str(calendar.monthrange(year, month)[1]))


def calculate_payroll_for_month(employee: Employee, month: int, year: int, up_to_date: datetime.date = None) -> dict:
    """Enterprise LOP Deduction calculation logic."""
    total_calendar_days = Decimal(calendar.monthrange(year, month)[1])
    base_salary = Decimal(str(employee.current_salary or employee.base_salary or 0))
    
    divisor = get_salary_divisor(month, year)
    payable_days = calculate_payable_days_for_month(employee, month, year, up_to_date=up_to_date)
    
    effective_total_days = Decimal(up_to_date.day) if up_to_date else total_calendar_days
    unpaid_days = max(Decimal('0.0'), effective_total_days - payable_days)
    
    if unpaid_days <= Decimal('0.0'):
        attendance_deduction = Decimal('0.00')
        if not up_to_date:
            earned_basic = base_salary
        else:
            earned_basic = min(base_salary, _quantize_decimal(payable_days * (base_salary / divisor)))
    elif payable_days <= Decimal('0.0'):
        attendance_deduction = base_salary
        earned_basic = Decimal('0.00')
    else:
        exact_daily_rate = base_salary / divisor
        attendance_deduction = _quantize_decimal(unpaid_days * exact_daily_rate)
        earned_basic = max(Decimal('0.00'), base_salary - attendance_deduction)

    return {
        "payable_days": payable_days,
        "unpaid_days": unpaid_days,
        "attendance_deduction": attendance_deduction,
        "earned_basic": earned_basic
    }


def calculate_month_attendance_summary(employee: Employee, month: int, year: int) -> dict:
    """
    Analyzes attendance records for an employee for the given month and year:
    - Dynamic weekly off day evaluation (defaulting to Friday=4).
    - month_days, weekly_offs_count, working_days = month_days - weekly_offs_count.
    - Counts for present, absent, half_paid, half_unpaid, leave_paid, leave_unpaid, not_marked.
    - per_day_rate = current_salary / working_days if working_days > 0 else 0.
    - absent_deduction = absent_days * per_day_rate
    - half_unpaid_deduction = half_unpaid_days * (per_day_rate / 2)
    - leave_unpaid_deduction = leave_unpaid_days * per_day_rate
    - attendance_deduction_total = absent_deduction + half_unpaid_deduction + leave_unpaid_deduction
    """
    month_days = calendar.monthrange(year, month)[1]
    configured_off_days = get_configured_weekly_off_days()

    weekly_offs_count = 0
    for day in range(1, month_days + 1):
        d = datetime.date(year, month, day)
        if d.weekday() in configured_off_days:
            weekly_offs_count += 1

    working_days = month_days - weekly_offs_count
    records = Attendance.objects.filter(
        employee=employee,
        date__year=year,
        date__month=month
    )

    present_days = records.filter(status="present").count()
    absent_days = records.filter(status="absent").count()
    half_paid_days = records.filter(status="half_paid").count()
    half_unpaid_days = records.filter(status__in=["half_unpaid", "half_day"]).count()
    leave_paid_days = records.filter(status__in=["leave_paid", "paid_leave"]).count()
    leave_unpaid_days = records.filter(status__in=["leave_unpaid", "unpaid_leave"]).count()

    marked_days = (
        present_days + absent_days + half_paid_days + half_unpaid_days + leave_paid_days + leave_unpaid_days
    )
    not_marked_days = max(0, working_days - marked_days)

    payroll_data = calculate_payroll_for_month(employee, month, year)
    attendance_deduction = payroll_data["attendance_deduction"]
    earned_basic = payroll_data["earned_basic"]
    
    # We maintain these for API backwards compatibility, but map them to the unified deduction.
    absent_deduction = attendance_deduction
    half_unpaid_deduction = Decimal("0.00")
    leave_unpaid_deduction = Decimal("0.00")
    
    per_day_rate = _quantize_decimal(employee.current_salary / get_salary_divisor(month, year))

    return {
        "monthDays": month_days,
        "weeklyOffs": weekly_offs_count,
        "fridaysCount": weekly_offs_count,
        "fridaysOffDays": weekly_offs_count,
        "workingDays": working_days,
        "totalWorkingDays": working_days,
        "presentDays": present_days,
        "absentDays": Decimal(absent_days),
        "rawAbsentDays": absent_days,
        "halfPaidDays": half_paid_days,
        "halfUnpaidDays": Decimal(half_unpaid_days),
        "halfDays": half_unpaid_days,
        "leavePaidDays": leave_paid_days,
        "paidLeaves": leave_paid_days,
        "leaveUnpaidDays": Decimal(leave_unpaid_days),
        "unpaidLeaves": leave_unpaid_days,
        "notMarkedDays": not_marked_days,
        "perDayRate": per_day_rate,
        "absentDeduction": absent_deduction,
        "halfUnpaidDeduction": half_unpaid_deduction,
        "leaveUnpaidDeduction": leave_unpaid_deduction,
        "attendanceDeduction": attendance_deduction,
        "earnedBasic": earned_basic,
    }


@transaction.atomic
def process_fifo_advance_recovery(employee: Employee, deduction_amount: Decimal):
    """
    Loops through pending/partial SalaryAdvance records for the employee ordered by date, id ASC.
    Allocates deduction_amount sequentially to each advance record until fully recovered.
    Executes inside transaction.atomic with select_for_update for concurrency safety.
    """
    deduction_amount = _quantize_decimal(Decimal(str(deduction_amount)))
    if deduction_amount <= Decimal("0.00"):
        return

    advances = SalaryAdvance.objects.filter(
        employee=employee,
        status__in=["pending", "partial"]
    ).select_for_update().order_by("created_at")

    adv_deduction = deduction_amount

    for adv in advances:
        remaining_unpaid = adv.amount - (adv.recovered_amount or Decimal("0.00"))
        if remaining_unpaid <= Decimal("0.00"):
            adv.status = "recovered"
            adv.save()
            continue

        if adv_deduction >= remaining_unpaid:
            adv_deduction -= remaining_unpaid
            adv.recovered_amount = adv.amount
            adv.status = "recovered"
            adv.save()
        else:
            adv.recovered_amount = (adv.recovered_amount or Decimal("0.00")) + adv_deduction
            adv.status = "partial"
            adv.save()
            adv_deduction = Decimal("0.00")
            break

        if adv_deduction <= Decimal("0.00"):
            break


@transaction.atomic
def record_salary_payment(employee: Employee, payload: dict) -> EmployeeSalary:
    """
    Records or updates a month's salary for an employee and logs installment payment if provided.
    Calculates net_salary = current_salary + bonus - deductions - advance_deduction - attendance_deduction.
    Executes inside transaction.atomic with select_for_update on target records.
    """
    employee = Employee.objects.select_for_update().get(id=employee.id)

    month = int(payload.get("month"))
    year = int(payload.get("year"))

    today = timezone.now().date()
    import calendar
    import datetime
    last_day_of_target_month = calendar.monthrange(year, month)[1]
    target_month_end_date = datetime.date(year, month, last_day_of_target_month)

    if today < target_month_end_date:
        raise ValueError(
            f"Salary for {calendar.month_name[month]} {year} cannot be processed before month-end ({target_month_end_date}). "
            "If you wish to pay early, please issue an Advance instead."
        )

    payroll_data = calculate_payroll_for_month(employee, month, year)
    earned_basic_val = payroll_data.get("earned_basic", Decimal('0.00'))
    payable_days_val = payroll_data.get("payable_days", Decimal('0.0'))

    # Guard: If no attendance is marked or 0 days worked
    if payable_days_val <= Decimal('0.0') or earned_basic_val <= Decimal('0.00'):
        raise ValueError(
            f"Cannot create or pay salary for {calendar.month_name[month]} {year}: "
            "Employee has 0 marked working days (0.00 earned salary). "
            "Please mark attendance first, or issue an Advance if paying early."
        )

    att_summary = calculate_month_attendance_summary(employee, month, year)
    month_days = att_summary["monthDays"]
    working_days = int(payload.get("workingDays", att_summary["workingDays"]))

    absent_days = Decimal(str(payload.get("absentDays", att_summary["absentDays"])))
    half_unpaid_days = Decimal(str(payload.get("halfUnpaidDays", att_summary["halfUnpaidDays"])))
    leave_unpaid_days = Decimal(str(payload.get("leaveUnpaidDays", att_summary["leaveUnpaidDays"])))

    bonus = _quantize_decimal(Decimal(str(payload.get("bonus", "0.00"))))
    deductions = _quantize_decimal(Decimal(str(payload.get("deductions", "0.00"))))
    advance_deduction = _quantize_decimal(Decimal(str(payload.get("advanceDeduction", "0.00"))))

    if "attendanceDeduction" in payload and payload["attendanceDeduction"] is not None:
        attendance_deduction = _quantize_decimal(Decimal(str(payload["attendanceDeduction"])))
        earned_basic = max(Decimal("0.00"), _quantize_decimal(employee.current_salary - attendance_deduction))
    else:
        attendance_deduction = att_summary["attendanceDeduction"]
        earned_basic = att_summary.get("earnedBasic", max(Decimal("0.00"), _quantize_decimal(employee.current_salary - attendance_deduction)))

    # Calculate net salary
    net_salary = max(Decimal("0.00"), _quantize_decimal(earned_basic + bonus - deductions - advance_deduction))

    slip_no = payload.get("slipNo")
    if not slip_no:
        slip_no = f"SS-{year}{month:02d}-{employee.emp_no}"

    salary_obj, created = EmployeeSalary.objects.select_for_update().get_or_create(
        employee=employee,
        month=month,
        year=year,
        defaults={
            "slip_no": slip_no,
            "basic_salary": employee.basic_salary,
            "current_salary": employee.current_salary,
            "working_days": working_days,
            "month_days": month_days,
            "absent_days": absent_days,
            "half_unpaid_days": half_unpaid_days,
            "leave_unpaid_days": leave_unpaid_days,
            "attendance_deduction": attendance_deduction,
            "bonus": bonus,
            "deductions": deductions,
            "advance_deduction": advance_deduction,
            "net_salary": net_salary,
            "amount_paid": Decimal("0.00"),
            "status": "pending",
        }
    )

    if not created:
        salary_obj.basic_salary = employee.basic_salary
        salary_obj.current_salary = employee.current_salary
        salary_obj.working_days = working_days
        salary_obj.month_days = month_days
        salary_obj.absent_days = absent_days
        salary_obj.half_unpaid_days = half_unpaid_days
        salary_obj.leave_unpaid_days = leave_unpaid_days
        salary_obj.attendance_deduction = attendance_deduction
        salary_obj.bonus = bonus
        salary_obj.deductions = deductions
        salary_obj.advance_deduction = advance_deduction
        salary_obj.net_salary = net_salary
        salary_obj.save()

    # Process FIFO advance recovery if advance_deduction > 0 on salary creation
    if created and advance_deduction > Decimal("0.00"):
        process_fifo_advance_recovery(employee, advance_deduction)

    # Process Payment Installment if amount provided
    payment_amount = _quantize_decimal(Decimal(str(payload.get("amount", "0.00"))))
    
    if payment_amount > Decimal("0.00"):
        # Ensure a salary cannot be paid more than its balance_remaining
        current_balance = get_salary_balance_remaining(salary_obj).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        payment_amount = payment_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        
        if not created and (current_balance <= Decimal("0.00") or salary_obj.status == 'paid'):
            raise ValueError("This salary is already fully paid. No further payments allowed.")
            
        if payment_amount > current_balance:
            salary_portion = current_balance
            surplus_amount = payment_amount - current_balance
        else:
            salary_portion = payment_amount
            surplus_amount = Decimal('0.00')

        payment_date = payload.get("paymentDate") or timezone.now().date()
        payment_method = payload.get("paymentMethod", "Cash")
        paid_by = payload.get("paidBy", "Finance Manager")
        remarks = payload.get("remarks", "")

        payment_obj = SalaryPayment.objects.create(
            salary=salary_obj,
            payment_date=payment_date,
            amount=salary_portion,
            payment_method=payment_method,
            paid_by=paid_by,
            remarks=remarks,
        )
        
        if surplus_amount > Decimal('0.00'):
            SalaryAdvance.objects.create(
                employee=employee,
                amount=surplus_amount,
                date=payment_date,
                payment_method=payment_method,
                reason=f"Excess salary payment from {calendar.month_name[month]} {year}",
                status='pending'
            )
            salary_obj._surplus_amount = surplus_amount
            salary_obj._salary_portion = salary_portion

        try:
            from purchase.services import record_auto_expense
            record_auto_expense(
                amount=payment_obj.amount,
                date=payment_obj.payment_date,
                category="Salary",
                description=f"Salary Payment: {employee.name} ({employee.emp_no}) - {salary_obj.month}/{salary_obj.year}",
                payment_method=payment_obj.payment_method,
                paid_by=payment_obj.paid_by,
                reference_type="salary_payment",
                reference_id=payment_obj.id,
            )
        except Exception:
            pass

    # Re-evaluate total payments and status
    total_paid_agg = sum(p.amount for p in salary_obj.payments.all())
    salary_obj.amount_paid = _quantize_decimal(total_paid_agg)
    
    try:
        salary_obj.balance_remaining = max(Decimal('0.00'), salary_obj.net_salary - salary_obj.amount_paid)
        balance_check = salary_obj.balance_remaining
    except Exception:
        balance_check = max(Decimal('0.00'), salary_obj.net_salary - salary_obj.amount_paid)

    salary_obj.status = 'paid' if balance_check == Decimal('0.00') else ('partial' if salary_obj.amount_paid > Decimal('0.00') else 'unpaid')

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


def number_to_words(num):
    """Lightweight, pure-Python helper for amount in words."""
    try:
        val = int(Decimal(str(num)))
    except (ValueError, TypeError):
        return "Zero"

    if val == 0:
        return "Zero"

    units = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
             "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def convert_below_thousand(n):
        if n == 0:
            return ""
        elif n < 20:
            return units[n] + " "
        elif n < 100:
            return tens[n // 10] + (" " + units[n % 10] if n % 10 != 0 else "") + " "
        else:
            return units[n // 100] + " Hundred " + (convert_below_thousand(n % 100) if n % 100 != 0 else "")

    if val < 0:
        return "Minus " + number_to_words(abs(val))

    words = ""
    billions = val // 1000000000
    millions = (val % 1000000000) // 1000000
    thousands = (val % 1000000) // 1000
    remainder = val % 1000

    if billions > 0:
        words += convert_below_thousand(billions) + "Billion "
    if millions > 0:
        words += convert_below_thousand(millions) + "Million "
    if thousands > 0:
        words += convert_below_thousand(thousands) + "Thousand "
    if remainder > 0:
        words += convert_below_thousand(remainder)

    return words.strip() + " Only"


def generate_payslip_data(salary: EmployeeSalary) -> dict:
    """
    Produces formatted printable JSON payload for a payslip document.
    Dynamic version conforming to instructions.
    """
    employee = salary.employee
    
    # 1. Dynamic Company Details Retrieval
    try:
        from erp_backend.models import BusinessSettings
        settings = BusinessSettings.objects.first()
        if not settings and hasattr(BusinessSettings, 'get_solo'):
            settings = BusinessSettings.get_solo()
    except Exception:
        settings = None

    company = {
        "name": getattr(settings, 'company_name', None) or getattr(settings, 'business_name', 'Company Name'),
        "address": getattr(settings, 'address', '') or "Company Address",
        "phone": getattr(settings, 'phone', '') or getattr(settings, 'phone_number', ''),
        "email": getattr(settings, 'email', '') or getattr(settings, 'company_email', '')
    }

    # 2. Dynamic Employee Details
    employee_details = {
        "id": employee.id,
        "empId": employee.emp_no or f"EMP-{employee.id:03d}",
        "name": employee.name or f"{getattr(employee, 'first_name', '')} {getattr(employee, 'last_name', '')}".strip(),
        "designation": str(employee.designation) if employee.designation else "Staff",
        "department": str(employee.department) if employee.department else "General",
        "joiningDate": str(employee.joining_date) if employee.joining_date else None,
        "cnic": employee.cnic,
        "phone": employee.phone
    }

    # 3. Dynamic Payslip Metadata
    issue_date = salary.created_at.strftime('%Y-%m-%d') if salary.created_at else str(timezone.now().date())
    payslip_meta = {
        "slipNo": f"SS-{salary.year}{salary.month:02d}-{employee.emp_no or employee.id}",
        "month": salary.month,
        "year": salary.year,
        "issueDate": issue_date,
        "status": salary.status
    }

    # 4. Dynamic Attendance & Deduction Audit
    year = salary.year
    month = salary.month
    
    att_summary = calculate_month_attendance_summary(employee, month, year)
    month_days = att_summary["monthDays"]
    weekly_offs = att_summary["weeklyOffs"]
    present_days = att_summary["presentDays"]
    half_days = att_summary.get("halfDays", 0)
    
    if salary.working_days is not None:
        working_days = float(salary.working_days)
    else:
        working_days = float(att_summary.get("payable_days", calculate_payroll_for_month(employee, month, year)["payable_days"]))
        
    if salary.absent_days is not None:
        absent_days = float(salary.absent_days)
    else:
        absent_days = float(att_summary["absentDays"])

    # 1. Stored Record Snapshot Mapping
    saved_base_salary = float(getattr(salary, 'current_salary', None) or getattr(salary, 'basic_salary', 0) or getattr(employee, 'current_salary', 0) or 0)
    
    try:
        base_net = float(salary.net_salary or 0)
        base_bonus = float(salary.bonus or 0)
        base_ded = float(salary.deductions or 0)
        base_adv = float(salary.advance_deduction or 0)
        
        saved_earned_salary = float(
            getattr(salary, 'earned_salary', None) or 
            getattr(salary, 'basic_earned', None) or 
            (base_net - base_bonus + base_ded + base_adv)
        )
    except Exception:
        saved_earned_salary = 0.0

    saved_bonus = float(salary.bonus or 0)
    saved_other_deductions = float(salary.deductions or 0)
    saved_advance_deduction = float(salary.advance_deduction or 0)
    saved_net_salary = float(salary.net_salary or 0)
    
    if salary.amount_paid:
        saved_paid_amount = float(salary.amount_paid)
    else:
        saved_paid_amount = float(sum(p.amount for p in salary.payments.all()) or 0)
        
    try:
        saved_balance_remaining = float(get_salary_balance_remaining(salary))
    except Exception:
        saved_balance_remaining = float(saved_net_salary - saved_paid_amount)

    # 2. Derive Attendance Deduction from Saved Snapshot
    stored_attendance_deduction = max(0.0, round(saved_base_salary - saved_earned_salary, 2))
    total_earnings = round(saved_base_salary + saved_bonus, 2)
    total_deductions = round(stored_attendance_deduction + saved_advance_deduction + saved_other_deductions, 2)

    amount_in_words = number_to_words(saved_net_salary)

    # 7. Dynamic Payments Array
    payments = []
    for p in salary.payments.all().order_by('-payment_date', '-id'):
        payments.append({
            "id": p.id,
            "paymentDate": str(p.payment_date),
            "amount": float(p.amount),
            "paymentMethod": p.payment_method,
            "paidBy": p.paid_by,
            "remarks": p.remarks
        })

    # 8. Return the complete nested payslip JSON response
    return {
        "company": company,
        "employee": employee_details,
        "payslip": payslip_meta,
        "attendance": {
            "monthDays": month_days,
            "workingDays": working_days,
            "presentDays": present_days,
            "absentDays": absent_days,
            "halfDays": half_days,
            "weeklyOffs": weekly_offs,
        },
        "earnings": {
            "basicSalary": saved_base_salary,
            "currentSalary": saved_base_salary,
            "bonus": saved_bonus,
            "totalEarnings": total_earnings
        },
        "deductions": {
            "attendanceDeduction": stored_attendance_deduction,
            "advanceDeduction": saved_advance_deduction,
            "otherDeductions": saved_other_deductions,
            "totalDeductions": total_deductions
        },
        "summary": {
            "netSalary": saved_net_salary,
            "paidAmount": saved_paid_amount,
            "balanceRemaining": saved_balance_remaining,
            "amountInWords": amount_in_words
        },
        "payments": payments
    }


def get_bulk_attendance_data(target_date) -> dict:
    active_employees = Employee.objects.filter(
        status="active",
        is_deleted=False,
        joining_date__lte=target_date
    ).order_by("emp_no")
    
    records = Attendance.objects.filter(date=target_date)
    is_already_marked = records.exists()
    att_map = {att.employee_id: att for att in records}

    result_records = []
    for emp in active_employees:
        att = att_map.get(emp.id)
        if att:
            st = att.status
            mapped_status = st
            sub_type = None
            if st == "half_paid":
                mapped_status = "half_day"
                sub_type = "paid"
            elif st == "half_unpaid":
                mapped_status = "half_day"
                sub_type = "unpaid"
            elif st == "leave_paid":
                mapped_status = "leave"
                sub_type = "paid"
            elif st == "leave_unpaid":
                mapped_status = "leave"
                sub_type = "unpaid"
            
            result_records.append({
                "employeeId": emp.id,
                "empId": emp.emp_no,
                "employeeName": emp.name,
                "status": mapped_status,
                "subType": sub_type,
                "remarks": att.remarks,
                "isMarked": True
            })
        else:
            result_records.append({
                "employeeId": emp.id,
                "empId": emp.emp_no,
                "employeeName": emp.name,
                "status": None,
                "subType": None,
                "remarks": "",
                "isMarked": False
            })

    return {
        "date": target_date,
        "isAlreadyMarked": is_already_marked,
        "records": result_records
    }


@transaction.atomic
def save_bulk_attendance_records(target_date, records_data: list) -> dict:
    for rec in records_data:
        emp_id = rec.get("employeeId")
        front_status = rec.get("status")
        sub_type = rec.get("subType")
        remarks = rec.get("remarks", "")
        
        mapped_status = front_status
        if front_status == "half_day":
            mapped_status = "half_paid" if sub_type == "paid" else "half_unpaid"
        elif front_status == "leave":
            mapped_status = "leave_paid" if sub_type == "paid" else "leave_unpaid"
        
        Attendance.objects.update_or_create(
            employee_id=emp_id,
            date=target_date,
            defaults={
                "status": mapped_status,
                "remarks": remarks
            }
        )
    return {"success": True, "count": len(records_data)}


def get_employee_360_overview(employee: Employee) -> dict:
    """
    Returns complete 360 overview for an employee, including header, topMetrics, tabBadges,
    personalInfo, and salaryInfo.
    """
    advance_balance = calculate_employee_advance_balance(employee)

    pending_salaries = EmployeeSalary.objects.filter(employee=employee, status__in=["pending", "partial"])
    pending_salary_amount = Decimal("0.00")
    for s in pending_salaries:
        pending_salary_amount += get_salary_balance_remaining(s)

    increments_qs = EmployeeIncrement.objects.filter(employee=employee)
    no_of_increments = increments_qs.count()
    total_incremented = Decimal("0.00")
    for inc in increments_qs:
        total_incremented += inc.increment_amount

    salary_records_count = EmployeeSalary.objects.filter(employee=employee).count()
    unpaid_salaries_count = pending_salaries.count()
    advances_count = SalaryAdvance.objects.filter(employee=employee).count()

    now = timezone.now()
    current_month_absents = Attendance.objects.filter(
        employee=employee,
        date__year=now.year,
        date__month=now.month,
        status="absent"
    ).count()
    attendance_alert_badge = f"{current_month_absents}A" if current_month_absents > 0 else ""

    all_salaries = EmployeeSalary.objects.filter(employee=employee)
    total_earned = sum((s.net_salary for s in all_salaries), Decimal("0.00"))
    total_paid = sum((s.amount_paid for s in all_salaries), Decimal("0.00"))
    pending_balance = max(Decimal("0.00"), total_earned - total_paid)

    advance_balance = calculate_employee_advance_balance(employee)

    total_pending_salaries = sum(
        (get_salary_balance_remaining(s) for s in all_salaries if s.status in ['pending', 'partial']),
        Decimal("0.00")
    )

    salary_generated = EmployeeSalary.objects.filter(
        employee=employee, month=now.month, year=now.year
    ).exists()
    
    if salary_generated:
        payable_days = Decimal('0.00')
        current_month_accrued = Decimal('0.00')
    else:
        payroll_data = calculate_payroll_for_month(employee, now.month, now.year, up_to_date=now.date())
        payable_days = payroll_data["payable_days"]
        current_month_accrued = payroll_data["earned_basic"]
        
    net_settlement_amount = total_pending_salaries + current_month_accrued - advance_balance

    return {
        "header": {
            "id": employee.id,
            "empNo": employee.emp_no,
            "name": employee.name,
            "designation": employee.designation,
            "department": employee.department,
            "status": employee.status,
        },
        "topMetrics": {
            "currentSalary": _quantize_decimal(employee.current_salary),
            "pendingSalary": _quantize_decimal(pending_salary_amount),
            "advanceBalance": float(advance_balance),
            "salaryRecordsCount": salary_records_count,
            "noOfIncrements": no_of_increments,
        },
        "tabBadges": {
            "unpaidSalariesCount": unpaid_salaries_count,
            "incrementsCount": no_of_increments,
            "advancesCount": advances_count,
            "attendanceAlertBadge": attendance_alert_badge,
        },
        "personalInfo": {
            "empNo": employee.emp_no,
            "phone": employee.phone,
            "email": employee.email,
            "cnic": employee.cnic,
            "address": employee.address,
            "joiningDate": employee.joining_date,
            "leavingDate": employee.leaving_date,
            "rejoiningDate": employee.rejoining_date,
        },
        "salaryInfo": {
            "basicSalary": _quantize_decimal(employee.basic_salary),
            "currentSalary": _quantize_decimal(employee.current_salary),
            "totalIncremented": _quantize_decimal(total_incremented),
            "noOfIncrements": no_of_increments,
            "totalEarned": _quantize_decimal(total_earned),
            "totalPaid": _quantize_decimal(total_paid),
            "pendingBalance": _quantize_decimal(pending_balance),
            "baseSalary": _quantize_decimal(employee.current_salary),
            "currentMonthDaysWorked": float(payable_days),
            "currentMonthAccrued": float(current_month_accrued),
            "netSettlementAmount": float(net_settlement_amount),
            "suggestedAdvanceDeduction": float(advance_balance),
        },
    }


def get_employee_salaries_tab_summary(employee: Employee) -> dict:
    """
    Returns summary metrics for an employee's salary history tab.
    """
    salaries = EmployeeSalary.objects.filter(employee=employee)
    
    total_paid = Decimal("0.00")
    total_bonus = Decimal("0.00")
    pending_salary = Decimal("0.00")
    partial_pending_count = 0

    total_earned = Decimal("0.00")

    for s in salaries:
        paid_amt = _quantize_decimal(s.amount_paid)
        bonus_amt = _quantize_decimal(s.bonus)
        balance = get_salary_balance_remaining(s)

        total_paid += paid_amt
        total_bonus += bonus_amt
        total_earned += _quantize_decimal(s.net_salary)

        if s.status in ["pending", "partial"]:
            pending_salary += balance
            partial_pending_count += 1
            
    pending_balance = max(Decimal("0.00"), total_earned - total_paid)
    now = timezone.now()
    advance_balance = calculate_employee_advance_balance(employee)

    total_pending_salaries = sum(
        (get_salary_balance_remaining(s) for s in salaries if s.status in ['pending', 'partial']),
        Decimal("0.00")
    )

    salary_generated = EmployeeSalary.objects.filter(
        employee=employee, month=now.month, year=now.year
    ).exists()
    
    if salary_generated:
        payable_days = Decimal('0.00')
        current_month_accrued = Decimal('0.00')
    else:
        payroll_data = calculate_payroll_for_month(employee, now.month, now.year, up_to_date=now.date())
        payable_days = payroll_data["payable_days"]
        current_month_accrued = payroll_data["earned_basic"]
        
    net_settlement_amount = total_pending_salaries + current_month_accrued - advance_balance

    return {
        "totalPaid": _quantize_decimal(total_paid),
        "totalBonus": _quantize_decimal(total_bonus),
        "pendingSalary": _quantize_decimal(pending_salary),
        "partialPendingCount": partial_pending_count,
        "totalEarned": _quantize_decimal(total_earned),
        "pendingBalance": _quantize_decimal(pending_balance),
        "baseSalary": _quantize_decimal(employee.current_salary),
        "currentMonthDaysWorked": float(payable_days),
        "currentMonthAccrued": float(current_month_accrued),
        "netSettlementAmount": float(net_settlement_amount),
        "advanceBalance": float(advance_balance),
        "suggestedAdvanceDeduction": float(advance_balance),
    }


def get_employee_salaries_tab_data(employee: Employee) -> dict:
    """
    Returns salaries tab data including top summary cards and detailed month records with payments.
    """
    salaries = EmployeeSalary.objects.filter(employee=employee).order_by("-year", "-month")
    
    total_paid = Decimal("0.00")
    total_bonus = Decimal("0.00")
    pending_salary = Decimal("0.00")
    partial_pending_count = 0

    results = []
    for s in salaries:
        paid_amt = _quantize_decimal(s.amount_paid)
        bonus_amt = _quantize_decimal(s.bonus)
        balance = get_salary_balance_remaining(s)

        total_paid += paid_amt
        total_bonus += bonus_amt

        if s.status in ["pending", "partial"]:
            pending_salary += balance
            partial_pending_count += 1

        month_label = f"{calendar.month_name[s.month]} {s.year}"

        payments_list = [
            {
                "id": p.id,
                "paymentDate": p.payment_date,
                "amount": _quantize_decimal(p.amount),
                "paymentMethod": p.payment_method,
                "paidBy": p.paid_by,
                "remarks": p.remarks,
            }
            for p in s.payments.all()
        ]

        results.append({
            "id": s.id,
            "month": s.month,
            "year": s.year,
            "monthLabel": month_label,
            "basicSalary": _quantize_decimal(s.basic_salary),
            "currentSalary": _quantize_decimal(s.current_salary),
            "workingDays": s.working_days,
            "monthDays": s.month_days,
            "absentDays": s.absent_days,
            "halfUnpaidDays": s.half_unpaid_days,
            "leaveUnpaidDays": s.leave_unpaid_days,
            "attendanceDeduction": _quantize_decimal(s.attendance_deduction),
            "bonus": bonus_amt,
            "deductions": _quantize_decimal(s.deductions),
            "advanceDeduction": _quantize_decimal(s.advance_deduction),
            "netSalary": _quantize_decimal(s.net_salary),
            "amountPaid": paid_amt,
            "balanceRemaining": balance,
            "status": s.status,
            "payments": payments_list,
        })

    return {
        "summary": {
            "totalPaid": _quantize_decimal(total_paid),
            "totalBonus": _quantize_decimal(total_bonus),
            "pendingSalary": _quantize_decimal(pending_salary),
            "partialPendingCount": partial_pending_count,
        },
        "results": results,
    }


def get_employee_increments_tab_summary(employee: Employee) -> dict:
    """
    Returns summary statistics for the employee salary increments tab.
    """
    increments = EmployeeIncrement.objects.filter(employee=employee)
    total_incremented = Decimal("0.00")
    for inc in increments:
        total_incremented += inc.increment_amount

    return {
        "basicSalary": _quantize_decimal(employee.basic_salary),
        "currentSalary": _quantize_decimal(employee.current_salary),
        "totalIncremented": _quantize_decimal(total_incremented),
        "noOfIncrements": increments.count(),
    }


def get_employee_increments_tab_data(employee: Employee) -> dict:
    """
    Returns increments tab data including top summary and list of serialized increment records.
    """
    increments = EmployeeIncrement.objects.filter(employee=employee).order_by("-effective_date")
    
    total_incremented = Decimal("0.00")
    results = []
    for inc in increments:
        inc_amt = _quantize_decimal(inc.increment_amount)
        total_incremented += inc_amt
        results.append({
            "id": inc.id,
            "effectiveDate": inc.effective_date,
            "previousSalary": _quantize_decimal(inc.previous_salary),
            "incrementAmount": inc_amt,
            "newSalary": _quantize_decimal(inc.new_salary),
            "reason": inc.reason,
            "approvedBy": inc.approved_by,
            "createdAt": inc.created_at,
        })

    return {
        "summary": {
            "basicSalary": _quantize_decimal(employee.basic_salary),
            "currentSalary": _quantize_decimal(employee.current_salary),
            "totalIncremented": _quantize_decimal(total_incremented),
            "noOfIncrements": len(results),
        },
        "results": results,
    }


def get_employee_advances_tab_summary(employee: Employee) -> dict:
    """
    Returns summary statistics for the employee salary advances tab.
    """
    advances = SalaryAdvance.objects.filter(employee=employee)

    total_given = Decimal("0.00")
    total_recovered = Decimal("0.00")

    for adv in advances:
        total_given += adv.amount
        total_recovered += adv.recovered_amount

    outstanding_balance = calculate_employee_advance_balance(employee)

    return {
        "outstandingBalance": outstanding_balance,
        "totalGiven": _quantize_decimal(total_given),
        "totalRecovered": _quantize_decimal(total_recovered),
    }


def get_employee_advances_tab_data(employee: Employee) -> dict:
    """
    Returns advances tab data including outstanding balance, total given, total recovered, and advances list.
    """
    advances = SalaryAdvance.objects.filter(employee=employee).order_by("-date")

    total_given = Decimal("0.00")
    total_recovered = Decimal("0.00")
    results = []

    for adv in advances:
        amt = _quantize_decimal(adv.amount)
        rec = _quantize_decimal(adv.recovered_amount)
        rem = get_advance_remaining_amount(adv)

        total_given += amt
        total_recovered += rec

        results.append({
            "id": adv.id,
            "date": adv.date,
            "amount": amt,
            "recoveredAmount": rec,
            "remainingAmount": rem,
            "paymentMethod": adv.payment_method,
            "reason": adv.reason,
            "status": adv.status,
            "createdAt": adv.created_at,
        })

    outstanding_balance = calculate_employee_advance_balance(employee)

    return {
        "summary": {
            "outstandingBalance": outstanding_balance,
            "totalGiven": _quantize_decimal(total_given),
            "totalRecovered": _quantize_decimal(total_recovered),
        },
        "results": results,
    }


def get_employee_monthly_attendance_tab(employee: Employee, month: int, year: int) -> dict:
    month_days = calendar.monthrange(year, month)[1]
    off_days_list = get_configured_weekly_off_days()

    att_qs = Attendance.objects.filter(employee=employee, date__year=year, date__month=month)
    att_map = {att.date.day: att for att in att_qs}

    present_count = 0
    absent_count = 0
    half_day_count = 0
    leave_count = 0
    off_days_count = 0
    not_marked_count = 0

    calendar_grid = []
    logs = []

    status_badge_map = {
        "present": "P",
        "absent": "A",
        "half_paid": "½P",
        "half_unpaid": "½U",
        "leave_paid": "LP",
        "leave_unpaid": "LU",
        "weekly_off": "off",
    }
    
    now = timezone.now().date()

    for day in range(1, month_days + 1):
        date_obj = datetime.date(year, month, day)
        day_of_week_idx = date_obj.weekday()
        day_name = date_obj.strftime("%a")
        is_off = day_of_week_idx in off_days_list

        att_record = att_map.get(day)
        st = None
        badge = None
        remarks = ""
        
        if att_record:
            st = att_record.status
            badge = status_badge_map.get(st)
            remarks = att_record.remarks
            
            if st == "present":
                present_count += 1
            elif st == "absent":
                absent_count += 1
            elif st in ["half_paid", "half_unpaid"]:
                half_day_count += 1
            elif st in ["leave_paid", "leave_unpaid"]:
                leave_count += 1
            elif st == "weekly_off":
                off_days_count += 1
            
            logs.append({
                "id": att_record.id,
                "date": date_obj.strftime("%Y-%m-%d"),
                "dayName": day_name,
                "status": dict(Attendance.STATUS_CHOICES).get(st, st),
                "statusCode": st,
                "remarks": remarks,
            })
        else:
            if is_off:
                st = "weekly_off"
                badge = "off"
                off_days_count += 1
            else:
                if date_obj > now:
                    st = "not_marked"
                    badge = None
                else:
                    st = "not_marked"
                    badge = None
                    not_marked_count += 1

        calendar_grid.append({
            "date": date_obj.strftime("%Y-%m-%d"),
            "day": day,
            "dayOfWeek": day_name,
            "status": st,
            "badge": badge,
            "isWeeklyOff": is_off,
        })
        
    logs.sort(key=lambda x: x["date"], reverse=True)

    return {
        "alerts": {
            "totalAbsents": absent_count,
            "totalLeaves": leave_count,
            "totalHalfDays": half_day_count,
        },
        "summary": {
            "present": present_count,
            "absent": absent_count,
            "halfDay": half_day_count,
            "leave": leave_count,
            "offDays": off_days_count,
            "notMarked": not_marked_count,
        },
        "calendarGrid": calendar_grid,
        "logs": logs,
    }


def calculate_employee_attendance_deduction(employee: Employee, month: int, year: int) -> dict:
    total_month_days = calendar.monthrange(year, month)[1]
    if total_month_days > 0:
        per_day_salary = employee.current_salary / Decimal(total_month_days)
    else:
        per_day_salary = Decimal("0.00")

    records = Attendance.objects.filter(employee=employee, date__year=year, date__month=month)
    absent_count = records.filter(status="absent").count()
    leave_unpaid_count = records.filter(status="leave_unpaid").count()
    half_unpaid_count = records.filter(status="half_unpaid").count()

    deduction_days = Decimal(absent_count) + Decimal(leave_unpaid_count) + (Decimal('0.5') * Decimal(half_unpaid_count))
    attendance_deduction = _quantize_decimal(per_day_salary * deduction_days)

    return {
        "deductionDays": float(deduction_days),
        "attendanceDeduction": float(attendance_deduction)
    }
