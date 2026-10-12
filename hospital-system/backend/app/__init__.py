import uuid
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request

from .api import api
from .auth import require_csrf
from .config import Config
from .db import init_db


def create_app(config_object=None):
    app = Flask(__name__, instance_relative_config=True)
    if isinstance(config_object, dict):
        app.config.from_mapping(config_object)
    else:
        app.config.from_object(config_object or Config)

    if not app.config.get("TESTING"):
        required = (
            "SECRET_KEY",
            "HOSPITAL_DATABASE_URL",
            "CENTRAL_API_BASE_URL",
            "HOSPITAL_CREDENTIAL_KEY_ID",
            "HOSPITAL_CREDENTIAL_SECRET",
        )
        missing = [key for key in required if not app.config.get(key)]
        if missing:
            raise RuntimeError(
                "Missing required Hospital server configuration: " + ", ".join(missing)
            )
        environment = app.config.get("APP_ENV", "development").lower()
        if environment not in {"development", "production"}:
            raise RuntimeError("APP_ENV must be development or production")
        central = urlsplit(app.config["CENTRAL_API_BASE_URL"])
        local_http = (
            environment == "development"
            and central.scheme == "http"
            and central.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if central.scheme != "https" and not local_http:
            raise RuntimeError(
                "CENTRAL_API_BASE_URL must use HTTPS; loopback HTTP is allowed only in development"
            )
        database_url = app.config["HOSPITAL_DATABASE_URL"].lower()
        if environment == "production":
            if not database_url.startswith(("postgresql://", "postgres://")):
                raise RuntimeError(
                    "Production requires the migrated PostgreSQL database"
                )
            if len(app.config["SECRET_KEY"]) < 32:
                raise RuntimeError(
                    "Production session signing key must be at least 32 characters"
                )

    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = "testing-only-not-for-deployment"
    app.config["COOKIE_SECURE"] = (
        app.config.get("APP_ENV", "development").lower() == "production"
    )

    init_db(app)
    app.register_blueprint(api, url_prefix="/api/v1")

    @app.before_request
    def before():
        g.request_id = str(uuid.uuid4())
        if (
            request.content_length
            and request.content_length > app.config["MAX_CONTENT_LENGTH"]
        ):
            return jsonify(
                {
                    "error": {
                        "code": "REQUEST_TOO_LARGE",
                        "message": "Request exceeds the configured limit",
                    },
                    "request_id": g.request_id,
                }
            ), 413
        return require_csrf()

    @app.after_request
    def after(resp):
        resp.headers["X-Request-ID"] = g.request_id
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        origin = request.headers.get("Origin")
        if origin and origin in app.config.get("ALLOWED_ORIGINS", []):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Vary"] = "Origin"
        return resp

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(
            {
                "error": {
                    "code": "REQUEST_TOO_LARGE",
                    "message": "Request exceeds the configured limit",
                },
                "request_id": g.get("request_id"),
            }
        ), 413

    return app
