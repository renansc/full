import os

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def normalize_database_url(value, *, env_name="DATABASE_URL"):
    if not value:
        return "sqlite:///zap.db"

    normalized = value.strip()
    if not normalized or normalized.lower() in {"false", "none", "null"}:
        return "sqlite:///zap.db"

    replacements = {
        "postgres://": "postgresql+psycopg2://",
        "postgresql://": "postgresql+psycopg2://",
        "mysql://": "mysql+pymysql://",
        "mariadb://": "mysql+pymysql://",
    }

    for prefix, replacement in replacements.items():
        if normalized.startswith(prefix):
            normalized = normalized.replace(prefix, replacement, 1)
            break

    try:
        make_url(normalized)
    except ArgumentError as exc:
        raise RuntimeError(
            f"{env_name} invalida para o Zap. Use uma URL completa, por exemplo "
            "'postgresql://usuario:senha@host:5432/banco', ou deixe a variavel vazia "
            "para usar SQLite."
        ) from exc
    return normalized


def resolve_database_url():
    explicit_url = os.getenv("ZAP_DATABASE_URL")
    if explicit_url is not None:
        return normalize_database_url(explicit_url, env_name="ZAP_DATABASE_URL")
    return normalize_database_url(os.getenv("DATABASE_URL"), env_name="DATABASE_URL")


class BaseConfig:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "zap_session")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = resolve_database_url()
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "instance/uploads")
    PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
    BACKUP_DATABASE_URL = os.getenv("BACKUP_DATABASE_URL", "")
    TICKET_ARCHIVE_DAYS = int(os.getenv("TICKET_ARCHIVE_DAYS", "2"))
    WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "verify-token")
    WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v20.0")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")
    GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    BOOTSTRAP_ADMIN_NAME = os.getenv("BOOTSTRAP_ADMIN_NAME", "Administrador")
    BOOTSTRAP_ADMIN_EMAIL = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@empresa.com")
    BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")


def get_config():
    return BaseConfig
