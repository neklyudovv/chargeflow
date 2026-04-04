from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from accounts.models import ApiKey, Customer, Organization, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "username", "is_staff", "is_active")
    search_fields = ("email", "username")


admin.site.register(Organization)
admin.site.register(Customer)
admin.site.register(ApiKey)
