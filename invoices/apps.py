from django.apps import AppConfig


class InvoicesConfig(AppConfig):
    name = "invoices"

    def ready(self):
        from infrastructure.events import event_bus
        from invoices import tasks
        from payments.domain.events import PaymentFailed, PaymentSucceeded
        from subscriptions.domain.events import (
            SubscriptionActivated,
            SubscriptionCanceled,
            SubscriptionCreated,
            SubscriptionRenewed,
        )

        # The bus stays the router; each handler now enqueues a Celery task
        # (event -> task happens here, inside the subscription registration).
        event_bus.subscribe(
            SubscriptionCreated,
            lambda e: tasks.generate_invoice_for_subscription.delay(e.subscription_id),
        )
        event_bus.subscribe(
            SubscriptionActivated,
            lambda e: tasks.generate_invoice_for_subscription.delay(e.subscription_id),
        )
        event_bus.subscribe(
            SubscriptionRenewed,
            lambda e: tasks.generate_invoice_for_subscription.delay(e.subscription_id),
        )
        event_bus.subscribe(
            PaymentSucceeded,
            lambda e: tasks.mark_invoice_paid.delay(e.payment_attempt_id),
        )
        event_bus.subscribe(
            PaymentFailed,
            lambda e: tasks.mark_invoice_failed.delay(e.payment_attempt_id),
        )
        event_bus.subscribe(
            SubscriptionCanceled,
            lambda e: tasks.cancel_open_invoices_for_subscription.delay(e.subscription_id),
        )
