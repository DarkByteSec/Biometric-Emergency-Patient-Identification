import base64
import os

import pytest

os.environ.update(
    TESTING="true",
    CENTRAL_DATABASE_URL="sqlite:///:memory:",
    CENTRAL_ENCRYPTION_KEY=base64.urlsafe_b64encode(b"e" * 32).decode(),
    CENTRAL_LOOKUP_HMAC_KEY=base64.urlsafe_b64encode(b"h" * 32).decode(),
)
from app import create_app
from app.config import decode_key
from app.db import execute, now, uid
from app.services import hash_password


@pytest.fixture
def client(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}",
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(b"e" * 32).decode(),
            "LOOKUP_HMAC_KEY": base64.urlsafe_b64encode(b"h" * 32).decode(),
            "CORS_ORIGINS": ["http://localhost:5173"],
        }
    )
    with app.test_client() as c:
        with app.app_context():
            execute(
                "INSERT INTO admin_users VALUES(?,?,?,?,?,?,?,?)",
                (
                    uid(),
                    "admin@example.test",
                    hash_password("correct horse battery staple"),
                    "admin",
                    1,
                    now(),
                    now(),
                    None,
                ),
            )
        yield c


def login(c):
    r = c.post(
        "/api/v1/admin/login",
        json={
            "email": "admin@example.test",
            "password": "correct horse battery staple",
        },
    )
    assert r.status_code == 200
    return r.get_json()["data"]["csrf_token"]


def test_health_and_security_headers(client):
    r = client.get("/api/v1/health/live")
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_auth_and_csrf(client):
    assert client.get("/api/v1/admin/dashboard").status_code == 401
    csrf = login(client)
    assert (
        client.post("/api/v1/citizens", json={"national_id": "DEMO-1"}).status_code
        == 403
    )
    r = client.post(
        "/api/v1/citizens",
        headers={"X-CSRF-Token": csrf},
        json={
            "national_id": "DEMO-1",
            "first_name": "Ada",
            "last_name": "Synthetic",
            "blood_type": "O+",
        },
    )
    assert r.status_code == 201
    assert "DEMO-1" not in r.get_data(as_text=True)


def test_validation_and_role(client):
    csrf = login(client)
    r = client.post(
        "/api/v1/citizens",
        headers={"X-CSRF-Token": csrf},
        json={
            "national_id": "X",
            "first_name": "A",
            "last_name": "B",
            "gender": "invalid",
        },
    )
    assert r.status_code == 422
    assert client.get("/api/v1/access-events").status_code == 200


def test_production_keys_fail_closed():
    with pytest.raises(RuntimeError):
        create_app(
            {
                "APP_ENV": "production",
                "SECRET_KEY": None,
                "ENCRYPTION_KEY": None,
                "LOOKUP_HMAC_KEY": None,
            }
        )


def test_security_key_decoder_accepts_base64url_and_hex():
    raw = b"k" * 32
    assert decode_key(base64.urlsafe_b64encode(raw).decode()) == raw
    assert decode_key(raw.hex()) == raw


def test_csrf_cookie_must_match_header(client):
    token = login(client)
    client.set_cookie("central_csrf", "not-the-session-token")
    denied = client.post("/api/v1/admin/logout", headers={"X-CSRF-Token": token})
    assert denied.status_code == 403
    client.set_cookie("central_csrf", token)
    assert (
        client.post("/api/v1/admin/logout", headers={"X-CSRF-Token": token}).status_code
        == 200
    )


def test_hospital_lookup_scope_and_response_contract(client):
    csrf = login(client)
    citizen_response = client.post(
        "/api/v1/citizens",
        headers={"X-CSRF-Token": csrf},
        json={
            "national_id": "SYNTHETIC-LOOKUP-01",
            "first_name": "Synthetic",
            "last_name": "Patient",
            "blood_type": "O+",
        },
    )
    assert citizen_response.status_code == 201
    citizen_id = citizen_response.get_json()["data"]["id"]
    assert (
        client.post(
            f"/api/v1/citizens/{citizen_id}/allergies",
            headers={"X-CSRF-Token": csrf},
            json={
                "substance": "synthetic test substance",
                "reaction": "synthetic test reaction",
                "status": "active",
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/citizens/{citizen_id}/history",
            headers={"X-CSRF-Token": csrf},
            json={"category": "note", "summary": "synthetic test note"},
        ).status_code
        == 201
    )

    hospital = client.post(
        "/api/v1/hospitals",
        headers={"X-CSRF-Token": csrf},
        json={
            "code": "SYNTHETIC-01",
            "name": "Synthetic Test Hospital",
            "allowed_scopes": ["patient.lookup"],
        },
    )
    assert hospital.status_code == 201
    hospital_id = hospital.get_json()["data"]["id"]
    assert (
        client.post(
            f"/api/v1/hospitals/{hospital_id}/credentials",
            headers={"X-CSRF-Token": csrf},
            json={"scopes": [{"unexpected": "object"}]},
        ).status_code
        == 422
    )

    lookup_only_credential = client.post(
        f"/api/v1/hospitals/{hospital_id}/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"scopes": ["patient.lookup"]},
    )
    assert lookup_only_credential.status_code == 201
    bearer = lookup_only_credential.get_json()["data"]["credential"]
    result = client.post(
        "/api/v1/hospital/patient-lookups",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"national_id": "SYNTHETIC-LOOKUP-01"},
    )
    assert result.status_code == 200
    data = result.get_json()["data"]
    assert data["display_name"] == "Synthetic Patient"
    assert data["blood_type"] is None
    assert data["allergies"] is None
    assert data["limited_history"] is None
    assert "SYNTHETIC-LOOKUP-01" not in result.get_data(as_text=True)

    health_hospital = client.post(
        "/api/v1/hospitals",
        headers={"X-CSRF-Token": csrf},
        json={
            "code": "SYNTHETIC-02",
            "name": "Synthetic Health-Read Hospital",
            "allowed_scopes": ["patient.lookup", "health.read"],
        },
    )
    assert health_hospital.status_code == 201
    health_hospital_id = health_hospital.get_json()["data"]["id"]
    health_credential = client.post(
        f"/api/v1/hospitals/{health_hospital_id}/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"scopes": ["patient.lookup", "health.read"]},
    )
    assert health_credential.status_code == 201
    health_bearer = health_credential.get_json()["data"]["credential"]
    health_result = client.post(
        "/api/v1/hospital/patient-lookups",
        headers={"Authorization": f"Bearer {health_bearer}"},
        json={"citizen_id": citizen_id},
    )
    assert health_result.status_code == 200
    health_data = health_result.get_json()["data"]
    assert health_data["blood_type"] == "O+"
    assert health_data["allergies"][0]["name"] == "synthetic test substance"
    assert health_data["limited_history"][0]["label"] == "synthetic test note"

    scope_denied = client.post(
        "/api/v1/hospital/enrollments/verify",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"citizen_id": citizen_id},
    )
    assert scope_denied.status_code == 403

    enrollment_hospital = client.post(
        "/api/v1/hospitals",
        headers={"X-CSRF-Token": csrf},
        json={
            "code": "SYNTHETIC-03",
            "name": "Synthetic Enrollment Hospital",
            "allowed_scopes": ["patient.enroll"],
        },
    )
    assert enrollment_hospital.status_code == 201
    enrollment_hospital_id = enrollment_hospital.get_json()["data"]["id"]
    enrollment_credential = client.post(
        f"/api/v1/hospitals/{enrollment_hospital_id}/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"scopes": ["patient.enroll"]},
    )
    assert enrollment_credential.status_code == 201
    enrollment_bearer = enrollment_credential.get_json()["data"]["credential"]
    verification = client.post(
        "/api/v1/hospital/enrollments/verify",
        headers={"Authorization": f"Bearer {enrollment_bearer}"},
        json={"citizen_id": citizen_id},
    )
    assert verification.status_code == 200
    assert verification.get_json()["data"] == {
        "citizen_id": citizen_id,
        "enrollment_allowed": True,
    }
    missing = client.post(
        "/api/v1/hospital/enrollments/verify",
        headers={"Authorization": f"Bearer {enrollment_bearer}"},
        json={"citizen_id": "00000000-0000-0000-0000-000000000099"},
    )
    assert missing.status_code == 404
