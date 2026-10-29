from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from accounts.models import ApiKey


class ProjectPrincipal:
    is_authenticated = True
    is_anonymous = False

    def __init__(self, api_key):
        self.organization_id = api_key.organization_id
        self.scope = api_key.scope


class ApiKeyAuthentication(BaseAuthentication):
    """authenticate a service key without impersonating a user"""

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        raw_key = auth_header.removeprefix("Bearer ").strip()
        if not raw_key:
            return None

        api_key = ApiKey.authenticate(raw_key)
        if api_key is None:
            raise AuthenticationFailed("Invalid or expired API key")

        request.organization = api_key.organization
        return (ProjectPrincipal(api_key), api_key)


class ApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    """Describes ApiKeyAuthentication for the OpenAPI schema (Swagger 'Authorize')."""

    target_class = "accounts.authentication.ApiKeyAuthentication"
    name = "ApiKeyAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "cf_<secret>",
            "description": "Service-to-service API key: `Authorization: Bearer cf_<secret>`",
        }
