import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import current_app


class PGResult:
    def __init__(self, cursor):
        self._cursor = cursor

    @staticmethod
    def _normalize(row):
        if row is None:
            return None

        def value(item):
            if isinstance(item, (datetime, date)):
                return item.isoformat()
            if isinstance(item, uuid.UUID):
                return str(item)
            if isinstance(item, dict):
                return {key: value(nested) for key, nested in item.items()}
            if isinstance(item, list):
                return [value(nested) for nested in item]
            return item

        return {key: value(item) for key, item in row.items()}

    def fetchone(self):
        return self._normalize(self._cursor.fetchone())

    def fetchall(self):
        return [self._normalize(row) for row in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return getattr(self._cursor, "lastrowid", None)

    def close(self):
        self._cursor.close()


class PGConnection:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        params = tuple(params)
        if "INSERT INTO local_operational_events" in sql and params:
            from psycopg.types.json import Jsonb

            details = params[-1]
            if isinstance(details, str):
                params = (*params[:-1], Jsonb(json.loads(details)))
        cursor = self._conn.execute(sql.replace("?", "%s"), params)
        return PGResult(cursor)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS hospital_profile (id INTEGER PRIMARY KEY CHECK(id=1), hospital_name TEXT NOT NULL, central_hospital_id TEXT NOT NULL UNIQUE, central_base_url TEXT NOT NULL, config_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hospital_users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','operator')), is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_login_at TEXT);
CREATE TABLE IF NOT EXISTS hospital_sessions (id TEXT PRIMARY KEY, hospital_user_id TEXT NOT NULL REFERENCES hospital_users(id) ON DELETE CASCADE, token_hash BLOB NOT NULL UNIQUE, csrf_secret_hash BLOB NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_seen_at TEXT, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS local_patient_subjects (id TEXT PRIMARY KEY, central_citizen_id TEXT NOT NULL, recognizer_label INTEGER NOT NULL UNIQUE, status TEXT NOT NULL CHECK(status IN ('active','inactive','needs_review')), model_version TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS local_biometric_samples (id TEXT PRIMARY KEY, subject_id TEXT NOT NULL REFERENCES local_patient_subjects(id) ON DELETE CASCADE, file_key TEXT NOT NULL UNIQUE, captured_at TEXT NOT NULL, image_width INTEGER NOT NULL, image_height INTEGER NOT NULL, quality_status TEXT NOT NULL CHECK(quality_status IN ('accepted','rejected','needs_review')), deleted_at TEXT);
CREATE TABLE IF NOT EXISTS local_scan_events (id TEXT PRIMARY KEY, hospital_user_id TEXT REFERENCES hospital_users(id) ON DELETE SET NULL, started_at TEXT NOT NULL, completed_at TEXT, method TEXT NOT NULL CHECK(method IN ('face','national_id')), outcome TEXT NOT NULL, candidate_citizen_id TEXT, score REAL, model_version TEXT, central_request_id TEXT, central_status INTEGER, error_code TEXT);
CREATE TABLE IF NOT EXISTS local_operational_events (id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, event_type TEXT NOT NULL, outcome TEXT NOT NULL, request_id TEXT, safe_details TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS hospital_users_active_role_idx ON hospital_users(is_active,role);
CREATE INDEX IF NOT EXISTS hospital_sessions_user_expiry_idx ON hospital_sessions(hospital_user_id,expires_at);
CREATE INDEX IF NOT EXISTS hospital_sessions_active_expiry_idx ON hospital_sessions(expires_at) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS local_patient_subjects_central_id_idx ON local_patient_subjects(central_citizen_id);
CREATE INDEX IF NOT EXISTS local_patient_subjects_status_idx ON local_patient_subjects(status);
CREATE INDEX IF NOT EXISTS local_biometric_samples_subject_date_idx ON local_biometric_samples(subject_id,captured_at DESC);
CREATE INDEX IF NOT EXISTS local_scan_events_time_idx ON local_scan_events(started_at DESC);
CREATE INDEX IF NOT EXISTS local_scan_events_outcome_time_idx ON local_scan_events(outcome,started_at DESC);
CREATE INDEX IF NOT EXISTS local_scan_events_operator_time_idx ON local_scan_events(hospital_user_id,started_at DESC);
CREATE INDEX IF NOT EXISTS local_scan_events_candidate_time_idx ON local_scan_events(candidate_citizen_id,started_at DESC) WHERE candidate_citizen_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS local_scan_events_request_idx ON local_scan_events(central_request_id) WHERE central_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS local_operational_events_time_idx ON local_operational_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS local_operational_events_type_time_idx ON local_operational_events(event_type,occurred_at DESC);
"""

HOSPITAL_TABLES = (
    "hospital_profile",
    "hospital_users",
    "hospital_sessions",
    "local_patient_subjects",
    "local_biometric_samples",
    "local_scan_events",
    "local_operational_events",
)


def _postgres_url(url):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    mode = query.get("sslmode", "").lower()
    if mode in {"disable", "allow", "prefer"}:
        raise RuntimeError("HOSPITAL_DATABASE_URL must require TLS")
    if not mode:
        query["sslmode"] = "require"
    return urlunsplit(parts._replace(query=urlencode(query)))


def now():
    return datetime.now().astimezone().isoformat()


def new_id():
    return str(uuid.uuid4())


def init_db(app):
    url = app.config.get("HOSPITAL_DATABASE_URL", "")
    path = app.config.get("DB_PATH") or url
    path = path.removeprefix("sqlite:///")
    path = path.removeprefix("sqlite:")
    if url.startswith(("postgresql://", "postgres://")):
        try:
            import psycopg
            from psycopg.rows import dict_row

            conn = psycopg.connect(
                _postgres_url(url), row_factory=dict_row, connect_timeout=5
            )
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL support requires psycopg; install backend requirements.txt"
            ) from exc
        try:
            login = conn.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname=session_user"
            ).fetchone()
            if login and login["rolsuper"]:
                raise RuntimeError(
                    "Use a dedicated Hospital runtime login, not the project-owner/superuser account"
                )
            conn.execute("SET ROLE hospital_app")
            columns = ", ".join(
                f"to_regclass('public.{name}') IS NOT NULL AS {name}"
                for name in HOSPITAL_TABLES
            )
            schema = conn.execute(f"SELECT {columns}").fetchone()
            missing = [name for name in HOSPITAL_TABLES if not schema[name]]
            if missing:
                raise RuntimeError(
                    "Hospital PostgreSQL schema is not migrated; missing tables: "
                    + ", ".join(missing)
                )
            app.extensions["hospital_db"] = PGConnection(conn)
            return
        except Exception:
            conn.close()
            raise
    if url.startswith(("postgresql:", "postgres:")):
        raise RuntimeError("HOSPITAL_DATABASE_URL must use a PostgreSQL URI")
    if not path:
        path = str(app.instance_path / "hospital.sqlite3")
    if path != ":memory:":
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    app.extensions["hospital_db"] = conn


@contextmanager
def connection():
    conn = current_app.extensions.get("hospital_db")
    if conn is None:
        raise RuntimeError("Hospital database adapter is not initialized")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def add_scan(**kwargs):
    fields = {"id": new_id(), "started_at": now(), "completed_at": now(), **kwargs}
    keys = (
        "id",
        "hospital_user_id",
        "started_at",
        "completed_at",
        "method",
        "outcome",
        "candidate_citizen_id",
        "score",
        "model_version",
        "central_request_id",
        "central_status",
        "error_code",
    )
    with connection() as conn:
        conn.execute(
            "INSERT INTO local_scan_events ("
            + ",".join(keys)
            + ") VALUES ("
            + ",".join("?" for _ in keys)
            + ")",
            tuple(fields.get(key) for key in keys),
        )
    return fields["id"]


def add_event(event_type, outcome, request_id, details=None):
    with connection() as conn:
        conn.execute(
            "INSERT INTO local_operational_events (id,occurred_at,event_type,outcome,request_id,safe_details) VALUES (?,?,?,?,?,?)",
            (
                new_id(),
                now(),
                event_type,
                outcome,
                request_id,
                json.dumps(details or {}, separators=(",", ":")),
            ),
        )
