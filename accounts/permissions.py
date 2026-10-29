from rest_framework.permissions import BasePermission, SAFE_METHODS

from accounts.authentication import ProjectPrincipal
from accounts.models import ApiKey, OrganizationMembership
from accounts.request_context import get_request_organization


class IsHuman(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and not isinstance(request.user, ProjectPrincipal)


class IsOrgAdmin(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated or isinstance(request.user, ProjectPrincipal):
            return False
        get_request_organization(request)
        membership = getattr(request, "membership", None)
        return membership is not None and membership.role in (
            OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN
        )


class ProjectAccess(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        if isinstance(request.user, ProjectPrincipal):
            return request.user.scope == ApiKey.Scope.READ_WRITE
        return IsOrgAdmin().has_permission(request, view)
