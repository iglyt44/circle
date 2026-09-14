from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Comment, Post, User


def register_and_login(client: TestClient, suffix: str) -> tuple[int, str]:
    username = f"admin_test_user_{suffix}"
    email = f"{username}@example.com"
    password = "admin test password"
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


def test_admin_reports_review_and_moderation() -> None:
    suffix = uuid4().hex

    with TestClient(app) as client:
        author_id, author_token = register_and_login(client, f"author_{suffix}")
        reporter_id, reporter_token = register_and_login(client, f"reporter_{suffix}")
        admin_id, admin_token = register_and_login(client, f"admin_{suffix}")
        author_headers = {"Authorization": f"Bearer {author_token}"}
        reporter_headers = {"Authorization": f"Bearer {reporter_token}"}
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        with SessionLocal() as database:
            admin = database.get(User, admin_id)
            assert admin is not None
            admin.is_admin = True
            database.commit()
            assert database.get(User, reporter_id).is_admin is False

        community = client.post(
            "/api/communities",
            headers=author_headers,
            json={"name": f"admin_community_{suffix}", "description": "Admin"},
        )
        assert community.status_code == 201, community.text
        post = client.post(
            "/api/posts",
            headers=author_headers,
            json={
                "title": f"Admin moderation post {suffix}",
                "content": "Admin content",
                "community_id": community.json()["id"],
            },
        )
        assert post.status_code == 201, post.text
        post_id = post.json()["id"]
        comment = client.post(
            f"/api/posts/{post_id}/comments",
            headers=author_headers,
            json={"content": "Admin moderation comment"},
        )
        assert comment.status_code == 201, comment.text
        comment_id = comment.json()["id"]

        report_post = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"post_id": post_id, "reason": "spam"},
        )
        assert report_post.status_code == 201, report_post.text
        report_comment = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"comment_id": comment_id, "reason": "harassment"},
        )
        assert report_comment.status_code == 201, report_comment.text
        post_report_id = report_post.json()["id"]
        comment_report_id = report_comment.json()["id"]

        unauthenticated = client.get("/api/admin/reports")
        assert unauthenticated.status_code == 401
        normal = client.get("/api/admin/reports", headers=reporter_headers)
        assert normal.status_code == 403
        forged_normal = client.request(
            "GET",
            "/api/admin/reports",
            headers=reporter_headers,
            json={"is_admin": True, "user_id": admin_id},
        )
        assert forged_normal.status_code == 403

        pending = client.get(
            "/api/admin/reports",
            headers=admin_headers,
            params={"status": "pending", "limit": 1},
        )
        assert pending.status_code == 200, pending.text
        assert len(pending.json()) == 1
        assert pending.json()[0]["reporter_username"].startswith("admin_test_user_")
        pending_offset = client.get(
            "/api/admin/reports",
            headers=admin_headers,
            params={"status": "pending", "limit": 1, "offset": 1},
        )
        assert pending_offset.status_code == 200
        assert pending_offset.json()[0]["id"] == post_report_id

        invalid_review = client.post(
            f"/api/admin/reports/{post_report_id}/review",
            headers=admin_headers,
            json={"status": "pending"},
        )
        assert invalid_review.status_code == 422
        normal_review = client.post(
            f"/api/admin/reports/{post_report_id}/review",
            headers=reporter_headers,
            json={"status": "reviewed"},
        )
        assert normal_review.status_code == 403
        reviewed = client.post(
            f"/api/admin/reports/{post_report_id}/review",
            headers=admin_headers,
            json={"status": "reviewed", "is_admin": False},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["status"] == "reviewed"
        reviewed_filter = client.get(
            "/api/admin/reports",
            headers=admin_headers,
            params={"status": "reviewed"},
        )
        assert reviewed_filter.status_code == 200
        assert reviewed_filter.json()[0]["id"] == post_report_id
        missing_review = client.post(
            "/api/admin/reports/999999/review",
            headers=admin_headers,
            json={"status": "dismissed"},
        )
        assert missing_review.status_code == 404
        assert reviewed.json()["id"] != comment_report_id

        unauthenticated_delete = client.delete(f"/api/admin/posts/{post_id}")
        assert unauthenticated_delete.status_code == 401
        normal_delete = client.request(
            "DELETE",
            f"/api/admin/posts/{post_id}",
            headers=reporter_headers,
            json={"is_admin": True, "user_id": admin_id},
        )
        assert normal_delete.status_code == 403
        admin_delete = client.delete(
            f"/api/admin/posts/{post_id}",
            headers=admin_headers,
        )
        assert admin_delete.status_code == 200, admin_delete.text
        already_deleted = client.delete(
            f"/api/admin/posts/{post_id}",
            headers=admin_headers,
        )
        assert already_deleted.status_code == 404

        admin_delete_comment = client.delete(
            f"/api/admin/comments/{comment_id}",
            headers=admin_headers,
        )
        assert admin_delete_comment.status_code == 200, admin_delete_comment.text
        already_deleted_comment = client.delete(
            f"/api/admin/comments/{comment_id}",
            headers=admin_headers,
        )
        assert already_deleted_comment.status_code == 404

        with SessionLocal() as database:
            stored_post = database.query(Post).filter_by(id=post_id).one()
            stored_comment = database.query(Comment).filter_by(id=comment_id).one()
            assert stored_post.is_deleted is True
            assert stored_post.deleted_at is not None
            assert stored_comment.is_deleted is True
            assert stored_comment.deleted_at is not None
            assert stored_post.author_id == author_id