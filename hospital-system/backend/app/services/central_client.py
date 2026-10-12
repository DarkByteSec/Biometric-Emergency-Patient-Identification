import uuid
from dataclasses import dataclass
from urllib.parse import urljoin

import requests


@dataclass
class CentralResult:
    kind: str
    status: int | None = None
    request_id: str | None = None
    data: dict | None = None
    code: str | None = None


class CentralClient:
    def __init__(self, app):
        self.base = app.config["CENTRAL_API_BASE_URL"].rstrip("/") + "/"
        self.key_id = app.config["HOSPITAL_CREDENTIAL_KEY_ID"]
        self.secret = app.config["HOSPITAL_CREDENTIAL_SECRET"]
        self.timeout = (3.0, 8.0)
        self.session = requests.Session()

    def _headers(self):
        return {
            "Authorization": "Bearer " + self.key_id + "." + self.secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def lookup(self, payload):
        if set(payload) - {"national_id", "citizen_id"} or len(payload) != 1:
            return CentralResult("validation", 400, code="invalid_lookup")
        try:
            r = self.session.post(
                urljoin(self.base, "api/v1/hospital/patient-lookups"),
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
                verify=True,
            )
        except requests.Timeout:
            return CentralResult("timeout", 504, code="central_timeout")
        except requests.RequestException:
            return CentralResult("unavailable", 503, code="central_unavailable")
        req = r.headers.get("X-Request-ID")
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 404:
            return CentralResult("not_found", 404, req, code="not_found")
        if r.status_code == 401:
            return CentralResult("denied", 401, req, code="central_unauthorized")
        if r.status_code == 403:
            return CentralResult("denied", 403, req, code="central_forbidden")
        if 500 <= r.status_code <= 599:
            return CentralResult("unavailable", r.status_code, req, code="central_5xx")
        if not 200 <= r.status_code < 300:
            return CentralResult("error", r.status_code, req, code="central_error")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return CentralResult(
                "error", r.status_code, req, code="invalid_central_response"
            )
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
        return CentralResult(
            "success", r.status_code, req, {k: data[k] for k in data if k in allow}
        )

    def verify_enrollment(self, citizen_id):
        try:
            canonical_id = str(uuid.UUID(citizen_id))
        except (AttributeError, TypeError, ValueError):
            return CentralResult("validation", 400, code="invalid_citizen_id")
        try:
            response = self.session.post(
                urljoin(self.base, "api/v1/hospital/enrollments/verify"),
                json={"citizen_id": canonical_id},
                headers=self._headers(),
                timeout=self.timeout,
                verify=True,
            )
        except requests.Timeout:
            return CentralResult("timeout", 504, code="central_timeout")
        except requests.RequestException:
            return CentralResult("unavailable", 503, code="central_unavailable")
        request_id = response.headers.get("X-Request-ID")
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 404:
            return CentralResult("not_found", 404, request_id, code="not_found")
        if response.status_code == 401:
            return CentralResult("denied", 401, request_id, code="central_unauthorized")
        if response.status_code == 403:
            return CentralResult("denied", 403, request_id, code="central_forbidden")
        if 500 <= response.status_code <= 599:
            return CentralResult(
                "unavailable", response.status_code, request_id, code="central_5xx"
            )
        if not 200 <= response.status_code < 300:
            return CentralResult(
                "error", response.status_code, request_id, code="central_error"
            )
        data = body.get("data") if isinstance(body, dict) else None
        if (
            not isinstance(data, dict)
            or data.get("citizen_id") != canonical_id
            or data.get("enrollment_allowed") is not True
        ):
            return CentralResult(
                "error",
                response.status_code,
                request_id,
                code="invalid_central_response",
            )
        return CentralResult(
            "success",
            response.status_code,
            request_id,
            {"citizen_id": canonical_id, "enrollment_allowed": True},
        )
