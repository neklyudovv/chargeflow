from django.apps import AppConfig


class InvoicesConfig(AppConfig):
    name = "invoices"

    def ready(self):
        from infrastructure.events import event_dispatcher
        from invoices import tasks
        from payments.domain.events import PaymentFailed, PaymentSucceeded
        from subscriptions.domain.events import (
            SubscriptionActivated,
            SubscriptionCanceled,
            SubscriptionCreated,
            SubscriptionRenewed,
        )

        # The dispatcher enqueues each subscribed task itself, passing the
        # event's fields as kwargs — task params must mirror event fields.
        event_dispatcher.subscribe(
            SubscriptionCreated, tasks.generate_invoice_for_subscription
        )
        event_dispatcher.subscribe(
            SubscriptionActivated, tasks.generate_invoice_for_subscription
        )
        event_dispatcher.subscribe(
            SubscriptionRenewed, tasks.generate_invoice_for_subscription
        )
        event_dispatcher.subscribe(PaymentSucceeded, tasks.mark_invoice_paid)
        event_dispatcher.subscribe(PaymentFailed, tasks.mark_invoice_failed)
        event_dispatcher.subscribe(
            SubscriptionCanceled, tasks.cancel_open_invoices_for_subscription
        )
