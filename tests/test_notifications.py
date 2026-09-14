from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Notification


def register_and_login(client: TestClient, suffix: str) -> tuple[int, str, str]:
    username = f"notification_user_{suffix}"
    email = f"{username}@example.com"
    password = "notification password"
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


def test_interaction_notifications_and_access_control() -> None:
    suffix = uuid4().hex

    with TestClient(app) as client:
        author_id, author_username, author_token = register_and_login(
            client, f"author_{suffix}"
        )
        actor_id, actor_username, actor_token = register_and_login(
            client, f"actor_{suffix}"
        )
        _, _, other_token = register_and_login(client, f"other_{suffix}")
        author_headers = {"Authorization": f"Bearer {author_token}"}
        actor_headers = {"Authorization": f"Bearer {actor_token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}

        community = client.post(
            "/api/communities",
            headers=author_headers,
            json={
                "name": f"notification_community_{suffix}",
                "description": "Notifications",
            },
        )
        assert community.status_code == 201, community.text
        post = client.post(
            "/api/posts",
            headers=author_headers,
            json={
                "title": "Notification post",
                "content": "Post interactions",
                "community_id": community.json()["id"],
            },
        )
        assert post.status_code == 201, post.text
        post_id = post.json()["id"]

        own_comment = client.post(
            f"/api/posts/{post_id}/comments",
            headers=author_headers,
            json={"content": "Author comment"},
        )
        assert own_comment.status_code == 201, own_comment.text
        own_vote = client.post(
            f"/api/posts/{post_id}/vote",
            headers=author_headers,
            json={"value": 1},
        )
        assert own_vote.status_code == 200, own_vote.text
        assert client.get("/api/notifications", headers=author_headers).json() == []

        comment = client.post(
            f"/api/posts/{post_id}/comments",
            headers=actor_headers,
            json={"content": "Actor comment"},
        )
        assert comment.status_code == 201, comment.text
        vote = client.post(
            f"/api/posts/{post_id}/vote",
            headers=actor_headers,
            json={"value": 1},
        )
        assert vote.status_code == 200, vote.text

        actor_notifications = client.get("/api/notifications", headers=actor_headers)
        assert actor_notifications.status_code == 200
        assert actor_notifications.json() == []

        with SessionLocal() as database:
            notifications = (
                database.query(Notification)
                .filter_by(recipient_id=author_id)
                .order_by(Notification.id)
                .all()
            )
            assert len(notifications) == 2
            assert all(notification.actor_id == actor_id for notification in notifications)
            assert all(notification.post_id == post_id for notification in notifications)

        listed = client.get("/api/notifications", headers=author_headers)
        assert listed.status_code == 200, listed.text
        notification_items = listed.json()
        assert len(notification_items) == 2
        assert notification_items[0]["type"] == "vote"
        assert notification_items[1]["type"] == "comment"
        assert notification_items[0]["actor_username"] == actor_username
        assert "recipient_id" not in notification_items[0]
        assert notification_items[0]["is_read"] is False

        limited = client.get(
            "/api/notifications",
            headers=author_headers,
            params={"limit": 1},
        )
        assert limited.status_code == 200
        assert len(limited.json()) == 1
        offset = client.get(
            "/api/notifications",
            headers=author_headers,
            params={"limit": 1, "offset": 1},
        )
        assert offset.status_code == 200
        assert offset.json()[0]["type"] == "comment"

        notification_id = notification_items[0]["id"]
        marked = client.post(
            f"/api/notifications/{notification_id}/read",
            headers=author_headers,
        )
        assert marked.status_code == 200, marked.text
        refreshed = client.get("/api/notifications", headers=author_headers).json()
        assert refreshed[0]["is_read"] is True

        another_user = client.post(
            f"/api/notifications/{notification_items[1]['id']}/read",
            headers=other_headers,
        )
        assert another_user.status_code == 404
        nonexistent = client.post(
            "/api/notifications/999999/read",
            headers=author_headers,
        )
        assert nonexistent.status_code == 404

        unauthenticated_list = client.get("/api/notifications")
        assert unauthenticated_list.status_code == 401
        unauthenticated_read = client.post(
            f"/api/notifications/{notification_id}/read",
        )
        assert unauthenticated_read.status_code == 401

        repeated_vote = client.post(
            f"/api/posts/{post_id}/vote",
            headers=actor_headers,
            json={"value": 1},
        )
        assert repeated_vote.status_code == 200
        assert len(client.get("/api/notifications", headers=author_headers).json()) == 2
        assert author_username != actor_username