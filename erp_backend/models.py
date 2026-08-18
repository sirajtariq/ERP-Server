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

class BusinessSettings(models.Model):
    """
    Singleton model to hold business identity settings.
    """
    logo = models.TextField(blank=True, null=True)
    business_name = models.CharField(max_length=255)
    contact = models.CharField(max_length=50, blank=True)
    whatsapp = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True, default='')
    SALARY_CALCULATION_CHOICES = [
        ('working_days', 'Working Days (Excluding Off Days)'),
        ('fixed_30', 'Fixed 30 Days'),
        ('month_days', 'Total Calendar Days in Month'),
    ]
    salary_calculation_basis = models.CharField(
        max_length=30,
        choices=SALARY_CALCULATION_CHOICES,
        default='working_days',
    )
    weekly_off_days = models.JSONField(default=list, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def get_solo(cls):
        obj, created = cls.objects.get_or_create(pk=1, defaults={"business_name": "My Business"})
        return obj

    def __str__(self):
        return self.business_name or "Business Settings"

class BackupSetting(models.Model):
    """
    Singleton model to hold database backup configurations.
    """
    FREQUENCY_CHOICES = [
        ('DAILY', 'Daily'),
        ('WEEKLY', 'Weekly'),
        ('MONTHLY', 'Monthly'),
        ('NEVER', 'Never')
    ]
    
    backup_directory = models.CharField(max_length=500, default="C:/ERP_Backups", blank=True)
    backup_frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default='DAILY')
    backup_time = models.TimeField(default="20:00:00")
    retention_days = models.IntegerField(default=30)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def get_settings(cls):
        setting, _ = cls.objects.get_or_create(pk=1)
        return setting

    def __str__(self):
        return f"Backup Settings (Freq: {self.backup_frequency}, Time: {self.backup_time})"
