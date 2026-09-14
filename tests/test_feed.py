from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def register_and_login(client: TestClient, suffix: str) -> str:
    email = f"feed_user_{suffix}@example.com"
    password = "feed password"
    registration = client.post(
        "/api/auth/register",
        json={
            "username": f"feed_user_{suffix}",
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
    return login.json()["access_token"]


def create_post(client: TestClient, headers: dict[str, str], community_id: int, title: str) -> int:
    response = client.post(
        "/api/posts",
        headers=headers,
        json={
            "title": title,
            "content": f"Content for {title}",
            "community_id": community_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_feed_returns_ordered_paginated_posts_with_counts() -> None:
    suffix = uuid4().hex

    with TestClient(app) as client:
        token = register_and_login(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}
        community = client.post(
            "/api/communities",
            headers=headers,
            json={
                "name": f"feed_community_{suffix}",
                "description": "Feed community",
            },
        )
        assert community.status_code == 201, community.text
        community_id = community.json()["id"]

        post_ids = [
            create_post(client, headers, community_id, f"Feed post {suffix} {index}")
            for index in range(3)
        ]

        upvote = client.post(
            f"/api/posts/{post_ids[0]}/vote",
            headers=headers,
            json={"value": 1},
        )
        assert upvote.status_code == 200, upvote.text
        comment_one = client.post(
            f"/api/posts/{post_ids[0]}/comments",
            headers=headers,
            json={"content": "First feed comment"},
        )
        assert comment_one.status_code == 201, comment_one.text
        comment_two = client.post(
            f"/api/posts/{post_ids[0]}/comments",
            headers=headers,
            json={"content": "Second feed comment"},
        )
        assert comment_two.status_code == 201, comment_two.text

        feed_response = client.get("/api/feed")
        assert feed_response.status_code == 200, feed_response.text
        feed = feed_response.json()
        feed_by_id = {item["id"]: item for item in feed}
        assert post_ids[0] in feed_by_id
        item = feed_by_id[post_ids[0]]
        assert item["title"] == f"Feed post {suffix} 0"
        assert item["content"] == f"Content for Feed post {suffix} 0"
        assert item["author_username"] == f"feed_user_{suffix}"
        assert item["community_name"] == f"feed_community_{suffix}"
        assert item["upvotes"] == 1
        assert item["downvotes"] == 0
        assert item["score"] == 1
        assert item["comment_count"] == 2

        created_feed_ids = [item["id"] for item in feed if item["id"] in post_ids]
        assert created_feed_ids == list(reversed(post_ids))

        limited = client.get("/api/feed", params={"limit": 2})
        assert limited.status_code == 200, limited.text
        assert [item["id"] for item in limited.json()] == [
            item["id"] for item in feed[:2]
        ]

        offset = client.get("/api/feed", params={"limit": 2, "offset": 1})
        assert offset.status_code == 200, offset.text
        assert [item["id"] for item in offset.json()] == [
            item["id"] for item in feed[1:3]
        ]

        empty_page = client.get("/api/feed", params={"offset": 100000})
        assert empty_page.status_code == 200
        assert empty_page.json() == []