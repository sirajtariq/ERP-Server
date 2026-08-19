import calendar
import datetime
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from employees.models import (
    Employee,
    Attendance,
    EmployeeIncrement,
    SalaryAdvance,
    EmployeeSalary,
    SalaryPayment,
)
from employees.serializers import (
    EmployeeSerializer,
    EmployeeListSerializer,
    AttendanceSerializer,
    BulkAttendanceSerializer,
    EmployeeIncrementSerializer,
    SalaryAdvanceSerializer,
    SalaryPaymentSerializer,
    EmployeeSalarySerializer,
    EmployeeSalaryTabSerializer,
)
import employees.services as services
from employees.schema import extend_schema


class EmployeePagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    page_query_param = "page"
    max_page_size = 100

    def get_page_number(self, request, paginator):
        page_number = request.query_params.get(self.page_query_param) or request.query_params.get("page_number")
        if page_number:
            return page_number
        return super().get_page_number(request, paginator)

    def get_page_size(self, request):
        if "pageSize" in request.query_params:
            try:
                ps = int(request.query_params["pageSize"])
                if ps > 0:
                    return min(ps, self.max_page_size)
            except (ValueError, TypeError):
                pass
        return super().get_page_size(request)


class EmployeeViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Employee records.
    Enforces soft delete, zero math service logic, camelCase JSON, and returns KPI envelope on list.
    """
    queryset = Employee.objects.filter(is_deleted=False).order_by("-id")
    serializer_class = EmployeeSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = EmployeePagination

    def get_queryset(self):
        qs = Employee.objects.filter(is_deleted=False).order_by("-id")
        params = self.request.query_params

        search = (
            params.get("search", "").strip()
            or params.get("name", "").strip()
            or params.get("empId", "").strip()
        )
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(emp_no__icontains=search) |
                Q(department__icontains=search) |
                Q(designation__icontains=search)
            )

        department = params.get("department", "").strip()
        if department and department.lower() != "all":
            qs = qs.filter(department__iexact=department)

        status_param = params.get("status", "").strip().lower()
        if status_param and status_param != "all":
            qs = qs.filter(status=status_param)

        return qs

    @extend_schema(
        summary="Retrieve paginated list of employees with global summary KPI envelope.",
        parameters=[
            openapi.Parameter("search", openapi.IN_QUERY, description="Search by name, empId, department, or designation", type=openapi.TYPE_STRING),
            openapi.Parameter("status", openapi.IN_QUERY, description="Filter by status ('all', 'active', 'inactive')", type=openapi.TYPE_STRING),
            openapi.Parameter("department", openapi.IN_QUERY, description="Filter by department", type=openapi.TYPE_STRING),
            openapi.Parameter("page", openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter("page_size", openapi.IN_QUERY, description="Page size", type=openapi.TYPE_INTEGER),
        ]
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = EmployeeListSerializer(page, many=True)
            paginated_response = self.get_paginated_response(serializer.data)
            summary_kpis = services.calculate_payroll_global_kpis()
            paginated_response.data["summary"] = summary_kpis
            return paginated_response

        serializer = EmployeeListSerializer(queryset, many=True)
        summary_kpis = services.calculate_payroll_global_kpis()
        return Response({
            "results": serializer.data,
            "summary": summary_kpis,
        })

    @extend_schema(summary="Create a new employee record.")
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee = serializer.save()
        return Response(EmployeeSerializer(employee).data, status=status.HTTP_201_CREATED)

    @extend_schema(summary="Retrieve 360-degree employee overview with top metrics and tab badges.")
    def retrieve(self, request, *args, **kwargs):
        employee = self.get_object()
        data_360 = services.get_employee_360_overview(employee)
        return Response(data_360, status=status.HTTP_200_OK)

    @extend_schema(summary="Get paginated salary history tab data for employee.")
    @action(detail=True, methods=["get"], url_path="salaries-tab")
    def get_salaries(self, request, pk=None):
        employee = self.get_object()
        queryset = EmployeeSalary.objects.filter(employee=employee).prefetch_related("payments").order_by("-year", "-month")
        
        summary_data = services.get_employee_salaries_tab_summary(employee)
        
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = EmployeeSalaryTabSerializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data["summary"] = summary_data
            return response

        serializer = EmployeeSalaryTabSerializer(queryset, many=True)
        return Response({
            "summary": summary_data,
            "results": serializer.data,
        }, status=status.HTTP_200_OK)

    @extend_schema(summary="Get paginated salary increments tab data for employee.")
    @action(detail=True, methods=["get"], url_path="increments-tab")
    def get_increments(self, request, pk=None):
        employee = self.get_object()
        queryset = EmployeeIncrement.objects.filter(employee=employee).order_by("-effective_date", "-created_at", "-id")

        summary_data = services.get_employee_increments_tab_summary(employee)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = EmployeeIncrementSerializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data["summary"] = summary_data
            return response

        serializer = EmployeeIncrementSerializer(queryset, many=True)
        return Response({
            "count": queryset.count(),
            "next": None,
            "previous": None,
            "results": serializer.data,
            "summary": summary_data,
        }, status=status.HTTP_200_OK)

    @extend_schema(summary="Get paginated salary advances tab data for employee.")
    @action(detail=True, methods=["get"], url_path="advances-tab")
    def get_advances(self, request, pk=None):
        employee = self.get_object()
        queryset = SalaryAdvance.objects.filter(employee=employee).order_by("-date", "-id")

        summary_data = services.get_employee_advances_tab_summary(employee)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = SalaryAdvanceSerializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data["summary"] = summary_data
            return response

        serializer = SalaryAdvanceSerializer(queryset, many=True)
        return Response({
            "count": queryset.count(),
            "next": None,
            "previous": None,
            "results": serializer.data,
            "summary": summary_data,
        }, status=status.HTTP_200_OK)

    @extend_schema(summary="Update an employee record.")
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        employee = self.get_object()
        serializer = self.get_serializer(employee, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        updated_emp = serializer.save()
        return Response(EmployeeSerializer(updated_emp).data, status=status.HTTP_200_OK)

    @extend_schema(summary="Soft delete an employee record.")
    def destroy(self, request, *args, **kwargs):
        employee = self.get_object()
        employee.is_deleted = True
        employee.status = "inactive"
        employee.save()
        return Response({"message": "Employee soft deleted successfully."}, status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Record a salary payment / monthly salary installment for an employee.",
        request=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "month": openapi.Schema(type=openapi.TYPE_INTEGER, description="1-12"),
                "year": openapi.Schema(type=openapi.TYPE_INTEGER, description="e.g. 2026"),
                "workingDays": openapi.Schema(type=openapi.TYPE_INTEGER, default=30),
                "bonus": openapi.Schema(type=openapi.TYPE_STRING, default="0.00"),
                "deductions": openapi.Schema(type=openapi.TYPE_STRING, default="0.00"),
                "advanceDeduction": openapi.Schema(type=openapi.TYPE_STRING, default="0.00"),
                "amount": openapi.Schema(type=openapi.TYPE_STRING, description="Payment installment amount"),
                "paymentDate": openapi.Schema(type=openapi.TYPE_STRING, format="date"),
                "paymentMethod": openapi.Schema(type=openapi.TYPE_STRING, default="Cash"),
                "paidBy": openapi.Schema(type=openapi.TYPE_STRING),
                "remarks": openapi.Schema(type=openapi.TYPE_STRING),
            },
            required=["month", "year"]
        )
    )
    @action(detail=True, methods=["post"], url_path="salaries")
    def record_salary(self, request, pk=None):
        employee = self.get_object()
        try:
            salary_instance = services.record_salary_payment(employee, request.data)
            return Response(EmployeeSalarySerializer(salary_instance).data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Issue a salary advance to an employee.",
        request=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "date": openapi.Schema(type=openapi.TYPE_STRING, format="date"),
                "amount": openapi.Schema(type=openapi.TYPE_STRING, description="Advance amount"),
                "paymentMethod": openapi.Schema(type=openapi.TYPE_STRING, default="Cash"),
                "reason": openapi.Schema(type=openapi.TYPE_STRING),
            },
            required=["amount"]
        )
    )
    @action(detail=True, methods=["post"], url_path="advances")
    def issue_advance(self, request, pk=None):
        employee = self.get_object()
        try:
            advance_instance = services.issue_salary_advance(employee, request.data)
            return Response(SalaryAdvanceSerializer(advance_instance).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Add a salary increment for an employee.",
        request=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "effectiveDate": openapi.Schema(type=openapi.TYPE_STRING, format="date"),
                "incrementAmount": openapi.Schema(type=openapi.TYPE_STRING, description="Increment amount"),
                "approvedBy": openapi.Schema(type=openapi.TYPE_STRING),
                "reason": openapi.Schema(type=openapi.TYPE_STRING),
            },
            required=["incrementAmount"]
        )
    )
    @action(detail=True, methods=["post"], url_path="increments")
    def add_increment(self, request, pk=None):
        employee = self.get_object()
        try:
            increment_instance = services.process_salary_increment(employee, request.data)
            return Response(EmployeeIncrementSerializer(increment_instance).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Toggle active/inactive status for an employee.",
        request=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "status": openapi.Schema(type=openapi.TYPE_STRING, description="'active' or 'inactive'"),
                "leavingDate": openapi.Schema(type=openapi.TYPE_STRING, format="date"),
                "rejoiningDate": openapi.Schema(type=openapi.TYPE_STRING, format="date"),
            },
            required=["status"]
        )
    )
    @action(detail=True, methods=["post"], url_path="status")
    def toggle_status(self, request, pk=None):
        employee = self.get_object()
        try:
            updated_emp = services.toggle_employee_status(employee, request.data)
            return Response(EmployeeSerializer(updated_emp).data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(summary="Generate printable JSON payslip payload for a specific salary record.")
    @action(detail=True, methods=["get"], url_path=r"salaries/(?P<salary_id>[^/.]+)/payslip")
    def get_payslip(self, request, pk=None, salary_id=None):
        employee = self.get_object()
        try:
            salary_instance = EmployeeSalary.objects.get(id=salary_id, employee=employee)
            payslip_data = services.generate_payslip_data(salary_instance)
            return Response(payslip_data, status=status.HTTP_200_OK)
        except EmployeeSalary.DoesNotExist:
            return Response({"detail": "Salary record not found for this employee."}, status=status.HTTP_404_NOT_FOUND)


class AttendanceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing employee daily attendance, monthly calendar grid, and bulk attendance logging.
    """
    queryset = Attendance.objects.filter(employee__is_deleted=False).order_by("-date", "-id")
    serializer_class = AttendanceSerializer
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Retrieve daily attendance sheet OR monthly attendance calendar grid for an employee.",
        parameters=[
            openapi.Parameter("date", openapi.IN_QUERY, description="Daily sheet date YYYY-MM-DD", type=openapi.TYPE_STRING, format="date"),
            openapi.Parameter("employeeId", openapi.IN_QUERY, description="Employee ID for monthly grid", type=openapi.TYPE_INTEGER),
            openapi.Parameter("month", openapi.IN_QUERY, description="Month 1-12 for monthly grid", type=openapi.TYPE_INTEGER),
            openapi.Parameter("year", openapi.IN_QUERY, description="Year e.g. 2026 for monthly grid", type=openapi.TYPE_INTEGER),
        ]
    )
    def list(self, request, *args, **kwargs):
        emp_id = request.query_params.get("employeeId") or request.query_params.get("employee")
        month_param = request.query_params.get("month")
        year_param = request.query_params.get("year")

        # Monthly Calendar Grid Mode
        if emp_id:
            try:
                emp = Employee.objects.get(id=emp_id, is_deleted=False)
            except (Employee.DoesNotExist, ValueError):
                return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)

            now = timezone.now().date()
            month = int(month_param) if month_param else now.month
            year = int(year_param) if year_param else now.year

            data = services.get_employee_monthly_attendance_tab(emp, month, year)
            return Response(data, status=status.HTTP_200_OK)

        # Daily Sheet Mode
        target_date_str = request.query_params.get("date")
        if target_date_str:
            try:
                target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
            except ValueError:
                return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            target_date = timezone.now().date()

        active_employees = Employee.objects.filter(status="active", is_deleted=False).order_by("emp_no")
        attendances = Attendance.objects.filter(date=target_date, employee__is_deleted=False)
        att_map = {att.employee_id: att for att in attendances}

        sheet = []
        for emp in active_employees:
            att_obj = att_map.get(emp.id)
            if att_obj:
                sheet.append(AttendanceSerializer(att_obj).data)
            else:
                sheet.append({
                    "id": None,
                    "employee": emp.id,
                    "employeeId": emp.id,
                    "empId": emp.emp_no,
                    "employeeName": emp.name,
                    "date": target_date,
                    "status": "unmarked",
                    "remarks": None,
                })

        return Response({
            "date": target_date,
            "totalEmployees": len(sheet),
            "records": sheet,
        }, status=status.HTTP_200_OK)

    @extend_schema(summary="Get configured weekly off days and salary calculation basis for frontend.")
    @action(detail=False, methods=["get"], url_path="config")
    def get_config(self, request):
        return Response({
            "weeklyOffDays": services.get_configured_weekly_off_days(),
            "salaryCalculationBasis": services.get_configured_salary_calculation_basis(),
        }, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        method='get',
        operation_summary="Get bulk attendance sheet for active employees",
        manual_parameters=[
            openapi.Parameter(
                'date',
                openapi.IN_QUERY,
                description="Target date in YYYY-MM-DD format (default: today)",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE,
                required=False
            )
        ]
    )
    @swagger_auto_schema(
        method='post',
        operation_summary="Save bulk attendance records for a specific date",
        request_body=BulkAttendanceSerializer
    )
    @action(detail=False, methods=["get", "post"], url_path="bulk")
    def bulk(self, request):
        if request.method == "GET":
            target_date_str = request.query_params.get("date")
            if not target_date_str:
                return Response({"detail": "date parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
            try:
                target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
            except ValueError:
                return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
            data = services.get_bulk_attendance_data(target_date)
            return Response(data, status=status.HTTP_200_OK)
        else:
            serializer = BulkAttendanceSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            target_date = serializer.validated_data["date"]
            records_data = serializer.validated_data["records"]

            result = services.save_bulk_attendance_records(target_date, records_data)
            return Response(result, status=status.HTTP_200_OK)
