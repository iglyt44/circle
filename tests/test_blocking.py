from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Comment, Post, User, UserBlock, Vote


def register_and_login(client: TestClient, suffix: str) -> tuple[int, str, str]:
    username = f"block_user_{suffix}"
    email = f"{username}@example.com"
    password = "block password"
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


def test_user_blocking_and_interaction_restrictions() -> None:
    suffix = uuid4().hex

    with TestClient(app) as client:
        blocker_id, blocker_username, blocker_token = register_and_login(
            client, f"blocker_{suffix}"
        )
        blocked_id, blocked_username, blocked_token = register_and_login(
            client, f"blocked_{suffix}"
        )
        second_blocked_id, _, second_blocked_token = register_and_login(
            client, f"second_{suffix}"
        )
        blocker_headers = {"Authorization": f"Bearer {blocker_token}"}
        blocked_headers = {"Authorization": f"Bearer {blocked_token}"}
        second_blocked_headers = {"Authorization": f"Bearer {second_blocked_token}"}

        community = client.post(
            "/api/communities",
            headers=blocker_headers,
            json={"name": f"block_community_{suffix}", "description": "Blocking"},
        )
        assert community.status_code == 201, community.text
        post = client.post(
            "/api/posts",
            headers=blocker_headers,
            json={
                "title": "Blocker post",
                "content": "Existing interactions stay",
                "community_id": community.json()["id"],
            },
        )
        assert post.status_code == 201, post.text
        post_id = post.json()["id"]

        existing_comment = client.post(
            f"/api/posts/{post_id}/comments",
            headers=blocked_headers,
            json={"content": "Existing comment"},
        )
        assert existing_comment.status_code == 201, existing_comment.text
        existing_comment_id = existing_comment.json()["id"]
        existing_vote = client.post(
            f"/api/posts/{post_id}/vote",
            headers=blocked_headers,
            json={"value": 1},
        )
        assert existing_vote.status_code == 200, existing_vote.text
        notifications_before = client.get(
            "/api/notifications", headers=blocker_headers
        ).json()

        unauthenticated_block = client.post(
            f"/api/users/{blocked_id}/block",
            json={"blocker_id": blocker_id},
        )
        assert unauthenticated_block.status_code == 401
        unauthenticated_unblock = client.delete(f"/api/users/{blocked_id}/block")
        assert unauthenticated_unblock.status_code == 401
        unauthenticated_list = client.get("/api/users/blocked")
        assert unauthenticated_list.status_code == 401

        self_block = client.post(
            f"/api/users/{blocker_id}/block",
            headers=blocker_headers,
        )
        assert self_block.status_code == 400
        missing_block = client.post(
            "/api/users/999999/block",
            headers=blocker_headers,
        )
        assert missing_block.status_code == 404

        blocked = client.post(
            f"/api/users/{blocked_id}/block",
            headers=blocker_headers,
            json={"blocker_id": 999999},
        )
        assert blocked.status_code == 201, blocked.text
        assert blocked.json()["id"] == blocked_id
        assert blocked.json()["username"] == blocked_username
        duplicate = client.post(
            f"/api/users/{blocked_id}/block",
            headers=blocker_headers,
        )
        assert duplicate.status_code == 409

        second_blocked = client.post(
            f"/api/users/{second_blocked_id}/block",
            headers=blocker_headers,
        )
        assert second_blocked.status_code == 201
        blocked_list = client.get(
            "/api/users/blocked",
            headers=blocker_headers,
            params={"limit": 1},
        )
        assert blocked_list.status_code == 200, blocked_list.text
        assert len(blocked_list.json()) == 1
        assert blocked_list.json()[0]["id"] == second_blocked_id
        blocked_offset = client.get(
            "/api/users/blocked",
            headers=blocker_headers,
            params={"limit": 1, "offset": 1},
        )
        assert blocked_offset.status_code == 200
        assert blocked_offset.json()[0]["id"] == blocked_id
        assert blocked_offset.json()[0]["username"] == blocked_username

        blocked_comment = client.post(
            f"/api/posts/{post_id}/comments",
            headers=blocked_headers,
            json={"content": "Rejected comment"},
        )
        assert blocked_comment.status_code == 403
        blocked_vote = client.post(
            f"/api/posts/{post_id}/vote",
            headers=blocked_headers,
            json={"value": 1},
        )
        assert blocked_vote.status_code == 403
        assert client.get("/api/notifications", headers=blocker_headers).json() == notifications_before

        with SessionLocal() as database:
            relationship = (
                database.query(UserBlock)
                .filter_by(blocker_id=blocker_id, blocked_id=blocked_id)
                .one()
            )
            assert relationship.blocker_id == blocker_id
            assert database.query(Post).filter_by(id=post_id).one().id == post_id
            assert database.query(Comment).filter_by(id=existing_comment_id).one().id == existing_comment_id
            assert (
                database.query(Vote)
                .filter_by(user_id=blocked_id, post_id=post_id)
                .one()
                .value
                == 1
            )

        unblocked = client.delete(
            f"/api/users/{blocked_id}/block",
            headers=blocker_headers,
        )
        assert unblocked.status_code == 200
        with SessionLocal() as database:
            assert (
                database.query(UserBlock)
                .filter_by(blocker_id=blocker_id, blocked_id=blocked_id)
                .count()
                == 0
            )
        unblock_again = client.delete(
            f"/api/users/{blocked_id}/block",
            headers=blocker_headers,
        )
        assert unblock_again.status_code == 404
        assert blocker_username.startswith("block_user_")
        assert second_blocked_headers["Authorization"].startswith("Bearer ")