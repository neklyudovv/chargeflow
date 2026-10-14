"""Proves the billing chain through a real worker: the charge hand-off
(commit -> broker -> worker) settles the invoice and the subscription.

Needs a migrated database and a running worker listening on the io and batch queues.
"""
import os
import sys
import time
from datetime import datetime

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from accounts.models import Customer, Organization, User  # noqa: E402
from invoices.domain.models import Invoice, InvoiceStatus  # noqa: E402
from payments.domain.models import PaymentStatus  # noqa: E402
from plans.models import Plan  # noqa: E402
from subscriptions.application.services import SubscriptionService  # noqa: E402

TIMEOUT_SECONDS = 60


def log(message):
    print(f"{datetime.now():%H:%M:%S} {message}", flush=True)


user = User(email="e2e@example.com")
user.set_password("e2e-password-123!")
user.save()
organization = Organization.objects.create(owner=user, name="E2E")
plan = Plan.objects.create(organization=organization, name="E2E", price="10", currency="USD")
customer = Customer.objects.create(organization=organization, email="e2e@example.com", name="E2E")

subscription = SubscriptionService.create(customer, plan)
log(f"subscription {subscription.pk} created")

last_state = None
for _ in range(TIMEOUT_SECONDS):
    invoice = Invoice.objects.filter(subscription=subscription).first()
    successes = invoice.payment_attempts.filter(status=PaymentStatus.SUCCESS).count() if invoice else 0
    state = (invoice.status if invoice else None, successes)
    if state != last_state:
        log(f"invoice status={state[0]} successful attempts={state[1]}")
        last_state = state
    if state == (InvoiceStatus.PAID, 1):
        log("chain settled")
        sys.exit(0)
    time.sleep(1)

log(f"timeout after {TIMEOUT_SECONDS}s: invoice not PAID with exactly one SUCCESS attempt (last state {last_state})")
sys.exit(1)
