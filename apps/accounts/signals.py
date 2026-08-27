from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile, User, Verification


@receiver(post_save, sender=User)
def create_related_rows(sender, instance, created, **kwargs):
    """Every user has exactly one Profile and one Verification, always."""
    if created:
        Profile.objects.get_or_create(user=instance)
        Verification.objects.get_or_create(user=instance)
