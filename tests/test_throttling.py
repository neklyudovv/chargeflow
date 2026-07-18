from unittest import mock

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import OrganizationMembership

REGISTER_URL = "/api/auth/register/"
LOGIN_URL = "/api/auth/login/"
SUBSCRIPTIONS_URL = "/api/subscriptions/"


@pytest.fixture
def member(user, organization):
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.OWNER,
    )
    return user


@pytest.fixture
def auth_client(member):
    token, _ = Token.objects.get_or_create(user=member)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def test_register_is_throttled_by_ip(db):
    client = APIClient()
    # unique email each time so a rejection is the throttle, not a duplicate
    statuses = [
        client.post(
            REGISTER_URL,
            {"email": f"new{i}@example.com", "password": "sup3r-secret-pw!"},
            format="json",
        ).status_code
        for i in range(6)
    ]

    # 5/min: the first five go through, the 6th from the same IP is rejected
    assert 429 not in statuses[:5]
    assert statuses[-1] == 429


def test_login_is_throttled_by_ip(db):
    client = APIClient()
    payload = {"email": "nobody@example.com", "password": "wrong"}

    statuses = [
        client.post(LOGIN_URL, payload, format="json").status_code
        for _ in range(6)
    ]

    # 5/min regardless of whether the credentials are valid - this is the
    # brute-force guard
    assert statuses[-1] == 429
    assert statuses.count(429) == 1


def test_spoofed_forwarded_for_does_not_escape_login_throttle(db):
    # NUM_PROXIES=0 -> the throttle keys on REMOTE_ADDR, so a client rotating
    # X-Forwarded-For can't land each attempt in a fresh bucket
    client = APIClient()
    payload = {"email": "nobody@example.com", "password": "wrong"}

    statuses = [
        client.post(
            LOGIN_URL, payload, format="json", HTTP_X_FORWARDED_FOR=f"9.9.9.{i}"
        ).status_code
        for i in range(6)
    ]

    assert statuses[-1] == 429


def test_429_carries_retry_after(db):
    client = APIClient()
    payload = {"email": "nobody@example.com", "password": "wrong"}
    for _ in range(5):
        client.post(LOGIN_URL, payload, format="json")

    response = client.post(LOGIN_URL, payload, format="json")

    assert response.status_code == 429
    assert response.has_header("Retry-After")


def test_org_requests_share_one_budget(auth_client):
    # all of an org's requests draw on a single counter
    with mock.patch(
        "accounts.throttling.OrganizationRateThrottle.get_rate", return_value="3/min"
    ):
        statuses = [
            auth_client.get(SUBSCRIPTIONS_URL).status_code for _ in range(4)
        ]

    assert statuses[-1] == 429


def test_backend_error_fails_open(auth_client):
    # a throttle backend outage must not take the API down
    with mock.patch(
        "rest_framework.throttling.SimpleRateThrottle.allow_request",
        side_effect=RuntimeError("redis down"),
    ):
        response = auth_client.get(SUBSCRIPTIONS_URL)

    assert response.status_code == 200
