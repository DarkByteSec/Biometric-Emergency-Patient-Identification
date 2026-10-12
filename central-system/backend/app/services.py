import hashlib
import hmac
import json
import re
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app, g, request

from .config import decode_key
from .db import execute, now, query, scopes_read, uid

PH = PasswordHasher()
SCOPES = {"patient.lookup", "patient.enroll", "health.read"}


def _key(name):
    return decode_key(current_app.config[name])


def encrypt_id(value):
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(_key("ENCRYPTION_KEY")).encrypt(nonce, value.encode(), None)


def decrypt_id(blob):
    return AESGCM(_key("ENCRYPTION_KEY")).decrypt(blob[:12], blob[12:], None).decode()


def id_hmac(value):
    return hmac.new(_key("LOOKUP_HMAC_KEY"), value.encode(), hashlib.sha256).digest()


def fingerprint(value):
    return hmac.new(
        _key("LOOKUP_HMAC_KEY"), ("fingerprint:" + value).encode(), hashlib.sha256
    ).digest()[:16]


def hash_secret(value):
    return hashlib.sha256(value.encode()).digest()


def verify_secret(value, stored):
    return hmac.compare_digest(hash_secret(value), bytes(stored))


def hash_password(value):
    return PH.hash(value)


def verify_password(stored, value):
    try:
        return PH.verify(stored, value)
    except VerifyMismatchError:
        return False


def safe_error(code, message, status):
    return {
        "error": {"code": code, "message": message},
        "request_id": g.request_id,
    }, status


def audit(
    action,
    outcome="success",
    actor_type="system",
    admin_id=None,
    hospital_id=None,
    target_type=None,
    target_id=None,
    details=None,
):
    execute(
        "INSERT INTO security_audit_events(id,occurred_at,actor_type,actor_admin_id,actor_hospital_id,action,target_type,target_id,outcome,request_id,details) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            uid(),
            now(),
            actor_type,
            admin_id,
            hospital_id,
            action,
            target_type,
            target_id,
            outcome,
            g.get("request_id"),
            json.dumps(details or {}, separators=(",", ":")),
        ),
    )


def access_event(
    hospital_id,
    credential_id,
    outcome,
    status,
    identifier=None,
    citizen_id=None,
    error_code=None,
    duration_ms=None,
    method="manual_national_id",
    operation="patient_lookup",
):
    fp = fingerprint(identifier) if identifier else None
    last2 = identifier[-2:] if identifier else None
    execute(
        "INSERT INTO patient_access_events(id,request_id,hospital_id,credential_id,occurred_at,operation,outcome,http_status,citizen_id,identifier_fingerprint,identifier_last2,duration_ms,error_code,recognition_method) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            uid(),
            g.request_id,
            hospital_id,
            credential_id,
            now(),
            operation,
            outcome,
            status,
            citizen_id,
            fp,
            last2,
            duration_ms,
            error_code,
            method,
        ),
    )


def create_session(user_id):
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    created = datetime.now(UTC)
    exp = created + timedelta(seconds=current_app.config["SESSION_TTL_SECONDS"])
    execute(
        "INSERT INTO admin_sessions(id,admin_user_id,token_hash,csrf_secret_hash,created_at,expires_at,last_seen_at) VALUES(?,?,?,?,?,?,?)",
        (
            uid(),
            user_id,
            hash_secret(token),
            hash_secret(csrf),
            created.isoformat(),
            exp.isoformat(),
            created.isoformat(),
        ),
    )
    return token, csrf, exp


def current_admin():
    token = request.cookies.get(current_app.config["SESSION_COOKIE_NAME"])
    if not token:
        return None
    row = query(
        "SELECT s.*,u.email,u.role,u.is_active FROM admin_sessions s JOIN admin_users u ON u.id=s.admin_user_id WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?",
        (hash_secret(token), now()),
        one=True,
    )
    if row:
        g.session_token = token
        return row
    return None


def require_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    session = current_admin()
    supplied = request.headers.get("X-CSRF-Token", "")
    cookie = request.cookies.get("central_csrf", "")
    return bool(
        session
        and supplied
        and cookie
        and hmac.compare_digest(supplied, cookie)
        and verify_secret(supplied, session["csrf_secret_hash"])
    )


def require_role(*roles):
    user = current_admin()
    if not user:
        return None, safe_error("AUTH_REQUIRED", "Authentication required.", 401)
    if not user["is_active"] or user["role"] not in roles:
        return None, safe_error(
            "FORBIDDEN", "You are not allowed to perform this action.", 403
        )
    if not require_csrf():
        return None, safe_error("CSRF_REQUIRED", "A valid CSRF token is required.", 403)
    g.admin = user
    return user, None


def require_hospital(scope="patient.lookup"):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, safe_error("UNAUTHORIZED", "Authorization failed.", 401)
    raw = auth[7:].strip()
    parts = raw.split(".", 1)
    if (
        len(parts) != 2
        or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", parts[0])
        or len(parts[1]) < 40
    ):
        return None, safe_error("UNAUTHORIZED", "Authorization failed.", 401)
    row = query(
        "SELECT c.*,h.code,h.name,h.status,h.allowed_scopes FROM hospital_credentials c JOIN hospitals h ON h.id=c.hospital_id WHERE c.key_id=?",
        (parts[0],),
        one=True,
    )
    if not row or not verify_secret(parts[1], row["secret_hash"]):
        return None, safe_error("UNAUTHORIZED", "Authorization failed.", 401)
    if (
        row["status"] != "active"
        or row["revoked_at"]
        or (row["expires_at"] and row["expires_at"] <= now())
    ):
        return None, safe_error("FORBIDDEN", "Credential is not active.", 403)
    scopes = scopes_read(row["scopes"])
    allowed = scopes_read(row["allowed_scopes"])
    if scope not in scopes or not set(scopes) <= set(allowed):
        return None, safe_error("FORBIDDEN", "Credential scope is insufficient.", 403)
    execute(
        "UPDATE hospitals SET last_seen_at=? WHERE id=?", (now(), row["hospital_id"])
    )
    g.hospital = row
    return row, None


def validate_string(value, name, max_len, required=True):
    if required and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} is required")
    if value is not None and (
        not isinstance(value, str) or len(value.strip()) > max_len
    ):
        raise ValueError(f"{name} is invalid")
    return value.strip() if isinstance(value, str) else value


def citizen_view(row, include_medical=False):
    out = {
        "id": row["id"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "date_of_birth": row["date_of_birth"],
        "gender": row["gender"],
        "blood_type": row["blood_type"],
        "national_id_masked": ("••••" + row["national_id_mask_suffix"])
        if row["national_id_mask_suffix"]
        else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_medical:
        out["history"] = [
            dict(x)
            for x in query(
                "SELECT id,category,summary,recorded_at,source_label,is_active FROM medical_history_entries WHERE citizen_id=? ORDER BY recorded_at DESC",
                (row["id"],),
            )
        ]
        out["allergies"] = [
            dict(x)
            for x in query(
                "SELECT id,substance,reaction,status,recorded_at FROM patient_allergies WHERE citizen_id=? ORDER BY recorded_at DESC",
                (row["id"],),
            )
        ]
    return out


def lookup_patient(identifier=None, citizen_id=None):
    if citizen_id:
        return query(
            "SELECT * FROM citizens WHERE id=? AND archived_at IS NULL",
            (citizen_id,),
            one=True,
        )
    return query(
        "SELECT * FROM citizens WHERE national_id_lookup_hmac=? AND archived_at IS NULL",
        (id_hmac(identifier),),
        one=True,
    )
