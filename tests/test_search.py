from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def register_and_login(client: TestClient, suffix: str) -> tuple[str, str]:
    username = f"search_user_{suffix}"
    email = f"{username}@example.com"
    password = "search password"
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


def test_search_types_case_insensitive_pagination_and_validation() -> None:
    suffix = uuid4().hex

    with TestClient(app) as client:
        username, token = register_and_login(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}
        community = client.post(
            "/api/communities",
            headers=headers,
            json={
                "name": f"SearchPlace_{suffix}",
                "description": f"A searchable description {suffix}",
            },
        )
        assert community.status_code == 201, community.text
        community_id = community.json()["id"]

        post_ids = []
        for index in range(3):
            post = client.post(
                "/api/posts",
                headers=headers,
                json={
                    "title": f"SearchTitle_{suffix}_{index}",
                    "content": (
                        f"Unique searchable content {suffix}"
                        if index == 0
                        else f"Other content {index}"
                    ),
                    "community_id": community_id,
                },
            )
            assert post.status_code == 201, post.text
            post_ids.append(post.json()["id"])

        vote = client.post(
            f"/api/posts/{post_ids[0]}/vote",
            headers=headers,
            json={"value": 1},
        )
        assert vote.status_code == 200, vote.text
        comment = client.post(
            f"/api/posts/{post_ids[0]}/comments",
            headers=headers,
            json={"content": "Search comment"},
        )
        assert comment.status_code == 201, comment.text

        post_search = client.get(
            "/api/search",
            params={"q": f"searchtitle_{suffix}_0", "type": "posts"},
        )
        assert post_search.status_code == 200, post_search.text
        post_result = post_search.json()
        assert len(post_result) == 1
        assert post_result[0]["id"] == post_ids[0]
        assert post_result[0]["author_username"] == username
        assert post_result[0]["community_name"] == f"SearchPlace_{suffix}"
        assert post_result[0]["score"] == 1
        assert post_result[0]["comment_count"] == 1

        content_search = client.get(
            "/api/search",
            params={"q": f"UNIQUE SEARCHABLE CONTENT {suffix}", "type": "posts"},
        )
        assert content_search.status_code == 200, content_search.text
        assert [item["id"] for item in content_search.json()] == [post_ids[0]]

        community_search = client.get(
            "/api/search",
            params={"q": f"searchplace_{suffix}", "type": "communities"},
        )
        assert community_search.status_code == 200, community_search.text
        assert community_search.json()[0]["id"] == community_id
        assert community_search.json()[0]["creator_username"] == username

        user_search = client.get(
            "/api/search",
            params={"q": username.upper(), "type": "users"},
        )
        assert user_search.status_code == 200, user_search.text
        assert user_search.json()[0]["username"] == username

        all_search = client.get(
            "/api/search",
            params={"q": suffix, "type": "all"},
        )
        assert all_search.status_code == 200, all_search.text
        all_result = all_search.json()
        assert set(all_result) == {"posts", "communities", "users"}
        assert any(item["id"] == post_ids[0] for item in all_result["posts"])
        assert any(item["id"] == community_id for item in all_result["communities"])
        assert any(item["username"] == username for item in all_result["users"])

        limited = client.get(
            "/api/search",
            params={"q": suffix, "type": "posts", "limit": 2},
        )
        assert limited.status_code == 200, limited.text
        assert len(limited.json()) == 2
        offset = client.get(
            "/api/search",
            params={"q": suffix, "type": "posts", "limit": 1, "offset": 1},
        )
        assert offset.status_code == 200, offset.text
        assert len(offset.json()) == 1
        assert offset.json()[0]["id"] == post_ids[1]

        no_match = client.get(
            "/api/search",
            params={"q": f"no-match-{suffix}", "type": "all"},
        )
        assert no_match.status_code == 200, no_match.text
        assert no_match.json() == {"posts": [], "communities": [], "users": []}

        empty = client.get("/api/search", params={"q": "   "})
        assert empty.status_code == 422
        missing = client.get("/api/search")
        assert missing.status_code == 422
        invalid_type = client.get(
            "/api/search", params={"q": "anything", "type": "comments"}
        )
        assert invalid_type.status_code == 422