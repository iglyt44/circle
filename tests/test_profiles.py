from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def register_and_login(client: TestClient, suffix: str) -> tuple[int, str, str]:
    username = f"profile_user_{suffix}"
    email = f"{username}@example.com"
    password = "profile password"
    registration = client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert registration.status_code == 201, registration.text
    user_id = registration.json()["id"]
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    return user_id, username, login.json()["access_token"]


def create_post(
    client: TestClient,
    headers: dict[str, str],
    community_id: int,
    title: str,
) -> int:
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


def test_public_user_profile_and_posts() -> None:
    suffix = uuid4().hex

    with TestClient(app) as client:
        user_id, username, token = register_and_login(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}
        community = client.post(
            "/api/communities",
            headers=headers,
            json={
                "name": f"profile_community_{suffix}",
                "description": "Profile posts",
            },
        )
        assert community.status_code == 201, community.text
        community_id = community.json()["id"]

        first_post_id = create_post(
            client, headers, community_id, f"Older profile post {suffix}"
        )
        second_post_id = create_post(
            client, headers, community_id, f"Newer profile post {suffix}"
        )

        voter_one_id, _, voter_one_token = register_and_login(client, f"voter1_{suffix}")
        voter_two_id, _, voter_two_token = register_and_login(client, f"voter2_{suffix}")
        assert voter_one_id != voter_two_id
        voter_one_headers = {"Authorization": f"Bearer {voter_one_token}"}
        voter_two_headers = {"Authorization": f"Bearer {voter_two_token}"}
        assert client.post(
            f"/api/posts/{first_post_id}/vote",
            headers=voter_one_headers,
            json={"value": 1},
        ).status_code == 200
        assert client.post(
            f"/api/posts/{first_post_id}/vote",
            headers=voter_two_headers,
            json={"value": 1},
        ).status_code == 200
        assert client.post(
            f"/api/posts/{second_post_id}/vote",
            headers=voter_one_headers,
            json={"value": -1},
        ).status_code == 200

        for content, post_id in (
            ("First profile comment", first_post_id),
            ("Second profile comment", second_post_id),
        ):
            comment = client.post(
                f"/api/posts/{post_id}/comments",
                headers=headers,
                json={"content": content},
            )
            assert comment.status_code == 201, comment.text
        other_comment = client.post(
            f"/api/posts/{first_post_id}/comments",
            headers=voter_one_headers,
            json={"content": "Another user's comment"},
        )
        assert other_comment.status_code == 201, other_comment.text

        profile = client.get(f"/api/users/{user_id}")
        assert profile.status_code == 200, profile.text
        assert profile.json()["id"] == user_id
        assert profile.json()["username"] == username
        assert profile.json()["post_count"] == 2
        assert profile.json()["comment_count"] == 2
        assert profile.json()["karma"] == 1

        posts = client.get(f"/api/users/{user_id}/posts")
        assert posts.status_code == 200, posts.text
        post_items = posts.json()
        assert [item["id"] for item in post_items[:2]] == [
            second_post_id,
            first_post_id,
        ]
        newest = post_items[0]
        assert newest["title"] == f"Newer profile post {suffix}"
        assert newest["community_name"] == f"profile_community_{suffix}"
        assert newest["upvotes"] == 0
        assert newest["downvotes"] == 1
        assert newest["score"] == -1
        assert newest["comment_count"] == 1
        older = post_items[1]
        assert older["upvotes"] == 2
        assert older["downvotes"] == 0
        assert older["score"] == 2
        assert older["comment_count"] == 2

        limited = client.get(f"/api/users/{user_id}/posts", params={"limit": 1})
        assert limited.status_code == 200, limited.text
        assert [item["id"] for item in limited.json()] == [second_post_id]
        offset = client.get(
            f"/api/users/{user_id}/posts",
            params={"limit": 1, "offset": 1},
        )
        assert offset.status_code == 200, offset.text
        assert [item["id"] for item in offset.json()] == [first_post_id]

        missing = client.get("/api/users/999999")
        assert missing.status_code == 404

        no_posts_id, _, _ = register_and_login(client, f"empty_{suffix}")
        empty = client.get(f"/api/users/{no_posts_id}/posts")
        assert empty.status_code == 200
        assert empty.json() == []