from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Community, User


def test_create_and_list_communities_requires_authentication() -> None:
    suffix = uuid4().hex
    email = f"community_user_{suffix}@example.com"
    password = "community password"
    name = f"community_{suffix}"
    description = "A test community"

    with TestClient(app) as client:
        registration = client.post(
            "/api/auth/register",
            json={
                "username": f"community_user_{suffix}",
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
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        unauthenticated = client.post(
            "/api/communities",
            json={"name": name, "description": description},
        )
        assert unauthenticated.status_code == 401

        created = client.post(
            "/api/communities",
            headers=headers,
            json={
                "name": name,
                "description": description,
                "creator_id": 999999,
            },
        )
        assert created.status_code == 201, created.text
        community = created.json()
        assert community["name"] == name
        assert community["description"] == description

        with SessionLocal() as database:
            stored = database.query(Community).filter_by(name=name).one()
            user = database.query(User).filter_by(email=email).one()
            assert stored.description == description
            assert stored.creator_id == user.id

        listed = client.get("/api/communities")
        assert listed.status_code == 200, listed.text
        assert any(item["id"] == community["id"] for item in listed.json())

        duplicate = client.post(
            "/api/communities",
            headers=headers,
            json={"name": name, "description": "Another description"},
        )
        assert duplicate.status_code == 409, duplicate.text