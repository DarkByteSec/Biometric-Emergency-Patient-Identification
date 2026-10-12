"""Local-only, candidate-producing face recognition helpers.

This module deliberately keeps camera probes in memory.  Only samples explicitly
uploaded through the admin enrollment API are persisted, and only as cropped
face images in a private directory.  A match is an educational candidate and
never an identity assertion.
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .db import new_id, now

logger = logging.getLogger(__name__)

MAX_FRAME_BYTES = 8 * 1024 * 1024
DEFAULT_DISTANCE_THRESHOLD = 70.0
DEFAULT_SAMPLE_DIR = Path(__file__).resolve().parents[1] / "private/biometric_samples"


def validate_frame(stream, max_pixels):
    """Validate a bounded JPEG/PNG stream without retaining it on disk."""
    raw = stream.read(MAX_FRAME_BYTES + 1)
    if len(raw) > MAX_FRAME_BYTES:
        return None, "too_large"
    try:
        im = Image.open(BytesIO(raw))
        im.verify()
        im = Image.open(BytesIO(raw))
        width, height = im.size
        if im.format not in ("JPEG", "PNG"):
            return None, "unsupported_type"
        if width < 64 or height < 64:
            return None, "low_quality"
        if width * height > max_pixels:
            return None, "too_many_pixels"
        im.load()
        return (width, height), None
    except (UnidentifiedImageError, OSError, ValueError):
        return None, "invalid_image"


def _config_value(config: Any, key: str, default: Any = None):
    if hasattr(config, "get"):
        value = config.get(key)
    else:
        value = getattr(config, key, None)
    return default if value in (None, "") else value


def sample_dir(config: Any) -> Path:
    configured = _config_value(config, "RECOGNITION_SAMPLE_DIR")
    if configured is None:
        configured = os.environ.get("HOSPITAL_RECOGNITION_SAMPLE_DIR")
    return Path(configured or DEFAULT_SAMPLE_DIR).expanduser()


def model_path(config: Any) -> Path:
    configured = _config_value(config, "RECOGNITION_MODEL_PATH")
    if configured is None:
        configured = os.environ.get("HOSPITAL_RECOGNITION_MODEL_PATH")
    if configured:
        return Path(configured).expanduser()
    return sample_dir(config) / "lbph-model.yml"


def model_version(config: Any) -> str:
    value = _config_value(config, "RECOGNITION_MODEL_VERSION")
    return str(value or "lbph-local")


def distance_threshold(config: Any) -> float:
    value = _config_value(config, "RECOGNITION_DISTANCE_THRESHOLD")
    if value is None:
        value = os.environ.get(
            "HOSPITAL_RECOGNITION_DISTANCE_THRESHOLD", DEFAULT_DISTANCE_THRESHOLD
        )
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        threshold = DEFAULT_DISTANCE_THRESHOLD
    # Prevent accidental configuration of a nonsensical negative threshold.
    return max(0.0, threshold)


def _opencv():
    try:
        import cv2
        import numpy as np

        if not hasattr(cv2, "face") or not hasattr(
            cv2.face, "LBPHFaceRecognizer_create"
        ):
            return None, None, "opencv_contrib_unavailable"
        return cv2, np, None
    except Exception:  # noqa: BLE001 - optional OpenCV failures become unavailable
        return None, None, "opencv_unavailable"


def _cascade(cv2, config):
    configured = _config_value(config, "RECOGNITION_CASCADE_PATH")
    path = (
        Path(configured).expanduser()
        if configured
        else Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    )
    if not path.is_file():
        return None
    cascade = cv2.CascadeClassifier(str(path))
    return None if cascade.empty() else cascade


def _decode(raw: bytes, cv2, np):
    encoded = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
    return image


def _prepare_face(stream, config):
    """Return (source size, normalized grayscale face, outcome/error)."""
    source_size, error_code = validate_frame(
        stream, int(_config_value(config, "MAX_IMAGE_PIXELS", 12_000_000))
    )
    if error_code:
        return (
            source_size,
            None,
            "low_quality" if error_code == "low_quality" else "error",
            error_code,
        )

    # Re-read the bounded bytes once; this is memory-only and is never written.
    # validate_frame consumed the stream, so callers pass a rewindable BytesIO.
    return source_size, None, "error", "frame_read_failed"


def _prepare_face_bytes(raw: bytes, source_size, config, cv2, np):
    image = _decode(raw, cv2, np)
    if image is None or image.size == 0:
        return None, "error", "invalid_image"
    cascade = _cascade(cv2, config)
    if cascade is None:
        return None, "unavailable", "cascade_unavailable"
    try:
        min_size = int(_config_value(config, "RECOGNITION_FACE_MIN_SIZE", 48))
        faces = cascade.detectMultiScale(
            image, scaleFactor=1.1, minNeighbors=5, minSize=(min_size, min_size)
        )
    except Exception:  # noqa: BLE001 - cascade errors never produce a candidate
        return None, "unavailable", "cascade_failed"
    if len(faces) == 0:
        return None, "no_face", "no_face_detected"
    if len(faces) != 1:
        return None, "multiple_faces", "multiple_faces_detected"
    x, y, width, height = [int(v) for v in faces[0]]
    if width < min_size or height < min_size:
        return None, "low_quality", "face_too_small"
    # Normalize all enrollment/probe ROIs to one size for deterministic LBPH input.
    roi = image[max(0, y) : y + height, max(0, x) : x + width]
    if roi.size == 0:
        return None, "low_quality", "face_crop_empty"
    try:
        roi = cv2.resize(roi, (200, 200), interpolation=cv2.INTER_AREA)
    except Exception as exc:  # noqa: BLE001 - OpenCV conversion fails closed
        logger.warning("Local face resize failed error_type=%s", type(exc).__name__)
        return None, "low_quality", "face_crop_failed"
    return roi, None, None


def _read_raw(stream) -> bytes:
    raw = stream.read(MAX_FRAME_BYTES + 1)
    return raw


def _safe_subject(db, label):
    if db is None:
        return None
    row = db.execute(
        "SELECT id, central_citizen_id, recognizer_label, status, model_version "
        "FROM local_patient_subjects WHERE recognizer_label=? AND status='active'",
        (int(label),),
    ).fetchone()
    return row


def recognize(frame, config, db):
    """Process one probe entirely in memory and return a safe outcome."""
    raw = _read_raw(frame)
    if len(raw) > MAX_FRAME_BYTES:
        return {
            "outcome": "error",
            "error_code": "too_large",
            "model_version": model_version(config),
        }
    size, error_code = validate_frame(
        BytesIO(raw), int(_config_value(config, "MAX_IMAGE_PIXELS", 12_000_000))
    )
    if error_code:
        return {
            "outcome": "low_quality" if error_code == "low_quality" else "error",
            "error_code": error_code,
            "model_version": model_version(config),
            **({"width": size[0], "height": size[1]} if size else {}),
        }

    cv2, np, opencv_error = _opencv()
    if opencv_error:
        return {
            "outcome": "unavailable",
            "error_code": opencv_error,
            "model_version": model_version(config),
            "width": size[0],
            "height": size[1],
        }
    path = model_path(config)
    if not path.is_file():
        return {
            "outcome": "unavailable",
            "error_code": "model_unavailable",
            "model_version": model_version(config),
            "width": size[0],
            "height": size[1],
        }
    try:
        roi, outcome, reason = _prepare_face_bytes(raw, size, config, cv2, np)
        if outcome:
            return {
                "outcome": outcome,
                "error_code": reason,
                "model_version": model_version(config),
                "width": size[0],
                "height": size[1],
            }
        recognizer = cv2.face.LBPHFaceRecognizer_create()
        recognizer.read(str(path))
        label, distance = recognizer.predict(roi)
    except Exception as exc:  # noqa: BLE001 - model errors never produce a candidate
        logger.warning("Local LBPH probe failed error_type=%s", type(exc).__name__)
        return {
            "outcome": "unavailable",
            "error_code": "model_load_failed",
            "model_version": model_version(config),
            "width": size[0],
            "height": size[1],
        }

    score = float(distance)
    result = {
        "outcome": "no_match",
        "error_code": "no_match",
        "score": score,
        "model_version": model_version(config),
        "width": size[0],
        "height": size[1],
    }
    if score > distance_threshold(config):
        return result
    subject = _safe_subject(db, label)
    if not subject:
        result["error_code"] = "unknown_label"
        return result
    result.update(
        {"outcome": "candidate", "error_code": None, "candidate_id": subject["id"]}
    )
    return result


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _write_private_png(face, root: Path, cv2) -> str:
    root = _private_dir(root)
    encoded_ok, encoded = cv2.imencode(".png", face)
    if not encoded_ok:
        raise ValueError("face_encode_failed")
    filename = f"{uuid.uuid4()}.png"
    destination = root / filename
    # UUID-derived names plus exclusive create prevent user-controlled paths and overwrite races.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded.tobytes())
        fd = None
    finally:
        if fd is not None:
            os.close(fd)
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    return filename


def _canonical_citizen(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def enroll(stream, config, db, central_citizen_id: str, subject_id: str | None = None):
    """Accept one explicitly uploaded authorized sample; never call Central."""
    canonical = _canonical_citizen(central_citizen_id)
    if not canonical:
        return {
            "outcome": "error",
            "error_code": "invalid_central_citizen_id",
            "model_version": model_version(config),
        }
    raw = _read_raw(stream)
    if len(raw) > MAX_FRAME_BYTES:
        return {
            "outcome": "error",
            "error_code": "too_large",
            "model_version": model_version(config),
        }
    size, error_code = validate_frame(
        BytesIO(raw), int(_config_value(config, "MAX_IMAGE_PIXELS", 12_000_000))
    )
    if error_code:
        return {
            "outcome": "low_quality" if error_code == "low_quality" else "error",
            "error_code": error_code,
            "model_version": model_version(config),
        }
    cv2, np, opencv_error = _opencv()
    if opencv_error:
        return {
            "outcome": "unavailable",
            "error_code": opencv_error,
            "model_version": model_version(config),
        }
    roi, outcome, reason = _prepare_face_bytes(raw, size, config, cv2, np)
    if outcome:
        return {
            "outcome": outcome,
            "error_code": reason,
            "model_version": model_version(config),
        }
    if subject_id:
        try:
            subject_uuid = str(uuid.UUID(str(subject_id)))
        except (ValueError, TypeError, AttributeError):
            return {
                "outcome": "error",
                "error_code": "invalid_subject_id",
                "model_version": model_version(config),
            }
        subject = db.execute(
            "SELECT * FROM local_patient_subjects WHERE id=? AND status='active'",
            (subject_uuid,),
        ).fetchone()
        if not subject or str(subject["central_citizen_id"]) != canonical:
            return {
                "outcome": "error",
                "error_code": "subject_not_found",
                "model_version": model_version(config),
            }
        subject_uuid = subject["id"]
    else:
        existing = db.execute(
            "SELECT * FROM local_patient_subjects WHERE central_citizen_id=? AND status='active'",
            (canonical,),
        ).fetchone()
        if existing:
            subject_uuid = existing["id"]
        else:
            subject_uuid = new_id()
            next_label = db.execute(
                "SELECT COALESCE(MAX(recognizer_label),0)+1 AS next_label FROM local_patient_subjects"
            ).fetchone()["next_label"]
            db.execute(
                "INSERT INTO local_patient_subjects (id,central_citizen_id,recognizer_label,status,model_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (
                    subject_uuid,
                    canonical,
                    int(next_label),
                    "active",
                    model_version(config),
                    now(),
                    now(),
                ),
            )
    filename = None
    try:
        filename = _write_private_png(roi, sample_dir(config), cv2)
        sample_id = new_id()
        db.execute(
            "INSERT INTO local_biometric_samples (id,subject_id,file_key,captured_at,image_width,image_height,quality_status,deleted_at) VALUES (?,?,?,?,?,?,?,NULL)",
            (
                sample_id,
                subject_uuid,
                filename,
                now(),
                int(size[0]),
                int(size[1]),
                "accepted",
            ),
        )
    except Exception:
        if filename:
            try:
                (sample_dir(config) / filename).unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return {
        "outcome": "accepted",
        "sample_id": sample_id,
        "subject_id": subject_uuid,
        "model_version": model_version(config),
        "width": size[0],
        "height": size[1],
    }


def _sample_file(root: Path, file_key: str) -> Path | None:
    # Metadata is generated by this module, but still reject traversal if a DB row is altered.
    candidate = Path(str(file_key))
    if candidate.name != str(file_key) or candidate.suffix.lower() != ".png":
        return None
    root = root.resolve()
    resolved = (root / candidate.name).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def retrain(config, db):
    """Train and atomically save a local LBPH model from accepted local samples."""
    cv2, np, opencv_error = _opencv()
    if opencv_error:
        return {
            "outcome": "unavailable",
            "error_code": opencv_error,
            "model_version": model_version(config),
        }
    rows = db.execute(
        "SELECT s.file_key,s.subject_id,p.recognizer_label FROM local_biometric_samples s "
        "JOIN local_patient_subjects p ON p.id=s.subject_id "
        "WHERE s.deleted_at IS NULL AND s.quality_status='accepted' AND p.status='active'"
    ).fetchall()
    root = sample_dir(config)
    faces, labels = [], []
    for row in rows:
        path = _sample_file(root, row["file_key"])
        if path is None or not path.is_file():
            continue
        try:
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is not None and image.size:
                faces.append(image)
                labels.append(int(row["recognizer_label"]))
        except Exception as exc:  # noqa: BLE001 - skip corrupt local samples only
            logger.warning(
                "Skipping unreadable local enrollment sample error_type=%s",
                type(exc).__name__,
            )
            continue
    if not faces:
        return {
            "outcome": "unavailable",
            "error_code": "no_enrollment_samples",
            "model_version": model_version(config),
        }
    try:
        recognizer = cv2.face.LBPHFaceRecognizer_create()
        recognizer.train(faces, np.asarray(labels, dtype=np.int32))
        destination = model_path(config)
        _private_dir(destination.parent)
        temporary = (
            destination.parent / f".{destination.name}.{secrets.token_hex(12)}.tmp"
        )
        recognizer.write(str(temporary))
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        try:
            os.chmod(destination, 0o600)
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001 - failed model publication stays unavailable
        logger.warning("Local LBPH retrain failed error_type=%s", type(exc).__name__)
        try:
            temporary.unlink(missing_ok=True)
        except (UnboundLocalError, OSError):
            pass
        return {
            "outcome": "unavailable",
            "error_code": "model_save_failed",
            "model_version": model_version(config),
        }
    version = model_version(config)
    db.execute(
        "UPDATE local_patient_subjects SET model_version=?,updated_at=? WHERE status='active'",
        (version, now()),
    )
    return {
        "outcome": "trained",
        "model_version": version,
        "sample_count": len(faces),
        "subject_count": len(set(labels)),
    }
