import logging
from base64 import b64decode
from binascii import Error as Base64Error

from django.core.exceptions import ValidationError as ModelValidationError
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.filters import BaseFilterBackend
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

logger = logging.getLogger(__name__)

ERROR_CODES = (
    "validation_error", "invalid_request", "parse_error", "not_authenticated", "authentication_failed",
    "permission_denied", "organization_required", "organization_ambiguous", "organization_forbidden",
    "not_found", "method_not_allowed", "not_acceptable", "unsupported_media_type", "throttled",
    "invalid_transition", "renewal_not_due", "plan_not_active", "invoice_not_payable",
    "idempotency_conflict", "subscription_exists", "invalid_webhook_signature",
    "service_unavailable", "internal_error",
)


@extend_schema_serializer(many=False)
class ErrorResponse(serializers.Serializer):
    code = serializers.ChoiceField(choices=ERROR_CODES)
    message = serializers.CharField()
    fields = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))


class APIError(APIException):
    def __init__(self, code, message, status_code=400, fields=None):
        self.status_code = status_code
        self.detail = {"code": code, "message": message, "fields": fields or {}}


def field_errors(detail, prefix=""):
    fields = {}
    if isinstance(detail, dict):
        for name, value in detail.items():
            key = f"{prefix}.{name}" if prefix else str(name)
            fields.update(field_errors(value, key))
    elif isinstance(detail, list) and any(isinstance(value, (dict, list)) for value in detail):
        for index, value in enumerate(detail):
            fields.update(field_errors(value, f"{prefix}.{index}"))
    else:
        values = detail if isinstance(detail, list) else [detail]
        fields[prefix or "non_field_errors"] = [str(value) for value in values]
    return fields


def exception_handler(exc, context):
    # defer view imports until DRF finishes loading its configured throttles
    from rest_framework.views import exception_handler as drf_exception_handler, set_rollback

    if isinstance(exc, ModelValidationError):
        exc = ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API exception")
        set_rollback()
        return Response({"code": "internal_error", "message": "An unexpected server error occurred.", "fields": {}}, status=500)
    if isinstance(exc, APIError):
        return response
    if isinstance(exc, ValidationError):
        response.data = {"code": "validation_error", "message": "Request validation failed.", "fields": field_errors(exc.detail)}
    else:
        detail = response.data.get("detail", "Request could not be completed.")
        code = getattr(detail, "code", getattr(exc, "default_code", "invalid_request"))
        response.data = {"code": code, "message": str(detail), "fields": {}}
    return response


class StrictRequestMixin:
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - self.fields.keys()
            if unknown:
                raise ValidationError({name: ["Unknown field."] for name in sorted(unknown)})
        return super().to_internal_value(data)


class Request(StrictRequestMixin, serializers.Serializer):
    pass


class ModelRequest(StrictRequestMixin, serializers.ModelSerializer):
    pass


class OptionalFieldsMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False


class WriteResponseMixin:
    def get_serializer_class(self):
        return self.request_serializers.get(self.action, self.response_serializer_class)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(self.response_serializer_class(serializer.instance, context=self.get_serializer_context()).data, status=201)

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=kwargs.get("partial", False))
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(self.response_serializer_class(serializer.instance, context=self.get_serializer_context()).data)


class CursorPage(CursorPagination):
    page_size_query_param = "page_size"
    max_page_size = 100

    def decode_cursor(self, request):
        raw = request.query_params.get(self.cursor_query_param)
        if raw is not None:
            try:
                if not raw:
                    raise ValueError
                b64decode(raw.encode("ascii"), validate=True)
            except (Base64Error, UnicodeError, ValueError):
                raise ValidationError({"cursor": ["Invalid cursor."]})
        try:
            return super().decode_cursor(request)
        except NotFound:
            raise ValidationError({"cursor": ["Invalid cursor."]})

    def get_ordering(self, request, queryset, view):
        return (f"-{getattr(view, 'timestamp_field', 'created_at')}", "-id")

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "required": ["next", "previous", "results"],
            "properties": {
                "next": {"type": ["string", "null"], "format": "uri"},
                "previous": {"type": ["string", "null"], "format": "uri"},
                "results": schema,
            },
        }

    def get_schema_operation_parameters(self, view):
        params = super().get_schema_operation_parameters(view)
        for param in params:
            param.pop("description", None)
            if param["name"] == "cursor":
                param["schema"].update(minLength=1, format="byte")
            if param["name"] == "page_size":
                param["schema"].update(minimum=1, maximum=100, default=25)
        return params


class TimeFilters(serializers.Serializer):
    created_after = serializers.DateTimeField(required=False)
    created_before = serializers.DateTimeField(required=False)

    def validate(self, attrs):
        if attrs.get("created_after") and attrs.get("created_before") and attrs["created_after"] > attrs["created_before"]:
            raise ValidationError({"created_before": ["Must be at or after created_after."]})
        return attrs


class PageQuery(serializers.Serializer):
    page_size = serializers.IntegerField(min_value=1, max_value=100, required=False)


class QueryFilters(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        if view.action != "list":
            return queryset
        query = view.filter_serializer_class(data={name: request.query_params[name] for name in request.query_params})
        allowed = set(query.fields) | {"cursor", "page_size", "format"}
        unknown = set(request.query_params) - allowed
        if unknown:
            raise ValidationError({key: ["Unknown query parameter."] for key in sorted(unknown)})
        page = PageQuery(data=request.query_params)
        page.is_valid(raise_exception=True)
        query.is_valid(raise_exception=True)
        timestamp = getattr(view, "timestamp_field", "created_at")
        lookups = {"created_after": f"{timestamp}__gte", "created_before": f"{timestamp}__lte", **getattr(view, "filter_lookups", {})}
        return queryset.filter(**{lookups.get(name, name): value for name, value in query.validated_data.items()})

    def get_schema_operation_parameters(self, view):
        return []


def get_idempotency_key(request):
    value = request.headers.get("Idempotency-Key")
    if value is None:
        return None
    if not value.strip() or len(value) > 255:
        raise APIError("validation_error", "Request validation failed.", fields={"Idempotency-Key": ["Must contain 1 to 255 characters."]})
    return value
