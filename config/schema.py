from http import HTTPStatus

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes

from config.api import ErrorResponse


class PublicSchema(AutoSchema):
    def get_serializer_name(self, serializer, direction):
        name = super().get_serializer_name(serializer, direction)
        if direction == "request" and name.endswith("Request"):
            return name[:-7]
        return name

    def get_paginated_name(self, serializer_name):
        return serializer_name.removesuffix("Response") + "Page"

    def get_override_parameters(self):
        params = list(super().get_override_parameters())
        if getattr(self.view, "organization_scoped", False):
            params.append(OpenApiParameter(
                name="X-Organization-Id", type=OpenApiTypes.INT, location=OpenApiParameter.HEADER,
            ))
        if getattr(self.view, "action", None) == "list":
            params.append(self.view.filter_serializer_class)
        params.append(OpenApiParameter(
            name="Retry-After", type=OpenApiTypes.INT, location=OpenApiParameter.HEADER,
            response=[429],
        ))
        params.append(OpenApiParameter(
            name="WWW-Authenticate", type=OpenApiTypes.STR, location=OpenApiParameter.HEADER,
            response=[401],
        ))
        return params

    def _get_response_bodies(self, direction="response"):
        responses = super()._get_response_bodies(direction)
        if self.path.startswith("/api/v1/") or self.path == "/health/":
            codes = [405, 406, 500]
            if self.method in ["POST", "PUT", "PATCH"]:
                codes += [400, 415]
            if getattr(self.view, "organization_scoped", False) or getattr(self.view, "action", None) == "list":
                codes += [400]
            if getattr(self.view, "action", None) in ["retrieve", "partial_update", "destroy", "activate", "cancel", "renew"]:
                codes += [404]
            if self.view.throttle_classes:
                codes += [429]
            if self.view.authentication_classes:
                codes += [401, 403]
            if getattr(self.view, "authentication_errors", False):
                codes += [401]
            for code in codes:
                responses.setdefault(str(code), self._get_response_for_code(ErrorResponse, str(code), direction=direction))
        for code, response in responses.items():
            if code.isdigit():
                response["description"] = HTTPStatus(int(code)).phrase
            content = response.get("content", {}).get("application/json")
            if content is None:
                continue
            name = content.get("schema", {}).get("$ref", "").rsplit("/", 1)[-1]
            example = RESPONSE_EXAMPLES.get(name)
            if name.endswith("Page"):
                row = RESPONSE_EXAMPLES.get(name.removesuffix("Page") + "Response")
                if row is not None:
                    example = {"next": None, "previous": None, "results": [row]}
            if code in ERROR_EXAMPLES:
                example = ERROR_EXAMPLES[code]
            if example is not None:
                content.setdefault("examples", {"Example": {"value": example}})
        return responses


def inline_empty_enums(node):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref == "#/components/schemas/NullEnum":
            return {"type": "null"}
        if ref == "#/components/schemas/BlankEnum":
            return {"const": ""}
        return {key: inline_empty_enums(value) for key, value in node.items()}
    if isinstance(node, list):
        return [inline_empty_enums(value) for value in node]
    return node


def contract_components(result, generator, request, public):
    # inline synthetic null and blank enums without dropping their allowed values
    result = inline_empty_enums(result)
    components = result.get("components", {}).get("schemas", {})
    components.pop("NullEnum", None)
    components.pop("BlankEnum", None)
    for name, component in components.items():
        if name.endswith("Request") and component.get("type") == "object":
            component["additionalProperties"] = False
    return result


EXAMPLE_TIME = "2030-01-15T12:00:00Z"
RESPONSE_EXAMPLES = {
    "HealthResponse": {"status": "ok"},
    "LoginResponse": {"token": "example-user-token"},
    "RegisterResponse": {"token": "example-user-token", "user": {"id": 1, "email": "owner@example.com"}},
    "OrganizationResponse": {"id": 1, "name": "Production", "created_at": EXAMPLE_TIME, "updated_at": EXAMPLE_TIME, "my_role": "owner"},
    "CustomerResponse": {"id": 1, "email": "customer@example.com", "name": "Example Customer", "provider_customer_id": "", "provider_payment_method_id": "", "created_at": EXAMPLE_TIME, "updated_at": EXAMPLE_TIME},
    "PlanResponse": {"id": 1, "name": "Pro", "description": "Monthly subscription", "price": "19.99", "currency": "USD", "status": "active", "interval": "month", "trial_period_days": 0, "created_at": EXAMPLE_TIME, "updated_at": EXAMPLE_TIME},
    "SubscriptionResponse": {"id": 1, "customer_id": 1, "plan_id": 1, "status": "active", "current_period_start": EXAMPLE_TIME, "current_period_end": "2030-02-15T12:00:00Z", "canceled_at": None, "canceled_reason": None, "created_at": EXAMPLE_TIME, "updated_at": EXAMPLE_TIME},
    "InvoiceResponse": {"id": 1, "subscription_id": 1, "status": "paid", "total": "19.99", "currency": "USD", "period_start": EXAMPLE_TIME, "period_end": "2030-02-15T12:00:00Z", "first_failed_at": None, "next_retry_at": None, "attempt_count": 1, "issued_at": EXAMPLE_TIME, "paid_at": EXAMPLE_TIME, "created_at": EXAMPLE_TIME, "updated_at": EXAMPLE_TIME, "lines": [{"id": 1, "description": "Pro monthly subscription", "amount": "19.99"}]},
    "PaymentAttemptResponse": {"id": 1, "invoice_id": 1, "status": "success", "amount": "19.99", "currency": "USD", "provider": "mock", "provider_charge_id": "mock_example", "attempted_at": EXAMPLE_TIME, "needs_reconciliation": False},
    "ApiKeyResponse": {"id": 1, "name": "Reporting", "prefix": "cf_example", "created_at": EXAMPLE_TIME, "expires_at": None, "revoked": False, "scope": "read"},
    "MembershipResponse": {"id": 1, "user_email": "member@example.com", "role": "member", "joined_at": EXAMPLE_TIME},
    "InvitationResponse": {"id": 1, "email": "member@example.com", "role": "member", "expires_at": "2030-01-22T12:00:00Z", "accepted_at": None, "created_at": EXAMPLE_TIME},
    "PaymentEventResponse": {"status": "ok"},
}
RESPONSE_EXAMPLES["MeResponse"] = {"user": {"id": 1, "email": "owner@example.com"}, "organization": RESPONSE_EXAMPLES["OrganizationResponse"], "role": "owner"}
RESPONSE_EXAMPLES["ApiKeyCreatedResponse"] = {**RESPONSE_EXAMPLES["ApiKeyResponse"], "key": "cf_example_not_a_real_key"}
RESPONSE_EXAMPLES["OrganizationCreatedResponse"] = {"organization": RESPONSE_EXAMPLES["OrganizationResponse"], "api_key": RESPONSE_EXAMPLES["ApiKeyCreatedResponse"]}
RESPONSE_EXAMPLES["InvitationCreatedResponse"] = {**RESPONSE_EXAMPLES["InvitationResponse"], "token": "example-invitation-token"}


ERROR_EXAMPLES = {'400': {'code': 'validation_error',
         'message': 'Request validation failed.',
         'fields': {'non_field_errors': ['Invalid request.']}},
 '401': {'code': 'not_authenticated',
         'message': 'Authentication credentials were not provided.',
         'fields': {}},
 '403': {'code': 'permission_denied',
         'message': 'You do not have permission to perform this action.',
         'fields': {}},
 '404': {'code': 'not_found', 'message': 'Resource not found.', 'fields': {}},
 '405': {'code': 'method_not_allowed', 'message': 'Method is not allowed.', 'fields': {}},
 '406': {'code': 'not_acceptable', 'message': 'Could not satisfy the request Accept header.', 'fields': {}},
 '409': {'code': 'idempotency_conflict',
         'message': 'The key is already bound to another request.',
         'fields': {}},
 '415': {'code': 'unsupported_media_type', 'message': 'Unsupported request media type.', 'fields': {}},
 '429': {'code': 'throttled', 'message': 'Request was throttled.', 'fields': {}},
 '500': {'code': 'internal_error', 'message': 'An unexpected server error occurred.', 'fields': {}},
 '503': {'code': 'service_unavailable',
         'message': 'Payment event verification is temporarily unavailable.',
         'fields': {}}}
