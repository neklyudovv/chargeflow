from django.http import HttpRequest
from rest_framework.exceptions import PermissionDenied, ValidationError

from accounts.models import Organization


def get_request_organization(request: HttpRequest) -> Organization | None:
    """
    Resolves organization for the request.

    - API key auth: ``request.organization`` is set by authentication.
    - Token auth: optional header ``X-Organization-Id`` (must be owned by the user).
      If the user has exactly one organization and the header is omitted, that org is used.
      Raises ValidationError when the user owns multiple orgs and no header is provided.
      Raises PermissionDenied when the header references an org the user does not own.
      Returns None only when the user has no organizations yet.
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
            return Organization.objects.get(pk=int(raw_id), owner=user)
        except (Organization.DoesNotExist, ValueError, TypeError):
            raise PermissionDenied("Organization not found or not accessible.")

    orgs = list(user.organizations.all()[:2])
    if len(orgs) == 1:
        return orgs[0]
    if len(orgs) > 1:
        raise ValidationError(
            "Multiple organizations found. Provide the X-Organization-Id header to specify which one."
        )
    return None
