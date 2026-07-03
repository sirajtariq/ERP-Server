from django.db import models
from django.utils import timezone


class SoftDeleteManager(models.Manager):
    """Default manager — only returns non-deleted records."""
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AllRecordsManager(models.Manager):
    """Returns both deleted and non-deleted records (for trash views)."""
    def get_queryset(self):
        return super().get_queryset()


class SoftDeleteModel(models.Model):
    """Abstract base model providing soft-delete behavior."""
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = AllRecordsManager()

    class Meta:
        abstract = True

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=['is_deleted', 'deleted_at'])
