from invoices.domain.models import Invoice
from subscriptions.domain.models import Subscription, SubscriptionStatus


def on_invoice_failed(event) -> None:
    invoice = Invoice.objects.select_related("subscription").get(pk=event.invoice_id)
    subscription = invoice.subscription
    if subscription.status == SubscriptionStatus.ACTIVE:
        subscription.transition_to(SubscriptionStatus.OVERDUE)
