from uuid import uuid4

import jwt
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import JWT_ALGORITHM, JWT_SECRET_KEY, app
from app.models import User


def test_registration_and_login() -> None:
    suffix = uuid4().hex
    username = f"auth_user_{suffix}"
    email = f"{suffix}@example.com"
    password = "correct horse battery staple"

    with TestClient(app) as client:
        registration = client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )
        assert registration.status_code == 201, registration.text
        response_data = registration.json()
        assert response_data["username"] == username
        assert "password" not in response_data
        assert "password_hash" not in response_data
        assert "JWT_SECRET_KEY" not in response_data
        assert "OPENAI_API_KEY" not in response_data

        with SessionLocal() as database:
            user = database.query(User).filter_by(username=username).one()
            assert user.password_hash is not None
            assert user.password_hash != password

        login = client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200, login.text
        token_data = login.json()
        assert token_data["token_type"] == "bearer"
        assert jwt.decode(
            token_data["access_token"], JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )["email"] == email

        wrong_password = client.post(
            "/api/auth/login",
            json={"email": email, "password": "wrong password"},
        )
        assert wrong_password.status_code == 401

        duplicate = client.post(
            "/api/auth/register",
            json={
                "username": username,
                "email": f"other_{suffix}@example.com",
                "password": password,
            },
        )
        assert duplicate.status_code == 409

        duplicate_email = client.post(
            "/api/auth/register",
            json={
                "username": f"other_{suffix}",
                "email": email,
                "password": password,
            },
        )
        assert duplicate_email.status_code == 409