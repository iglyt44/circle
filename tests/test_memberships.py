from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import CommunityMember


def register_and_login(client: TestClient, suffix: str) -> tuple[int, str, str]:
    username = f"member_user_{suffix}"
    email = f"{username}@example.com"
    password = "member password"
    registration = client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert registration.status_code == 201, registration.text
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    return registration.json()["id"], username, login.json()["access_token"]


def test_join_leave_and_list_community_members() -> None:
    suffix = uuid4().hex

    with TestClient(app) as client:
        user_id, username, token = register_and_login(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}
        community = client.post(
            "/api/communities",
            headers=headers,
            json={
                "name": f"membership_community_{suffix}",
                "description": "Membership testing",
            },
        )
        assert community.status_code == 201, community.text
        community_id = community.json()["id"]
        assert community.json()["member_count"] == 0

        unauthenticated_join = client.post(
            f"/api/communities/{community_id}/join",
            json={"user_id": 999999},
        )
        assert unauthenticated_join.status_code == 401
        unauthenticated_leave = client.post(
            f"/api/communities/{community_id}/leave",
        )
        assert unauthenticated_leave.status_code == 401

        joined = client.post(
            f"/api/communities/{community_id}/join",
            headers=headers,
            json={"user_id": 999999},
        )
        assert joined.status_code == 201, joined.text
        assert joined.json()["id"] == user_id
        assert joined.json()["username"] == username

        with SessionLocal() as database:
            membership = (
                database.query(CommunityMember)
                .filter_by(user_id=user_id, community_id=community_id)
                .one()
            )
            assert membership.user_id == user_id

        duplicate = client.post(
            f"/api/communities/{community_id}/join",
            headers=headers,
        )
        assert duplicate.status_code == 409

        members = client.get(f"/api/communities/{community_id}/members")
        assert members.status_code == 200, members.text
        assert members.json()[0]["id"] == user_id
        assert members.json()[0]["username"] == username

        communities = client.get("/api/communities")
        assert communities.status_code == 200, communities.text
        matching = [item for item in communities.json() if item["id"] == community_id]
        assert matching[0]["member_count"] == 1

        left = client.post(
            f"/api/communities/{community_id}/leave",
            headers=headers,
        )
        assert left.status_code == 200

        with SessionLocal() as database:
            assert (
                database.query(CommunityMember)
                .filter_by(user_id=user_id, community_id=community_id)
                .count()
                == 0
            )

        assert client.get(f"/api/communities/{community_id}/members").json() == []
        left_again = client.post(
            f"/api/communities/{community_id}/leave",
            headers=headers,
        )
        assert left_again.status_code == 404

        nonexistent_join = client.post(
            "/api/communities/999999/join",
            headers=headers,
        )
        assert nonexistent_join.status_code == 404
        nonexistent_leave = client.post(
            "/api/communities/999999/leave",
            headers=headers,
        )
        assert nonexistent_leave.status_code == 404
        nonexistent_members = client.get("/api/communities/999999/members")
        assert nonexistent_members.status_code == 404