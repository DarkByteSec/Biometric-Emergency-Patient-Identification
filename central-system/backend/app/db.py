import json
import sqlite3
import uuid
from datetime import UTC, date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import current_app, g


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return str(uuid.uuid4())


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS admin_users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','auditor')),is_active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_login_at TEXT);
CREATE TABLE IF NOT EXISTS admin_sessions(id TEXT PRIMARY KEY,admin_user_id TEXT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,token_hash BLOB UNIQUE NOT NULL,csrf_secret_hash BLOB NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,last_seen_at TEXT,revoked_at TEXT);
CREATE TABLE IF NOT EXISTS citizens(id TEXT PRIMARY KEY,national_id_ciphertext BLOB NOT NULL,national_id_lookup_hmac BLOB UNIQUE NOT NULL,national_id_mask_suffix TEXT,first_name TEXT NOT NULL,last_name TEXT NOT NULL,date_of_birth TEXT,gender TEXT,blood_type TEXT NOT NULL DEFAULT 'unknown',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,archived_at TEXT);
CREATE TABLE IF NOT EXISTS medical_history_entries(id TEXT PRIMARY KEY,citizen_id TEXT NOT NULL REFERENCES citizens(id) ON DELETE RESTRICT,category TEXT NOT NULL,summary TEXT NOT NULL,recorded_at TEXT NOT NULL,source_label TEXT,is_active INTEGER NOT NULL DEFAULT 1,created_by TEXT REFERENCES admin_users(id) ON DELETE SET NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS patient_allergies(id TEXT PRIMARY KEY,citizen_id TEXT NOT NULL REFERENCES citizens(id) ON DELETE RESTRICT,substance TEXT NOT NULL,reaction TEXT,status TEXT NOT NULL,recorded_at TEXT NOT NULL,created_by TEXT REFERENCES admin_users(id) ON DELETE SET NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hospitals(id TEXT PRIMARY KEY,code TEXT UNIQUE NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',allowed_scopes TEXT NOT NULL,contact_label TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_seen_at TEXT);
CREATE TABLE IF NOT EXISTS hospital_credentials(id TEXT PRIMARY KEY,hospital_id TEXT NOT NULL REFERENCES hospitals(id) ON DELETE RESTRICT,key_id TEXT UNIQUE NOT NULL,secret_hash BLOB NOT NULL,scopes TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT,revoked_at TEXT,created_by TEXT REFERENCES admin_users(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS patient_access_events(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,hospital_id TEXT NOT NULL REFERENCES hospitals(id) ON DELETE RESTRICT,credential_id TEXT REFERENCES hospital_credentials(id) ON DELETE SET NULL,occurred_at TEXT NOT NULL,operation TEXT NOT NULL,outcome TEXT NOT NULL,http_status INTEGER,citizen_id TEXT REFERENCES citizens(id) ON DELETE SET NULL,identifier_fingerprint BLOB,identifier_last2 TEXT,duration_ms INTEGER,error_code TEXT,recognition_method TEXT,model_version TEXT);
CREATE TABLE IF NOT EXISTS biometric_registrations(id TEXT PRIMARY KEY,hospital_id TEXT NOT NULL REFERENCES hospitals(id) ON DELETE RESTRICT,citizen_id TEXT NOT NULL REFERENCES citizens(id) ON DELETE RESTRICT,status TEXT NOT NULL,algorithm TEXT NOT NULL,model_version TEXT NOT NULL,sample_count INTEGER NOT NULL DEFAULT 0,registered_at TEXT NOT NULL,updated_at TEXT NOT NULL,created_by_label TEXT,UNIQUE(hospital_id,citizen_id));
CREATE TABLE IF NOT EXISTS security_audit_events(id TEXT PRIMARY KEY,occurred_at TEXT NOT NULL,actor_type TEXT NOT NULL,actor_admin_id TEXT REFERENCES admin_users(id) ON DELETE SET NULL,actor_hospital_id TEXT REFERENCES hospitals(id) ON DELETE SET NULL,action TEXT NOT NULL,target_type TEXT,target_id TEXT,outcome TEXT NOT NULL,request_id TEXT,details TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS citizens_name_search_idx ON citizens(last_name,first_name);
CREATE INDEX IF NOT EXISTS access_time_idx ON patient_access_events(occurred_at);
CREATE INDEX IF NOT EXISTS audit_time_idx ON security_audit_events(occurred_at);
"""


POSTGRES_TABLES = (
    "admin_users",
    "admin_sessions",
    "citizens",
    "medical_history_entries",
    "patient_allergies",
    "hospitals",
    "hospital_credentials",
    "patient_access_events",
    "biometric_registrations",
    "security_audit_events",
)


def _postgres_url(url):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    mode = query.get("sslmode", "").lower()
    if mode in {"disable", "allow", "prefer"}:
        raise RuntimeError("CENTRAL_DATABASE_URL must require TLS")
    if not mode:
        query["sslmode"] = "require"
    return urlunsplit(parts._replace(query=urlencode(query)))


def _normalize(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def connect():
    url = current_app.config["DATABASE_URL"]
    if url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path != ":memory:" and not path.startswith("/"):
            path = str(current_app.instance_path / path)
        db = sqlite3.connect(path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db
    if url.startswith(("postgresql://", "postgres://")):
        try:
            import psycopg
            from psycopg.rows import dict_row

            db = psycopg.connect(
                _postgres_url(url), row_factory=dict_row, connect_timeout=5
            )
        except ImportError as e:
            raise RuntimeError("PostgreSQL support requires psycopg[binary]") from e
        try:
            login = db.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname=session_user"
            ).fetchone()
            if login and login["rolsuper"]:
                raise RuntimeError(
                    "Use a dedicated Central runtime login, not the project-owner/superuser account"
                )
            db.execute("SET ROLE central_app")
            columns = ", ".join(
                f"to_regclass('public.{name}') IS NOT NULL AS {name}"
                for name in POSTGRES_TABLES
            )
            schema = db.execute(f"SELECT {columns}").fetchone()
            missing = [name for name in POSTGRES_TABLES if not schema[name]]
            if missing:
                raise RuntimeError(
                    "Central PostgreSQL schema is not migrated; missing tables: "
                    + ", ".join(missing)
                )
            return db
        except Exception:
            db.close()
            raise
    raise RuntimeError("CENTRAL_DATABASE_URL must use SQLite or PostgreSQL")


def get_db():
    if "db" not in g:
        g.db = connect()
        if isinstance(g.db, sqlite3.Connection):
            g.db.executescript(SCHEMA)
            g.db.commit()
    return g.db


def _bound_sql(sql):
    return sql if isinstance(get_db(), sqlite3.Connection) else sql.replace("?", "%s")


def _bound_args(sql, args):
    if isinstance(get_db(), sqlite3.Connection) or "security_audit_events" not in sql:
        return args
    if not args or not isinstance(args[-1], str):
        return args
    from psycopg.types.json import Jsonb

    values = list(args)
    values[-1] = Jsonb(json.loads(values[-1]))
    return tuple(values)


def close_db(_=None):
    db = g.pop("db", None)
    if db:
        db.close()


def query(sql, args=(), one=False):
    db = get_db()
    cur = db.execute(_bound_sql(sql), _bound_args(sql, args))
    rows = cur.fetchall()
    cur.close()
    if not isinstance(db, sqlite3.Connection):
        rows = [_normalize(dict(row)) for row in rows]
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(_bound_sql(sql), _bound_args(sql, args))
    result = getattr(cur, "lastrowid", None)
    db.commit()
    cur.close()
    return result


def jsonable(row):
    return dict(row) if row else None


def scopes_param(values):
    return (
        values if not isinstance(get_db(), sqlite3.Connection) else json.dumps(values)
    )


def scopes_read(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return [x for x in value.strip("{}").split(",") if x]
    return list(value or [])
