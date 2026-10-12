import uuid

import pytest
from argon2 import PasswordHasher

from app import create_app
from app.db import connection, now


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    DB_PATH = ":memory:"
    HOSPITAL_DATABASE_URL = "sqlite:///:memory:"
    CENTRAL_API_BASE_URL = "https://central.test"
    HOSPITAL_CREDENTIAL_KEY_ID = "test-key"
    HOSPITAL_CREDENTIAL_SECRET = "test-secret"
    COOKIE_SECURE = False
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024
    MAX_IMAGE_PIXELS = 12000000
    ALLOWED_ORIGINS = ["http://localhost:5173"]
    RECOGNITION_MODEL_PATH = ""
    RECOGNITION_MODEL_VERSION = "none"
    SESSION_TTL_SECONDS = 3600


@pytest.fixture
def app():
    a = create_app(TestConfig)
    with a.app_context():
        with connection() as c:
            c.execute(
                "INSERT INTO hospital_users (id,email,password_hash,role,is_active,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    "operator@test.invalid",
                    PasswordHasher().hash("correct horse battery staple"),
                    "operator",
                    1,
                    now(),
                    now(),
                ),
            )
    return a


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in(client):
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": "operator@test.invalid",
            "password": "correct horse battery staple",
        },
    )
    assert r.status_code == 200
    return client, r.get_json()["data"]["csrf_token"]
