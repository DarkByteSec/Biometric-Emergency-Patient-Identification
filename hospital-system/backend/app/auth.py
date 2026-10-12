import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from functools import wraps

from argon2 import PasswordHasher
from flask import current_app, g, jsonify, request

from .db import connection, new_id
from .models import session_by_token_hash

PH = PasswordHasher()


def digest(value):
    return hashlib.sha256(value.encode()).digest()


def current_session():
    token = request.cookies.get("hospital_session")
    if not token:
        return None
    row = session_by_token_hash(digest(token))
    if not row or not row["is_active"]:
        return None
    try:
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            return None
    except ValueError:
        return None
    return row


def login_user(user_id):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    created = datetime.now(UTC)
    expires = created + timedelta(seconds=current_app.config["SESSION_TTL_SECONDS"])
    with connection() as c:
        c.execute(
            "INSERT INTO hospital_sessions (id,hospital_user_id,token_hash,csrf_secret_hash,created_at,expires_at,last_seen_at) VALUES (?,?,?,?,?,?,?)",
            (
                new_id(),
                user_id,
                digest(token),
                digest(csrf),
                created.isoformat(),
                expires.isoformat(),
                created.isoformat(),
            ),
        )
    return token, csrf, expires


def require_auth(roles=None):
    def deco(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            row = current_session()
            if not row:
                return error("AUTH_REQUIRED", "Authentication required", 401)
            if roles and row["role"] not in roles:
                return error("FORBIDDEN", "This role is not allowed", 403)
            g.user = row
            return fn(*args, **kwargs)

        return wrapped

    return deco


def require_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if request.endpoint in ("api.login", "api.live"):
        return None
    row = getattr(g, "user", None) or current_session()
    if not row:
        return None
    supplied = request.headers.get("X-CSRF-Token", "")
    cookie = request.cookies.get("hospital_csrf", "")
    if (
        not supplied
        or not cookie
        or not hmac.compare_digest(supplied, cookie)
        or not hmac.compare_digest(digest(supplied), row["csrf_secret_hash"])
    ):
        return error("CSRF_FAILED", "CSRF validation failed", 403)
    return None


def error(code, message, status):
    return jsonify(
        {"error": {"code": code, "message": message}, "request_id": g.get("request_id")}
    ), status


def set_auth_cookies(resp, token, csrf, expires):
    secure = current_app.config.get("COOKIE_SECURE", False)
    resp.set_cookie(
        "hospital_session",
        token,
        httponly=True,
        samesite="Lax",
        secure=secure,
        expires=expires,
    )
    resp.set_cookie(
        "hospital_csrf",
        csrf,
        httponly=False,
        samesite="Lax",
        secure=secure,
        expires=expires,
    )
    return resp
