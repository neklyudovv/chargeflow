from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from accounts.models import ApiKey


class ApiKeyAuthentication(BaseAuthentication):
    """
    Authenticates requests using an API key passed in the Authorization header.
    Expected format: Authorization: Bearer cf_<token>
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

        return (organization, None)
