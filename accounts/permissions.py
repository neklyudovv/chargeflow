from rest_framework.permissions import BasePermission

from accounts.models import OrganizationMembership


class IsOrgAdmin(BasePermission):
    """Allows OWNER or ADMIN roles. API key auth (no membership) is treated as full access."""

    def has_permission(self, request, view):
        if hasattr(request, "organization") and not hasattr(request, "membership"):
            return True
        if not hasattr(request, "membership"):
            # Permission runs before the view, so membership may not be resolved yet.
            from accounts.request_context import get_request_organization
            get_request_organization(request)
        membership = getattr(request, "membership", None)
        if membership is None:
            return False
        return membership.role in (OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN)
