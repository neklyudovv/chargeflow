from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Invitation, OrganizationMembership, User

ACCEPT_URL = "/api/accounts/invitations/accept/"


def test_accept_invitation_matches_email_case_insensitively(organization):
    # the invite was addressed with different casing than the user registered
    # with; accepting it must still match on email regardless of case.
    invitation = Invitation.create_for_organization(
        organization, "Casey@Example.COM", OrganizationMembership.Role.MEMBER
    )
    invitee = User(email="casey@example.com")
    invitee.set_password("pw-invitee-123!")
    invitee.save()
    client = APIClient()
    token, _ = Token.objects.get_or_create(user=invitee)
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    response = client.post(ACCEPT_URL, {"token": invitation.token}, format="json")

    assert response.status_code == 201
    assert OrganizationMembership.objects.filter(
        organization=organization, user=invitee
    ).exists()
