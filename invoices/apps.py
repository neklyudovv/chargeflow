from django.apps import AppConfig


class InvoicesConfig(AppConfig):
    name = "invoices"

    def ready(self):
        from infrastructure.events import event_bus
        from invoices.handlers import (
            on_payment_failed,
            on_payment_succeeded,
            on_subscription_activated,
            on_subscription_created,
            on_subscription_renewed,
        )
        from payments.domain.events import PaymentFailed, PaymentSucceeded
        from subscriptions.domain.events import (
            SubscriptionActivated,
            SubscriptionCreated,
            SubscriptionRenewed,
        )

        event_bus.subscribe(SubscriptionCreated, on_subscription_created)
        event_bus.subscribe(SubscriptionActivated, on_subscription_activated)
        event_bus.subscribe(SubscriptionRenewed, on_subscription_renewed)
        event_bus.subscribe(PaymentSucceeded, on_payment_succeeded)
        event_bus.subscribe(PaymentFailed, on_payment_failed)
