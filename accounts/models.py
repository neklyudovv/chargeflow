import secrets
from datetime import timedelta

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


class OrganizationMembership(models.Model):
    class Role(models.TextChoices):
        OWNER  = "owner",  "Owner"
        ADMIN  = "admin",  "Admin"
        MEMBER = "member", "Member"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_organization_membership"
        unique_together = [("organization", "user")]
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(role="owner"),
                name="unique_org_owner",
            )
        ]

    def __str__(self):
        return f"{self.user.email} @ {self.organization.name} ({self.role})"


class Invitation(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="invitations"
    )
    email = models.EmailField()
    role = models.CharField(
        max_length=20,
        choices=OrganizationMembership.Role.choices,
        default=OrganizationMembership.Role.MEMBER,
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_invitation"

    def __str__(self):
        return f"Invite {self.email} → {self.organization.name}"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_accepted(self):
        return self.accepted_at is not None

    @classmethod
    def create_for_organization(cls, organization, email, role=OrganizationMembership.Role.MEMBER):
        return cls.objects.create(
            organization=organization,
            email=email,
            role=role,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(days=7),
        )


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
