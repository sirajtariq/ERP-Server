import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from employees.models import (
    Employee,
    Attendance,
    EmployeeIncrement,
    SalaryAdvance,
    EmployeeSalary,
    SalaryPayment,
)
import employees.services as services

User = get_user_model()


class EmployeeServiceTestCase(TestCase):
    def setUp(self):
        self.emp1 = Employee.objects.create(
            emp_no="EMP-001",
            name="Ali Khan",
            designation="Software Engineer",
            department="Operations",
            joining_date=datetime.date(2025, 1, 1),
            basic_salary=Decimal("100000.00"),
            current_salary=Decimal("100000.00"),
            status="active",
        )
        self.emp2 = Employee.objects.create(
            emp_no="EMP-002",
            name="Sara Ahmed",
            designation="Accountant",
            department="Finance",
            joining_date=datetime.date(2025, 2, 1),
            basic_salary=Decimal("80000.00"),
            current_salary=Decimal("80000.00"),
            status="active",
        )

    def test_calculate_payroll_global_kpis(self):
        kpis = services.calculate_payroll_global_kpis()
        self.assertEqual(kpis["activeEmployees"], 2)
        self.assertEqual(kpis["monthlyPayroll"], Decimal("180000.00"))
        self.assertEqual(kpis["averageSalary"], Decimal("90000.00"))
        self.assertEqual(kpis["advanceOutstanding"], Decimal("0.00"))

    def test_salary_advance_and_fifo_recovery(self):
        # Issue two advances for emp1
        adv1 = services.issue_salary_advance(self.emp1, {
            "date": datetime.date(2026, 8, 1),
            "amount": Decimal("10000.00"),
            "paymentMethod": "Cash",
            "reason": "Emergency Advance 1",
        })
        adv2 = services.issue_salary_advance(self.emp1, {
            "date": datetime.date(2026, 8, 5),
            "amount": Decimal("15000.00"),
            "paymentMethod": "Bank Transfer",
            "reason": "Emergency Advance 2",
        })

        bal = services.calculate_employee_advance_balance(self.emp1)
        self.assertEqual(bal, Decimal("25000.00"))

        kpis = services.calculate_payroll_global_kpis()
        self.assertEqual(kpis["advanceOutstanding"], Decimal("25000.00"))

        # Recover 15000 via FIFO: adv1 should be fully recovered (10000), adv2 partially (5000)
        services.process_fifo_advance_recovery(self.emp1, Decimal("15000.00"))

        adv1.refresh_from_db()
        adv2.refresh_from_db()
        self.assertEqual(adv1.status, "recovered")
        self.assertEqual(adv1.recovered_amount, Decimal("10000.00"))
        self.assertEqual(adv2.status, "partial")
        self.assertEqual(adv2.recovered_amount, Decimal("5000.00"))

        bal_after = services.calculate_employee_advance_balance(self.emp1)
        self.assertEqual(bal_after, Decimal("10000.00"))

    def test_attendance_summary_calculation(self):
        # Log attendance: 2 absent, 2 half_day, rest present
        Attendance.objects.create(employee=self.emp1, date=datetime.date(2026, 8, 1), status="absent")
        Attendance.objects.create(employee=self.emp1, date=datetime.date(2026, 8, 2), status="absent")
        Attendance.objects.create(employee=self.emp1, date=datetime.date(2026, 8, 3), status="half_day")
        Attendance.objects.create(employee=self.emp1, date=datetime.date(2026, 8, 4), status="half_day")

        summary = services.calculate_month_attendance_summary(self.emp1, 8, 2026)
        self.assertEqual(summary["totalWorkingDays"], 30)
        self.assertEqual(summary["rawAbsentDays"], 2)
        self.assertEqual(summary["halfDays"], 2)
        # perDayRate = 100000 / 30 = 3333.33
        # absentDeduction = 2 * 3333.33 + 2 * (3333.33 / 2) = 6666.66 + 3333.33 = 9999.99
        self.assertGreater(summary["attendanceDeduction"], Decimal("0.00"))

    def test_salary_increment_process(self):
        inc = services.process_salary_increment(self.emp1, {
            "effectiveDate": datetime.date(2026, 8, 10),
            "incrementAmount": Decimal("20000.00"),
            "approvedBy": "CEO",
            "reason": "Annual Appraisal",
        })
        self.emp1.refresh_from_db()
        self.assertEqual(self.emp1.current_salary, Decimal("120000.00"))
        self.assertEqual(inc.previous_salary, Decimal("100000.00"))
        self.assertEqual(inc.new_salary, Decimal("120000.00"))

    def test_record_salary_and_payslip_generation(self):
        salary_instance = services.record_salary_payment(self.emp1, {
            "month": 8,
            "year": 2026,
            "workingDays": 30,
            "bonus": Decimal("5000.00"),
            "deductions": Decimal("1000.00"),
            "advanceDeduction": Decimal("0.00"),
            "amount": Decimal("50000.00"),
            "paymentDate": datetime.date(2026, 8, 31),
            "paymentMethod": "Bank Transfer",
            "paidBy": "Finance Lead",
            "remarks": "Part 1 Payment",
        })

        self.assertEqual(salary_instance.status, "partial")
        payslip = services.generate_payslip_data(salary_instance)
        self.assertEqual(payslip["payslip"]["slipNo"], salary_instance.slip_no)
        self.assertEqual(payslip["summary"]["paidAmount"], Decimal("50000.00"))
        self.assertIn("Only", payslip["summary"]["amountInWords"])


class EmployeeAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="adminuser", password="password123")
        self.client.force_authenticate(user=self.user)

        self.emp = Employee.objects.create(
            emp_no="EMP-100",
            name="Zubair Qureshi",
            designation="Sales Executive",
            department="Sales",
            joining_date=datetime.date(2025, 3, 15),
            basic_salary=Decimal("60000.00"),
            current_salary=Decimal("60000.00"),
            status="active",
        )

    def test_get_employees_list_and_kpis(self):
        res = self.client.get("/api/employees/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("results", res.data)
        self.assertIn("summary", res.data)
        self.assertEqual(res.data["summary"]["activeEmployees"], 1)

    def test_create_employee(self):
        payload = {
            "empNo": "EMP-101",
            "name": "Hamza Tariq",
            "designation": "Manager",
            "department": "Operations",
            "joiningDate": "2026-01-01",
            "basicSalary": "95000.00",
        }
        res = self.client.post("/api/employees/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["empNo"], "EMP-101")
        self.assertEqual(res.data["currentSalary"], "95000.00")

    def test_create_employee_with_empty_string_dates(self):
        payload = {
            "empNo": "EMP-102",
            "name": "Usman Ali",
            "designation": "Developer",
            "department": "IT",
            "joiningDate": "2026-01-01",
            "leavingDate": "",
            "rejoiningDate": "",
        }
        res = self.client.post("/api/employees/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(res.data["leavingDate"])
        self.assertIsNone(res.data["rejoiningDate"])

    def test_employee_360_detail(self):
        res = self.client.get(f"/api/employees/{self.emp.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("employee", res.data)
        self.assertIn("timeline", res.data)
        self.assertIn("advanceBalance", res.data)

    def test_soft_delete(self):
        res = self.client.delete(f"/api/employees/{self.emp.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.emp.refresh_from_db()
        self.assertTrue(self.emp.is_deleted)
        self.assertEqual(self.emp.status, "inactive")

        # Must not appear in list endpoint
        list_res = self.client.get("/api/employees/")
        self.assertEqual(len(list_res.data["results"]), 0)

    def test_record_salary_endpoint(self):
        payload = {
            "month": 8,
            "year": 2026,
            "workingDays": 30,
            "bonus": "2000.00",
            "amount": "62000.00",
            "paymentDate": "2026-08-31",
            "paymentMethod": "Bank Transfer",
            "paidBy": "Admin",
        }
        res = self.client.post(f"/api/employees/{self.emp.id}/salaries/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "paid")

    def test_payslip_endpoint(self):
        # Create salary record first
        sal = services.record_salary_payment(self.emp, {
            "month": 8,
            "year": 2026,
            "amount": "60000.00",
        })
        res = self.client.get(f"/api/employees/{self.emp.id}/salaries/{sal.id}/payslip/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("payslip", res.data)
        self.assertIn("earnings", res.data)

    def test_attendance_list_and_bulk_save(self):
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        res = self.client.get(f"/api/attendance/?date={today_str}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["totalEmployees"], 1)

        bulk_payload = {
            "date": today_str,
            "records": [
                {
                    "employeeId": self.emp.id,
                    "status": "present",
                    "checkIn": "09:00:00",
                    "checkOut": "17:00:00",
                    "remarks": "On time",
                }
            ]
        }
        bulk_res = self.client.post("/api/attendance/bulk/", bulk_payload, format="json")
        self.assertEqual(bulk_res.status_code, status.HTTP_200_OK)
        self.assertEqual(bulk_res.data["totalSaved"], 1)


class EmployeeSerializerTestCase(TestCase):
    def test_to_internal_value_empty_string_conversion(self):
        from employees.serializers import EmployeeSerializer
        payload = {
            "empNo": "EMP-999",
            "name": "Test User",
            "designation": "Tester",
            "department": "QA",
            "joiningDate": "2026-01-01",
            "leavingDate": "",
            "rejoiningDate": "",
        }
        serializer = EmployeeSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIsNone(serializer.validated_data.get("leaving_date"))
        self.assertIsNone(serializer.validated_data.get("rejoining_date"))

        # Test with snake_case field names in payload
        payload_snake = {
            "empNo": "EMP-998",
            "name": "Test User 2",
            "designation": "Tester",
            "department": "QA",
            "joiningDate": "2026-01-01",
            "leaving_date": "",
            "rejoining_date": "",
        }
        serializer_snake = EmployeeSerializer(data=payload_snake)
        self.assertTrue(serializer_snake.is_valid(), serializer_snake.errors)
        self.assertIsNone(serializer_snake.validated_data.get("leaving_date"))
        self.assertIsNone(serializer_snake.validated_data.get("rejoining_date"))
