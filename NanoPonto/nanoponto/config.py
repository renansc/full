from __future__ import annotations

import os
import tempfile
from pathlib import Path

from sqlalchemy.engine import URL


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WINDOWS_DATA_DIR = Path(os.getenv("LOCALAPPDATA", tempfile.gettempdir())) / "NanoPonto"
DEFAULT_DATABASE_PATH = (WINDOWS_DATA_DIR if os.name == "nt" else DATA_DIR) / "nanoponto.db"


def resolve_database_url() -> str:
    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        return configured_url

    mysql_host = os.getenv("MYSQL_HOST", "").strip()
    if mysql_host:
        return URL.create(
            drivername="mysql+pymysql",
            username=os.getenv("MYSQL_USER", "nanoponto"),
            password=os.getenv("MYSQL_PASSWORD", "nanoponto"),
            host=mysql_host,
            port=int(os.getenv("MYSQL_PORT", "3306")),
            database=os.getenv("MYSQL_DATABASE", "nanoponto"),
            query={"charset": "utf8mb4"},
        ).render_as_string(hide_password=False)

    return f"sqlite:///{DEFAULT_DATABASE_PATH}"


class Config:
    APP_NAME = os.getenv("APP_NAME", "NanoPonto")
    SECRET_KEY = os.getenv("SECRET_KEY", "nanoponto-dev-key")
    DATA_DIR = DATA_DIR
    DATABASE_URL = resolve_database_url()
    DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/Sao_Paulo")
    ALLOW_SYSTEM_TIME_FALLBACK = os.getenv("ALLOW_SYSTEM_TIME_FALLBACK", "0") == "1"
    DEFAULT_EMPLOYER_NAME = os.getenv("DEFAULT_EMPLOYER_NAME", "Nanotech")
    DEFAULT_EMPLOYER_DOCUMENT = os.getenv("DEFAULT_EMPLOYER_DOCUMENT", "4625190000100")
    DEFAULT_EMPLOYER_DOCUMENT_TYPE = os.getenv("DEFAULT_EMPLOYER_DOCUMENT_TYPE", "1")
    DEFAULT_EMPLOYER_WORKPLACE = os.getenv("DEFAULT_EMPLOYER_WORKPLACE", "Matriz")
    DEFAULT_EMPLOYEE_NAME = os.getenv("DEFAULT_EMPLOYEE_NAME", "Renan Santos Coutinho")
    DEFAULT_EMPLOYEE_CPF = os.getenv("DEFAULT_EMPLOYEE_CPF", "06587583903")
    DEFAULT_EMPLOYEE_CODE = os.getenv("DEFAULT_EMPLOYEE_CODE", "E001")
    DEFAULT_EMPLOYEE_BADGE_ID = os.getenv("DEFAULT_EMPLOYEE_BADGE_ID", "")
    DEFAULT_EMPLOYEE_EMAIL = os.getenv("DEFAULT_EMPLOYEE_EMAIL", "")
    NTP_SERVERS = (
        "200.20.186.75",
        "200.20.186.94",
        "200.20.224.100",
        "200.20.224.101",
    )
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "no-reply@nanoponto.local")
    ATESTADO_UPLOAD_DIR = Path(os.getenv("ATESTADO_UPLOAD_DIR", str(DATA_DIR / "atestados")))
    MAX_ATESTADO_UPLOAD_MB = int(os.getenv("MAX_ATESTADO_UPLOAD_MB", "10"))
    MAX_CONTENT_LENGTH = MAX_ATESTADO_UPLOAD_MB * 1024 * 1024
