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
    logo = models.ImageField(upload_to='business_logo/', blank=True, null=True)
    business_name = models.CharField(max_length=255)
    contact = models.CharField(max_length=50, blank=True)
    whatsapp = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
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
