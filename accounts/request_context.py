from django.http import HttpRequest

from accounts.models import Organization


def get_request_organization(request: HttpRequest) -> Organization | None:
    """
    Resolves organization for the request.

    - API key auth: ``request.organization`` is set by authentication.
    - Token auth: optional header ``X-Organization-Id`` (must be owned by the user).
      If the user has exactly one organization and the header is omitted, that org is used.
      If the user has zero or multiple organizations and the header is omitted, returns None
      (list endpoints return empty; create requires an explicit org scope).
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
            return None

    qs = user.organizations.all()
    if qs.count() == 1:
        return qs.first()
    return None
