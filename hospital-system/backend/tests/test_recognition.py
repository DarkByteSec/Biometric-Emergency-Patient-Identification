import io
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from argon2 import PasswordHasher
from PIL import Image

from app.db import connection, now
from app.recognition import recognize, validate_frame


def jpeg_bytes(width=128, height=128, color="white"):
    image = Image.new("RGB", (width, height), color)
    stream = io.BytesIO()
    image.save(stream, format="JPEG")
    return stream.getvalue()


def recognition_config(tmp_path):
    return {
        "MAX_IMAGE_PIXELS": 12_000_000,
        "RECOGNITION_MODEL_PATH": str(tmp_path / "model.yml"),
        "RECOGNITION_MODEL_VERSION": "synthetic-test-model",
        "RECOGNITION_DISTANCE_THRESHOLD": "70",
        "RECOGNITION_SAMPLE_DIR": str(tmp_path / "samples"),
    }


def add_admin(app, email="recognition-admin@test.invalid"):
    user_id = str(uuid.uuid4())
    with app.app_context(), connection() as db:
        db.execute(
            "INSERT INTO hospital_users (id,email,password_hash,role,is_active,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (
                user_id,
                email,
                PasswordHasher().hash("a secure synthetic test password"),
                "admin",
                True,
                now(),
                now(),
            ),
        )
    return email


def login_admin(app, client, email="recognition-admin@test.invalid"):
    add_admin(app, email)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "a secure synthetic test password"},
    )
    assert response.status_code == 200
    return response.get_json()["data"]["csrf_token"]


def test_validate_frame_accepts_jpeg_and_rejects_small_or_invalid():
    valid_size, valid_error = validate_frame(io.BytesIO(jpeg_bytes()), 12_000_000)
    small_size, small_error = validate_frame(io.BytesIO(jpeg_bytes(32, 32)), 12_000_000)
    invalid_size, invalid_error = validate_frame(
        io.BytesIO(b"not-an-image"), 12_000_000
    )
    assert valid_size == (128, 128)
    assert valid_error is None
    assert small_size is None
    assert small_error == "low_quality"
    assert invalid_size is None
    assert invalid_error == "invalid_image"


def test_probe_fails_closed_without_model_and_never_persists_frame(app, logged_in):
    client, csrf = logged_in
    response = client.post(
        "/api/v1/recognition/probes",
        data={"frame": (io.BytesIO(jpeg_bytes()), "probe.jpg")},
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["outcome"] == "unavailable"
    with app.app_context(), connection() as db:
        assert (
            db.execute("SELECT COUNT(*) AS n FROM local_biometric_samples").fetchone()[
                "n"
            ]
            == 0
        )
        assert (
            db.execute("SELECT COUNT(*) AS n FROM local_scan_events").fetchone()["n"]
            == 1
        )


@pytest.mark.parametrize(
    ("distance", "expected"),
    [(20.0, "candidate"), (100.0, "no_match")],
)
def test_recognition_threshold_returns_candidate_only_below_threshold(
    tmp_path, distance, expected
):
    import app.recognition as recognition

    config = recognition_config(tmp_path)
    model_path = recognition.model_path(config)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text("synthetic model placeholder", encoding="utf-8")

    class FakeRecognizer:
        def read(self, _path):
            return None

        def predict(self, _roi):
            return 7, distance

    fake_cv2 = SimpleNamespace(
        face=SimpleNamespace(LBPHFaceRecognizer_create=lambda: FakeRecognizer())
    )
    fake_db = SimpleNamespace(
        execute=lambda *_args: SimpleNamespace(
            fetchone=lambda: {"id": "synthetic-candidate-id"}
        )
    )
    with (
        patch.object(recognition, "_opencv", return_value=(fake_cv2, object(), None)),
        patch.object(
            recognition,
            "_prepare_face_bytes",
            return_value=(object(), None, None),
        ),
    ):
        result = recognize(io.BytesIO(jpeg_bytes()), config, fake_db)
    assert result["outcome"] == expected
    if expected == "candidate":
        assert result["candidate_id"] == "synthetic-candidate-id"
        assert result["error_code"] is None
    else:
        assert "candidate_id" not in result
        assert result["error_code"] == "no_match"


def test_enrollment_requires_admin_confirmation_and_central_scope(
    app, client, logged_in
):
    _, operator_csrf = logged_in
    denied_for_operator = client.post(
        "/api/v1/recognition/enrollments",
        data={
            "central_citizen_id": str(uuid.uuid4()),
            "authorization_confirmed": "true",
            "sample": (io.BytesIO(jpeg_bytes()), "sample.jpg"),
        },
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": operator_csrf},
    )
    assert denied_for_operator.status_code == 403

    admin_csrf = login_admin(app, client)
    with (
        patch("app.api.client") as central_client,
        patch("app.api.enroll") as enroll_sample,
    ):
        unconfirmed = client.post(
            "/api/v1/recognition/enrollments",
            data={
                "central_citizen_id": str(uuid.uuid4()),
                "sample": (io.BytesIO(jpeg_bytes()), "sample.jpg"),
            },
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": admin_csrf},
        )
        assert unconfirmed.status_code == 403
        central_client.assert_not_called()
        enroll_sample.assert_not_called()

    citizen_id = str(uuid.uuid4())
    with (
        patch("app.api.client") as central_client,
        patch("app.api.enroll") as enroll_sample,
    ):
        central_client.return_value.verify_enrollment.return_value = SimpleNamespace(
            kind="denied"
        )
        denied_by_central = client.post(
            "/api/v1/recognition/enrollments",
            data={
                "central_citizen_id": citizen_id,
                "authorization_confirmed": "true",
                "sample": (io.BytesIO(jpeg_bytes()), "sample.jpg"),
            },
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": admin_csrf},
        )
        assert denied_by_central.status_code == 403
        central_client.return_value.verify_enrollment.assert_called_once_with(
            citizen_id
        )
        enroll_sample.assert_not_called()

    with app.app_context(), connection() as db:
        assert (
            db.execute("SELECT COUNT(*) AS n FROM local_biometric_samples").fetchone()[
                "n"
            ]
            == 0
        )


def test_enrollment_passes_only_after_central_verification(app, client):
    admin_csrf = login_admin(app, client, "authorized-admin@test.invalid")
    citizen_id = str(uuid.uuid4())
    with (
        patch("app.api.client") as central_client,
        patch(
            "app.api.enroll",
            return_value={
                "outcome": "unavailable",
                "error_code": "opencv_unavailable",
                "model_version": "none",
            },
        ),
    ):
        central_client.return_value.verify_enrollment.return_value = SimpleNamespace(
            kind="success"
        )
        response = client.post(
            "/api/v1/recognition/enroll",
            data={
                "central_citizen_id": citizen_id,
                "authorization_confirmed": "true",
                "sample": (io.BytesIO(jpeg_bytes()), "sample.jpg"),
            },
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": admin_csrf},
        )
    assert response.status_code == 200
    assert response.get_json()["data"]["outcome"] == "unavailable"
    central_client.return_value.verify_enrollment.assert_called_once_with(citizen_id)


def test_enrollment_transaction_commits_and_rolls_back(app, client):
    csrf = login_admin(app, client, "transaction-admin@test.invalid")
    subject_id, sample_id, citizen_id = (
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )

    def commit_enroll(_stream, _config, db, central_citizen_id, _subject_id=None):
        db.execute(
            "INSERT INTO local_patient_subjects (id,central_citizen_id,recognizer_label,status,model_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (subject_id, central_citizen_id, 99, "active", "test", now(), now()),
        )
        db.execute(
            "INSERT INTO local_biometric_samples (id,subject_id,file_key,captured_at,image_width,image_height,quality_status,deleted_at) VALUES (?,?,?,?,?,?,?,NULL)",
            (sample_id, subject_id, "synthetic.png", now(), 128, 128, "accepted"),
        )
        return {
            "outcome": "accepted",
            "sample_id": sample_id,
            "subject_id": subject_id,
            "model_version": "test",
        }

    with (
        patch("app.api.client") as central_client,
        patch("app.api.enroll", side_effect=commit_enroll),
    ):
        central_client.return_value.verify_enrollment.return_value = SimpleNamespace(
            kind="success"
        )
        response = client.post(
            "/api/v1/recognition/enroll",
            data={
                "central_citizen_id": citizen_id,
                "authorization_confirmed": "true",
                "sample": (io.BytesIO(jpeg_bytes()), "sample.jpg"),
            },
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": csrf},
        )
    assert response.status_code == 200
    with app.app_context(), connection() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) AS n FROM local_biometric_samples WHERE id=?",
                (sample_id,),
            ).fetchone()["n"]
            == 1
        )

    rollback_subject = str(uuid.uuid4())

    def failing_enroll(_stream, _config, db, central_citizen_id, _subject_id=None):
        db.execute(
            "INSERT INTO local_patient_subjects (id,central_citizen_id,recognizer_label,status,model_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (rollback_subject, central_citizen_id, 100, "active", "test", now(), now()),
        )
        raise RuntimeError("synthetic failure")

    with (
        patch("app.api.client") as central_client,
        patch("app.api.enroll", side_effect=failing_enroll),
    ):
        central_client.return_value.verify_enrollment.return_value = SimpleNamespace(
            kind="success"
        )
        response = client.post(
            "/api/v1/recognition/enroll",
            data={
                "central_citizen_id": str(uuid.uuid4()),
                "authorization_confirmed": "true",
                "sample": (io.BytesIO(jpeg_bytes()), "sample.jpg"),
            },
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": csrf},
        )
    assert response.status_code == 500
    with app.app_context(), connection() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) AS n FROM local_patient_subjects WHERE id=?",
                (rollback_subject,),
            ).fetchone()["n"]
            == 0
        )


def test_enroll_rejects_invalid_uuid_before_database_or_file_access(tmp_path):
    import app.recognition as recognition

    class NoDatabaseAccess:
        def execute(self, *_args):
            raise AssertionError("invalid UUID must not reach the database")

    config = recognition_config(tmp_path)
    result = recognition.enroll(
        io.BytesIO(jpeg_bytes()), config, NoDatabaseAccess(), "not-a-uuid"
    )
    assert result["outcome"] == "error"
    assert result["error_code"] == "invalid_central_citizen_id"
    assert not (tmp_path / "samples").exists()


def test_retrain_writes_private_model_and_updates_subject_version(tmp_path):
    import stat

    import app.recognition as recognition

    config = recognition_config(tmp_path)
    sample_root = tmp_path / "samples"
    sample_root.mkdir()
    (sample_root / "synthetic.png").write_bytes(b"synthetic-only-placeholder")
    sample_rows = [
        {
            "file_key": "synthetic.png",
            "subject_id": "synthetic-subject-id",
            "recognizer_label": 4,
        }
    ]

    class FakeDatabase:
        updated = None

        def execute(self, sql, params=()):
            if sql.startswith("SELECT s.file_key"):
                return SimpleNamespace(fetchall=lambda: sample_rows)
            self.updated = (sql, params)
            return None

    class FakeRecognizer:
        def train(self, faces, labels):
            assert len(faces) == 1
            assert list(labels) == [4]

        def write(self, path):
            from pathlib import Path

            Path(path).write_text("synthetic LBPH model", encoding="utf-8")

    fake_cv2 = SimpleNamespace(
        IMREAD_GRAYSCALE=0,
        imread=lambda _path, _flag: SimpleNamespace(size=1),
        face=SimpleNamespace(LBPHFaceRecognizer_create=lambda: FakeRecognizer()),
    )
    fake_numpy = SimpleNamespace(asarray=lambda values, dtype=None: values, int32=int)
    database = FakeDatabase()
    with patch.object(
        recognition, "_opencv", return_value=(fake_cv2, fake_numpy, None)
    ):
        result = recognition.retrain(config, database)

    destination = recognition.model_path(config)
    assert result["outcome"] == "trained"
    assert result["sample_count"] == 1
    assert result["subject_count"] == 1
    assert destination.read_text(encoding="utf-8") == "synthetic LBPH model"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert database.updated[0].startswith("UPDATE local_patient_subjects")
    assert database.updated[1][0] == "synthetic-test-model"
    assert isinstance(database.updated[1][1], str)
