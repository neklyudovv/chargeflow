from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthResponse(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok"])


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    @extend_schema(
        operation_id="getHealth", tags=["Health"], summary="Check application liveness",
        description="",
        request=None, responses={200: HealthResponse}, auth=[],
    )
    def get(self, request):
        return Response(HealthResponse({"status": "ok"}).data)
