from django.contrib import admin

from accounts.models import Customer, Organization

admin.site.register(Organization)
admin.site.register(Customer)
