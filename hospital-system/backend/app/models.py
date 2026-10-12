import uuid

from .db import connection


def user_by_email(email):
    with connection() as c:
        return c.execute(
            "SELECT * FROM hospital_users WHERE lower(email)=lower(?)", (email,)
        ).fetchone()


def user_by_id(user_id):
    with connection() as c:
        return c.execute(
            "SELECT * FROM hospital_users WHERE id=?", (user_id,)
        ).fetchone()


def session_by_token_hash(token_hash):
    with connection() as c:
        return c.execute(
            "SELECT s.*,u.email,u.role,u.is_active FROM hospital_sessions s JOIN hospital_users u ON u.id=s.hospital_user_id WHERE s.token_hash=? AND s.revoked_at IS NULL",
            (token_hash,),
        ).fetchone()


def candidate_by_id(candidate_id):
    try:
        uuid.UUID(candidate_id)
    except (ValueError, TypeError, AttributeError):
        return None
    with connection() as c:
        return c.execute(
            "SELECT * FROM local_patient_subjects WHERE id=? AND status='active'",
            (candidate_id,),
        ).fetchone()


def scan_rows(limit=50, offset=0):
    with connection() as c:
        return c.execute(
            "SELECT id,started_at,completed_at,method,outcome,score,model_version,central_status,error_code,candidate_citizen_id FROM local_scan_events ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
