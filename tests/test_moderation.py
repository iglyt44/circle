from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Comment, Post


def register_and_login(client: TestClient, suffix: str) -> tuple[str, str]:
    username = f"moderation_user_{suffix}"
    email = f"{username}@example.com"
    password = "moderation password"
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
    return registration.json()["id"], login.json()["access_token"]


def test_posts_and_comments_are_soft_deleted() -> None:
    suffix = uuid4().hex

    with TestClient(app) as client:
        author_id, author_token = register_and_login(client, f"author_{suffix}")
        _, other_token = register_and_login(client, f"other_{suffix}")
        author_headers = {"Authorization": f"Bearer {author_token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}
        community = client.post(
            "/api/communities",
            headers=author_headers,
            json={
                "name": f"moderation_community_{suffix}",
                "description": "Moderation",
            },
        )
        assert community.status_code == 201, community.text
        post = client.post(
            "/api/posts",
            headers=author_headers,
            json={
                "title": f"Moderation post {suffix}",
                "content": "This post will be removed softly",
                "community_id": community.json()["id"],
            },
        )
        assert post.status_code == 201, post.text
        post_id = post.json()["id"]

        comment = client.post(
            f"/api/posts/{post_id}/comments",
            headers=other_headers,
            json={"content": "This comment will be removed softly"},
        )
        assert comment.status_code == 201, comment.text
        comment_id = comment.json()["id"]

        unauthorized_post_delete = client.delete(f"/api/posts/{post_id}")
        assert unauthorized_post_delete.status_code == 401
        forbidden_post_delete = client.delete(
            f"/api/posts/{post_id}",
            headers=other_headers,
        )
        assert forbidden_post_delete.status_code == 403
        missing_post_delete = client.delete(
            "/api/posts/999999",
            headers=author_headers,
        )
        assert missing_post_delete.status_code == 404

        unauthorized_comment_delete = client.delete(f"/api/comments/{comment_id}")
        assert unauthorized_comment_delete.status_code == 401
        forbidden_comment_delete = client.delete(
            f"/api/comments/{comment_id}",
            headers=author_headers,
        )
        assert forbidden_comment_delete.status_code == 403
        missing_comment_delete = client.delete(
            "/api/comments/999999",
            headers=other_headers,
        )
        assert missing_comment_delete.status_code == 404

        deleted_comment = client.delete(
            f"/api/comments/{comment_id}",
            headers=other_headers,
        )
        assert deleted_comment.status_code == 200, deleted_comment.text
        comments = client.get(f"/api/posts/{post_id}/comments")
        assert comments.status_code == 200
        assert all(comment["id"] != comment_id for comment in comments.json())
        feed_before_post_delete = client.get("/api/feed").json()
        feed_item = next(item for item in feed_before_post_delete if item["id"] == post_id)
        assert feed_item["comment_count"] == 0

        deleted_post = client.delete(
            f"/api/posts/{post_id}",
            headers=author_headers,
        )
        assert deleted_post.status_code == 200, deleted_post.text

        with SessionLocal() as database:
            stored_post = database.query(Post).filter_by(id=post_id).one()
            stored_comment = database.query(Comment).filter_by(id=comment_id).one()
            assert stored_post.is_deleted is True
            assert stored_post.deleted_at is not None
            assert stored_comment.is_deleted is True
            assert stored_comment.deleted_at is not None
            assert stored_post.author_id == author_id

        assert all(item["id"] != post_id for item in client.get("/api/feed").json())
        profile_posts = client.get(f"/api/users/{author_id}/posts")
        assert profile_posts.status_code == 200
        assert all(item["id"] != post_id for item in profile_posts.json())
        search = client.get(
            "/api/search",
            params={"q": f"Moderation post {suffix}", "type": "posts"},
        )
        assert search.status_code == 200
        assert all(item["id"] != post_id for item in search.json())