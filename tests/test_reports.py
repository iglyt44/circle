from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Comment, Post, Report


def register_and_login(client: TestClient, suffix: str) -> tuple[int, str, str]:
    username = f"report_user_{suffix}"
    email = f"{username}@example.com"
    password = "report password"
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


def test_reports_validate_targets_ownership_and_private_history() -> None:
    suffix = uuid4().hex

    with TestClient(app) as client:
        author_id, _, author_token = register_and_login(client, f"author_{suffix}")
        reporter_id, reporter_username, reporter_token = register_and_login(
            client, f"reporter_{suffix}"
        )
        _, _, other_token = register_and_login(client, f"other_{suffix}")
        author_headers = {"Authorization": f"Bearer {author_token}"}
        reporter_headers = {"Authorization": f"Bearer {reporter_token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}

        community = client.post(
            "/api/communities",
            headers=author_headers,
            json={
                "name": f"report_community_{suffix}",
                "description": "Reports",
            },
        )
        assert community.status_code == 201, community.text
        post = client.post(
            "/api/posts",
            headers=author_headers,
            json={
                "title": f"Reportable post {suffix}",
                "content": "Reportable content",
                "community_id": community.json()["id"],
            },
        )
        assert post.status_code == 201, post.text
        post_id = post.json()["id"]
        comment = client.post(
            f"/api/posts/{post_id}/comments",
            headers=author_headers,
            json={"content": "Reportable comment"},
        )
        assert comment.status_code == 201, comment.text
        comment_id = comment.json()["id"]

        unauthenticated = client.post(
            "/api/reports",
            json={"post_id": post_id, "reason": "spam"},
        )
        assert unauthenticated.status_code == 401
        neither = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"reason": "spam"},
        )
        assert neither.status_code == 422
        both = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={
                "post_id": post_id,
                "comment_id": comment_id,
                "reason": "spam",
            },
        )
        assert both.status_code == 422
        invalid_reason = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"post_id": post_id, "reason": "invalid"},
        )
        assert invalid_reason.status_code == 422

        report_post = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={
                "post_id": post_id,
                "reason": "spam",
                "reporter_id": 999999,
                "status": "actioned",
            },
        )
        assert report_post.status_code == 201, report_post.text
        post_report = report_post.json()
        assert post_report["target_type"] == "post"
        assert post_report["status"] == "pending"

        report_comment = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"comment_id": comment_id, "reason": "harassment"},
        )
        assert report_comment.status_code == 201, report_comment.text
        comment_report = report_comment.json()
        assert comment_report["target_type"] == "comment"

        with SessionLocal() as database:
            reports = database.query(Report).filter_by(reporter_id=reporter_id).all()
            assert len(reports) == 2
            assert all(report.status == "pending" for report in reports)
            assert all(report.reporter_id == reporter_id for report in reports)

        duplicate_post = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"post_id": post_id, "reason": "other"},
        )
        assert duplicate_post.status_code == 409
        duplicate_comment = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"comment_id": comment_id, "reason": "other"},
        )
        assert duplicate_comment.status_code == 409

        mine = client.get("/api/reports/mine", headers=reporter_headers)
        assert mine.status_code == 200, mine.text
        mine_items = mine.json()
        assert [item["id"] for item in mine_items] == [
            comment_report["id"],
            post_report["id"],
        ]
        assert all("reporter_id" not in item for item in mine_items)
        assert mine_items[0]["post_id"] is None
        assert mine_items[0]["comment_id"] == comment_id
        assert mine_items[0]["reason"] == "harassment"

        limited = client.get(
            "/api/reports/mine",
            headers=reporter_headers,
            params={"limit": 1},
        )
        assert limited.status_code == 200
        assert len(limited.json()) == 1
        offset = client.get(
            "/api/reports/mine",
            headers=reporter_headers,
            params={"limit": 1, "offset": 1},
        )
        assert offset.status_code == 200
        assert offset.json()[0]["id"] == post_report["id"]
        assert client.get("/api/reports/mine", headers=other_headers).json() == []

        own_post = client.post(
            "/api/posts",
            headers=reporter_headers,
            json={
                "title": f"Own post {suffix}",
                "content": "Own content",
                "community_id": community.json()["id"],
            },
        )
        assert own_post.status_code == 201, own_post.text
        own_post_report = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"post_id": own_post.json()["id"], "reason": "spam"},
        )
        assert own_post_report.status_code == 403
        own_comment = client.post(
            f"/api/posts/{post_id}/comments",
            headers=reporter_headers,
            json={"content": "Own comment"},
        )
        assert own_comment.status_code == 201, own_comment.text
        own_comment_report = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"comment_id": own_comment.json()["id"], "reason": "spam"},
        )
        assert own_comment_report.status_code == 403

        deleted_post = client.post(
            "/api/posts",
            headers=author_headers,
            json={
                "title": f"Deleted report post {suffix}",
                "content": "Deleted",
                "community_id": community.json()["id"],
            },
        )
        assert deleted_post.status_code == 201
        deleted_post_id = deleted_post.json()["id"]
        assert client.delete(
            f"/api/posts/{deleted_post_id}", headers=author_headers
        ).status_code == 200
        deleted_post_report = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"post_id": deleted_post_id, "reason": "spam"},
        )
        assert deleted_post_report.status_code == 404

        deleted_comment = client.post(
            f"/api/posts/{post_id}/comments",
            headers=author_headers,
            json={"content": "Deleted comment"},
        )
        assert deleted_comment.status_code == 201
        deleted_comment_id = deleted_comment.json()["id"]
        assert client.delete(
            f"/api/comments/{deleted_comment_id}", headers=author_headers
        ).status_code == 200
        deleted_comment_report = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"comment_id": deleted_comment_id, "reason": "spam"},
        )
        assert deleted_comment_report.status_code == 404

        missing_post = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"post_id": 999999, "reason": "spam"},
        )
        assert missing_post.status_code == 404
        missing_comment = client.post(
            "/api/reports",
            headers=reporter_headers,
            json={"comment_id": 999999, "reason": "spam"},
        )
        assert missing_comment.status_code == 404

        with SessionLocal() as database:
            assert database.query(Post).filter_by(id=post_id).one().author_id == author_id
            assert database.query(Comment).filter_by(id=comment_id).one().post_id == post_id
            assert reporter_username.startswith("report_user_")