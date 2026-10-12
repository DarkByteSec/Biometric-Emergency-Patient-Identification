import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from flask import Blueprint, current_app, g, jsonify, make_response, request

from .auth import error, login_user, require_auth, set_auth_cookies
from .db import add_event, add_scan, connection, now
from .models import candidate_by_id, scan_rows, user_by_email
from .recognition import enroll, recognize, retrain
from .services.central_client import CentralClient

api = Blueprint("api", __name__)
PH = PasswordHasher()


def ok(data, status=200):
    return jsonify({"data": data, "request_id": g.request_id}), status


def client():
    instance = current_app.extensions.get("central_client")
    if instance is None:
        instance = CentralClient(current_app)
        current_app.extensions["central_client"] = instance
    return instance


def safe_patient(data):
    if not isinstance(data, dict):
        return {}
    allow = {
        "citizen_id",
        "display_name",
        "date_of_birth",
        "blood_type",
        "allergies",
        "limited_history",
        "recorded_at",
        "record_status",
    }
    return {k: data[k] for k in allow if k in data}


def result_for(result):
    status = {
        "not_found": 404,
        "denied": 403,
        "timeout": 503,
        "unavailable": 503,
        "validation": 400,
        "error": 502,
    }.get(result.kind, 200)
    if result.kind == "success":
        return ok(safe_patient(result.data or {}))
    messages = {
        "not_found": "No record found",
        "denied": "Central access denied",
        "timeout": "Patient data is unavailable because Central timed out",
        "unavailable": "Patient data is currently unavailable",
        "validation": "Lookup request is invalid",
        "error": "Central returned an unusable response",
    }
    return error(
        {
            "not_found": "NOT_FOUND",
            "denied": "CENTRAL_DENIED",
            "timeout": "CENTRAL_TIMEOUT",
            "unavailable": "CENTRAL_UNAVAILABLE",
            "validation": "VALIDATION_ERROR",
            "error": "CENTRAL_ERROR",
        }.get(result.kind, "CENTRAL_ERROR"),
        messages[result.kind],
        status,
    )


@api.get("/health/live")
def live():
    return ok({"status": "live"})


@api.get("/health/ready")
def ready():
    try:
        with connection() as c:
            c.execute("SELECT 1")
        return ok(
            {
                "status": "ready",
                "central": "configured"
                if current_app.config.get("CENTRAL_API_BASE_URL")
                else "not_configured",
            }
        )
    except Exception:  # noqa: BLE001 - readiness fails closed for any DB error
        return error("NOT_READY", "Hospital dependencies are not ready", 503)


@api.post("/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    email = body.get("email", "")
    password = body.get("password", "")
    if (
        not isinstance(email, str)
        or not isinstance(password, str)
        or len(email) > 254
        or len(password) > 1024
    ):
        return error("AUTH_FAILED", "Invalid email or password", 401)
    user = user_by_email(email)
    valid = False
    if user:
        try:
            valid = PH.verify(user["password_hash"], password)
        except (VerifyMismatchError, VerificationError):
            valid = False
    if not valid or not user["is_active"]:
        return error("AUTH_FAILED", "Invalid email or password", 401)
    token, csrf, expires = login_user(user["id"])
    with connection() as c:
        c.execute(
            "UPDATE hospital_users SET last_login_at=?,updated_at=? WHERE id=?",
            (now(), now(), user["id"]),
        )
    resp = make_response(
        ok(
            {
                "user": {
                    "id": user["id"],
                    "email": user["email"],
                    "role": user["role"],
                },
                "csrf_token": csrf,
                "expires_at": expires.isoformat(),
            }
        )
    )
    set_auth_cookies(resp, token, csrf, expires)
    return resp


@api.post("/auth/logout")
@require_auth()
def logout():
    with connection() as c:
        c.execute(
            "UPDATE hospital_sessions SET revoked_at=? WHERE id=?",
            (now(), g.user["id"]),
        )
    resp = make_response(ok({"logged_out": True}))
    resp.delete_cookie("hospital_session")
    resp.delete_cookie("hospital_csrf")
    return resp


@api.get("/auth/session")
@require_auth()
def session():
    return ok(
        {
            "user": {
                "id": g.user["hospital_user_id"],
                "email": g.user["email"],
                "role": g.user["role"],
            },
            "expires_at": g.user["expires_at"],
        }
    )


@api.post("/patient-lookups")
@require_auth(("admin", "operator"))
def manual_lookup():
    body = request.get_json(silent=True)
    if (
        not isinstance(body, dict)
        or set(body) != {"national_id"}
        or not isinstance(body["national_id"], str)
        or not (3 <= len(body["national_id"]) <= 128)
    ):
        return error(
            "VALIDATION_ERROR", "Provide one valid National ID in the JSON body", 400
        )
    res = client().lookup({"national_id": body["national_id"]})
    outcome = {
        "success": "verified",
        "not_found": "not_found",
        "denied": "denied",
        "timeout": "unavailable",
        "unavailable": "unavailable",
        "error": "error",
        "validation": "error",
    }.get(res.kind, "error")
    add_scan(
        hospital_user_id=g.user["hospital_user_id"],
        method="national_id",
        outcome=outcome,
        central_request_id=res.request_id,
        central_status=res.status,
        error_code=res.code,
    )
    add_event(
        "patient_lookup",
        "success"
        if res.kind == "success"
        else ("denied" if res.kind == "denied" else "failure"),
        g.request_id,
        {"method": "national_id", "status": res.status, "outcome": outcome},
    )
    return result_for(res)


@api.post("/recognition/probes")
@require_auth(("admin", "operator"))
def probe():
    upload = request.files.get("frame") or request.files.get("image")
    if not upload:
        return error("VALIDATION_ERROR", "Provide a JPEG or PNG frame as frame", 400)
    rec = recognize(
        upload.stream, current_app.config, current_app.extensions.get("hospital_db")
    )
    outcome = rec["outcome"]
    scan = add_scan(
        hospital_user_id=g.user["hospital_user_id"],
        method="face",
        outcome=outcome,
        score=rec.get("score"),
        model_version=rec.get("model_version"),
        error_code=rec.get("error_code"),
    )
    payload = {
        "outcome": outcome,
        "scan_id": scan,
        "model_version": rec.get("model_version"),
    }
    if outcome == "candidate" and rec.get("candidate_id"):
        payload["candidate_id"] = rec["candidate_id"]
        payload["operator_confirmation_required"] = True
    if rec.get("error_code"):
        payload["reason_code"] = rec["error_code"]
    return ok(payload, 200)


@api.post("/recognition/enroll")
@api.post("/recognition/enrollments")
@require_auth(("admin",))
def enrollment():
    upload = request.files.get("sample") or request.files.get("image")
    central_citizen_id = request.form.get("central_citizen_id", "")
    subject_id = request.form.get("subject_id") or None
    if request.form.get("authorization_confirmed") != "true":
        return error(
            "AUTHORIZATION_REQUIRED",
            "Confirm that the test sample is authorized before enrollment",
            403,
        )
    if not upload or not central_citizen_id:
        return error(
            "VALIDATION_ERROR",
            "Provide an explicitly uploaded sample and central_citizen_id",
            400,
        )
    try:
        central_citizen_id = str(uuid.UUID(central_citizen_id))
    except (AttributeError, TypeError, ValueError):
        return error("VALIDATION_ERROR", "central_citizen_id must be a UUID", 400)
    verification = client().verify_enrollment(central_citizen_id)
    if verification.kind != "success":
        return result_for(verification)
    try:
        with connection() as db:
            result = enroll(
                upload.stream, current_app.config, db, central_citizen_id, subject_id
            )
    except Exception:  # noqa: BLE001 - enrollment fails closed for file/DB errors
        return error(
            "ENROLLMENT_FAILED", "The authorized sample could not be stored safely", 500
        )
    if result["outcome"] == "error":
        return error(
            "VALIDATION_ERROR", result.get("error_code", "invalid_sample"), 400
        )
    payload = {
        "outcome": result["outcome"],
        "model_version": result.get("model_version"),
    }
    for key in (
        "sample_id",
        "subject_id",
        "width",
        "height",
        "reason_code",
        "error_code",
    ):
        if result.get(key) is not None:
            payload[key] = result[key]
    return ok(payload)


@api.post("/recognition/retrain")
@require_auth(("admin",))
def recognition_retrain():
    try:
        with connection() as db:
            result = retrain(current_app.config, db)
    except Exception:  # noqa: BLE001 - retraining fails closed on any model error
        return error(
            "RETRAIN_FAILED",
            "The local recognition model could not be trained safely",
            500,
        )
    payload = {key: value for key, value in result.items() if key != "error_code"}
    if result.get("error_code"):
        payload["reason_code"] = result["error_code"]
    return ok(payload)


@api.post("/patient-lookups/<candidate_id>/confirm")
@require_auth(("admin", "operator"))
def confirm(candidate_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("decision") not in ("confirm", "reject"):
        return error("VALIDATION_ERROR", "decision must be confirm or reject", 400)
    candidate = candidate_by_id(candidate_id)
    if not candidate:
        return error("CANDIDATE_NOT_FOUND", "Candidate is no longer available", 404)
    if body["decision"] == "reject":
        add_scan(
            hospital_user_id=g.user["hospital_user_id"],
            method="face",
            outcome="rejected",
            candidate_citizen_id=candidate["central_citizen_id"],
            model_version=candidate["model_version"],
        )
        return ok({"outcome": "rejected", "central_retrieval_performed": False})
    res = client().lookup({"citizen_id": candidate["central_citizen_id"]})
    outcome = {
        "success": "verified",
        "not_found": "not_found",
        "denied": "denied",
        "timeout": "unavailable",
        "unavailable": "unavailable",
        "error": "error",
    }.get(res.kind, "error")
    add_scan(
        hospital_user_id=g.user["hospital_user_id"],
        method="face",
        outcome=outcome,
        candidate_citizen_id=candidate["central_citizen_id"],
        model_version=candidate["model_version"],
        central_request_id=res.request_id,
        central_status=res.status,
        error_code=res.code,
    )
    if res.kind != "success":
        return result_for(res)
    return ok(
        {
            "outcome": "verified",
            "candidate_confirmation_recorded": True,
            "patient": safe_patient(res.data or {}),
        }
    )


@api.get("/scans")
@require_auth(("admin", "operator"))
def scans():
    try:
        limit = min(max(int(request.args.get("limit", "50")), 1), 100)
        offset = max(int(request.args.get("offset", "0")), 0)
    except ValueError:
        return error("VALIDATION_ERROR", "Invalid pagination", 400)
    rows = scan_rows(limit, offset)
    # Redacted history deliberately omits candidate UUIDs, national IDs, images, and full Central payloads.
    return ok(
        {
            "items": [
                {
                    "id": r["id"],
                    "started_at": r["started_at"],
                    "completed_at": r["completed_at"],
                    "method": r["method"],
                    "outcome": r["outcome"],
                    "score": r["score"],
                    "model_version": r["model_version"],
                    "central_status": r["central_status"],
                    "error_code": r["error_code"],
                    "candidate_present": bool(r["candidate_citizen_id"]),
                }
                for r in rows
            ],
            "limit": limit,
            "offset": offset,
        }
    )
