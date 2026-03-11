from invoices.application.services import InvoiceService
from payments.domain.models import PaymentAttempt
from subscriptions.domain.models import Subscription, SubscriptionStatus


def on_subscription_created(event) -> None:
    subscription = Subscription.objects.select_related("plan").get(pk=event.subscription_id)
    if subscription.status != SubscriptionStatus.TRIAL:
        InvoiceService.generate_for_subscription(subscription)


def on_subscription_activated(event) -> None:
    subscription = Subscription.objects.select_related("plan").get(pk=event.subscription_id)
    InvoiceService.generate_for_subscription(subscription)


def on_subscription_renewed(event) -> None:
    subscription = Subscription.objects.select_related("plan").get(pk=event.subscription_id)
    InvoiceService.generate_for_subscription(subscription)


def on_payment_succeeded(event) -> None:
    payment = PaymentAttempt.objects.select_related("invoice").get(pk=event.payment_attempt_id)
    InvoiceService.mark_paid(payment.invoice)


def on_payment_failed(event) -> None:
    payment = PaymentAttempt.objects.select_related("invoice").get(pk=event.payment_attempt_id)
    InvoiceService.mark_failed(payment.invoice)
