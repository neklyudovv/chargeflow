from django.contrib import admin

from payments.domain.models import PaymentAttempt, WebhookEvent

admin.site.register(PaymentAttempt)
admin.site.register(WebhookEvent)
