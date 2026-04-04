from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from accounts.models import ApiKey


class ApiKeyAuthentication(BaseAuthentication):
    """
    Service-to-service: Authorization: Bearer cf_<secret>
    Sets request.organization and authenticates as the owning User (for permissions).
    """

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        raw_key = auth_header.removeprefix("Bearer ").strip()
        if not raw_key:
            return None

        organization = ApiKey.authenticate(raw_key)
        if organization is None:
            raise AuthenticationFailed("Invalid or expired API key")

        request.organization = organization
        return (organization.owner, None)
