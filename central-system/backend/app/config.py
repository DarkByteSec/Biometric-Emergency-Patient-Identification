import base64
import binascii
import json
import os
import secrets
from pathlib import Path


class Config:
    APP_ENV = os.getenv("APP_ENV", "development").lower()
    TESTING = os.getenv("TESTING", "false").lower() == "true"
    DATABASE_URL = os.getenv("CENTRAL_DATABASE_URL", "sqlite:///central-demo.db")
    SECRET_KEY = os.getenv("CENTRAL_SESSION_SIGNING_KEY")
    MAX_CONTENT_LENGTH = 64 * 1024
    SESSION_COOKIE_NAME = "central_session"
    SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "1800"))
    CORS_ORIGINS = tuple(
        origin.strip()
        for origin in os.getenv("CENTRAL_CORS_ORIGINS", "http://localhost:5173").split(
            ","
        )
        if origin.strip()
    )
    ENCRYPTION_KEY = os.getenv("CENTRAL_ENCRYPTION_KEY")
    LOOKUP_HMAC_KEY = os.getenv("CENTRAL_LOOKUP_HMAC_KEY")
    ALLOW_DEMO_SEED = os.getenv("ALLOW_DEMO_SEED", "false").lower() == "true"

    @classmethod
    def init_app(cls, app):
        app.config.from_object(cls)
        environment = app.config.get("APP_ENV", "development").lower()
        if environment not in {"development", "production"} and not app.config.get(
            "TESTING"
        ):
            raise RuntimeError("APP_ENV must be development or production")

        if environment == "production":
            required = (
                "SECRET_KEY",
                "ENCRYPTION_KEY",
                "LOOKUP_HMAC_KEY",
                "DATABASE_URL",
            )
            missing = [key for key in required if not app.config.get(key)]
            if missing:
                raise RuntimeError(
                    "Missing required production security/database configuration: "
                    + ", ".join(missing)
                )
            if not app.config["DATABASE_URL"].startswith(
                ("postgresql://", "postgres://")
            ):
                raise RuntimeError(
                    "Production requires the migrated PostgreSQL database"
                )
            for name in ("ENCRYPTION_KEY", "LOOKUP_HMAC_KEY"):
                decode_key(app.config[name])

        if environment == "development" and not app.config.get("TESTING"):
            _load_or_create_development_keys(app)
        else:
            if not app.config.get("SECRET_KEY"):
                app.config["SECRET_KEY"] = secrets.token_urlsafe(48)
            if not app.config.get("ENCRYPTION_KEY"):
                app.config["ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
                    secrets.token_bytes(32)
                ).decode()
            if not app.config.get("LOOKUP_HMAC_KEY"):
                app.config["LOOKUP_HMAC_KEY"] = base64.urlsafe_b64encode(
                    secrets.token_bytes(32)
                ).decode()


def _load_or_create_development_keys(app):
    path = Path(app.instance_path) / "development-keys.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    stored = {}
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}

    for key in ("SECRET_KEY", "ENCRYPTION_KEY", "LOOKUP_HMAC_KEY"):
        if not app.config.get(key):
            app.config[key] = stored.get(key)
        if not app.config.get(key):
            app.config[key] = (
                secrets.token_urlsafe(48)
                if key == "SECRET_KEY"
                else base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
            )

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(
            {
                key: app.config[key]
                for key in ("SECRET_KEY", "ENCRYPTION_KEY", "LOOKUP_HMAC_KEY")
            },
            output,
        )
        output.write("\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def decode_key(value: str) -> bytes:
    if not isinstance(value, str):
        raise TypeError("security key must be a string")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) != 32:
        try:
            raw = bytes.fromhex(value)
        except ValueError as error:
            raise ValueError("security key must be base64url or hex") from error
    if len(raw) != 32:
        raise ValueError("security key must decode to 32 bytes")
    return raw
