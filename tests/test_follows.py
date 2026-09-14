from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Notification, UserFollow


def register_and_login(client: TestClient, suffix: str) -> tuple[int, str, str]:
    username = f"follow_user_{suffix}"
    email = f"{username}@example.com"
    password = "follow password"
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


def test_follow_unfollow_lists_counts_notifications_and_blocking() -> None:
    suffix = uuid4().hex

    with TestClient(app) as client:
        follower_id, follower_username, follower_token = register_and_login(
            client, f"follower_{suffix}"
        )
        following_id, following_username, following_token = register_and_login(
            client, f"following_{suffix}"
        )
        other_id, _, other_token = register_and_login(client, f"other_{suffix}")
        follower_headers = {"Authorization": f"Bearer {follower_token}"}
        following_headers = {"Authorization": f"Bearer {following_token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}

        unauthenticated_follow = client.post(f"/api/users/{following_id}/follow")
        assert unauthenticated_follow.status_code == 401
        unauthenticated_unfollow = client.delete(f"/api/users/{following_id}/follow")
        assert unauthenticated_unfollow.status_code == 401
        unauthenticated_followers = client.get(f"/api/users/{following_id}/followers")
        assert unauthenticated_followers.status_code == 200

        self_follow = client.post(f"/api/users/{follower_id}/follow", headers=follower_headers)
        assert self_follow.status_code == 400
        missing = client.post("/api/users/999999/follow", headers=follower_headers)
        assert missing.status_code == 404

        followed = client.post(
            f"/api/users/{following_id}/follow",
            headers=follower_headers,
            json={"follower_id": 999999},
        )
        assert followed.status_code == 201, followed.text
        assert followed.json()["id"] == following_id
        assert followed.json()["username"] == following_username
        duplicate = client.post(
            f"/api/users/{following_id}/follow", headers=follower_headers
        )
        assert duplicate.status_code == 409
        followed_other = client.post(
            f"/api/users/{other_id}/follow",
            headers=follower_headers,
        )
        assert followed_other.status_code == 201, followed_other.text

        with SessionLocal() as database:
            relationship = (
                database.query(UserFollow)
                .filter_by(follower_id=follower_id, following_id=following_id)
                .one()
            )
            assert relationship.follower_id == follower_id
            notifications = (
                database.query(Notification)
                .filter_by(recipient_id=following_id, actor_id=follower_id, type="follow")
                .all()
            )
            assert len(notifications) == 1
            assert notifications[0].post_id is None

        profile = client.get(f"/api/users/{following_id}")
        assert profile.status_code == 200, profile.text
        assert profile.json()["follower_count"] == 1
        assert profile.json()["following_count"] == 0

        followers = client.get(
            f"/api/users/{following_id}/followers",
            params={"limit": 1},
        )
        assert followers.status_code == 200, followers.text
        assert followers.json()[0]["id"] == follower_id
        assert followers.json()[0]["username"] == follower_username
        following = client.get(f"/api/users/{follower_id}/following")
        assert following.status_code == 200, following.text
        assert following.json()[0]["id"] == other_id
        following_offset = client.get(
            f"/api/users/{follower_id}/following",
            params={"limit": 1, "offset": 1},
        )
        assert following_offset.status_code == 200, following_offset.text
        assert following_offset.json()[0]["id"] == following_id

        unfollowed = client.delete(
            f"/api/users/{following_id}/follow", headers=follower_headers
        )
        assert unfollowed.status_code == 200
        assert client.delete(
            f"/api/users/{following_id}/follow", headers=follower_headers
        ).status_code == 404
        assert client.get(f"/api/users/{following_id}/followers").json() == []
        assert client.get(f"/api/users/{follower_id}/following").json()[0]["id"] == other_id
        assert client.delete(
            f"/api/users/{other_id}/follow", headers=follower_headers
        ).status_code == 200
        assert client.get(f"/api/users/{follower_id}/following").json() == []
        with SessionLocal() as database:
            assert (
                database.query(Notification)
                .filter_by(recipient_id=following_id, actor_id=follower_id, type="follow")
                .count()
                == 1
            )
        following_notifications_before_block = client.get(
            "/api/notifications", headers=following_headers
        ).json()

        blocked = client.post(
            f"/api/users/{follower_id}/block", headers=following_headers
        )
        assert blocked.status_code == 201
        blocked_follow = client.post(
            f"/api/users/{following_id}/follow", headers=follower_headers
        )
        assert blocked_follow.status_code == 403
        reverse_blocked_follow = client.post(
            f"/api/users/{follower_id}/follow", headers=following_headers
        )
        assert reverse_blocked_follow.status_code == 403
        assert (
            client.get("/api/notifications", headers=following_headers).json()
            == following_notifications_before_block
        )
        assert other_id != follower_id
        assert other_headers["Authorization"].startswith("Bearer ")