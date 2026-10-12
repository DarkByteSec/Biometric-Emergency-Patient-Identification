import io
import uuid
from unittest.mock import Mock, patch

from PIL import Image

from app.db import connection, now
from app.services.central_client import CentralResult


def csrf_headers(token):
    return {"X-CSRF-Token": token}


def test_auth_and_csrf(client, logged_in):
    c, csrf = logged_in
    assert c.get("/api/v1/auth/session").status_code == 200
    assert c.post("/api/v1/auth/logout").status_code == 403
    assert c.post("/api/v1/auth/logout", headers=csrf_headers(csrf)).status_code == 200


def test_validation_and_no_persisted_probe(app, logged_in):
    c, csrf = logged_in
    assert (
        c.post(
            "/api/v1/patient-lookups",
            json={"national_id": "x"},
            headers=csrf_headers(csrf),
        ).status_code
        == 400
    )
    im = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(im, "JPEG")
    im.seek(0)
    r = c.post(
        "/api/v1/recognition/probes",
        data={"frame": (im, "probe.jpg")},
        content_type="multipart/form-data",
        headers=csrf_headers(csrf),
    )
    assert r.status_code == 200
    assert r.get_json()["data"]["outcome"] == "unavailable"
    with app.app_context(), connection() as db:
        assert (
            db.execute("SELECT COUNT(*) n FROM local_biometric_samples").fetchone()["n"]
            == 0
        )


@patch("app.api.client")
def test_central_success_not_found_denial_timeout_and_redaction(
    mock_client, app, logged_in
):
    c, csrf = logged_in
    mock_client.return_value.lookup.side_effect = [
        CentralResult(
            "success",
            200,
            "req-1",
            {
                "citizen_id": "c",
                "display_name": "Synthetic Person",
                "blood_type": "O+",
                "allergies": [],
            },
        ),
        CentralResult("not_found", 404, "req-2"),
        CentralResult("denied", 403, "req-3"),
        CentralResult("timeout", 504, None),
    ]
    for expected in (200, 404, 403, 503):
        r = c.post(
            "/api/v1/patient-lookups",
            json={"national_id": "TEST-0001"},
            headers=csrf_headers(csrf),
        )
        assert r.status_code == expected
    rows = c.get("/api/v1/scans").get_json()["data"]["items"]
    assert all("candidate_citizen_id" not in row for row in rows)
    assert all("TEST-0001" not in str(row) for row in rows)


def test_candidate_confirmation_never_retrieves_on_reject(app, logged_in):
    c, csrf = logged_in
    cid = str(uuid.uuid4())
    local = str(uuid.uuid4())
    with app.app_context():
        with connection() as db:
            db.execute(
                "INSERT INTO local_patient_subjects (id,central_citizen_id,recognizer_label,status,model_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (local, cid, 1, "active", "test", now(), now()),
            )
    with patch("app.api.client") as mocked:
        r = c.post(
            f"/api/v1/patient-lookups/{local}/confirm",
            json={"decision": "reject"},
            headers=csrf_headers(csrf),
        )
        assert r.status_code == 200
        mocked.return_value.lookup.assert_not_called()


def test_central_client_uses_combined_bearer_and_allowlists_response():
    from types import SimpleNamespace

    from app.services.central_client import CentralClient

    session = Mock()
    session.post.return_value = Mock(
        status_code=200,
        headers={"X-Request-ID": "00000000-0000-0000-0000-000000000001"},
        json=lambda: {
            "data": {
                "citizen_id": "synthetic-id",
                "display_name": "Synthetic Patient",
                "blood_type": "O+",
                "allergies": [],
                "national_id": "MUST_NOT_PASS",
                "internal_notes": "MUST_NOT_PASS",
            }
        },
    )
    app = SimpleNamespace(
        config={
            "CENTRAL_API_BASE_URL": "https://central.example.test",
            "HOSPITAL_CREDENTIAL_KEY_ID": "synthetic-key-id",
            "HOSPITAL_CREDENTIAL_SECRET": "synthetic-secret-value-with-adequate-length",
        }
    )
    client = CentralClient(app)
    client.session = session
    result = client.lookup({"national_id": "SYNTHETIC-01"})
    assert result.kind == "success"
    assert result.data == {
        "citizen_id": "synthetic-id",
        "display_name": "Synthetic Patient",
        "blood_type": "O+",
        "allergies": [],
    }
    headers = session.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == (
        "Bearer synthetic-key-id.synthetic-secret-value-with-adequate-length"
    )
    assert "X-Hospital-Credential-Key-Id" not in headers


def test_central_enrollment_verification_sends_only_uuid():
    from types import SimpleNamespace

    from app.services.central_client import CentralClient

    citizen_id = str(uuid.uuid4())
    session = Mock()
    session.post.return_value = Mock(
        status_code=200,
        headers={"X-Request-ID": "synthetic-request-id"},
        json=lambda: {"data": {"citizen_id": citizen_id, "enrollment_allowed": True}},
    )
    app = SimpleNamespace(
        config={
            "CENTRAL_API_BASE_URL": "https://central.example.test",
            "HOSPITAL_CREDENTIAL_KEY_ID": "synthetic-key-id",
            "HOSPITAL_CREDENTIAL_SECRET": "synthetic-secret-value-with-adequate-length",
        }
    )
    client = CentralClient(app)
    client.session = session
    result = client.verify_enrollment(citizen_id)
    assert result.kind == "success"
    assert result.data == {"citizen_id": citizen_id, "enrollment_allowed": True}
    call = session.post.call_args
    assert (
        call.args[0]
        == "https://central.example.test/api/v1/hospital/enrollments/verify"
    )
    assert call.kwargs["json"] == {"citizen_id": citizen_id}
    assert call.kwargs["verify"] is True


def test_central_enrollment_verification_rejects_invalid_uuid_without_network():
    from types import SimpleNamespace

    from app.services.central_client import CentralClient

    app = SimpleNamespace(
        config={
            "CENTRAL_API_BASE_URL": "https://central.example.test",
            "HOSPITAL_CREDENTIAL_KEY_ID": "synthetic-key-id",
            "HOSPITAL_CREDENTIAL_SECRET": "synthetic-secret-value-with-adequate-length",
        }
    )
    client = CentralClient(app)
    client.session = Mock()
    result = client.verify_enrollment("not-a-uuid")
    assert result.kind == "validation"
    client.session.post.assert_not_called()
