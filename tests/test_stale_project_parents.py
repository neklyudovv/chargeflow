import pytest
from django.core.exceptions import ValidationError

from accounts.models import Customer, Organization
from plans.models import Plan
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import Subscription

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("parent_type", ["customer", "plan"])
def test_subscription_rechecks_persisted_parent_scope(organization, customer, plan, parent_type):
    other = Organization.objects.create(owner=organization.owner, name="Other")
    parent = Customer.objects.get(pk=customer.pk) if parent_type == "customer" else Plan.objects.get(pk=plan.pk)
    parent.organization = other
    parent.save()
    with pytest.raises((ValueError, ValidationError)):
        SubscriptionService.create(customer, plan)
    assert not Subscription.objects.exists()


def test_unsaved_parent_scope_cannot_relabel_subscription(organization, customer, plan):
    from django.utils import timezone
    other = Organization.objects.create(owner=organization.owner, name="Other")
    customer.organization = other
    plan.organization = other
    now = timezone.now()
    subscription = Subscription.objects.create(customer=customer, plan=plan, current_period_start=now, current_period_end=now)
    subscription.refresh_from_db()
    assert subscription.organization_id == organization.pk
