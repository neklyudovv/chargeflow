from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from accounts.tokens import KEY_PREFIX, KEY_PREFIX_LENGTH, generate_raw_key, hash_key


class User(AbstractUser):
    email = models.EmailField(unique=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "accounts_user"

    def save(self, *args, **kwargs):
        if not self.username:
            self.username = self.email
        super().save(*args, **kwargs)


class Organization(models.Model):
    owner = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="organizations",
    )
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Customer(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="customers"
    )
    email = models.EmailField()
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.email})"


class ApiKey(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="api_keys"
    )
    name = models.CharField(max_length=100, default="Default")
    prefix = models.CharField(max_length=KEY_PREFIX_LENGTH, db_index=True)
    key_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.organization} - {self.name} ({self.prefix}...)"

    @classmethod
    def create_for_organization(
        cls,
        organization: "Organization",
        name: str = "Default",
        expires_at=None,
    ) -> tuple["ApiKey", str]:
        raw_key = generate_raw_key()
        instance = cls.objects.create(
            organization=organization,
            name=name,
            prefix=raw_key[:KEY_PREFIX_LENGTH],
            key_hash=hash_key(raw_key),
            expires_at=expires_at,
        )
        return instance, raw_key

    @classmethod
    def authenticate(cls, raw_key: str) -> "Organization | None":
        if not raw_key.startswith(KEY_PREFIX):
            return None
        prefix = raw_key[:KEY_PREFIX_LENGTH]
        try:
            api_key = cls.objects.select_related("organization", "organization__owner").get(
                prefix=prefix,
                key_hash=hash_key(raw_key),
                revoked=False,
            )
        except cls.DoesNotExist:
            return None
        if api_key.expires_at and api_key.expires_at < timezone.now():
            return None
        return api_key.organization
