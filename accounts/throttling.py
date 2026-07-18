from logging import getLogger

from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle

from accounts.request_context import get_request_organization

logger = getLogger(__name__)


class FailOpenMixin:
    """Allow the request through when the throttle backend (Redis) is
    unreachable, instead of failing the whole API.

    A rate limiter that hard-fails on a Redis outage turns a cache problem into
    a full outage - worse than briefly not enforcing limits. So a backend error
    fails open; only a real over-limit verdict blocks.
    """

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            logger.exception("Throttle backend unavailable; allowing request through")
            return True


class OrganizationRateThrottle(FailOpenMixin, SimpleRateThrottle):
    """Default throttle for authenticated routes: one shared budget per tenant.

    Every API key and user token of an organization draws on the same counter,
    so limits can't be multiplied by minting more keys. Requests that don't
    resolve to an org (anonymous, misconfigured) fall back to a per-IP budget.
    """

    scope = "org"

    def get_cache_key(self, request, view):
        try:
            org = get_request_organization(request)
        except Exception:
            # Org resolution can reject the request (unknown/other org, ambiguous
            # membership); that's the view's job to answer, not the throttle's.
            org = None
        ident = f"org:{org.pk}" if org is not None else f"ip:{self.get_ident(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class AuthRateThrottle(FailOpenMixin, ScopedRateThrottle):
    pass
