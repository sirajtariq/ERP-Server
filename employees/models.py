from decimal import Decimal
from django.db import models
from django.utils import timezone


class Employee(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("inactive", "Inactive"),
    ]

    emp_no = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=255, db_index=True)
    designation = models.CharField(max_length=150)
    department = models.CharField(max_length=100, db_index=True)
    joining_date = models.DateField()
    leaving_date = models.DateField(null=True, blank=True)
    rejoining_date = models.DateField(null=True, blank=True)
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    current_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    phone = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    cnic = models.CharField(max_length=30, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active", db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.emp_no} - {self.name}"


class Attendance(models.Model):
    STATUS_CHOICES = [
        ("present", "Present"),
        ("absent", "Absent"),
        ("half_paid", "Half Day (Paid)"),
        ("half_unpaid", "Half Day (Unpaid)"),
        ("leave_paid", "Leave (Paid)"),
        ("leave_unpaid", "Leave (Unpaid)"),
        ("weekly_off", "Weekly Off"),
    ]

    employee = models.ForeignKey(Employee, related_name="attendances", on_delete=models.CASCADE)
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, db_index=True)
    remarks = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-date"]
        unique_together = ("employee", "date")

    def __str__(self):
        return f"{self.employee.emp_no} - {self.date} - {self.status}"


class EmployeeIncrement(models.Model):
    employee = models.ForeignKey(Employee, related_name="increments", on_delete=models.CASCADE)
    effective_date = models.DateField()
    previous_salary = models.DecimalField(max_digits=12, decimal_places=2)
    increment_amount = models.DecimalField(max_digits=12, decimal_places=2)
    new_salary = models.DecimalField(max_digits=12, decimal_places=2)
    approved_by = models.CharField(max_length=150, blank=True, null=True)
    reason = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_date", "-id"]

    def __str__(self):
        return f"{self.employee.emp_no} Increment +{self.increment_amount}"


class SalaryAdvance(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("partial", "Partial"),
        ("recovered", "Recovered"),
    ]

    employee = models.ForeignKey(Employee, related_name="advances", on_delete=models.CASCADE)
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    recovered_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    payment_method = models.CharField(max_length=50, default="Cash")
    reason = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "id"]

    def __str__(self):
        return f"{self.employee.emp_no} Advance {self.amount}"


class EmployeeSalary(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("partial", "Partial"),
        ("paid", "Paid"),
    ]

    employee = models.ForeignKey(Employee, related_name="salaries", on_delete=models.CASCADE)
    slip_no = models.CharField(max_length=60, unique=True, blank=True)
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2)
    current_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    working_days = models.PositiveSmallIntegerField(default=30)
    month_days = models.PositiveSmallIntegerField(default=30)
    absent_days = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("0.0"))
    half_unpaid_days = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("0.0"))
    leave_unpaid_days = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("0.0"))
    attendance_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    bonus = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    advance_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    net_salary = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year", "-month", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "month", "year"], name="unique_employee_month_year")
        ]

    def __str__(self):
        return f"{self.employee.emp_no} Salary {self.month}/{self.year} ({self.slip_no})"


class SalaryPayment(models.Model):
    salary = models.ForeignKey(EmployeeSalary, related_name="payments", on_delete=models.CASCADE)
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=50)
    paid_by = models.CharField(max_length=150)
    remarks = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]

    def __str__(self):
        return f"Payment {self.amount} for {self.salary.slip_no}"

    def delete(self, *args, **kwargs):
        payment_id = self.id
        super().delete(*args, **kwargs)
        try:
            from purchase.services import reverse_auto_expense
            reverse_auto_expense("salary_payment", payment_id)
        except Exception:
            pass
