from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Community, Post, User


def test_create_and_list_posts() -> None:
    suffix = uuid4().hex
    email = f"post_user_{suffix}@example.com"
    password = "post password"
    username = f"post_user_{suffix}"
    community_name = f"post_community_{suffix}"

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

        community_response = client.post(
            "/api/communities",
            headers=headers,
            json={"name": community_name, "description": "Posts live here"},
        )
        assert community_response.status_code == 201, community_response.text
        community_id = community_response.json()["id"]

        unauthenticated = client.post(
            "/api/posts",
            json={
                "title": "Unauthenticated",
                "content": "This must fail",
                "community_id": community_id,
            },
        )
        assert unauthenticated.status_code == 401

        nonexistent_community = client.post(
            "/api/posts",
            headers=headers,
            json={
                "title": "Missing community",
                "content": "This must fail",
                "community_id": 999999,
            },
        )
        assert nonexistent_community.status_code == 404

        created = client.post(
            "/api/posts",
            headers=headers,
            json={
                "title": "A real post",
                "content": "Post content",
                "community_id": community_id,
                "author_id": 999999,
            },
        )
        assert created.status_code == 201, created.text
        post_response = created.json()
        assert post_response["title"] == "A real post"
        assert post_response["community_id"] == community_id
        assert post_response["author_username"] == username
        assert post_response["community_name"] == community_name

        with SessionLocal() as database:
            user = database.query(User).filter_by(email=email).one()
            community = database.query(Community).filter_by(id=community_id).one()
            stored = database.query(Post).filter_by(id=post_response["id"]).one()
            assert stored.content == "Post content"
            assert stored.author_id == user.id
            assert stored.author_id != 999999
            assert stored.community_id == community.id

        listed = client.get(f"/api/communities/{community_id}/posts")
        assert listed.status_code == 200, listed.text
        assert any(post["id"] == post_response["id"] for post in listed.json())