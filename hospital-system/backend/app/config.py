import os


class Config:
    APP_ENV = os.environ.get("APP_ENV", "development").lower()
    TESTING = os.environ.get("TESTING", "false").lower() == "true"
    SECRET_KEY = os.environ.get("HOSPITAL_SESSION_SIGNING_KEY") or os.environ.get(
        "HOSPITAL_FLASK_SECRET"
    )
    HOSPITAL_DATABASE_URL = os.environ.get(
        "HOSPITAL_DATABASE_URL", "sqlite:///hospital-demo.db"
    )
    CENTRAL_API_BASE_URL = os.environ.get("CENTRAL_API_BASE_URL", "")
    HOSPITAL_CREDENTIAL_KEY_ID = os.environ.get("HOSPITAL_CREDENTIAL_KEY_ID", "")
    HOSPITAL_CREDENTIAL_SECRET = os.environ.get("HOSPITAL_CREDENTIAL_SECRET", "")
    SESSION_TTL_SECONDS = int(os.environ.get("HOSPITAL_SESSION_TTL_SECONDS", "28800"))
    MAX_CONTENT_LENGTH = int(
        os.environ.get("HOSPITAL_MAX_UPLOAD_BYTES", str(9 * 1024 * 1024))
    )
    MAX_IMAGE_PIXELS = int(os.environ.get("HOSPITAL_MAX_IMAGE_PIXELS", "12000000"))
    ALLOWED_ORIGINS = tuple(
        origin.strip()
        for origin in os.environ.get(
            "HOSPITAL_ALLOWED_ORIGINS", "http://localhost:5174"
        ).split(",")
        if origin.strip()
    )
    RECOGNITION_SAMPLE_DIR = os.environ.get("HOSPITAL_RECOGNITION_SAMPLE_DIR", "")
    RECOGNITION_MODEL_PATH = os.environ.get("HOSPITAL_RECOGNITION_MODEL_PATH", "")
    RECOGNITION_MODEL_VERSION = os.environ.get(
        "HOSPITAL_RECOGNITION_MODEL_VERSION", "lbph-local"
    )
    RECOGNITION_DISTANCE_THRESHOLD = os.environ.get(
        "HOSPITAL_RECOGNITION_DISTANCE_THRESHOLD", "70"
    )
    RECOGNITION_CASCADE_PATH = os.environ.get("HOSPITAL_RECOGNITION_CASCADE_PATH", "")
    COOKIE_SECURE = APP_ENV == "production"
