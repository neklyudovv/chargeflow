from django.http import HttpRequest
from rest_framework.exceptions import PermissionDenied, ValidationError

from accounts.models import Organization, OrganizationMembership


def get_request_organization(request: HttpRequest) -> Organization | None:
    """
    Resolves organization for the request and, for token auth, sets request.membership.

    - API key auth: request.organization is set by the authenticator; no membership applies.
    - Token auth: resolves via OrganizationMembership (not Organization.owner) so that
      non-owner members can access the org.
      Raises PermissionDenied when the header references an org the user is not a member of.
      Raises ValidationError when the user belongs to multiple orgs and no header is provided.
      Returns None only when the user has no memberships yet.
    """
    org = getattr(request, "organization", None)
    if org is not None:
        return org

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None

    raw_id = request.META.get("HTTP_X_ORGANIZATION_ID")
    if raw_id:
        try:
            membership = OrganizationMembership.objects.select_related("organization").get(
                organization_id=int(raw_id),
                user=user,
            )
        except (OrganizationMembership.DoesNotExist, ValueError, TypeError):
            raise PermissionDenied("Organization not found or not accessible.")
        request.membership = membership
        return membership.organization

    memberships = list(
        OrganizationMembership.objects.select_related("organization").filter(user=user)[:2]
    )
    if len(memberships) == 1:
        request.membership = memberships[0]
        return memberships[0].organization
    if len(memberships) > 1:
        raise ValidationError(
            "Multiple organizations found. Provide the X-Organization-Id header to specify which one."
        )
    return None
