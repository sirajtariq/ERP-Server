from django.db import models
from django.contrib.auth.models import User

class UserProfile(models.Model):
    EMPLOYMENT_CHOICES = [
        ('fulltime', 'Full-time'),
        ('parttime', 'Part-time'),
        ('contract', 'Contract'),
    ]

    SALARY_CHOICES = [
        ('monthly', 'Monthly'),
        ('daily', 'Daily'),
        ('perjob', 'Per-job'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    fullname = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    cnic = models.CharField(max_length=50)
    address = models.TextField(blank=True, null=True)
    designation = models.CharField(max_length=100)
    dateofjoining = models.DateField()
    employmenttype = models.CharField(max_length=20, choices=EMPLOYMENT_CHOICES)
    basicsalary = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    salarytype = models.CharField(max_length=20, choices=SALARY_CHOICES, blank=True, null=True)

    def __str__(self):
        return self.fullname
