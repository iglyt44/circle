from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Post, User, Vote


def register_and_login(client: TestClient, suffix: str) -> tuple[str, str]:
    email = f"vote_user_{suffix}@example.com"
    password = "vote password"
    username = f"vote_user_{suffix}"
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
    return username, login.json()["access_token"]


def test_post_voting_toggle_counts_and_authentication() -> None:
    suffix = uuid4().hex

    with TestClient(app) as client:
        username, token = register_and_login(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}

        community = client.post(
            "/api/communities",
            headers=headers,
            json={"name": f"vote_community_{suffix}", "description": "Voting"},
        )
        assert community.status_code == 201, community.text
        post = client.post(
            "/api/posts",
            headers=headers,
            json={
                "title": "Vote on this",
                "content": "Vote content",
                "community_id": community.json()["id"],
            },
        )
        assert post.status_code == 201, post.text
        post_id = post.json()["id"]

        unauthenticated = client.post(
            f"/api/posts/{post_id}/vote",
            json={"value": 1},
        )
        assert unauthenticated.status_code == 401

        nonexistent_post = client.post(
            "/api/posts/999999/vote",
            headers=headers,
            json={"value": 1},
        )
        assert nonexistent_post.status_code == 404

        invalid_value = client.post(
            f"/api/posts/{post_id}/vote",
            headers=headers,
            json={"value": 0, "user_id": 999999},
        )
        assert invalid_value.status_code == 422

        upvote = client.post(
            f"/api/posts/{post_id}/vote",
            headers=headers,
            json={"value": 1, "user_id": 999999},
        )
        assert upvote.status_code == 200, upvote.text
        assert upvote.json() == {"upvotes": 1, "downvotes": 0, "score": 1}

        with SessionLocal() as database:
            user = database.query(User).filter_by(username=username).one()
            stored_post = database.query(Post).filter_by(id=post_id).one()
            votes = database.query(Vote).filter_by(post_id=stored_post.id).all()
            assert len(votes) == 1
            assert votes[0].user_id == user.id
            assert votes[0].user_id != 999999

        changed_to_downvote = client.post(
            f"/api/posts/{post_id}/vote",
            headers=headers,
            json={"value": -1},
        )
        assert changed_to_downvote.status_code == 200
        assert changed_to_downvote.json() == {
            "upvotes": 0,
            "downvotes": 1,
            "score": -1,
        }

        toggled_off = client.post(
            f"/api/posts/{post_id}/vote",
            headers=headers,
            json={"value": -1},
        )
        assert toggled_off.status_code == 200
        assert toggled_off.json() == {"upvotes": 0, "downvotes": 0, "score": 0}

        second_username, second_token = register_and_login(client, f"second_{suffix}")
        second_headers = {"Authorization": f"Bearer {second_token}"}
        second_downvote = client.post(
            f"/api/posts/{post_id}/vote",
            headers=second_headers,
            json={"value": -1},
        )
        assert second_downvote.status_code == 200
        assert second_downvote.json() == {
            "upvotes": 0,
            "downvotes": 1,
            "score": -1,
        }
        assert second_username != username

        summary = client.get(f"/api/posts/{post_id}/votes")
        assert summary.status_code == 200, summary.text
        assert summary.json() == {"upvotes": 0, "downvotes": 1, "score": -1}

        with SessionLocal() as database:
            assert database.query(Vote).filter_by(post_id=post_id).count() == 1