import json
import time

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from invoices.domain.models import Invoice, InvoiceStatus
from payments.application.services import InvoiceNotPayable, PaymentService
from payments.domain.models import PaymentAttempt, PaymentStatus
from subscriptions.application.services import SubscriptionService
from subscriptions.domain.models import Subscription, SubscriptionStatus

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def issued(subscription, invoice):
    Subscription.objects.filter(pk=subscription.pk).update(status=SubscriptionStatus.ACTIVE)
    Invoice.objects.filter(pk=invoice.pk).update(status=InvoiceStatus.ISSUED)
    return invoice


def test_late_decline_cannot_revoke_a_newer_applied_success(issued):
    old = PaymentAttempt.objects.create(invoice=issued, amount=issued.total, status=PaymentStatus.FAILED)
    winner = PaymentAttempt.objects.create(invoice=issued, amount=issued.total)
    PaymentService.settle(winner, True, {'status': 'ok'})
    PaymentService.settle(old, False, {'status': 'declined'})
    issued.refresh_from_db()
    issued.subscription.refresh_from_db()
    assert issued.status == InvoiceStatus.PAID
    assert issued.subscription.status == SubscriptionStatus.ACTIVE


def test_canceled_subscription_cannot_start_a_new_charge_on_overdue_invoice(issued):
    Invoice.objects.filter(pk=issued.pk).update(status=InvoiceStatus.OVERDUE)
    SubscriptionService.cancel(issued.subscription)
    with pytest.raises(InvoiceNotPayable):
        PaymentService.attempt(issued)
    assert not PaymentAttempt.objects.exists()


def test_conflicting_provider_charge_is_retained_for_reconciliation(issued):
    pending = PaymentAttempt.objects.create(invoice=issued, amount=issued.total)
    PaymentService.settle(pending, True, {'provider_charge_id': 'charge_first'})
    PaymentService.settle(pending, True, {'provider_charge_id': 'charge_conflicting'})
    pending.refresh_from_db()
    assert pending.status == PaymentStatus.SUCCESS
    assert pending.needs_reconciliation
    assert 'charge_conflicting' in json.dumps(pending.provider_response)


@pytest.mark.parametrize('field,value', [('amount', '99.00'), ('currency', 'EUR'), ('provider_idempotency_key', 'changed-key'), ('provider_customer_id', 'changed-customer'), ('provider_payment_method_id', 'changed-method')])
def test_persisted_gateway_snapshot_cannot_be_edited(issued, field, value):
    pending = PaymentAttempt.objects.create(invoice=issued, amount=issued.total)
    setattr(pending, field, value)
    with pytest.raises(ValidationError):
        pending.save(update_fields=[field])


def test_non_ascii_signature_is_a_client_error_without_storing_event(db):
    client = APIClient(raise_request_exception=False)
    response = client.post('/api/payments/webhook/', b'{}', content_type='application/json', HTTP_X_WEBHOOK_SIGNATURE=f't={int(time.time())},v1=\u00e9')
    assert response.status_code == 400


def test_same_provider_charge_cannot_pay_two_invoices(issued):
    from datetime import timedelta

    second = Invoice.objects.create(subscription=issued.subscription, total=issued.total, currency='USD', period_start=issued.period_start + timedelta(seconds=1), period_end=issued.period_end + timedelta(seconds=1), status=InvoiceStatus.ISSUED)
    first_attempt = PaymentAttempt.objects.create(invoice=issued, amount=issued.total)
    second_attempt = PaymentAttempt.objects.create(invoice=second, amount=second.total)
    PaymentService.settle(first_attempt, True, {'provider_charge_id': 'charge_shared'})
    PaymentService.settle(second_attempt, True, {'provider_charge_id': 'charge_shared'})
    second_attempt.refresh_from_db()
    second.refresh_from_db()
    assert second_attempt.needs_reconciliation
    assert second.status != InvoiceStatus.PAID


def test_mock_receipt_does_not_retain_customer_tokens_after_billing_deletion(issued, customer):
    from accounts.models import Customer
    from payments.domain.models import MockChargeReceipt

    Customer.objects.filter(pk=customer.pk).update(provider_customer_id='cus_private_snapshot', provider_payment_method_id='pm_private_snapshot')
    PaymentService.attempt(issued)
    customer.delete()
    stored = json.dumps(list(MockChargeReceipt.objects.values()), default=str)
    assert 'cus_private_snapshot' not in stored
    assert 'pm_private_snapshot' not in stored
