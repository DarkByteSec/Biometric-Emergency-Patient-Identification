import json
import re
import time
import uuid
from datetime import UTC, date, datetime

from flask import Flask, g, jsonify, make_response, request

from .config import Config, decode_key
from .db import close_db, execute, now, query, scopes_param, scopes_read, uid
from .services import *


def envelope(data=None, error=None, status=200):
    return jsonify({"data": data, "error": error, "request_id": g.request_id}), status


def valid_scope_list(scopes, ceiling=None):
    if (
        not isinstance(scopes, list)
        or not scopes
        or any(not isinstance(scope, str) for scope in scopes)
        or len(set(scopes)) != len(scopes)
        or not set(scopes) <= SCOPES
    ):
        return False
    return ceiling is None or set(scopes) <= set(ceiling)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    Config.init_app(app)
    if test_config:
        app.config.update(test_config)
    environment = app.config.get("APP_ENV", "development").lower()
    if environment not in {"development", "production"} and not app.config.get(
        "TESTING"
    ):
        raise RuntimeError("APP_ENV must be development or production")
    if app.config.get("APP_ENV") == "production":
        if not app.config.get("DATABASE_URL", "").startswith(
            ("postgresql://", "postgres://")
        ):
            raise RuntimeError("Production requires the migrated PostgreSQL database")
        missing = [
            k
            for k in ("SECRET_KEY", "ENCRYPTION_KEY", "LOOKUP_HMAC_KEY")
            if not app.config.get(k)
        ]
        if missing:
            raise RuntimeError(
                "Missing required production security configuration: "
                + ", ".join(missing)
            )
        if len(app.config["SECRET_KEY"]) < 32:
            raise RuntimeError(
                "Production session signing key must be at least 32 characters"
            )
        try:
            decode_key(app.config["ENCRYPTION_KEY"])
            decode_key(app.config["LOOKUP_HMAC_KEY"])
        except ValueError as error:
            raise RuntimeError(
                "Production encryption and lookup keys must each encode exactly 32 bytes"
            ) from error
    import os

    os.makedirs(app.instance_path, exist_ok=True)
    app.teardown_appcontext(close_db)

    @app.before_request
    def before():
        g.request_id = str(uuid.uuid4())
        g.started = time.monotonic()
        if (
            request.content_length
            and request.content_length > app.config["MAX_CONTENT_LENGTH"]
        ):
            return envelope(
                None,
                {"code": "REQUEST_TOO_LARGE", "message": "Request is too large."},
                413,
            )

    @app.after_request
    def after(resp):
        resp.headers["X-Request-ID"] = g.request_id
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        origin = request.headers.get("Origin")
        if origin in app.config["CORS_ORIGINS"]:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, X-CSRF-Token, X-Request-ID, Authorization"
            )
            resp.headers["Access-Control-Allow-Methods"] = (
                "GET,POST,PATCH,PUT,DELETE,OPTIONS"
            )
        return resp

    @app.errorhandler(400)
    def bad(_):
        return envelope(
            None,
            {"code": "BAD_REQUEST", "message": "Request could not be processed."},
            400,
        )

    @app.errorhandler(404)
    def notfound(_):
        return envelope(
            None, {"code": "NOT_FOUND", "message": "Resource not found."}, 404
        )

    @app.errorhandler(405)
    def method(_):
        return envelope(
            None, {"code": "METHOD_NOT_ALLOWED", "message": "Method not allowed."}, 405
        )

    @app.errorhandler(Exception)
    def internal(e):
        app.logger.warning(
            "request failed request_id=%s type=%s",
            g.get("request_id"),
            type(e).__name__,
        )
        return envelope(
            None,
            {"code": "INTERNAL_ERROR", "message": "An internal error occurred."},
            500,
        )

    api = app

    @api.route("/api/v1/health/live")
    def live():
        return envelope({"status": "live", "version": "0.1.0"})

    @api.route("/api/v1/health/ready")
    def ready():
        try:
            query("SELECT 1")
            return envelope({"status": "ready", "database": "ok"})
        except Exception:  # noqa: BLE001 - fail closed for any DB adapter error
            return envelope(
                None, {"code": "NOT_READY", "message": "Service is not ready."}, 503
            )

    @api.route("/api/v1/admin/login", methods=["POST"])
    def login():
        body = request.get_json(silent=True) or {}
        email = (body.get("email") or "").strip().lower()
        password = body.get("password")
        row = (
            query(
                "SELECT * FROM admin_users WHERE email=? AND is_active=TRUE",
                (email,),
                one=True,
            )
            if isinstance(password, str)
            else None
        )
        if not row or not verify_password(row["password_hash"], password):
            audit("admin.login", "failure")
            return envelope(
                None,
                {"code": "AUTH_FAILED", "message": "Email or password is incorrect."},
                401,
            )
        token, csrf, exp = create_session(row["id"])
        execute(
            "UPDATE admin_users SET last_login_at=?,updated_at=? WHERE id=?",
            (now(), now(), row["id"]),
        )
        audit("admin.login", "success", "admin", row["id"])
        resp = make_response(
            envelope(
                {
                    "admin": {
                        "id": row["id"],
                        "email": row["email"],
                        "role": row["role"],
                    },
                    "csrf_token": csrf,
                    "expires_at": exp.isoformat(),
                }
            )
        )
        resp.set_cookie(
            app.config["SESSION_COOKIE_NAME"],
            token,
            httponly=True,
            samesite="Lax",
            secure=app.config["APP_ENV"] == "production",
            max_age=app.config["SESSION_TTL_SECONDS"],
            path="/",
        )
        resp.set_cookie(
            "central_csrf",
            csrf,
            httponly=False,
            samesite="Lax",
            secure=app.config["APP_ENV"] == "production",
            max_age=app.config["SESSION_TTL_SECONDS"],
            path="/",
        )
        return resp

    @api.route("/api/v1/admin/logout", methods=["POST"])
    def logout():
        user, err = require_role("admin", "auditor")
        if err:
            return err
        token = request.cookies.get(app.config["SESSION_COOKIE_NAME"])
        execute(
            "UPDATE admin_sessions SET revoked_at=? WHERE token_hash=?",
            (now(), hash_secret(token)),
        )
        audit("admin.logout", "success", "admin", user["admin_user_id"])
        resp = make_response(envelope({"logged_out": True}))
        resp.delete_cookie(app.config["SESSION_COOKIE_NAME"])
        resp.delete_cookie("central_csrf")
        return resp

    @api.route("/api/v1/admin/session")
    def session():
        user = current_admin()
        if not user:
            return envelope(
                None,
                {"code": "AUTH_REQUIRED", "message": "Authentication required."},
                401,
            )
        return envelope(
            {
                "admin": {
                    "id": user["admin_user_id"],
                    "email": user["email"],
                    "role": user["role"],
                }
            }
        )

    @api.route("/api/v1/admin/dashboard")
    def dashboard():
        _user, err = require_role("admin", "auditor")
        if err:
            return err
        counts = {
            k: query(v, one=True)["value"]
            for k, v in {
                "citizens": "SELECT COUNT(*) AS value FROM citizens WHERE archived_at IS NULL",
                "hospitals": "SELECT COUNT(*) AS value FROM hospitals WHERE status='active'",
                "access_events": "SELECT COUNT(*) AS value FROM patient_access_events",
                "security_events": "SELECT COUNT(*) AS value FROM security_audit_events",
            }.items()
        }
        return envelope(counts)

    def page_rows(sql, args=()):
        try:
            page = max(1, min(int(request.args.get("page", 1)), 100000))
            limit = max(1, min(int(request.args.get("limit", 25)), 100))
        except ValueError:
            raise ValueError("Invalid pagination")
        rows = query(sql + " LIMIT ? OFFSET ?", (*args, limit, (page - 1) * limit))
        return {"items": [dict(r) for r in rows], "page": page, "limit": limit}

    @api.route("/api/v1/citizens", methods=["GET", "POST"])
    def citizens():
        if request.method == "GET":
            _, err = require_role("admin", "auditor")
            if err:
                return err
            q = (request.args.get("q") or "").strip()
            base = "SELECT * FROM citizens WHERE archived_at IS NULL"
            args = []
            if q:
                base += " AND (lower(first_name) LIKE ? OR lower(last_name) LIKE ?)"
                args += ["%" + q.lower() + "%"] * 2
            base += " ORDER BY last_name,first_name"
            result = page_rows(base, args)
            result["items"] = [
                citizen_view(x)
                for x in query(
                    base + " LIMIT ? OFFSET ?",
                    (*args, result["limit"], (result["page"] - 1) * result["limit"]),
                )
            ]
            return envelope(result)
        user, err = require_role("admin")
        if err:
            return err
        body = request.get_json(silent=True) or {}
        try:
            nid = validate_string(body.get("national_id"), "national_id", 200)
            first = validate_string(body.get("first_name"), "first_name", 100)
            last = validate_string(body.get("last_name"), "last_name", 100)
            if not nid or len(nid) < 2:
                raise ValueError("national_id is invalid")
            gender = body.get("gender")
            blood = body.get("blood_type", "unknown")
            allowedg = {
                "female",
                "male",
                "non_binary",
                "other",
                "undisclosed",
                "unknown",
                None,
            }
            allowedb = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "unknown"}
            if gender not in allowedg or blood not in allowedb:
                raise ValueError("gender or blood_type is invalid")
            ident = uid()
            t = now()
            suffix = nid[-4:]
            execute(
                "INSERT INTO citizens VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    encrypt_id(nid),
                    id_hmac(nid),
                    suffix,
                    first,
                    last,
                    body.get("date_of_birth"),
                    gender,
                    blood,
                    t,
                    t,
                    None,
                ),
            )
            audit(
                "citizen.create",
                "success",
                "admin",
                user["admin_user_id"],
                None,
                "citizen",
                ident,
            )
            return envelope(
                citizen_view(
                    query("SELECT * FROM citizens WHERE id=?", (ident,), one=True)
                ),
                None,
                201,
            )
        except ValueError as e:
            return envelope(None, {"code": "VALIDATION_ERROR", "message": str(e)}, 422)
        except Exception as e:
            if "UNIQUE" in str(e):
                return envelope(
                    None,
                    {
                        "code": "CONFLICT",
                        "message": "A citizen with that identifier already exists.",
                    },
                    409,
                )
            raise

    @api.route("/api/v1/citizens/<cid>", methods=["GET", "PATCH"])
    def citizen(cid):
        user, err = (
            require_role("admin", "auditor")
            if request.method == "GET"
            else require_role("admin")
        )
        if err:
            return err
        row = query(
            "SELECT * FROM citizens WHERE id=? AND archived_at IS NULL",
            (cid,),
            one=True,
        )
        if not row:
            return envelope(
                None, {"code": "NOT_FOUND", "message": "Citizen not found."}, 404
            )
        if request.method == "GET":
            return envelope(citizen_view(row, True))
        body = request.get_json(silent=True) or {}
        fields = []
        args = []
        for key, maxlen in [
            ("first_name", 100),
            ("last_name", 100),
            ("date_of_birth", 10),
            ("gender", 20),
            ("blood_type", 10),
        ]:
            if key in body:
                val = body[key]
                if key in ("first_name", "last_name"):
                    val = validate_string(val, key, maxlen)
                fields.append(key + "=?")
                args.append(val)
        if "national_id" in body:
            nid = validate_string(body["national_id"], "national_id", 200)
            fields += [
                "national_id_ciphertext=?",
                "national_id_lookup_hmac=?",
                "national_id_mask_suffix=?",
            ]
            args += [encrypt_id(nid), id_hmac(nid), nid[-4:]]
        if not fields:
            return envelope(
                None,
                {"code": "VALIDATION_ERROR", "message": "No editable fields supplied."},
                422,
            )
        fields += ["updated_at=?"]
        args += [now(), cid]
        execute("UPDATE citizens SET " + ",".join(fields) + " WHERE id=?", args)
        audit(
            "citizen.update",
            "success",
            "admin",
            user["admin_user_id"],
            None,
            "citizen",
            cid,
        )
        return envelope(
            citizen_view(query("SELECT * FROM citizens WHERE id=?", (cid,), one=True))
        )

    @api.route("/api/v1/citizens/<cid>/history", methods=["GET", "POST"])
    def history(cid):
        user, err = (
            require_role("admin", "auditor")
            if request.method == "GET"
            else require_role("admin")
        )
        if err:
            return err
        if not query(
            "SELECT id FROM citizens WHERE id=? AND archived_at IS NULL",
            (cid,),
            one=True,
        ):
            return envelope(
                None, {"code": "NOT_FOUND", "message": "Citizen not found."}, 404
            )
        if request.method == "GET":
            return envelope(
                page_rows(
                    "SELECT id,category,summary,recorded_at,source_label,is_active FROM medical_history_entries WHERE citizen_id=? ORDER BY recorded_at DESC",
                    (cid,),
                )
            )
        b = request.get_json(silent=True) or {}
        try:
            category = validate_string(b.get("category"), "category", 20)
            summary = validate_string(b.get("summary"), "summary", 2000)
            recorded = b.get("recorded_at") or date.today().isoformat()
        except ValueError as e:
            return envelope(None, {"code": "VALIDATION_ERROR", "message": str(e)}, 422)
        if category not in {"condition", "medication", "procedure", "note"}:
            return envelope(
                None,
                {"code": "VALIDATION_ERROR", "message": "category is invalid"},
                422,
            )
        ident = uid()
        execute(
            "INSERT INTO medical_history_entries VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                ident,
                cid,
                category,
                summary,
                recorded,
                b.get("source_label"),
                True,
                user["admin_user_id"],
                now(),
                now(),
            ),
        )
        return envelope({"id": ident}, None, 201)

    @api.route("/api/v1/citizens/<cid>/allergies", methods=["GET", "POST"])
    def allergies(cid):
        user, err = (
            require_role("admin", "auditor")
            if request.method == "GET"
            else require_role("admin")
        )
        if err:
            return err
        if not query(
            "SELECT id FROM citizens WHERE id=? AND archived_at IS NULL",
            (cid,),
            one=True,
        ):
            return envelope(
                None, {"code": "NOT_FOUND", "message": "Citizen not found."}, 404
            )
        if request.method == "GET":
            return envelope(
                page_rows(
                    "SELECT id,substance,reaction,status,recorded_at FROM patient_allergies WHERE citizen_id=? ORDER BY recorded_at DESC",
                    (cid,),
                )
            )
        b = request.get_json(silent=True) or {}
        try:
            sub = validate_string(b.get("substance"), "substance", 240)
            reaction = validate_string(b.get("reaction"), "reaction", 1000, False)
        except ValueError as e:
            return envelope(None, {"code": "VALIDATION_ERROR", "message": str(e)}, 422)
        status = b.get("status", "unknown")
        if status not in {"active", "inactive", "unknown"}:
            return envelope(
                None, {"code": "VALIDATION_ERROR", "message": "status is invalid"}, 422
            )
        ident = uid()
        execute(
            "INSERT INTO patient_allergies VALUES(?,?,?,?,?,?,?,?,?)",
            (
                ident,
                cid,
                sub,
                reaction,
                status,
                b.get("recorded_at") or date.today().isoformat(),
                user["admin_user_id"],
                now(),
                now(),
            ),
        )
        return envelope({"id": ident}, None, 201)

    @api.route("/api/v1/hospitals", methods=["GET", "POST"])
    def hospitals():
        user, err = (
            require_role("admin", "auditor")
            if request.method == "GET"
            else require_role("admin")
        )
        if err:
            return err
        if request.method == "GET":
            rows = query(
                "SELECT id,code,name,status,allowed_scopes,contact_label,created_at,updated_at,last_seen_at FROM hospitals ORDER BY name"
            )
            out = []
            for r in rows:
                x = dict(r)
                x["allowed_scopes"] = scopes_read(x["allowed_scopes"])
                out.append(x)
            return envelope({"items": out})
        b = request.get_json(silent=True) or {}
        raw_code = b.get("code")
        raw_name = b.get("name")
        code = raw_code.strip().upper() if isinstance(raw_code, str) else ""
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        scopes = b.get("allowed_scopes", ["patient.lookup"])
        if (
            not re.fullmatch(r"[A-Z0-9_-]{2,32}", code)
            or not 2 <= len(name) <= 150
            or not valid_scope_list(scopes)
            or (
                b.get("contact_label") is not None
                and (
                    not isinstance(b["contact_label"], str)
                    or len(b["contact_label"]) > 160
                )
            )
        ):
            return envelope(
                None,
                {"code": "VALIDATION_ERROR", "message": "Invalid hospital fields."},
                422,
            )
        hid = uid()
        execute(
            "INSERT INTO hospitals VALUES(?,?,?,?,?,?,?,?,?)",
            (
                hid,
                code,
                name,
                "active",
                scopes_param(scopes),
                b.get("contact_label"),
                now(),
                now(),
                None,
            ),
        )
        audit(
            "hospital.create",
            "success",
            "admin",
            user["admin_user_id"],
            None,
            "hospital",
            hid,
        )
        return envelope(
            {
                "id": hid,
                "code": code,
                "name": name,
                "status": "active",
                "allowed_scopes": scopes,
            },
            None,
            201,
        )

    @api.route("/api/v1/hospitals/<hid>", methods=["PATCH"])
    def hospital_update(hid):
        _user, err = require_role("admin")
        if err:
            return err
        b = request.get_json(silent=True) or {}
        status = b.get("status")
        name = b.get("name")
        if status not in {"active", "disabled"} and name is None:
            return envelope(
                None,
                {"code": "VALIDATION_ERROR", "message": "Provide status or name."},
                422,
            )
        if status is not None and status not in {"active", "disabled"}:
            return envelope(
                None, {"code": "VALIDATION_ERROR", "message": "Invalid status."}, 422
            )
        sets = []
        args = []
        if status is not None:
            sets.append("status=?")
            args.append(status)
        if name is not None and isinstance(name, str) and 2 <= len(name.strip()) <= 150:
            sets.append("name=?")
            args.append(name.strip())
        elif name is not None:
            return envelope(
                None, {"code": "VALIDATION_ERROR", "message": "Invalid name."}, 422
            )
        sets.append("updated_at=?")
        args.extend([now(), hid])
        execute("UPDATE hospitals SET " + ",".join(sets) + " WHERE id=?", args)
        return envelope({"updated": True})

    @api.route("/api/v1/hospitals/<hid>/credentials", methods=["POST"])
    def credential_issue(hid):
        user, err = require_role("admin")
        if err:
            return err
        hospital_row = query(
            "SELECT allowed_scopes FROM hospitals WHERE id=?", (hid,), one=True
        )
        if not hospital_row:
            return envelope(
                None, {"code": "NOT_FOUND", "message": "Hospital not found."}, 404
            )
        allowed_scopes = scopes_read(hospital_row["allowed_scopes"])
        b = request.get_json(silent=True) or {}
        scopes = b.get("scopes", ["patient.lookup"])
        if not valid_scope_list(scopes, allowed_scopes):
            return envelope(
                None, {"code": "VALIDATION_ERROR", "message": "Invalid scopes."}, 422
            )
        expires_at = b.get("expires_at")
        if expires_at is not None:
            if not isinstance(expires_at, str):
                return envelope(
                    None,
                    {
                        "code": "VALIDATION_ERROR",
                        "message": "Invalid expiry timestamp.",
                    },
                    422,
                )
            try:
                expiry = datetime.fromisoformat(expires_at)
                if expiry.tzinfo is None or expiry <= datetime.now(UTC):
                    raise ValueError(
                        "expiry must be in the future and include a timezone"
                    )
                expires_at = expiry.astimezone(UTC).isoformat()
            except ValueError:
                return envelope(
                    None,
                    {
                        "code": "VALIDATION_ERROR",
                        "message": "Invalid expiry timestamp.",
                    },
                    422,
                )
        keyid = secrets.token_urlsafe(18).replace("-", "_")
        secret = secrets.token_urlsafe(32)
        cid = uid()
        execute(
            "INSERT INTO hospital_credentials VALUES(?,?,?,?,?,?,?,?,?)",
            (
                cid,
                hid,
                keyid,
                hash_secret(secret),
                scopes_param(scopes),
                now(),
                expires_at,
                None,
                user["admin_user_id"],
            ),
        )
        audit(
            "hospital.credential.issue",
            "success",
            "admin",
            user["admin_user_id"],
            None,
            "hospital_credential",
            cid,
        )
        return envelope(
            {
                "credential_id": cid,
                "key_id": keyid,
                "secret": secret,
                "credential": keyid + "." + secret,
                "scopes": scopes,
            },
            None,
            201,
        )

    @api.route("/api/v1/hospitals/<hid>/credentials/<cid>", methods=["POST"])
    def credential_action(hid, cid):
        user, err = require_role("admin")
        if err:
            return err
        action = (request.get_json(silent=True) or {}).get("action", "revoke")
        row = query(
            "SELECT id FROM hospital_credentials WHERE id=? AND hospital_id=?",
            (cid, hid),
            one=True,
        )
        if not row:
            return envelope(
                None, {"code": "NOT_FOUND", "message": "Credential not found."}, 404
            )
        if action == "revoke":
            execute(
                "UPDATE hospital_credentials SET revoked_at=? WHERE id=?", (now(), cid)
            )
            audit(
                "hospital.credential.revoke",
                "success",
                "admin",
                user["admin_user_id"],
                None,
                "hospital_credential",
                cid,
            )
            return envelope({"revoked": True})
        return envelope(
            None,
            {
                "code": "VALIDATION_ERROR",
                "message": "Use POST on hospital credentials to rotate by issuing a replacement.",
            },
            422,
        )

    def events(kind):
        _user, err = require_role("admin", "auditor")
        if err:
            return err
        if kind == "access":
            filters = []
            args = []
            if request.args.get("outcome"):
                filters.append("outcome=?")
                args.append(request.args["outcome"])
            if request.args.get("hospital_id"):
                filters.append("hospital_id=?")
                args.append(request.args["hospital_id"])
            where = (" WHERE " + " AND ".join(filters)) if filters else ""
            return envelope(
                page_rows(
                    "SELECT id,request_id,hospital_id,occurred_at,operation,outcome,http_status,citizen_id,identifier_last2,duration_ms,error_code,recognition_method FROM patient_access_events"
                    + where
                    + " ORDER BY occurred_at DESC",
                    args,
                )
            )
        filters = []
        args = []
        if request.args.get("outcome"):
            filters.append("outcome=?")
            args.append(request.args["outcome"])
        if request.args.get("action"):
            filters.append("action=?")
            args.append(request.args["action"])
        where = (" WHERE " + " AND ".join(filters)) if filters else ""
        rows = page_rows(
            "SELECT id,occurred_at,actor_type,actor_admin_id,actor_hospital_id,action,target_type,target_id,outcome,request_id,details FROM security_audit_events"
            + where
            + " ORDER BY occurred_at DESC",
            args,
        )
        for x in rows["items"]:
            if isinstance(x.get("details"), (dict, list)):
                continue
            try:
                x["details"] = json.loads(x["details"])
            except (TypeError, ValueError, json.JSONDecodeError):
                x["details"] = {}
        return envelope(rows)

    api.add_url_rule(
        "/api/v1/access-events",
        "access_events",
        lambda: events("access"),
        methods=["GET"],
    )
    api.add_url_rule(
        "/api/v1/security-events",
        "security_events",
        lambda: events("security"),
        methods=["GET"],
    )

    @api.route("/api/v1/hospital/enrollments/verify", methods=["POST"])
    def enrollment_verify():
        hospital, err = require_hospital("patient.enroll")
        if err:
            return err
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("citizen_id"), str):
            access_event(
                hospital["hospital_id"],
                hospital["id"],
                "invalid_request",
                422,
                error_code="INVALID_CITIZEN_ID",
                operation="enrollment_verify",
                method=None,
            )
            return envelope(
                None,
                {"code": "VALIDATION_ERROR", "message": "A citizen UUID is required."},
                422,
            )
        try:
            citizen_id = str(uuid.UUID(body["citizen_id"]))
        except ValueError:
            access_event(
                hospital["hospital_id"],
                hospital["id"],
                "invalid_request",
                422,
                error_code="INVALID_CITIZEN_ID",
                operation="enrollment_verify",
                method=None,
            )
            return envelope(
                None,
                {"code": "VALIDATION_ERROR", "message": "citizen_id must be a UUID."},
                422,
            )
        if not query(
            "SELECT id FROM citizens WHERE id=? AND archived_at IS NULL",
            (citizen_id,),
            one=True,
        ):
            access_event(
                hospital["hospital_id"],
                hospital["id"],
                "not_found",
                404,
                identifier=citizen_id,
                operation="enrollment_verify",
                method=None,
            )
            audit(
                "hospital.enrollment_verify",
                "failure",
                "hospital",
                hospital_id=hospital["hospital_id"],
                target_type="citizen",
                target_id=citizen_id,
                details={"outcome": "not_found"},
            )
            return envelope(
                None,
                {"code": "NOT_FOUND", "message": "No matching record found."},
                404,
            )
        access_event(
            hospital["hospital_id"],
            hospital["id"],
            "success",
            200,
            citizen_id=citizen_id,
            operation="enrollment_verify",
            method=None,
        )
        audit(
            "hospital.enrollment_verify",
            "success",
            "hospital",
            hospital_id=hospital["hospital_id"],
            target_type="citizen",
            target_id=citizen_id,
            details={"outcome": "record_exists"},
        )
        return envelope({"citizen_id": citizen_id, "enrollment_allowed": True})

    @api.route("/api/v1/hospital/patient-lookups", methods=["POST"])
    def patient_lookup():
        hospital, err = require_hospital()
        if err:
            return err
        started = time.monotonic()
        b = request.get_json(silent=True)
        if not isinstance(b, dict):
            access_event(
                hospital["hospital_id"],
                hospital["id"],
                "invalid_request",
                400,
                error_code="INVALID_JSON",
            )
            return envelope(
                None,
                {"code": "VALIDATION_ERROR", "message": "JSON object required."},
                422,
            )
        keys = [k for k in ("citizen_id", "national_id") if b.get(k) is not None]
        if (
            len(keys) != 1
            or not isinstance(b[keys[0]], str)
            or not b[keys[0]].strip()
            or len(b[keys[0]]) > 128
        ):
            access_event(
                hospital["hospital_id"],
                hospital["id"],
                "invalid_request",
                422,
                error_code="EXACTLY_ONE_IDENTIFIER",
            )
            return envelope(
                None,
                {
                    "code": "VALIDATION_ERROR",
                    "message": "Provide exactly one citizen_id or national_id.",
                },
                422,
            )
        identifier = b["national_id"].strip() if "national_id" in keys else None
        method = (
            "manual_national_id" if identifier is not None else "local_face_candidate"
        )
        citizen_id = None
        if "citizen_id" in keys:
            try:
                citizen_id = str(uuid.UUID(b["citizen_id"]))
            except ValueError:
                access_event(
                    hospital["hospital_id"],
                    hospital["id"],
                    "invalid_request",
                    422,
                    error_code="INVALID_CITIZEN_ID",
                    method=method,
                )
                return envelope(
                    None,
                    {
                        "code": "VALIDATION_ERROR",
                        "message": "citizen_id must be a UUID.",
                    },
                    422,
                )
        row = lookup_patient(identifier, citizen_id)
        duration = int((time.monotonic() - started) * 1000)
        if not row:
            access_event(
                hospital["hospital_id"],
                hospital["id"],
                "not_found",
                404,
                identifier=identifier,
                duration_ms=duration,
                method=method,
            )
            audit(
                "hospital.patient_lookup",
                "failure",
                "hospital",
                hospital_id=hospital["hospital_id"],
                details={"outcome": "not_found"},
            )
            return envelope(
                None, {"code": "NOT_FOUND", "message": "No matching record found."}, 404
            )
        access_event(
            hospital["hospital_id"],
            hospital["id"],
            "success",
            200,
            identifier=identifier,
            citizen_id=row["id"],
            duration_ms=duration,
            method=method,
        )
        audit(
            "hospital.patient_lookup",
            "success",
            "hospital",
            hospital_id=hospital["hospital_id"],
            target_type="citizen",
            target_id=row["id"],
        )
        health_allowed = "health.read" in scopes_read(hospital["scopes"])
        allergies = None
        limited_history = None
        blood_type = None
        if health_allowed:
            allergies = [
                dict(x)
                for x in query(
                    "SELECT substance AS name,reaction,status,recorded_at FROM patient_allergies WHERE citizen_id=? ORDER BY recorded_at DESC",
                    (row["id"],),
                )
            ]
            limited_history = [
                dict(x)
                for x in query(
                    "SELECT summary AS label,recorded_at FROM medical_history_entries WHERE citizen_id=? AND is_active=TRUE ORDER BY recorded_at DESC LIMIT 5",
                    (row["id"],),
                )
            ]
            blood_type = row["blood_type"]
        return envelope(
            {
                "citizen_id": row["id"],
                "display_name": f"{row['first_name']} {row['last_name']}",
                "date_of_birth": row["date_of_birth"],
                "blood_type": blood_type,
                "allergies": allergies,
                "limited_history": limited_history,
                "recorded_at": row["updated_at"],
                "record_status": "active",
            }
        )

    return app
