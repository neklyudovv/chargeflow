from django.contrib import admin

from invoices.domain.models import Invoice, InvoiceLine

admin.site.register(Invoice)
admin.site.register(InvoiceLine)
