from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Comment, Community, Post, User


def test_create_and_list_comments() -> None:
    suffix = uuid4().hex
    email = f"comment_user_{suffix}@example.com"
    password = "comment password"
    username = f"comment_user_{suffix}"

    with TestClient(app) as client:
        registration = client.post(
            "/api/auth/register",
            json={
                "username": username,
                "email": email,
                "password": password,
            },
        )
        assert registration.status_code == 201, registration.text

        login = client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        community = client.post(
            "/api/communities",
            headers=headers,
            json={"name": f"comment_community_{suffix}", "description": "Comments"},
        )
        assert community.status_code == 201, community.text
        community_id = community.json()["id"]

        post = client.post(
            "/api/posts",
            headers=headers,
            json={
                "title": "Commentable post",
                "content": "Post content",
                "community_id": community_id,
            },
        )
        assert post.status_code == 201, post.text
        post_id = post.json()["id"]

        unauthenticated = client.post(
            f"/api/posts/{post_id}/comments",
            json={"content": "This must fail"},
        )
        assert unauthenticated.status_code == 401

        nonexistent_post = client.post(
            "/api/posts/999999/comments",
            headers=headers,
            json={"content": "This must fail"},
        )
        assert nonexistent_post.status_code == 404

        created = client.post(
            f"/api/posts/{post_id}/comments",
            headers=headers,
            json={"content": "A real comment", "author_id": 999999},
        )
        assert created.status_code == 201, created.text
        comment_response = created.json()
        assert comment_response["content"] == "A real comment"
        assert comment_response["post_id"] == post_id
        assert comment_response["author_username"] == username

        with SessionLocal() as database:
            user = database.query(User).filter_by(email=email).one()
            stored_post = database.query(Post).filter_by(id=post_id).one()
            stored = database.query(Comment).filter_by(id=comment_response["id"]).one()
            assert stored.content == "A real comment"
            assert stored.author_id == user.id
            assert stored.author_id != 999999
            assert stored.post_id == stored_post.id

        listed = client.get(f"/api/posts/{post_id}/comments")
        assert listed.status_code == 200, listed.text
        assert any(
            comment["id"] == comment_response["id"] for comment in listed.json()
        )