from decimal import Decimal
from rest_framework import serializers

from employees.models import (
    Employee,
    Attendance,
    EmployeeIncrement,
    SalaryAdvance,
    EmployeeSalary,
    SalaryPayment,
)
import employees.services as services


class EmployeeSerializer(serializers.ModelSerializer):
    empNo = serializers.CharField(source="emp_no")
    joiningDate = serializers.DateField(source="joining_date")
    leavingDate = serializers.DateField(source="leaving_date", required=False, allow_null=True)
    rejoiningDate = serializers.DateField(source="rejoining_date", required=False, allow_null=True)
    basicSalary = serializers.DecimalField(source="basic_salary", max_digits=12, decimal_places=2, required=False, default=Decimal("0.00"))
    currentSalary = serializers.DecimalField(source="current_salary", max_digits=12, decimal_places=2, required=False, default=Decimal("0.00"))
    isDeleted = serializers.BooleanField(source="is_deleted", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = Employee
        fields = [
            "id",
            "empNo",
            "name",
            "designation",
            "department",
            "joiningDate",
            "leavingDate",
            "rejoiningDate",
            "basicSalary",
            "currentSalary",
            "phone",
            "email",
            "cnic",
            "address",
            "status",
            "isDeleted",
            "createdAt",
            "updatedAt",
        ]

    def to_internal_value(self, data):
        data = data.copy() if hasattr(data, 'copy') else dict(data)
        for field in ['leavingDate', 'leaving_date', 'rejoiningDate', 'rejoining_date']:
            if field in data and data[field] == '':
                data[field] = None
        return super().to_internal_value(data)

    def create(self, validated_data):
        basic_sal = validated_data.get("basic_salary", Decimal("0.00"))
        curr_sal = validated_data.get("current_salary")
        if not curr_sal or curr_sal == Decimal("0.00"):
            validated_data["current_salary"] = basic_sal
        return super().create(validated_data)


class EmployeeListSerializer(EmployeeSerializer):
    advanceBalance = serializers.SerializerMethodField()

    class Meta(EmployeeSerializer.Meta):
        fields = EmployeeSerializer.Meta.fields + ["advanceBalance"]

    def get_advanceBalance(self, obj):
        return services.calculate_employee_advance_balance(obj)


class AttendanceSerializer(serializers.ModelSerializer):
    employeeId = serializers.IntegerField(source="employee.id", read_only=True)
    empNo = serializers.CharField(source="employee.emp_no", read_only=True)
    employeeName = serializers.CharField(source="employee.name", read_only=True)
    checkIn = serializers.TimeField(source="check_in", required=False, allow_null=True)
    checkOut = serializers.TimeField(source="check_out", required=False, allow_null=True)

    class Meta:
        model = Attendance
        fields = [
            "id",
            "employee",
            "employeeId",
            "empNo",
            "employeeName",
            "date",
            "status",
            "checkIn",
            "checkOut",
            "remarks",
        ]


class BulkAttendanceItemSerializer(serializers.Serializer):
    employeeId = serializers.IntegerField()
    status = serializers.ChoiceField(choices=["present", "absent", "half_day", "paid_leave", "unpaid_leave"])
    checkIn = serializers.TimeField(required=False, allow_null=True)
    checkOut = serializers.TimeField(required=False, allow_null=True)
    remarks = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class BulkAttendanceSerializer(serializers.Serializer):
    date = serializers.DateField()
    records = BulkAttendanceItemSerializer(many=True)


class EmployeeIncrementSerializer(serializers.ModelSerializer):
    effectiveDate = serializers.DateField(source="effective_date")
    previousSalary = serializers.DecimalField(source="previous_salary", max_digits=12, decimal_places=2, read_only=True)
    incrementAmount = serializers.DecimalField(source="increment_amount", max_digits=12, decimal_places=2)
    newSalary = serializers.DecimalField(source="new_salary", max_digits=12, decimal_places=2, read_only=True)
    approvedBy = serializers.CharField(source="approved_by", required=False, allow_null=True, allow_blank=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = EmployeeIncrement
        fields = [
            "id",
            "employee",
            "effectiveDate",
            "previousSalary",
            "incrementAmount",
            "newSalary",
            "approvedBy",
            "reason",
            "createdAt",
        ]
        read_only_fields = ["employee", "previousSalary", "newSalary", "createdAt"]


class SalaryAdvanceSerializer(serializers.ModelSerializer):
    recoveredAmount = serializers.DecimalField(source="recovered_amount", max_digits=12, decimal_places=2, read_only=True)
    remainingAmount = serializers.SerializerMethodField()
    paymentMethod = serializers.CharField(source="payment_method", required=False, default="Cash")
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = SalaryAdvance
        fields = [
            "id",
            "employee",
            "date",
            "amount",
            "recoveredAmount",
            "remainingAmount",
            "paymentMethod",
            "reason",
            "status",
            "createdAt",
        ]
        read_only_fields = ["employee", "recoveredAmount", "remainingAmount", "status", "createdAt"]

    def get_remainingAmount(self, obj):
        diff = obj.amount - obj.recovered_amount
        return diff if diff > Decimal("0.00") else Decimal("0.00")


class SalaryPaymentSerializer(serializers.ModelSerializer):
    paymentDate = serializers.DateField(source="payment_date")
    paymentMethod = serializers.CharField(source="payment_method")
    paidBy = serializers.CharField(source="paid_by")
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = SalaryPayment
        fields = [
            "id",
            "salary",
            "paymentDate",
            "amount",
            "paymentMethod",
            "paidBy",
            "remarks",
            "createdAt",
        ]
        read_only_fields = ["salary", "createdAt"]


class EmployeeSalarySerializer(serializers.ModelSerializer):
    slipNo = serializers.CharField(source="slip_no", read_only=True)
    basicSalary = serializers.DecimalField(source="basic_salary", max_digits=12, decimal_places=2, read_only=True)
    workingDays = serializers.IntegerField(source="working_days", required=False, default=30)
    absentDays = serializers.DecimalField(source="absent_days", max_digits=4, decimal_places=1, read_only=True)
    attendanceDeduction = serializers.DecimalField(source="attendance_deduction", max_digits=12, decimal_places=2, read_only=True)
    advanceDeduction = serializers.DecimalField(source="advance_deduction", max_digits=12, decimal_places=2, required=False, default=Decimal("0.00"))
    netSalary = serializers.DecimalField(source="net_salary", max_digits=12, decimal_places=2, read_only=True)
    payments = SalaryPaymentSerializer(many=True, read_only=True)
    paidAmount = serializers.SerializerMethodField()
    balanceRemaining = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = EmployeeSalary
        fields = [
            "id",
            "employee",
            "slipNo",
            "month",
            "year",
            "basicSalary",
            "workingDays",
            "absentDays",
            "attendanceDeduction",
            "bonus",
            "deductions",
            "advanceDeduction",
            "netSalary",
            "status",
            "payments",
            "paidAmount",
            "balanceRemaining",
            "createdAt",
        ]
        read_only_fields = [
            "employee",
            "slipNo",
            "basicSalary",
            "absentDays",
            "attendanceDeduction",
            "netSalary",
            "status",
            "payments",
            "paidAmount",
            "balanceRemaining",
            "createdAt",
        ]

    def get_paidAmount(self, obj):
        total = sum(p.amount for p in obj.payments.all()) if obj.id else Decimal("0.00")
        return total

    def get_balanceRemaining(self, obj):
        paid = self.get_paidAmount(obj)
        rem = obj.net_salary - paid
        return rem if rem > Decimal("0.00") else Decimal("0.00")
