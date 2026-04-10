from __future__ import annotations

import base64
import binascii
import hmac
import json
import mimetypes
import os
import re
import secrets
import smtplib
import ssl
import textwrap
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session
from sqlalchemy import Boolean, Float, Integer, String, Text, create_engine, delete, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.dispatcher import DispatcherMiddleware

try:
    from finance_research import (
        DEFAULT_ALLOWED_DOMAINS,
        DEFAULT_USER_AGENT,
        ScraperError,
        build_scraper_diagnostic,
        run_scraper_purchase_research,
    )
except Exception as exc:  # pragma: no cover - optional dependency guard
    FINANCE_RESEARCH_IMPORT_ERROR = exc
    DEFAULT_ALLOWED_DOMAINS = (
        "mercadolivre.com.br",
        "amazon.com.br",
        "magazineluiza.com.br",
        "kabum.com.br",
        "casasbahia.com.br",
        "pontofrio.com.br",
        "carrefour.com.br",
        "leroymerlin.com.br",
        "madeiramadeira.com.br",
        "pichau.com.br",
    )
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )

    class ScraperError(RuntimeError):
        pass

    def build_scraper_diagnostic(*args, **kwargs):
        return {
            "ok": False,
            "error": "Modulo de pesquisa financeira indisponivel.",
            "details": str(FINANCE_RESEARCH_IMPORT_ERROR),
        }

    def run_scraper_purchase_research(*args, **kwargs):
        raise AppError(503, "Modulo de pesquisa financeira indisponivel.")

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - optional dependency
    Image = None
    ImageOps = None

try:
    import fitz
except ImportError:  # pragma: no cover - optional dependency
    fitz = None

try:
    from pyzbar.pyzbar import decode as pyzbar_decode
except ImportError:  # pragma: no cover - optional dependency
    pyzbar_decode = None


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

STORE_SITE = "site"
STORE_GPS = "gps-musical"
STORE_FINANCE = "financeiro-nanotech"
SUPPORTED_STORES = {STORE_SITE, STORE_GPS, STORE_FINANCE}

DEFAULT_SITE_APPS = [
    {
        "slug": "bpa",
        "nome": "RIS+BPA",
        "descricao": "Prontuario e integracao com o backend do Firebird.",
        "href": "/bpa/",
    },
    {
        "slug": "financeiro",
        "nome": "Financeiro",
        "descricao": "Controle de lancamentos, contas, categorias e conciliacao.",
        "href": "/financeiro/",
    },
    {
        "slug": "gpsmusical",
        "nome": "GPS Musical",
        "descricao": "Gerencie repertorio, letras e blocos musicais.",
        "href": "/gpsmusical/",
    },
    {
        "slug": "tatoo",
        "nome": "Tatoo Studio",
        "descricao": "Cadastro, consentimento, assinatura digital e contratos em PDF.",
        "href": "/tatoo/",
    },
    {
        "slug": "zap",
        "nome": "Zap Workflow",
        "descricao": "Atendimento, vendas e fluxo operacional do WhatsApp.",
        "href": "/zap/",
    },
]

BPA_API_BASE_URL = str(os.getenv("BPA_API_BASE_URL", "http://127.0.0.1:5002")).strip().rstrip("/")
try:
    BPA_API_TIMEOUT_SECONDS = max(5, int(str(os.getenv("BPA_API_TIMEOUT_SECONDS", "20")).strip()))
except ValueError:
    BPA_API_TIMEOUT_SECONDS = 20

PORTAL_PASSWORD = str(os.getenv("PORTAL_PASSWORD", "")).strip()
PORTAL_SESSION_KEY = "portal_authenticated"
PORTAL_USER_KEY = "portal_user"
GITHUB_OAUTH_CLIENT_ID = str(os.getenv("GITHUB_OAUTH_CLIENT_ID", "")).strip()
GITHUB_OAUTH_CLIENT_SECRET = str(os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "")).strip()
GITHUB_OAUTH_CALLBACK_URL = str(os.getenv("GITHUB_OAUTH_CALLBACK_URL", "")).strip()
GITHUB_ALLOWED_USERS = {
    item.strip().lower()
    for item in str(os.getenv("GITHUB_ALLOWED_USERS", "")).split(",")
    if item.strip()
}
GITHUB_OAUTH_STATE_KEY = "github_oauth_state"


class AppError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_bool(value: str | None, fallback: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return fallback
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_size(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None

    normalized = str(value).strip().lower()
    units = {"gb": 1024**3, "mb": 1024**2, "kb": 1024, "b": 1}

    for suffix, factor in units.items():
        if normalized.endswith(suffix):
            amount = float(normalized[: -len(suffix)].strip())
            return int(amount * factor)

    return int(normalized)


def normalize_provider(value: str | None) -> str:
    provider = str(value or "sqlite").strip().lower()
    aliases = {
        "postgresql": "postgres",
        "mariadb": "mysql",
        "file": "sqlite",
    }
    return aliases.get(provider, provider)


def assert_store_id(store_id: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", store_id or ""):
        raise AppError(400, "Invalid store id.")
    return store_id


def assert_table_name(value: str | None) -> str:
    table_name = str(value or "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9_]+", table_name):
        raise RuntimeError("Invalid table name.")
    return table_name


def normalize_database_url(value: str | None) -> str | None:
    if not value:
        return None

    normalized = value.strip()
    replacements = {
        "postgres://": "postgresql+psycopg://",
        "postgresql://": "postgresql+psycopg://",
        "mysql://": "mysql+pymysql://",
        "mariadb://": "mysql+pymysql://",
    }

    for prefix, replacement in replacements.items():
        if normalized.startswith(prefix):
            return normalized.replace(prefix, replacement, 1)

    return normalized


def resolve_data_dir() -> Path:
    configured = os.getenv("DATA_DIR")
    data_dir = Path(configured).expanduser() if configured else BASE_DIR / "data"
    if not data_dir.is_absolute():
        data_dir = BASE_DIR / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def resolve_attachment_dir() -> Path:
    configured = os.getenv("FINANCE_ATTACHMENTS_DIR") or os.getenv("ATTACHMENTS_DIR")
    attachment_dir = Path(configured).expanduser() if configured else BASE_DIR / "dados"
    if not attachment_dir.is_absolute():
        attachment_dir = BASE_DIR / attachment_dir
    attachment_dir.mkdir(parents=True, exist_ok=True)
    return attachment_dir


def resolve_contracts_dir() -> Path:
    configured = os.getenv("TATOO_CONTRACTS_DIR") or os.getenv("CONTRACTS_DIR")
    contracts_dir = Path(configured).expanduser() if configured else BASE_DIR / "contratos"
    if not contracts_dir.is_absolute():
        contracts_dir = BASE_DIR / contracts_dir
    contracts_dir.mkdir(parents=True, exist_ok=True)
    return contracts_dir


def resolve_sqlite_path(value: str, data_dir: Path) -> Path:
    sqlite_path = Path(value).expanduser() if value else data_dir / "app.db"
    if not sqlite_path.is_absolute():
        sqlite_path = BASE_DIR / sqlite_path
    sqlite_path = sqlite_path.resolve()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite_path


def build_database_url(data_dir: Path) -> str:
    explicit_url = normalize_database_url(os.getenv("DATABASE_URL"))
    if explicit_url:
        if explicit_url.startswith("sqlite:///"):
            sqlite_path = resolve_sqlite_path(explicit_url.removeprefix("sqlite:///"), data_dir)
            return f"sqlite:///{sqlite_path.as_posix()}"
        return explicit_url

    provider = normalize_provider(os.getenv("DB_PROVIDER", "sqlite"))
    database_name = str(os.getenv("DB_NAME", data_dir / "app.db")).strip()

    if provider == "sqlite":
        sqlite_path = resolve_sqlite_path(database_name, data_dir)
        return f"sqlite:///{sqlite_path.as_posix()}"

    host = str(os.getenv("DB_HOST", "localhost")).strip()
    port = str(os.getenv("DB_PORT", "")).strip()
    username = str(os.getenv("DB_USER", "")).strip()
    password = os.getenv("DB_PASSWORD", "")

    if not database_name or not host or not username:
        raise RuntimeError("Configure DB_NAME, DB_HOST and DB_USER in .env for SQL providers.")

    encoded_user = quote(username)
    encoded_password = quote(password) if password else ""
    credentials = encoded_user if not encoded_password else f"{encoded_user}:{encoded_password}"
    host_part = f"{host}:{port}" if port else host

    if provider == "postgres":
        return f"postgresql+psycopg://{credentials}@{host_part}/{quote(database_name)}"

    if provider == "mysql":
        return f"mysql+pymysql://{credentials}@{host_part}/{quote(database_name)}"

    raise RuntimeError(f"Unsupported DB_PROVIDER: {provider}")


def detect_provider(database_url: str) -> str:
    lowered = database_url.lower()
    if lowered.startswith("postgresql"):
        return "postgres"
    if lowered.startswith("mysql"):
        return "mysql"
    if lowered.startswith("sqlite"):
        return "sqlite"
    return "unknown"


def database_label(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return database_url.removeprefix("sqlite:///")
    return re.sub(r":[^:@/]+@", ":***@", database_url)


def build_bpa_proxy_url(requested_path: str) -> str:
    normalized_path = requested_path.strip().lstrip("/")
    if not normalized_path or ".." in normalized_path:
        raise AppError(400, "Invalid BPA API path.")

    base_url = BPA_API_BASE_URL or "http://127.0.0.1:5002"
    target = f"{base_url}/api/{normalized_path}"
    query_string = request.query_string.decode().strip()
    if query_string:
        target = f"{target}?{query_string}"
    return target


def proxy_bpa_request(requested_path: str):
    target_url = build_bpa_proxy_url(requested_path)
    request_body = request.get_data() or None
    headers = {"Accept": request.headers.get("Accept", "application/json")}
    if request.content_type:
        headers["Content-Type"] = request.content_type

    proxy_request = urllib.request.Request(
        target_url,
        data=request_body,
        headers=headers,
        method=request.method.upper(),
    )

    try:
        with urllib.request.urlopen(proxy_request, timeout=BPA_API_TIMEOUT_SECONDS) as response:
            payload = response.read()
            content_type = response.headers.get("Content-Type", "application/json; charset=utf-8")
            return app.response_class(payload, status=response.status, content_type=content_type)
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        content_type = exc.headers.get("Content-Type", "application/json; charset=utf-8") if exc.headers else "application/json; charset=utf-8"
        return app.response_class(payload, status=exc.code, content_type=content_type)
    except urllib.error.URLError as exc:
        return (
            jsonify(
                {
                    "error": "Servico BPA indisponivel.",
                    "detail": str(getattr(exc, "reason", exc)),
                    "baseUrl": BPA_API_BASE_URL,
                }
            ),
            502,
        )


def as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def as_date(value: Any) -> date | None:
    try:
        return datetime.strptime(as_text(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug or "item"


def unique_slug(base: str, used: set[str]) -> str:
    slug = base
    suffix = 2
    while slug in used:
        slug = f"{base}-{suffix}"
        suffix += 1
    used.add(slug)
    return slug


def compact_slug(value: str, fallback: str = "item", max_length: int = 48) -> str:
    slug = slugify(value)[:max_length].strip("-")
    return slug or fallback


def attachment_folder_name(vencimento: str) -> str:
    due_date = as_date(vencimento)
    return due_date.strftime("%Y-%m") if due_date else "sem-data"


def attachment_extension(filename: str, mime_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix and len(suffix) <= 10:
        return suffix
    guessed = mimetypes.guess_extension(mime_type or "")
    if guessed == ".jpe":
        return ".jpg"
    return guessed or ""


def build_attachment_filename(vencimento: str, conta_nome: str, pessoa: str, descricao: str, original_name: str, mime_type: str) -> str:
    parts = [
        compact_slug(vencimento, fallback="sem-data", max_length=16),
        compact_slug(conta_nome, fallback="sem-conta", max_length=32),
        compact_slug(pessoa, fallback="sem-pessoa", max_length=32),
        compact_slug(descricao, fallback="sem-descricao", max_length=40),
    ]
    extension = attachment_extension(original_name, mime_type)
    return "_".join(parts) + extension


def ensure_unique_file_path(directory: Path, file_name: str) -> Path:
    candidate = directory / file_name
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def ensure_within_directory(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        raise AppError(404, "Attachment not found.")
    return resolved_candidate


def build_attachment_url(relative_path: str) -> str:
    return f"/api/finance/attachments/{quote(relative_path, safe='/')}"


def build_contract_url(relative_path: str) -> str:
    return f"/api/tatoo/contracts/{quote(relative_path, safe='/')}"


def normalize_contract_datetime(value: str | None) -> datetime:
    raw_value = as_text(value)
    if not raw_value:
        return datetime.now(timezone.utc)

    normalized = raw_value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(timezone.utc)


def contract_folder_name(signed_at: str | None) -> str:
    return normalize_contract_datetime(signed_at).strftime("%Y-%m")


def build_contract_filename(consent_id: str, signer_name: str, signed_at: str | None, signature_mode: str) -> str:
    signed_prefix = compact_slug(normalize_contract_datetime(signed_at).strftime("%Y-%m-%d"), fallback="sem-data", max_length=16)
    signer_slug = compact_slug(signer_name, fallback="sem-assinante", max_length=40)
    consent_slug = compact_slug(consent_id, fallback="consentimento", max_length=40)
    mode_slug = compact_slug(signature_mode, fallback="assinatura", max_length=24)
    return f"{signed_prefix}_{signer_slug}_{consent_slug}_{mode_slug}.pdf"


def decode_attachment_payload(raw_value: str) -> dict[str, Any]:
    payload_text = as_text(raw_value)
    if not payload_text:
        return {}

    if payload_text.startswith("data:"):
        return {"dataUrl": payload_text}

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return {"dataUrl": payload_text}

    if not isinstance(payload, dict):
        return {"dataUrl": payload_text}

    relative_path = as_text(payload.get("path"))
    return {
        "dataUrl": as_text(payload.get("dataUrl")),
        "url": as_text(payload.get("url")) or (build_attachment_url(relative_path) if relative_path else ""),
        "path": relative_path,
        "size": as_int(payload.get("size"), 0),
        "storage": as_text(payload.get("storage"), "filesystem") or "filesystem",
    }


def encode_attachment_payload(attachment: dict[str, Any]) -> str:
    data_url = as_text(attachment.get("dataUrl"))
    if data_url.startswith("data:"):
        return data_url

    payload = {
        "storage": as_text(attachment.get("storage"), "filesystem") or "filesystem",
        "url": as_text(attachment.get("url")),
        "path": as_text(attachment.get("path")),
        "size": as_int(attachment.get("size"), 0),
    }
    if data_url:
        payload["dataUrl"] = data_url
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def cleanup_empty_attachment_dirs(start_dir: Path) -> None:
    current = start_dir
    root = ATTACHMENTS_DIR.resolve()
    while current.exists() and current != root:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def finance_mail_settings() -> dict[str, Any]:
    recipients = [item.strip() for item in str(os.getenv("FINANCE_REMINDER_TO", "")).split(",") if item.strip()]
    host = str(os.getenv("SMTP_HOST", "")).strip()
    return {
        "enabled": bool(host and recipients),
        "host": host,
        "port": as_int(os.getenv("SMTP_PORT"), 587),
        "username": str(os.getenv("SMTP_USER", "")).strip(),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "from": str(os.getenv("FINANCE_REMINDER_FROM") or os.getenv("SMTP_FROM") or "").strip(),
        "to": recipients,
        "use_tls": parse_bool(os.getenv("SMTP_USE_TLS"), True),
        "use_ssl": parse_bool(os.getenv("SMTP_USE_SSL"), False),
    }


def finance_ai_settings() -> dict[str, Any]:
    search_context_size = as_text(os.getenv("FINANCE_AI_SEARCH_CONTEXT"), "medium").lower()
    if search_context_size not in {"low", "medium", "high"}:
        search_context_size = "medium"

    return {
        "enabled": bool(as_text(os.getenv("OPENAI_API_KEY"))),
        "api_key": as_text(os.getenv("OPENAI_API_KEY")),
        "model": as_text(os.getenv("FINANCE_AI_MODEL") or os.getenv("OPENAI_MODEL"), "gpt-5"),
        "base_url": as_text(os.getenv("OPENAI_API_BASE"), "https://api.openai.com/v1").rstrip("/"),
        "organization": as_text(os.getenv("OPENAI_ORGANIZATION")),
        "project": as_text(os.getenv("OPENAI_PROJECT")),
        "search_context_size": search_context_size,
        "timeout_seconds": max(as_int(os.getenv("FINANCE_AI_TIMEOUT_SECONDS"), 45), 10),
        "max_offers": min(max(as_int(os.getenv("FINANCE_AI_MAX_OFFERS"), 6), 3), 12),
    }


def finance_scraper_settings() -> dict[str, Any]:
    raw_domains = [item.strip().lower() for item in as_text(os.getenv("FINANCE_SCRAPER_DOMAINS")).split(",") if item.strip()]
    return {
        "allowed_domains": tuple(raw_domains) if raw_domains else DEFAULT_ALLOWED_DOMAINS,
        "timeout_seconds": max(as_int(os.getenv("FINANCE_SCRAPER_TIMEOUT_SECONDS"), 12), 5),
        "max_offers": min(
            max(as_int(os.getenv("FINANCE_RESEARCH_MAX_OFFERS") or os.getenv("FINANCE_AI_MAX_OFFERS"), 6), 3),
            12,
        ),
        "user_agent": as_text(os.getenv("FINANCE_SCRAPER_USER_AGENT"), DEFAULT_USER_AGENT),
    }


def openai_request_headers(settings: dict[str, Any], *, include_json: bool = True) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {settings['api_key']}"}
    if include_json:
        headers["Content-Type"] = "application/json"
    if settings["organization"]:
        headers["OpenAI-Organization"] = settings["organization"]
    if settings["project"]:
        headers["OpenAI-Project"] = settings["project"]
    return headers


def parse_openai_error(raw_body: str, fallback: str = "Falha ao consultar a IA.") -> dict[str, str]:
    result = {"message": fallback, "type": "", "code": "", "param": ""}
    try:
        error_payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return result

    error_info = error_payload.get("error")
    if isinstance(error_info, dict):
        result["message"] = as_text(error_info.get("message")) or fallback
        result["type"] = as_text(error_info.get("type"))
        result["code"] = as_text(error_info.get("code"))
        result["param"] = as_text(error_info.get("param"))
        return result

    result["message"] = as_text(error_payload.get("message")) or fallback
    return result


def parse_openai_error_message(raw_body: str, fallback: str = "Falha ao consultar a IA.") -> str:
    return parse_openai_error(raw_body, fallback)["message"]


def friendly_openai_error_message(
    status_code: int,
    error_info: dict[str, str],
    fallback: str = "Falha ao consultar a IA.",
) -> tuple[str, str]:
    message = as_text(error_info.get("message")) or fallback
    error_code = as_text(error_info.get("code")).lower()
    error_type = as_text(error_info.get("type")).lower()
    normalized = f"{error_code} {error_type} {message}".lower()

    if "insufficient_quota" in normalized or "exceeded your current quota" in normalized:
        return (
            "insufficient_quota",
            "A OpenAI informou que o projeto esta sem saldo ou atingiu o limite de uso da API. Verifique Billing e Limits no painel da OpenAI.",
        )

    if status_code == 429:
        return (
            "rate_limited",
            "A OpenAI respondeu com limite temporario de requisicoes. Aguarde um pouco e tente novamente.",
        )

    if status_code == 401:
        return (
            "auth_error",
            "A OpenAI recusou a autenticacao da chave configurada no servidor.",
        )

    if status_code == 403:
        return (
            "forbidden",
            "A chave existe, mas este projeto nao tem permissao para acessar a API ou o modelo configurado.",
        )

    if status_code == 404:
        return (
            "model_not_found",
            "O modelo configurado nao foi encontrado ou nao esta liberado para esta chave.",
        )

    return ("api_error", message)


def send_openai_request(
    path: str,
    settings: dict[str, Any],
    *,
    method: str = "POST",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not settings["enabled"] or not settings["api_key"]:
        raise AppError(503, "Configure OPENAI_API_KEY no servidor para usar a Pesquisa I.A.")

    request_url = f"{settings['base_url']}/{path.lstrip('/')}"
    raw_payload = json.dumps(payload, ensure_ascii=True).encode("utf-8") if payload is not None else None
    openai_request = urllib.request.Request(
        request_url,
        data=raw_payload,
        headers=openai_request_headers(settings, include_json=payload is not None),
        method=method,
    )

    try:
        with urllib.request.urlopen(openai_request, timeout=settings["timeout_seconds"]) as response:
            return {
                "ok": True,
                "status_code": response.status,
                "body": response.read().decode("utf-8"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status_code": exc.code,
            "body": exc.read().decode("utf-8", errors="replace"),
        }
    except urllib.error.URLError as exc:
        raise AppError(502, "Nao foi possivel conectar ao servico de IA.") from exc


def mask_secret_hint(secret: str) -> str:
    clean = as_text(secret)
    if not clean:
        return ""
    if len(clean) <= 8:
        return "*" * len(clean)
    return f"{clean[:6]}...{clean[-4:]}"


def build_finance_ai_diagnostic(*, probe: bool = False) -> dict[str, Any]:
    settings = finance_ai_settings()
    api_key = settings["api_key"]
    key_present = bool(api_key)
    key_looks_valid = api_key.startswith("sk-") if api_key else False

    result = {
        "ok": True,
        "checkedAt": now_iso(),
        "config": {
            "apiKeyPresent": key_present,
            "apiKeyLooksValid": key_looks_valid,
            "apiKeyHint": mask_secret_hint(api_key),
            "model": settings["model"],
            "baseUrl": settings["base_url"],
            "organizationPresent": bool(settings["organization"]),
            "projectPresent": bool(settings["project"]),
        },
        "status": {
            "level": "warn",
            "code": "not_tested",
            "message": "Chave detectada localmente. Clique em atualizar para validar a conexao com a OpenAI.",
        },
        "probe": {
            "attempted": probe,
            "success": False,
            "httpStatus": None,
            "message": "",
            "modelId": "",
            "errorCode": "",
        },
    }

    if not key_present:
        result["status"] = {
            "level": "bad",
            "code": "missing_key",
            "message": "OPENAI_API_KEY nao foi encontrada no processo do servidor.",
        }
        result["probe"]["message"] = "Defina OPENAI_API_KEY no ambiente do servico e reinicie o deploy."
        return result

    if not key_looks_valid:
        result["status"] = {
            "level": "warn",
            "code": "unexpected_key_format",
            "message": "A chave esta presente, mas o formato nao parece ser uma secret key padrao da OpenAI.",
        }

    if not probe:
        return result

    try:
        response = send_openai_request(
            f"models/{quote(settings['model'], safe='')}",
            settings,
            method="GET",
            payload=None,
        )
    except AppError as exc:
        result["status"] = {
            "level": "bad",
            "code": "network_error",
            "message": str(exc),
        }
        result["probe"]["message"] = str(exc)
        return result
    result["probe"]["httpStatus"] = response["status_code"]

    if response["ok"]:
        payload = json.loads(response["body"])
        model_id = as_text(payload.get("id")) or settings["model"]
        result["probe"].update(
            {
                "success": True,
                "message": "Conexao com a OpenAI funcionando e modelo acessivel para este projeto.",
                "modelId": model_id,
            }
        )
        result["status"] = {
            "level": "ok",
            "code": "connected",
            "message": f"OpenAI conectada com sucesso usando o modelo {model_id}.",
        }
        return result

    status_code = response["status_code"]
    parsed_error = parse_openai_error(response["body"], "Falha ao validar a configuracao da OpenAI.")
    friendly_code, friendly_message = friendly_openai_error_message(status_code, parsed_error, parsed_error["message"])
    result["probe"]["message"] = friendly_message
    result["probe"]["errorCode"] = friendly_code

    if status_code == 401:
        result["status"] = {
            "level": "bad",
            "code": "auth_error",
            "message": "A chave foi encontrada, mas a OpenAI recusou a autenticacao.",
        }
    elif status_code == 403:
        result["status"] = {
            "level": "bad",
            "code": "forbidden",
            "message": "A chave existe, mas este projeto ou organizacao nao tem permissao para acessar a API/modelo.",
        }
    elif status_code == 404:
        result["status"] = {
            "level": "bad",
            "code": "model_not_found",
            "message": f"O modelo configurado ({settings['model']}) nao foi encontrado ou nao esta disponivel para a chave atual.",
        }
    elif status_code == 429:
        result["status"] = {
            "level": "warn",
            "code": friendly_code,
            "message": friendly_message,
        }
    else:
        result["status"] = {
            "level": "bad",
            "code": friendly_code,
            "message": friendly_message,
        }

    return result


def finance_purchase_research_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string"},
            "summary": {"type": "string"},
            "offers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": ["melhor_preco", "custo_beneficio", "alternativa"],
                        },
                        "title": {"type": "string"},
                        "store": {"type": "string"},
                        "url": {"type": "string"},
                        "price_text": {"type": "string"},
                        "price_value": {"type": "number"},
                        "currency": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": [
                        "category",
                        "title",
                        "store",
                        "url",
                        "price_text",
                        "price_value",
                        "currency",
                        "reason",
                    ],
                },
            },
        },
        "required": ["query", "summary", "offers"],
    }


def build_finance_purchase_research_input(payload: dict[str, str], max_offers: int) -> str:
    parts = [
        "Contexto da solicitacao de compra:",
        f"- Produto/servico: {payload['desc']}",
        f"- Fornecedor preferido: {payload['fornecedor'] or 'nao informado'}",
        f"- Justificativa: {payload['justificativa'] or 'nao informada'}",
        f"- Detalhes internos: {payload['obs'] or 'nao informados'}",
        f"- Link de referencia atual: {payload['produto_url'] or 'nao informado'}",
        "",
        "Objetivo:",
        "- encontrar ofertas atuais e aderentes ao pedido",
        "- priorizar lojas brasileiras, preco em BRL e paginas diretas de produto/plano",
        "- listar ate "
        f"{max_offers}"
        " links reais e acionaveis",
        "- organizar por melhor preco, custo-beneficio e alternativas",
    ]
    return "\n".join(parts)


def extract_openai_response_text(response_payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in response_payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and as_text(content.get("text")):
                chunks.append(as_text(content.get("text")))
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def extract_openai_response_sources(response_payload: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_source(title: Any, url: Any) -> None:
        clean_url = as_text(url)
        if not re.match(r"^https?://", clean_url) or clean_url in seen:
            return
        seen.add(clean_url)
        sources.append({"title": as_text(title) or clean_url, "url": clean_url})

    for item in response_payload.get("output", []):
        if not isinstance(item, dict):
            continue

        if item.get("type") == "web_search_call":
            action = item.get("action")
            if isinstance(action, dict):
                for source in action.get("sources", []):
                    if isinstance(source, dict):
                        add_source(source.get("title"), source.get("url"))

        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            for annotation in content.get("annotations", []):
                if isinstance(annotation, dict) and annotation.get("type") == "url_citation":
                    add_source(annotation.get("title"), annotation.get("url"))

    return sources


def request_openai_json(path: str, payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    response = send_openai_request(path, settings, method="POST", payload=payload)
    raw_body = response["body"]
    if not response["ok"]:
        parsed_error = parse_openai_error(raw_body)
        _, friendly_message = friendly_openai_error_message(response["status_code"], parsed_error, parsed_error["message"])
        raise AppError(502, friendly_message) from None

    try:
        return json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise AppError(502, "A resposta da IA veio em um formato invalido.") from exc


def run_finance_purchase_research(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AppError(400, "Envie um JSON valido com os dados da compra.")

    scraper_payload = {
        "desc": as_text(payload.get("desc"))[:240],
        "fornecedor": as_text(payload.get("fornecedor"))[:180],
        "justificativa": as_text(payload.get("justificativa"))[:500],
        "obs": as_text(payload.get("obs"))[:500],
        "produto_url": as_text(payload.get("produtoUrl"))[:500],
    }
    if len(scraper_payload["desc"]) < 3:
        raise AppError(400, "Informe a descricao do produto para pesquisar com a IA.")

    settings = finance_scraper_settings()
    try:
        return run_scraper_purchase_research(
            scraper_payload,
            allowed_domains=settings["allowed_domains"],
            max_offers=settings["max_offers"],
            timeout_seconds=settings["timeout_seconds"],
            user_agent=settings["user_agent"],
        )
    except ScraperError as exc:
        raise AppError(502, str(exc)) from exc


def image_bytes_from_data_url(data_url: str) -> bytes:
    payload = as_text(data_url)
    if not payload.startswith("data:") or "," not in payload:
        raise AppError(400, "Data URL invalida.")

    header, encoded = payload.split(",", 1)
    if ";base64" not in header:
        raise AppError(400, "O arquivo precisa estar em base64.")

    try:
        return base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise AppError(400, "Falha ao decodificar o arquivo enviado.") from exc


def append_detected_codes(
    results: list[dict[str, str]], seen: set[tuple[str, str]], candidates: list[dict[str, str]]
) -> None:
    for item in candidates:
        raw_value = as_text(item.get("rawValue"))
        code_type = as_text(item.get("format"), "UNKNOWN") or "UNKNOWN"
        key = (code_type, raw_value)
        if not raw_value or key in seen:
            continue
        seen.add(key)
        results.append({"format": code_type, "rawValue": raw_value})


def build_code_image_variants(image: Image.Image) -> list[Image.Image]:
    if Image is None:
        return []

    base = image.convert("RGB")
    grayscale = base.convert("L")
    variants: list[Image.Image] = [base, grayscale]

    if ImageOps is not None:
        variants.append(ImageOps.autocontrast(grayscale))

    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", getattr(Image, "LANCZOS", 1))
    max_side = max(base.size)
    scales = (2, 3) if max_side <= 1400 else (2,) if max_side <= 2400 else ()
    for scale in scales:
        resized = base.resize((base.width * scale, base.height * scale), resampling)
        resized_gray = resized.convert("L")
        variants.extend([resized, resized_gray])
        if ImageOps is not None:
            variants.append(ImageOps.autocontrast(resized_gray))

    return variants


def decode_codes_from_image(image: Image.Image) -> list[dict[str, str]]:
    if Image is None or pyzbar_decode is None:
        raise AppError(503, "Leitura de QR/codigo requer Pillow e pyzbar instalados no servidor.")

    results: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in build_code_image_variants(image):
        decoded_items = []
        for item in pyzbar_decode(candidate):
            decoded_items.append(
                {
                    "format": item.type or "UNKNOWN",
                    "rawValue": item.data.decode("utf-8", errors="replace"),
                }
            )
        append_detected_codes(results, seen, decoded_items)
        if results:
            return results

    return results


def extract_payment_codes_from_text(text: str) -> list[dict[str, str]]:
    normalized = as_text(text)
    if not normalized:
        return []

    results: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in re.finditer(r"(?:\d[\d\.\s-]{40,90}\d)", normalized):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) not in {44, 46, 47, 48}:
            continue
        code_type = "LINHA_DIGITAVEL" if len(digits) >= 46 else "CODIGO_DE_BARRAS"
        append_detected_codes(results, seen, [{"format": code_type, "rawValue": digits}])

    return results

def decode_image_codes(content: bytes) -> list[dict[str, str]]:
    if Image is None:
        raise AppError(503, "Leitura de imagem requer Pillow instalado no servidor.")
    try:
        image = Image.open(BytesIO(content))
        image.load()
    except Exception as exc:  # pragma: no cover - pillow details vary by backend
        raise AppError(400, "Nao foi possivel abrir a imagem do anexo.") from exc

    return decode_codes_from_image(image)


def decode_pdf_codes(content: bytes) -> list[dict[str, str]]:
    if fitz is None:
        raise AppError(503, "Leitura de PDF requer PyMuPDF instalado no servidor.")

    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:  # pragma: no cover - fitz backend specific
        raise AppError(400, "Nao foi possivel abrir o PDF do anexo.") from exc

    try:
        results: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        page_limit = min(len(document), 5)
        for page_index in range(page_limit):
            page = document.load_page(page_index)
            append_detected_codes(results, seen, extract_payment_codes_from_text(page.get_text("text")))

        if results or Image is None or pyzbar_decode is None:
            return results

        for page_index in range(page_limit):
            page = document.load_page(page_index)
            for zoom in (2, 3):
                pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
                append_detected_codes(results, seen, decode_codes_from_image(image))
                if results:
                    break
            if results:
                break

        return results
    finally:
        document.close()


def normalize_contract_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AppError(400, "Envie um JSON valido com os dados do contrato.")

    signature = payload.get("signature") if isinstance(payload.get("signature"), dict) else {}
    client = payload.get("client") if isinstance(payload.get("client"), dict) else {}
    contractor = payload.get("contractor") if isinstance(payload.get("contractor"), dict) else {}
    session_info = payload.get("session") if isinstance(payload.get("session"), dict) else {}

    consent_id = as_text(payload.get("consentId"))
    if not consent_id:
        raise AppError(400, "Consentimento invalido para gerar o PDF.")

    signer_name = as_text(signature.get("signerName"))
    if not signer_name:
        raise AppError(400, "Informe o nome do assinante.")

    signer_document = as_text(signature.get("signerDocument"))
    if not signer_document:
        raise AppError(400, "Informe o documento confirmado.")

    signature_mode = as_text(signature.get("signatureMode"), "draw") or "draw"
    if signature_mode not in {"draw", "external"}:
        signature_mode = "draw"

    image_data_url = as_text(signature.get("imageDataUrl"))
    if signature_mode == "draw" and not image_data_url.startswith("data:"):
        raise AppError(400, "A assinatura desenhada nao foi informada.")

    signed_at = as_text(signature.get("signedAt")) or now_iso()

    return {
        "consentId": consent_id,
        "termType": as_text(payload.get("termType"), "Contrato de tatuagem"),
        "createdAt": as_text(payload.get("createdAt")) or now_iso(),
        "notes": as_text(payload.get("notes")),
        "healthNotes": as_text(payload.get("healthNotes"), "Sem observacoes adicionais registradas na ficha do cliente."),
        "client": {
            "name": as_text(client.get("name"), "Nao informado"),
            "birthDate": as_text(client.get("birthDate")),
            "rg": as_text(client.get("rg")),
            "document": signer_document,
            "address": as_text(client.get("address")),
            "city": as_text(client.get("city")),
            "state": as_text(client.get("state")),
            "phone": as_text(client.get("phone")),
            "social": as_text(client.get("social")),
        },
        "contractor": {
            "studioName": as_text(contractor.get("studioName"), "Studio"),
            "cnpj": as_text(contractor.get("cnpj")),
            "address": as_text(contractor.get("address")),
            "postalCode": as_text(contractor.get("postalCode")),
            "city": as_text(contractor.get("city")),
            "state": as_text(contractor.get("state")),
            "representative": as_text(contractor.get("representative")),
            "representativeCpf": as_text(contractor.get("representativeCpf")),
        },
        "session": {
            "description": as_text(session_info.get("description")),
            "bodyArea": as_text(session_info.get("bodyArea")),
            "artist": as_text(session_info.get("artist")),
            "appointmentAt": as_text(session_info.get("appointmentAt")),
            "budget": as_float(session_info.get("budget"), 0.0),
        },
        "signature": {
            "signerName": signer_name,
            "signerDocument": signer_document,
            "signatureMode": signature_mode,
            "imageConsent": as_text(signature.get("imageConsent"), "A definir"),
            "annexConfirmation": as_text(signature.get("annexConfirmation")),
            "confirmation": as_text(signature.get("confirmation")),
            "signedAt": signed_at,
            "imageDataUrl": image_data_url,
            "userAgent": as_text(signature.get("userAgent")),
        },
    }


def wrap_contract_text(text: str, width: int = 92) -> list[str]:
    normalized = re.sub(r"\s+", " ", as_text(text)).strip()
    if not normalized:
        return [""]
    return textwrap.wrap(normalized, width=width, break_long_words=False, break_on_hyphens=False) or [normalized]


def new_contract_page(document: fitz.Document):
    return document.new_page(width=595, height=842)


def write_contract_line(document: fitz.Document, page, y: float, text: str, *, font_size: float = 11, font_name: str = "helv", color=(0.22, 0.16, 0.11)):
    if y > 790 - font_size:
        page = new_contract_page(document)
        y = 48
    page.insert_text((42, y), text, fontsize=font_size, fontname=font_name, fill=color)
    return page, y + font_size + 4


def write_contract_paragraph(document: fitz.Document, page, y: float, text: str, *, font_size: float = 11, font_name: str = "helv", width: int = 92, spacing_after: float = 6):
    for line in wrap_contract_text(text, width=width):
        page, y = write_contract_line(document, page, y, line, font_size=font_size, font_name=font_name)
    return page, y + spacing_after


def write_contract_title(document: fitz.Document, page, y: float, text: str):
    return write_contract_paragraph(document, page, y, text, font_size=16, font_name="helvB", width=58, spacing_after=8)


def write_contract_section(document: fitz.Document, page, y: float, title: str):
    page, y = write_contract_line(document, page, y, title.upper(), font_size=12, font_name="helvB", color=(0.38, 0.16, 0.08))
    return page, y + 2


def write_contract_bullets(document: fitz.Document, page, y: float, items: list[str]):
    for item in items:
        page, y = write_contract_paragraph(document, page, y, f"- {item}", width=88, spacing_after=3)
    return page, y + 4


def build_tatoo_contract_pdf(payload: Any) -> tuple[bytes, dict[str, Any]]:
    if fitz is None:
        raise AppError(503, "Geracao de PDF requer PyMuPDF instalado no servidor.")

    contract = normalize_contract_payload(payload)
    client = contract["client"]
    contractor = contract["contractor"]
    session_info = contract["session"]
    signature = contract["signature"]
    signed_at = signature["signedAt"]
    signature_mode = signature["signatureMode"]
    budget = currency_value = f"R$ {session_info['budget']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if session_info["budget"] else "Nao informado"
    created_at = normalize_contract_datetime(contract["createdAt"]).strftime("%d/%m/%Y %H:%M")
    appointment_at = normalize_contract_datetime(session_info["appointmentAt"]).strftime("%d/%m/%Y %H:%M") if as_text(session_info["appointmentAt"]) else "Nao informado"
    birth_date = normalize_contract_datetime(client["birthDate"]).strftime("%d/%m/%Y") if as_text(client["birthDate"]) else "Nao informado"
    signed_label = normalize_contract_datetime(signed_at).strftime("%d/%m/%Y %H:%M")
    mode_label = "Assinatura na tela" if signature_mode == "draw" else "Assinatura digital externa (Gov.br / certificado)"
    document = fitz.open()
    page = new_contract_page(document)
    y = 48

    page, y = write_contract_title(document, page, y, "Contrato de servico de pigmentacao artificial permanente")
    page, y = write_contract_paragraph(document, page, y, f"Tipo do termo: {contract['termType']}", font_name="helvB", width=84, spacing_after=2)
    page, y = write_contract_paragraph(document, page, y, f"Consentimento: {contract['consentId']} | Criado em: {created_at}", width=90, spacing_after=10)

    page, y = write_contract_section(document, page, y, "Partes")
    page, y = write_contract_paragraph(document, page, y, f"Contratante: {client['name']}, nascido em {birth_date}, RG {client['rg'] or 'Nao informado'}, CPF {client['document']}, residente em {client['address'] or 'Nao informado'}, {client['city'] or 'Nao informado'} / {client['state'] or 'NA'}, telefone {client['phone'] or 'Nao informado'}, redes sociais {client['social'] or 'Nao informado'}.", width=90)
    page, y = write_contract_paragraph(document, page, y, f"Contratado: {contractor['studioName']}, CNPJ {contractor['cnpj'] or 'Nao informado'}, localizado em {contractor['address'] or 'Nao informado'}, CEP {contractor['postalCode'] or 'Nao informado'}, {contractor['city'] or 'Nao informado'} / {contractor['state'] or 'NA'}, representado por {contractor['representative'] or 'Nao informado'}, CPF {contractor['representativeCpf'] or 'Nao informado'}.", width=90)

    page, y = write_contract_section(document, page, y, "Resumo do atendimento")
    page, y = write_contract_bullets(document, page, y, [
        f"Descricao da sessao: {session_info['description'] or 'Nao informado'}.",
        f"Area do corpo: {session_info['bodyArea'] or 'Nao informado'}.",
        f"Profissional: {session_info['artist'] or 'Nao informado'}.",
        f"Data e hora previstas: {appointment_at}.",
        f"Valor previsto: {currency_value}.",
    ])

    page, y = write_contract_section(document, page, y, "Clausula primeira - do objeto")
    page, y = write_contract_paragraph(document, page, y, "O presente instrumento tem como objeto a prestacao de servicos de pigmentacao artificial permanente da pele, consistente na pigmentacao exogena introduzida fisicamente na camada dermica ou subepidermica da pele, com resultado permanente, para fins de embelezamento ou correcao estetica.")
    page, y = write_contract_paragraph(document, page, y, "O contratante declara que forneceu historico de saude suficiente para a avaliacao do atendimento e que recebeu previamente, por meios digitais, as informacoes de agendamento e funcionamento equivalentes ao Anexo II.")

    page, y = write_contract_section(document, page, y, "Clausula segunda - saude e higiene do estudio")
    page, y = write_contract_paragraph(document, page, y, "Os produtos utilizados no procedimento e na higienizacao do ambiente seguem as normas sanitarias aplicaveis. Tintas sao fracionadas por cliente, sobras sao descartadas como residuo infectante e materiais nao descartaveis passam por limpeza, desinfeccao e ou esterilizacao.")
    page, y = write_contract_paragraph(document, page, y, "Luvas, agulhas, laminas e itens equivalentes sao de uso unico e descartavel.")

    page, y = write_contract_section(document, page, y, "Clausula terceira - valor e vigencia")
    page, y = write_contract_paragraph(document, page, y, f"O servico sera executado por sessoes. Para este atendimento, o valor previsto registrado e {currency_value}, podendo haver ajuste apenas se houver mudanca relevante de escopo, desenho ou cronograma previamente combinada entre as partes.")
    page, y = write_contract_paragraph(document, page, y, "O pagamento deve ocorrer no dia do procedimento, em especie, cartao ou PIX, observada eventual taxa de agendamento previamente combinada com o estudio.")

    page, y = write_contract_section(document, page, y, "Obrigacoes do contratante")
    page, y = write_contract_bullets(document, page, y, [
        "Zelar pela propria pele e seguir as orientacoes de preparo e cicatrizacao fornecidas pelo profissional.",
        "Informar alergias, medicamentos, condicoes cutaneas e qualquer dado de saude que possa impactar o procedimento.",
        "Justificar ausencia com antecedencia minima de 72 horas, quando aplicavel.",
        "Comparecer alimentado e com documento com foto no dia agendado.",
    ])

    page, y = write_contract_section(document, page, y, "Obrigacoes do contratado")
    page, y = write_contract_bullets(document, page, y, [
        "Executar o objeto do contrato buscando excelencia tecnica, seguranca e higiene.",
        "Apresentar materiais novos ou esterilizados antes da sessao e descartar corretamente os residuos ao final.",
        "Orientar o cliente sobre preparo, atendimento e cuidados posteriores ao procedimento.",
    ])

    page, y = write_contract_section(document, page, y, "Responsabilidade e uso de imagem")
    page, y = write_contract_paragraph(document, page, y, "O contratante declara que teve conhecimento dos cuidados anteriores, durante e posteriores ao procedimento, sendo de sua responsabilidade seguir essas orientacoes para alcancar o resultado esperado. O estudio nao podera ser responsabilizado por informacoes omitidas sobre alergias, problemas cutaneos, uso de medicacao ou outras condicoes que interfiram no resultado final.")
    page, y = write_contract_paragraph(document, page, y, f"Uso de imagem neste termo: {signature['imageConsent'] or 'Nao informado'}.")

    if contract['notes']:
        page, y = write_contract_section(document, page, y, "Clausulas adicionais do atendimento")
        page, y = write_contract_paragraph(document, page, y, contract['notes'])

    page, y = write_contract_section(document, page, y, "Anexo I - ficha de anamnese")
    page, y = write_contract_paragraph(document, page, y, f"Observacoes de saude registradas: {contract['healthNotes']}")
    page, y = write_contract_paragraph(document, page, y, "Declaracao: as informacoes acima devem ser verdadeiras, nao cabendo ao profissional responsabilidade por dados omitidos nesta avaliacao.")

    page, y = write_contract_section(document, page, y, "Anexo II - termo de agendamento e atendimento")
    page, y = write_contract_bullets(document, page, y, [
        "O estudio realiza procedimentos com rigor de higiene, usando materiais descartaveis e ou esterilizados.",
        "O orcamento pode variar conforme ideia, tamanho, cores, local da tatuagem e grau de complexidade da arte.",
        "O agendamento so e confirmado apos pagamento do sinal; reagendamentos dependem de antecedencia e disponibilidade.",
        "No dia do procedimento, o cliente deve comparecer alimentado, com documento com foto, sem uso de alcool ou drogas.",
        "Os cuidados de cicatrizacao serao repassados pelo profissional, e intercorrencias por descuido podem gerar novo custo.",
    ])
    page, y = write_contract_paragraph(document, page, y, f"Declaracao de recebimento: {signature['annexConfirmation'] or 'Nao informado'}")
    page, y = write_contract_paragraph(document, page, y, f"Declaracao final do cliente: {signature['confirmation'] or 'Nao informado'}")

    page, y = write_contract_section(document, page, y, "Assinatura")
    page, y = write_contract_paragraph(document, page, y, f"Modo de assinatura selecionado: {mode_label}.")
    page, y = write_contract_paragraph(document, page, y, f"Assinante: {signature['signerName']} | Documento confirmado: {signature['signerDocument']} | Data: {signed_label}")

    if signature_mode == "draw" and signature['imageDataUrl']:
        image_bytes = image_bytes_from_data_url(signature['imageDataUrl'])
        if y > 660:
            page = new_contract_page(document)
            y = 48
        page, y = write_contract_paragraph(document, page, y, "Assinatura coletada na tela:", font_name="helvB", spacing_after=4)
        rect = fitz.Rect(42, y, 252, y + 90)
        page.draw_rect(rect, color=(0.55, 0.45, 0.38), width=0.8)
        page.insert_image(rect, stream=image_bytes, keep_proportion=True)
        y = rect.y1 + 14
    else:
        page, y = write_contract_paragraph(document, page, y, "Documento preparado para assinatura digital externa em Gov.br, certificado ou outra plataforma compativel.")
        if y > 700:
            page = new_contract_page(document)
            y = 48
        signature_rect = fitz.Rect(42, y + 10, 300, y + 68)
        page.draw_rect(signature_rect, color=(0.55, 0.45, 0.38), width=0.8)
        page.insert_text((50, y + 36), "Area reservada para assinatura digital externa", fontsize=10, fontname="helv", fill=(0.35, 0.26, 0.18))
        y = signature_rect.y1 + 14

    if y > 720:
        page = new_contract_page(document)
        y = 48

    page.draw_line(fitz.Point(42, y + 18), fitz.Point(250, y + 18), color=(0.35, 0.24, 0.16), width=0.8)
    page.insert_text((42, y + 34), signature['signerName'], fontsize=10, fontname="helv")
    page.insert_text((42, y + 48), "Contratante", fontsize=9, fontname="helv")

    page.draw_line(fitz.Point(320, y + 18), fitz.Point(552, y + 18), color=(0.35, 0.24, 0.16), width=0.8)
    page.insert_text((320, y + 34), contractor['representative'] or contractor['studioName'], fontsize=10, fontname="helv")
    page.insert_text((320, y + 48), "Tatuador / representante do estudio", fontsize=9, fontname="helv")

    pdf_bytes = document.tobytes(garbage=4, deflate=True)
    document.close()
    return pdf_bytes, contract


def save_tatoo_contract_pdf(payload: Any) -> dict[str, Any]:
    pdf_bytes, contract = build_tatoo_contract_pdf(payload)
    signature = contract['signature']
    file_name = build_contract_filename(
        contract['consentId'],
        signature['signerName'],
        signature['signedAt'],
        signature['signatureMode'],
    )
    target_dir = CONTRACTS_DIR / contract_folder_name(signature['signedAt'])
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = ensure_unique_file_path(target_dir, file_name)
    target_file.write_bytes(pdf_bytes)
    relative_path = target_file.relative_to(CONTRACTS_DIR).as_posix()
    return {
        'fileName': target_file.name,
        'path': relative_path,
        'url': build_contract_url(relative_path),
        'size': len(pdf_bytes),
        'savedAt': now_iso(),
        'signedAt': signature['signedAt'],
        'signatureMode': signature['signatureMode'],
    }


DATA_DIR = resolve_data_dir()
ATTACHMENTS_DIR = resolve_attachment_dir()
CONTRACTS_DIR = resolve_contracts_dir()
DATABASE_URL = build_database_url(DATA_DIR)
DB_PROVIDER = detect_provider(DATABASE_URL)
LEGACY_STORE_TABLE = assert_table_name(os.getenv("LEGACY_STORE_TABLE", "app_stores"))
FINANCE_ATTACHMENT_MAX_SIZE = parse_size(os.getenv("FINANCE_ATTACHMENT_MAX_SIZE", "15mb")) or 15 * 1024 * 1024
FINANCE_REMINDER_LOOKAHEAD_DAYS = max(as_int(os.getenv("FINANCE_REMINDER_LOOKAHEAD_DAYS"), 3), 0)


class Base(DeclarativeBase):
    pass


class StoreMetadata(Base):
    __tablename__ = "store_metadata"

    store_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)


class SiteApp(Base):
    __tablename__ = "site_apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    href: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GpsSong(Base):
    __tablename__ = "gps_songs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    artist: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    song_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    audio_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audio_mime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)


class GpsSongTag(Base):
    __tablename__ = "gps_song_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    value: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class GpsSongBlock(Base):
    __tablename__ = "gps_song_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    block_type: Mapped[str] = mapped_column(String(80), nullable=False, default="Bloco")
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    chords: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lyrics: Mapped[str] = mapped_column(Text, nullable=False, default="")
    time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)


class FinanceConfig(Base):
    __tablename__ = "financeiro_config"

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    tol_dias: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    tol_valor: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    score_min: Mapped[int] = mapped_column(Integer, nullable=False, default=60)


class FinanceAccount(Base):
    __tablename__ = "financeiro_accounts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    moeda: Mapped[str] = mapped_column(String(16), nullable=False, default="BRL")
    saldo_inicial: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class FinanceCategory(Base):
    __tablename__ = "financeiro_categories"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    tipo: Mapped[str] = mapped_column(String(32), nullable=False)


class FinanceTransaction(Base):
    __tablename__ = "financeiro_transactions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data: Mapped[str] = mapped_column(String(16), nullable=False)
    conta_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(32), nullable=False)
    categoria_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    descricao: Mapped[str] = mapped_column(Text, nullable=False, default="")
    valor: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    conciliado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bank_tx_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class FinanceImport(Base):
    __tablename__ = "financeiro_imports"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conta_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, default="import.ofx")


class FinanceBankTransaction(Base):
    __tablename__ = "financeiro_bank_transactions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    import_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fitid: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    memo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trntype: Mapped[str] = mapped_column(String(80), nullable=False, default="")


class FinanceReconciliation(Base):
    __tablename__ = "financeiro_reconciliations"

    bank_tx_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    lanc_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FinanceTitle(Base):
    __tablename__ = "financeiro_titles"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tipo: Mapped[str] = mapped_column(String(16), nullable=False)
    pessoa: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    descricao: Mapped[str] = mapped_column(Text, nullable=False, default="")
    categoria_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    conta_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    valor: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    vencimento: Mapped[str] = mapped_column(String(16), nullable=False)
    centro_custo: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    observacoes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ABERTO")
    baixado_em: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lanc_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    bank_tx_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class FinanceTitleAttachment(Base):
    __tablename__ = "financeiro_title_attachments"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(255), nullable=False, default="application/octet-stream")
    data_url: Mapped[str] = mapped_column(Text, nullable=False)


class FinancePurchaseRequest(Base):
    __tablename__ = "financeiro_purchase_requests"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDENTE")
    descricao: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fornecedor: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    produto_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    foto_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    justificativa: Mapped[str] = mapped_column(Text, nullable=False, default="")
    categoria_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    conta_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    centro_custo: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    valor: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    vencimento: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    forma_pagamento: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    observacoes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    title_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rejected_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class FinanceReminderLog(Base):
    __tablename__ = "financeiro_reminder_logs"

    reminder_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    title_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    reminder_type: Mapped[str] = mapped_column(String(32), nullable=False)
    due_date: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)


engine_kwargs: dict[str, object] = {"pool_pre_ping": True}

if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
elif DB_PROVIDER == "postgres" and parse_bool(os.getenv("DB_SSL"), False):
    engine_kwargs["connect_args"] = {"sslmode": "require"}
elif DB_PROVIDER == "mysql" and parse_bool(os.getenv("DB_SSL"), False):
    engine_kwargs["connect_args"] = {"ssl": {}}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base.metadata.create_all(engine)
LEGACY_TABLE_AVAILABLE = inspect(engine).has_table(LEGACY_STORE_TABLE)


def default_finance_config() -> dict[str, Any]:
    return {"tolDias": 3, "tolValor": 0.5, "scoreMin": 60}


def br_currency(value: float) -> str:
    formatted = f"{float(value):,.2f}"
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def normalize_finance_attachment(raw_attachment: Any, title_index: int, attachment_index: int) -> dict[str, Any] | None:
    if not isinstance(raw_attachment, dict):
        return None

    payload = decode_attachment_payload(as_text(raw_attachment.get("dataUrl")))
    data_url = as_text(raw_attachment.get("dataUrl"))
    if data_url and not data_url.startswith("data:"):
        data_url = payload.get("dataUrl", "")

    return {
        "id": as_text(raw_attachment.get("id")) or f"anx-{title_index + 1}-{attachment_index + 1}",
        "sort_order": attachment_index,
        "name": as_text(raw_attachment.get("name")),
        "mime": as_text(raw_attachment.get("mime"), "application/octet-stream") or "application/octet-stream",
        "dataUrl": data_url,
        "url": as_text(raw_attachment.get("url")) or payload.get("url", ""),
        "path": as_text(raw_attachment.get("path")) or payload.get("path", ""),
        "size": as_int(raw_attachment.get("size"), payload.get("size", 0)),
        "storage": as_text(raw_attachment.get("storage")) or payload.get("storage", "filesystem"),
    }


def build_attachment_metadata(file_name: str, mime_type: str, relative_path: str, size: int) -> dict[str, Any]:
    return {
        "name": file_name,
        "mime": mime_type or "application/octet-stream",
        "url": build_attachment_url(relative_path),
        "path": relative_path,
        "size": size,
        "storage": "filesystem",
        "dataUrl": "",
    }


def save_finance_attachment(*, file_name: str, mime_type: str, vencimento: str, conta_nome: str, pessoa: str, descricao: str, content: bytes) -> dict[str, Any]:
    folder_name = attachment_folder_name(vencimento)
    target_dir = ATTACHMENTS_DIR / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    desired_name = build_attachment_filename(vencimento, conta_nome, pessoa, descricao, file_name, mime_type)
    target_file = ensure_unique_file_path(target_dir, desired_name)
    target_file.write_bytes(content)

    relative_path = target_file.relative_to(ATTACHMENTS_DIR).as_posix()
    return build_attachment_metadata(target_file.name, mime_type, relative_path, len(content))


def delete_finance_attachment_file(relative_path: str) -> bool:
    candidate = ensure_within_directory(ATTACHMENTS_DIR, ATTACHMENTS_DIR / relative_path)
    if not candidate.exists() or not candidate.is_file():
        return False

    candidate.unlink()
    cleanup_empty_attachment_dirs(candidate.parent)
    return True


def finance_reminder_subject(reference_date: date) -> str:
    return f"Financeiro Nanotech | Avisos de vencimento | {reference_date.strftime('%d/%m/%Y')}"


def finance_reminder_body(items: list[dict[str, Any]], reference_date: date) -> str:
    upcoming = [item for item in items if item["type"] == "upcoming"]
    overdue = [item for item in items if item["type"] == "overdue"]

    lines = [
        "Avisos de vencimento do Financeiro Nanotech",
        f"Data de referencia: {reference_date.strftime('%d/%m/%Y')}",
        "",
    ]

    if overdue:
        lines.append("Titulos vencidos:")
        for item in overdue:
            lines.append(
                f"- {item['titulo'].tipo} | venc. {item['titulo'].vencimento} | {item['conta_nome']} | "
                f"{item['titulo'].pessoa or '-'} | {item['titulo'].descricao} | R$ {br_currency(item['titulo'].valor)}"
            )
        lines.append("")

    if upcoming:
        lines.append(f"Titulos a vencer nos proximos {FINANCE_REMINDER_LOOKAHEAD_DAYS} dia(s):")
        for item in upcoming:
            lines.append(
                f"- {item['titulo'].tipo} | venc. {item['titulo'].vencimento} | {item['conta_nome']} | "
                f"{item['titulo'].pessoa or '-'} | {item['titulo'].descricao} | R$ {br_currency(item['titulo'].valor)}"
            )
        lines.append("")

    lines.append("Mensagem gerada automaticamente pelo sistema.")
    return "\n".join(lines)


def send_email_message(settings: dict[str, Any], subject: str, body: str) -> None:
    sender = settings["from"] or settings["username"]
    if not sender:
        raise AppError(503, "Configure FINANCE_REMINDER_FROM ou SMTP_USER para enviar avisos.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(settings["to"])
    message.set_content(body)

    timeout = 20
    if settings["use_ssl"]:
        with smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=timeout) as smtp:
            if settings["username"]:
                smtp.login(settings["username"], settings["password"])
            smtp.send_message(message)
        return

    with smtplib.SMTP(settings["host"], settings["port"], timeout=timeout) as smtp:
        if settings["use_tls"]:
            smtp.starttls()
        if settings["username"]:
            smtp.login(settings["username"], settings["password"])
        smtp.send_message(message)


def collect_finance_reminders(session, reference_date: date) -> list[dict[str, Any]]:
    accounts = {
        account.id: account.nome
        for account in session.execute(select(FinanceAccount).order_by(FinanceAccount.sort_order, FinanceAccount.id)).scalars().all()
    }
    titles = session.execute(select(FinanceTitle).order_by(FinanceTitle.vencimento, FinanceTitle.id)).scalars().all()

    items: list[dict[str, Any]] = []
    upcoming_limit = reference_date + timedelta(days=FINANCE_REMINDER_LOOKAHEAD_DAYS)

    for title in titles:
        if title.status != "ABERTO":
            continue

        due_date = as_date(title.vencimento)
        if due_date is None:
            continue

        reminder_type = ""
        if due_date < reference_date:
            reminder_type = "overdue"
        elif due_date <= upcoming_limit:
            reminder_type = "upcoming"
        else:
            continue

        reminder_key = f"{reminder_type}:{title.id}:{title.vencimento}"
        if session.get(FinanceReminderLog, reminder_key) is not None:
            continue

        items.append(
            {
                "key": reminder_key,
                "type": reminder_type,
                "titulo": title,
                "conta_nome": accounts.get(title.conta_id, "Conta nao informada"),
            }
        )

    return items


def run_finance_reminders(session) -> dict[str, Any]:
    settings = finance_mail_settings()
    if not settings["enabled"]:
        return {
            "enabled": False,
            "sent": 0,
            "pending": 0,
            "message": "Avisos por e-mail nao configurados.",
        }

    reference_date = datetime.now().date()
    items = collect_finance_reminders(session, reference_date)
    if not items:
        return {
            "enabled": True,
            "sent": 0,
            "pending": 0,
            "message": "Nenhum aviso novo para enviar.",
        }

    send_email_message(settings, finance_reminder_subject(reference_date), finance_reminder_body(items, reference_date))

    for item in items:
        session.add(
            FinanceReminderLog(
                reminder_key=item["key"],
                title_id=item["titulo"].id,
                reminder_type=item["type"],
                due_date=item["titulo"].vencimento,
            )
        )

    return {
        "enabled": True,
        "sent": len(items),
        "pending": len(items),
        "message": f"{len(items)} aviso(s) enviado(s).",
    }


def ensure_supported_store(store_id: str) -> str:
    safe_store_id = assert_store_id(store_id)
    if safe_store_id not in SUPPORTED_STORES:
        raise AppError(404, "Store not found.")
    return safe_store_id


def store_exists(session, store_id: str) -> bool:
    return session.get(StoreMetadata, store_id) is not None


def touch_store(session, store_id: str) -> str:
    timestamp = now_iso()
    metadata = session.get(StoreMetadata, store_id)
    if metadata is None:
        metadata = StoreMetadata(store_id=store_id, updated_at=timestamp)
        session.add(metadata)
    else:
        metadata.updated_at = timestamp
    return timestamp


def delete_store_metadata(session, store_id: str) -> None:
    metadata = session.get(StoreMetadata, store_id)
    if metadata is not None:
        session.delete(metadata)


def get_store_updated_at(session, store_id: str) -> str:
    metadata = session.get(StoreMetadata, store_id)
    return metadata.updated_at if metadata is not None else now_iso()


def decode_legacy_payload(raw_payload: Any) -> Any:
    if raw_payload is None:
        return None
    if isinstance(raw_payload, memoryview):
        raw_payload = raw_payload.tobytes()
    if isinstance(raw_payload, (bytes, bytearray)):
        raw_payload = raw_payload.decode("utf-8")
    if isinstance(raw_payload, str):
        return json.loads(raw_payload)
    return raw_payload


def read_legacy_payload(session, store_id: str) -> Any | None:
    if not LEGACY_TABLE_AVAILABLE:
        return None

    query = text(f"SELECT payload FROM {LEGACY_STORE_TABLE} WHERE store_id = :store_id")
    result = session.execute(query, {"store_id": store_id}).mappings().first()
    if result is None:
        return None
    return decode_legacy_payload(result["payload"])


def normalize_site_apps(value: Any) -> list[dict[str, str]]:
    raw_apps = value.get("apps") if isinstance(value, dict) else value
    if not isinstance(raw_apps, list):
        return []

    apps: list[dict[str, str]] = []
    used_slugs: set[str] = set()

    for index, raw_app in enumerate(raw_apps):
        if not isinstance(raw_app, dict):
            continue

        name = as_text(raw_app.get("nome") or raw_app.get("name"))
        description = as_text(raw_app.get("descricao") or raw_app.get("description"))
        href = as_text(raw_app.get("href"))

        if not name or not href:
            continue

        preferred_slug = as_text(raw_app.get("slug")) or slugify(href or name or f"app-{index + 1}")
        slug = unique_slug(preferred_slug, used_slugs)
        apps.append({"slug": slug, "nome": name, "descricao": description, "href": href})

    return apps


def normalize_gps_songs(value: Any) -> list[dict[str, Any]]:
    raw_songs = value.get("songs") if isinstance(value, dict) else value
    if not isinstance(raw_songs, list):
        return []

    songs: list[dict[str, Any]] = []

    for song_index, raw_song in enumerate(raw_songs):
        if not isinstance(raw_song, dict):
            continue

        song_id = as_text(raw_song.get("id")) or f"song-{song_index + 1}"
        tags = raw_song.get("tags") if isinstance(raw_song.get("tags"), list) else []
        blocks = raw_song.get("blocks") if isinstance(raw_song.get("blocks"), list) else []
        audio_meta = raw_song.get("audioMeta") if isinstance(raw_song.get("audioMeta"), dict) else None
        created_at = as_text(raw_song.get("createdAt")) or now_iso()
        updated_at = as_text(raw_song.get("updatedAt")) or created_at

        songs.append(
            {
                "id": song_id,
                "sort_order": song_index,
                "title": as_text(raw_song.get("title")),
                "artist": as_text(raw_song.get("artist")),
                "song_key": as_text(raw_song.get("key")),
                "notes": as_text(raw_song.get("notes")),
                "audio_name": as_text(audio_meta.get("name")) if audio_meta else None,
                "audio_mime": as_text(audio_meta.get("mime")) if audio_meta else None,
                "created_at": created_at,
                "updated_at": updated_at,
                "tags": [as_text(tag) for tag in tags if as_text(tag)],
                "blocks": [
                    {
                        "sort_order": block_index,
                        "block_type": as_text(raw_block.get("type"), "Bloco") or "Bloco",
                        "title": as_text(raw_block.get("title")),
                        "chords": as_text(raw_block.get("chords")),
                        "lyrics": as_text(raw_block.get("lyrics")),
                        "time_sec": as_float(raw_block.get("timeSec")) if raw_block.get("timeSec") not in (None, "") else None,
                    }
                    for block_index, raw_block in enumerate(blocks)
                    if isinstance(raw_block, dict)
                ],
            }
        )

    return songs


def normalize_finance_state(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}

    raw_config = data.get("config") if isinstance(data.get("config"), dict) else {}
    raw_accounts = data.get("contas") if isinstance(data.get("contas"), list) else []
    raw_categories = data.get("categorias") if isinstance(data.get("categorias"), list) else []
    raw_transactions = data.get("lancamentos") if isinstance(data.get("lancamentos"), list) else []
    raw_imports = data.get("imports") if isinstance(data.get("imports"), list) else []
    raw_reconciliations = data.get("reconciliations") if isinstance(data.get("reconciliations"), list) else []
    raw_titles = data.get("titulos") if isinstance(data.get("titulos"), list) else []
    raw_purchases = data.get("compras") if isinstance(data.get("compras"), list) else []

    return {
        "config": {
            "tolDias": as_int(raw_config.get("tolDias"), 3),
            "tolValor": as_float(raw_config.get("tolValor"), 0.5),
            "scoreMin": as_int(raw_config.get("scoreMin"), 60),
        },
        "contas": [
            {
                "id": as_text(raw_account.get("id")) or f"conta-{index + 1}",
                "sort_order": index,
                "nome": as_text(raw_account.get("nome")),
                "moeda": as_text(raw_account.get("moeda"), "BRL") or "BRL",
                "saldoInicial": as_float(raw_account.get("saldoInicial"), 0.0),
            }
            for index, raw_account in enumerate(raw_accounts)
            if isinstance(raw_account, dict)
        ],
        "categorias": [
            {
                "id": as_text(raw_category.get("id")) or f"cat-{index + 1}",
                "sort_order": index,
                "nome": as_text(raw_category.get("nome")),
                "tipo": as_text(raw_category.get("tipo"), "DESPESA") or "DESPESA",
            }
            for index, raw_category in enumerate(raw_categories)
            if isinstance(raw_category, dict)
        ],
        "lancamentos": [
            {
                "id": as_text(raw_transaction.get("id")) or f"lanc-{index + 1}",
                "sort_order": index,
                "data": as_text(raw_transaction.get("data")),
                "contaId": as_text(raw_transaction.get("contaId")),
                "tipo": as_text(raw_transaction.get("tipo"), "DESPESA") or "DESPESA",
                "categoriaId": as_text(raw_transaction.get("categoriaId")),
                "desc": as_text(raw_transaction.get("desc")),
                "valor": as_float(raw_transaction.get("valor"), 0.0),
                "conciliado": as_bool(raw_transaction.get("conciliado"), False),
                "bankTxId": as_text(raw_transaction.get("bankTxId")) or None,
            }
            for index, raw_transaction in enumerate(raw_transactions)
            if isinstance(raw_transaction, dict)
        ],
        "imports": [
            {
                "id": as_text(raw_import.get("id")) or f"imp-{index + 1}",
                "sort_order": index,
                "contaId": as_text(raw_import.get("contaId")),
                "createdAt": as_text(raw_import.get("createdAt")) or now_iso(),
                "fileName": as_text(raw_import.get("fileName"), "import.ofx") or "import.ofx",
                "txs": [
                    {
                        "id": as_text(raw_tx.get("id")) or f"banktx-{index + 1}-{tx_index + 1}",
                        "sort_order": tx_index,
                        "date": as_text(raw_tx.get("date")),
                        "amount": as_float(raw_tx.get("amount"), 0.0),
                        "fitid": as_text(raw_tx.get("fitid")),
                        "memo": as_text(raw_tx.get("memo")),
                        "trntype": as_text(raw_tx.get("trntype")),
                    }
                    for tx_index, raw_tx in enumerate(raw_import.get("txs") if isinstance(raw_import.get("txs"), list) else [])
                    if isinstance(raw_tx, dict)
                ],
            }
            for index, raw_import in enumerate(raw_imports)
            if isinstance(raw_import, dict)
        ],
        "reconciliations": [
            {
                "bankTxId": as_text(raw_reconciliation.get("bankTxId")),
                "lancId": as_text(raw_reconciliation.get("lancId")),
                "sort_order": index,
            }
            for index, raw_reconciliation in enumerate(raw_reconciliations)
            if isinstance(raw_reconciliation, dict)
            and as_text(raw_reconciliation.get("bankTxId"))
            and as_text(raw_reconciliation.get("lancId"))
        ],
        "titulos": [
            {
                "id": as_text(raw_title.get("id")) or f"tit-{index + 1}",
                "sort_order": index,
                "tipo": as_text(raw_title.get("tipo"), "AP") or "AP",
                "pessoa": as_text(raw_title.get("pessoa")),
                "desc": as_text(raw_title.get("desc")),
                "categoriaId": as_text(raw_title.get("categoriaId")),
                "contaId": as_text(raw_title.get("contaId")),
                "valor": as_float(raw_title.get("valor"), 0.0),
                "vencimento": as_text(raw_title.get("vencimento")),
                "centroCusto": as_text(raw_title.get("centroCusto")),
                "obs": as_text(raw_title.get("obs")),
                "status": as_text(raw_title.get("status"), "ABERTO") or "ABERTO",
                "baixadoEm": as_text(raw_title.get("baixadoEm")) or None,
                "lancId": as_text(raw_title.get("lancId")) or None,
                "bankTxId": as_text(raw_title.get("bankTxId")) or None,
                "anexos": [
                    attachment
                    for attachment_index, raw_attachment in enumerate(raw_title.get("anexos") if isinstance(raw_title.get("anexos"), list) else [])
                    if (attachment := normalize_finance_attachment(raw_attachment, index, attachment_index)) is not None
                ],
            }
            for index, raw_title in enumerate(raw_titles)
            if isinstance(raw_title, dict)
        ],
        "compras": [
            {
                "id": as_text(raw_purchase.get("id")) or f"compra-{index + 1}",
                "sort_order": index,
                "requestedAt": as_text(raw_purchase.get("requestedAt")) or now_iso(),
                "status": as_text(raw_purchase.get("status"), "PENDENTE") or "PENDENTE",
                "desc": as_text(raw_purchase.get("desc")),
                "fornecedor": as_text(raw_purchase.get("fornecedor")),
                "produtoUrl": as_text(raw_purchase.get("produtoUrl")),
                "fotoUrl": as_text(raw_purchase.get("fotoUrl")),
                "justificativa": as_text(raw_purchase.get("justificativa")),
                "categoriaId": as_text(raw_purchase.get("categoriaId")),
                "contaId": as_text(raw_purchase.get("contaId")),
                "centroCusto": as_text(raw_purchase.get("centroCusto")),
                "valor": as_float(raw_purchase.get("valor"), 0.0),
                "vencimento": as_text(raw_purchase.get("vencimento")),
                "formaPagamento": as_text(raw_purchase.get("formaPagamento")),
                "obs": as_text(raw_purchase.get("obs")),
                "titleId": as_text(raw_purchase.get("titleId")) or None,
                "approvedAt": as_text(raw_purchase.get("approvedAt")) or None,
                "rejectedAt": as_text(raw_purchase.get("rejectedAt")) or None,
            }
            for index, raw_purchase in enumerate(raw_purchases)
            if isinstance(raw_purchase, dict)
        ],
    }


def ensure_site_store(session) -> None:
    has_apps = session.execute(select(SiteApp.id).limit(1)).first() is not None
    if has_apps and store_exists(session, STORE_SITE):
        existing_apps = session.execute(select(SiteApp).order_by(SiteApp.sort_order, SiteApp.id)).scalars().all()
        existing_slugs = {app.slug for app in existing_apps}
        missing_defaults = [app for app in DEFAULT_SITE_APPS if app["slug"] not in existing_slugs]
        if not missing_defaults:
            return

        next_sort_order = max((app.sort_order for app in existing_apps), default=-1) + 1
        for app in missing_defaults:
            session.add(
                SiteApp(
                    slug=app["slug"],
                    name=app["nome"],
                    description=app["descricao"],
                    href=app["href"],
                    sort_order=next_sort_order,
                )
            )
            next_sort_order += 1

        touch_store(session, STORE_SITE)
        return
    replace_site_store(session, DEFAULT_SITE_APPS)


def read_site_store(session) -> list[dict[str, str]]:
    ensure_site_store(session)
    apps = session.execute(select(SiteApp).order_by(SiteApp.sort_order, SiteApp.id)).scalars().all()
    return [
        {"slug": app.slug, "nome": app.name, "descricao": app.description, "href": app.href}
        for app in apps
    ]


def replace_site_store(session, value: Any) -> str:
    apps = normalize_site_apps(value)
    session.execute(delete(SiteApp))

    for index, app in enumerate(apps):
        session.add(
            SiteApp(
                slug=app["slug"],
                name=app["nome"],
                description=app["descricao"],
                href=app["href"],
                sort_order=index,
            )
        )

    return touch_store(session, STORE_SITE)


def clear_site_store(session) -> None:
    session.execute(delete(SiteApp))
    delete_store_metadata(session, STORE_SITE)


def read_gps_store(session) -> list[dict[str, Any]]:
    songs = session.execute(select(GpsSong).order_by(GpsSong.sort_order, GpsSong.id)).scalars().all()
    tags = session.execute(select(GpsSongTag).order_by(GpsSongTag.song_id, GpsSongTag.sort_order, GpsSongTag.id)).scalars().all()
    blocks = session.execute(
        select(GpsSongBlock).order_by(GpsSongBlock.song_id, GpsSongBlock.sort_order, GpsSongBlock.id)
    ).scalars().all()

    tags_by_song: dict[str, list[str]] = {}
    for tag in tags:
        tags_by_song.setdefault(tag.song_id, []).append(tag.value)

    blocks_by_song: dict[str, list[dict[str, Any]]] = {}
    for block in blocks:
        blocks_by_song.setdefault(block.song_id, []).append(
            {
                "type": block.block_type,
                "title": block.title,
                "chords": block.chords,
                "lyrics": block.lyrics,
                "timeSec": block.time_sec,
            }
        )

    payload: list[dict[str, Any]] = []
    for song in songs:
        audio_meta = None
        if song.audio_name or song.audio_mime:
            audio_meta = {"name": song.audio_name or "", "mime": song.audio_mime or ""}

        payload.append(
            {
                "id": song.id,
                "title": song.title,
                "artist": song.artist,
                "key": song.song_key,
                "tags": tags_by_song.get(song.id, []),
                "notes": song.notes,
                "audioMeta": audio_meta,
                "blocks": blocks_by_song.get(song.id, []),
                "createdAt": song.created_at,
                "updatedAt": song.updated_at,
            }
        )

    return payload


def replace_gps_store(session, value: Any) -> str:
    songs = normalize_gps_songs(value)

    session.execute(delete(GpsSongBlock))
    session.execute(delete(GpsSongTag))
    session.execute(delete(GpsSong))

    for song in songs:
        session.add(
            GpsSong(
                id=song["id"],
                sort_order=song["sort_order"],
                title=song["title"],
                artist=song["artist"],
                song_key=song["song_key"],
                notes=song["notes"],
                audio_name=song["audio_name"],
                audio_mime=song["audio_mime"],
                created_at=song["created_at"],
                updated_at=song["updated_at"],
            )
        )

        for tag_index, tag_value in enumerate(song["tags"]):
            session.add(GpsSongTag(song_id=song["id"], sort_order=tag_index, value=tag_value))

        for block in song["blocks"]:
            session.add(
                GpsSongBlock(
                    song_id=song["id"],
                    sort_order=block["sort_order"],
                    block_type=block["block_type"],
                    title=block["title"],
                    chords=block["chords"],
                    lyrics=block["lyrics"],
                    time_sec=block["time_sec"],
                )
            )

    return touch_store(session, STORE_GPS)


def clear_gps_store(session) -> None:
    session.execute(delete(GpsSongBlock))
    session.execute(delete(GpsSongTag))
    session.execute(delete(GpsSong))
    delete_store_metadata(session, STORE_GPS)


def read_finance_store(session) -> dict[str, Any]:
    config = session.get(FinanceConfig, 1)
    accounts = session.execute(select(FinanceAccount).order_by(FinanceAccount.sort_order, FinanceAccount.id)).scalars().all()
    categories = session.execute(select(FinanceCategory).order_by(FinanceCategory.sort_order, FinanceCategory.id)).scalars().all()
    transactions = session.execute(
        select(FinanceTransaction).order_by(FinanceTransaction.sort_order, FinanceTransaction.id)
    ).scalars().all()
    imports = session.execute(select(FinanceImport).order_by(FinanceImport.sort_order, FinanceImport.id)).scalars().all()
    bank_transactions = session.execute(
        select(FinanceBankTransaction).order_by(
            FinanceBankTransaction.import_id,
            FinanceBankTransaction.sort_order,
            FinanceBankTransaction.id,
        )
    ).scalars().all()
    reconciliations = session.execute(
        select(FinanceReconciliation).order_by(FinanceReconciliation.sort_order, FinanceReconciliation.bank_tx_id)
    ).scalars().all()
    titles = session.execute(select(FinanceTitle).order_by(FinanceTitle.sort_order, FinanceTitle.id)).scalars().all()
    purchases = session.execute(
        select(FinancePurchaseRequest).order_by(FinancePurchaseRequest.sort_order, FinancePurchaseRequest.id)
    ).scalars().all()
    attachments = session.execute(
        select(FinanceTitleAttachment).order_by(
            FinanceTitleAttachment.title_id,
            FinanceTitleAttachment.sort_order,
            FinanceTitleAttachment.id,
        )
    ).scalars().all()

    bank_tx_by_import: dict[str, list[dict[str, Any]]] = {}
    for bank_tx in bank_transactions:
        bank_tx_by_import.setdefault(bank_tx.import_id, []).append(
            {
                "id": bank_tx.id,
                "date": bank_tx.data,
                "amount": bank_tx.amount,
                "fitid": bank_tx.fitid,
                "memo": bank_tx.memo,
                "trntype": bank_tx.trntype,
            }
        )

    attachments_by_title: dict[str, list[dict[str, Any]]] = {}
    for attachment in attachments:
        attachment_payload = decode_attachment_payload(attachment.data_url)
        attachments_by_title.setdefault(attachment.title_id, []).append(
            {
                "id": attachment.id,
                "name": attachment.name,
                "mime": attachment.mime,
                "dataUrl": attachment_payload.get("dataUrl", ""),
                "url": attachment_payload.get("url", ""),
                "path": attachment_payload.get("path", ""),
                "size": attachment_payload.get("size", 0),
                "storage": attachment_payload.get("storage", "filesystem"),
            }
        )

    cfg = default_finance_config()
    return {
        "contas": [
            {"id": account.id, "nome": account.nome, "moeda": account.moeda, "saldoInicial": account.saldo_inicial}
            for account in accounts
        ],
        "categorias": [
            {"id": category.id, "nome": category.nome, "tipo": category.tipo}
            for category in categories
        ],
        "lancamentos": [
            {
                "id": transaction.id,
                "data": transaction.data,
                "contaId": transaction.conta_id,
                "tipo": transaction.tipo,
                "categoriaId": transaction.categoria_id,
                "desc": transaction.descricao,
                "valor": transaction.valor,
                "conciliado": transaction.conciliado,
                "bankTxId": transaction.bank_tx_id,
            }
            for transaction in transactions
        ],
        "imports": [
            {
                "id": imported.id,
                "contaId": imported.conta_id,
                "createdAt": imported.created_at,
                "fileName": imported.file_name,
                "txs": bank_tx_by_import.get(imported.id, []),
            }
            for imported in imports
        ],
        "reconciliations": [
            {"bankTxId": reconciliation.bank_tx_id, "lancId": reconciliation.lanc_id}
            for reconciliation in reconciliations
        ],
        "titulos": [
            {
                "id": title.id,
                "tipo": title.tipo,
                "pessoa": title.pessoa,
                "desc": title.descricao,
                "categoriaId": title.categoria_id,
                "contaId": title.conta_id,
                "valor": title.valor,
                "vencimento": title.vencimento,
                "centroCusto": title.centro_custo,
                "obs": title.observacoes,
                "status": title.status,
                "baixadoEm": title.baixado_em,
                "lancId": title.lanc_id,
                "bankTxId": title.bank_tx_id,
                "anexos": attachments_by_title.get(title.id, []),
            }
            for title in titles
        ],
        "compras": [
            {
                "id": purchase.id,
                "requestedAt": purchase.requested_at,
                "status": purchase.status,
                "desc": purchase.descricao,
                "fornecedor": purchase.fornecedor,
                "produtoUrl": purchase.produto_url,
                "fotoUrl": purchase.foto_url,
                "justificativa": purchase.justificativa,
                "categoriaId": purchase.categoria_id,
                "contaId": purchase.conta_id,
                "centroCusto": purchase.centro_custo,
                "valor": purchase.valor,
                "vencimento": purchase.vencimento,
                "formaPagamento": purchase.forma_pagamento,
                "obs": purchase.observacoes,
                "titleId": purchase.title_id,
                "approvedAt": purchase.approved_at,
                "rejectedAt": purchase.rejected_at,
            }
            for purchase in purchases
        ],
        "config": {
            "tolDias": config.tol_dias if config is not None else cfg["tolDias"],
            "tolValor": config.tol_valor if config is not None else cfg["tolValor"],
            "scoreMin": config.score_min if config is not None else cfg["scoreMin"],
        },
    }


def replace_finance_store(session, value: Any) -> str:
    state = normalize_finance_state(value)

    session.execute(delete(FinanceReconciliation))
    session.execute(delete(FinanceTitleAttachment))
    session.execute(delete(FinancePurchaseRequest))
    session.execute(delete(FinanceTitle))
    session.execute(delete(FinanceBankTransaction))
    session.execute(delete(FinanceImport))
    session.execute(delete(FinanceTransaction))
    session.execute(delete(FinanceCategory))
    session.execute(delete(FinanceAccount))
    session.execute(delete(FinanceConfig))

    config = state["config"]
    session.add(
        FinanceConfig(
            singleton_id=1,
            tol_dias=config["tolDias"],
            tol_valor=config["tolValor"],
            score_min=config["scoreMin"],
        )
    )

    for account in state["contas"]:
        session.add(
            FinanceAccount(
                id=account["id"],
                sort_order=account["sort_order"],
                nome=account["nome"],
                moeda=account["moeda"],
                saldo_inicial=account["saldoInicial"],
            )
        )

    for category in state["categorias"]:
        session.add(
            FinanceCategory(
                id=category["id"],
                sort_order=category["sort_order"],
                nome=category["nome"],
                tipo=category["tipo"],
            )
        )

    for transaction in state["lancamentos"]:
        session.add(
            FinanceTransaction(
                id=transaction["id"],
                sort_order=transaction["sort_order"],
                data=transaction["data"],
                conta_id=transaction["contaId"],
                tipo=transaction["tipo"],
                categoria_id=transaction["categoriaId"],
                descricao=transaction["desc"],
                valor=transaction["valor"],
                conciliado=transaction["conciliado"],
                bank_tx_id=transaction["bankTxId"],
            )
        )

    for imported in state["imports"]:
        session.add(
            FinanceImport(
                id=imported["id"],
                sort_order=imported["sort_order"],
                conta_id=imported["contaId"],
                created_at=imported["createdAt"],
                file_name=imported["fileName"],
            )
        )

        for bank_tx in imported["txs"]:
            session.add(
                FinanceBankTransaction(
                    id=bank_tx["id"],
                    import_id=imported["id"],
                    sort_order=bank_tx["sort_order"],
                    data=bank_tx["date"],
                    amount=bank_tx["amount"],
                    fitid=bank_tx["fitid"],
                    memo=bank_tx["memo"],
                    trntype=bank_tx["trntype"],
                )
            )

    for reconciliation in state["reconciliations"]:
        session.add(
            FinanceReconciliation(
                bank_tx_id=reconciliation["bankTxId"],
                lanc_id=reconciliation["lancId"],
                sort_order=reconciliation["sort_order"],
            )
        )

    for title in state["titulos"]:
        session.add(
            FinanceTitle(
                id=title["id"],
                sort_order=title["sort_order"],
                tipo=title["tipo"],
                pessoa=title["pessoa"],
                descricao=title["desc"],
                categoria_id=title["categoriaId"],
                conta_id=title["contaId"],
                valor=title["valor"],
                vencimento=title["vencimento"],
                centro_custo=title["centroCusto"],
                observacoes=title["obs"],
                status=title["status"],
                baixado_em=title["baixadoEm"],
                lanc_id=title["lancId"],
                bank_tx_id=title["bankTxId"],
            )
        )

        for attachment in title["anexos"]:
            session.add(
                FinanceTitleAttachment(
                    id=attachment["id"],
                    title_id=title["id"],
                    sort_order=attachment["sort_order"],
                    name=attachment["name"],
                    mime=attachment["mime"],
                    data_url=encode_attachment_payload(attachment),
                )
            )

    for purchase in state["compras"]:
        session.add(
            FinancePurchaseRequest(
                id=purchase["id"],
                sort_order=purchase["sort_order"],
                requested_at=purchase["requestedAt"],
                status=purchase["status"],
                descricao=purchase["desc"],
                fornecedor=purchase["fornecedor"],
                produto_url=purchase["produtoUrl"],
                foto_url=purchase["fotoUrl"],
                justificativa=purchase["justificativa"],
                categoria_id=purchase["categoriaId"],
                conta_id=purchase["contaId"],
                centro_custo=purchase["centroCusto"],
                valor=purchase["valor"],
                vencimento=purchase["vencimento"],
                forma_pagamento=purchase["formaPagamento"],
                observacoes=purchase["obs"],
                title_id=purchase["titleId"],
                approved_at=purchase["approvedAt"],
                rejected_at=purchase["rejectedAt"],
            )
        )

    return touch_store(session, STORE_FINANCE)


def clear_finance_store(session) -> None:
    session.execute(delete(FinanceReminderLog))
    session.execute(delete(FinanceReconciliation))
    session.execute(delete(FinanceTitleAttachment))
    session.execute(delete(FinancePurchaseRequest))
    session.execute(delete(FinanceTitle))
    session.execute(delete(FinanceBankTransaction))
    session.execute(delete(FinanceImport))
    session.execute(delete(FinanceTransaction))
    session.execute(delete(FinanceCategory))
    session.execute(delete(FinanceAccount))
    session.execute(delete(FinanceConfig))
    delete_store_metadata(session, STORE_FINANCE)


def get_store_value(session, store_id: str) -> Any:
    safe_store_id = ensure_supported_store(store_id)
    if safe_store_id == STORE_SITE:
        ensure_site_store(session)
        return read_site_store(session)

    if not store_exists(session, safe_store_id):
        raise AppError(404, "Store not found.")

    if safe_store_id == STORE_GPS:
        return read_gps_store(session)

    return read_finance_store(session)


def put_store_value(session, store_id: str, value: Any) -> str:
    safe_store_id = ensure_supported_store(store_id)
    if safe_store_id == STORE_SITE:
        return replace_site_store(session, value)
    if safe_store_id == STORE_GPS:
        return replace_gps_store(session, value)
    return replace_finance_store(session, value)


def delete_store_value(session, store_id: str) -> None:
    safe_store_id = ensure_supported_store(store_id)
    if safe_store_id == STORE_SITE:
        clear_site_store(session)
        return
    if safe_store_id == STORE_GPS:
        clear_gps_store(session)
        return
    clear_finance_store(session)


def migrate_legacy_store(session, store_id: str) -> None:
    if store_exists(session, store_id):
        return

    payload = read_legacy_payload(session, store_id)
    if payload is None:
        return

    put_store_value(session, store_id, payload)


def initialize_data() -> None:
    with SessionLocal() as session:
        with session.begin():
            ensure_site_store(session)
            migrate_legacy_store(session, STORE_GPS)
            migrate_legacy_store(session, STORE_FINANCE)


initialize_data()

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or secrets.token_hex(32)

try:
    from zap.app import create_app as create_zap_app
except Exception as exc:  # pragma: no cover - optional embedded app
    create_zap_app = None
    app.logger.warning("Zap app not mounted: %s", exc)
else:
    zap_app = create_zap_app()
    app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {"/zap": zap_app})

max_body_size = parse_size(os.getenv("MAX_BODY_SIZE", "25mb"))
if max_body_size is not None:
    app.config["MAX_CONTENT_LENGTH"] = max_body_size


def ensure_within_root(candidate: Path) -> Path:
    resolved_root = BASE_DIR.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        abort(404)
    return resolved_candidate


@app.get("/api/health")
def healthcheck():
    scraper_settings = finance_scraper_settings()
    with SessionLocal() as session:
        session.execute(text("SELECT 1"))
        storage = {
            "provider": DB_PROVIDER,
            "database": database_label(DATABASE_URL),
            "legacyTable": LEGACY_STORE_TABLE if LEGACY_TABLE_AVAILABLE else None,
            "tables": [
                "store_metadata",
                "site_apps",
                "gps_songs",
                "gps_song_tags",
                "gps_song_blocks",
                "financeiro_config",
                "financeiro_accounts",
                "financeiro_categories",
                "financeiro_transactions",
                "financeiro_imports",
                "financeiro_bank_transactions",
                "financeiro_reconciliations",
                "financeiro_titles",
                "financeiro_title_attachments",
                "financeiro_purchase_requests",
                "financeiro_reminder_logs",
            ],
        }

    return jsonify(
        {
            "ok": True,
            "storage": storage,
            "integrations": {
                "researchProvider": "python_scraper",
                "researchDomains": len(scraper_settings["allowed_domains"]),
                "researchMaxOffers": scraper_settings["max_offers"],
            },
            "timestamp": now_iso(),
        }
    )


def portal_auth_enabled() -> bool:
    return bool(PORTAL_PASSWORD or github_oauth_enabled())


def portal_is_authenticated() -> bool:
    if not portal_auth_enabled():
        return True
    return bool(session.get(PORTAL_SESSION_KEY))


def github_oauth_enabled() -> bool:
    return bool(GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET)


def github_callback_url() -> str:
    if GITHUB_OAUTH_CALLBACK_URL:
        return GITHUB_OAUTH_CALLBACK_URL
    return "http://127.0.0.1:5000/auth/github/callback"


def fetch_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, data: dict[str, Any] | None = None) -> Any:
    payload = None
    request_headers = headers.copy() if headers else {}
    if data is not None:
        payload = urlencode(data).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    request_headers.setdefault("Accept", "application/json")
    request_headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
    request = urllib.request.Request(url, headers=request_headers, data=payload, method=method)
    with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
        return json.loads(response.read().decode("utf-8"))


@app.get("/api/auth/status")
def auth_status():
    return jsonify(
        {
            "enabled": portal_auth_enabled(),
            "authenticated": portal_is_authenticated(),
            "providers": {
                "password": bool(PORTAL_PASSWORD),
                "github": github_oauth_enabled(),
            },
            "user": session.get(PORTAL_USER_KEY),
        }
    )


@app.post("/api/auth/login")
def auth_login():
    body = request.get_json(silent=True) or {}
    password = str(body.get("password", ""))

    if not portal_auth_enabled():
        session[PORTAL_SESSION_KEY] = True
        return jsonify({"ok": True, "authenticated": True, "enabled": False})

    if not hmac.compare_digest(password, PORTAL_PASSWORD):
        session.pop(PORTAL_SESSION_KEY, None)
        return jsonify({"error": "Senha invalida."}), 401

    session[PORTAL_SESSION_KEY] = True
    session[PORTAL_USER_KEY] = {"provider": "password", "login": "local"}
    return jsonify({"ok": True, "authenticated": True, "enabled": True})


@app.post("/api/auth/logout")
def auth_logout():
    session.pop(PORTAL_SESSION_KEY, None)
    session.pop(PORTAL_USER_KEY, None)
    session.pop(GITHUB_OAUTH_STATE_KEY, None)
    return jsonify({"ok": True, "authenticated": False})


@app.get("/auth/github/start")
def github_auth_start():
    if not github_oauth_enabled():
        return redirect("/?auth_error=github_not_configured", code=302)

    state = secrets.token_urlsafe(24)
    session[GITHUB_OAUTH_STATE_KEY] = state
    query = urlencode(
        {
            "client_id": GITHUB_OAUTH_CLIENT_ID,
            "redirect_uri": github_callback_url(),
            "scope": "read:user",
            "state": state,
        }
    )
    return redirect(f"https://github.com/login/oauth/authorize?{query}", code=302)


@app.get("/auth/github/callback")
def github_auth_callback():
    if not github_oauth_enabled():
        return redirect("/?auth_error=github_not_configured", code=302)

    error = str(request.args.get("error", "")).strip()
    if error:
        return redirect("/?auth_error=github_access_denied", code=302)

    state = str(request.args.get("state", "")).strip()
    expected_state = str(session.get(GITHUB_OAUTH_STATE_KEY, "")).strip()
    if not state or not expected_state or not hmac.compare_digest(state, expected_state):
        return redirect("/?auth_error=github_invalid_state", code=302)

    code = str(request.args.get("code", "")).strip()
    if not code:
        return redirect("/?auth_error=github_missing_code", code=302)

    try:
        token_payload = fetch_json(
            "https://github.com/login/oauth/access_token",
            method="POST",
            data={
                "client_id": GITHUB_OAUTH_CLIENT_ID,
                "client_secret": GITHUB_OAUTH_CLIENT_SECRET,
                "code": code,
                "redirect_uri": github_callback_url(),
                "state": state,
            },
        )
        access_token = str(token_payload.get("access_token", "")).strip()
        if not access_token:
            raise RuntimeError("Missing access token.")

        user_payload = fetch_json(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        login = str(user_payload.get("login", "")).strip()
        user_id = user_payload.get("id")
        avatar_url = str(user_payload.get("avatar_url", "")).strip()
        profile_url = str(user_payload.get("html_url", "")).strip()

        if not login or not user_id:
            raise RuntimeError("Missing GitHub user data.")

        if GITHUB_ALLOWED_USERS and login.lower() not in GITHUB_ALLOWED_USERS:
            session.pop(PORTAL_SESSION_KEY, None)
            session.pop(PORTAL_USER_KEY, None)
            return redirect("/?auth_error=github_user_not_allowed", code=302)

        session[PORTAL_SESSION_KEY] = True
        session[PORTAL_USER_KEY] = {
            "provider": "github",
            "login": login,
            "id": user_id,
            "avatarUrl": avatar_url,
            "profileUrl": profile_url,
        }
        session.pop(GITHUB_OAUTH_STATE_KEY, None)
        return redirect("/", code=302)
    except Exception:
        session.pop(PORTAL_SESSION_KEY, None)
        session.pop(PORTAL_USER_KEY, None)
        session.pop(GITHUB_OAUTH_STATE_KEY, None)
        return redirect("/?auth_error=github_auth_failed", code=302)


@app.get("/api/site/apps")
def get_site_apps():
    with SessionLocal() as session:
        apps = read_site_store(session)
        updated_at = get_store_updated_at(session, STORE_SITE)

    return jsonify({"apps": apps, "updatedAt": updated_at})


@app.route("/api/bpa/<path:requested_path>", methods=["GET", "POST", "PUT", "DELETE"])
def proxy_bpa_api(requested_path: str):
    return proxy_bpa_request(requested_path)


@app.get("/api/finance/attachments/<path:attachment_path>")
def get_finance_attachment(attachment_path: str):
    candidate = ensure_within_directory(ATTACHMENTS_DIR, ATTACHMENTS_DIR / attachment_path)
    if not candidate.exists() or not candidate.is_file():
        abort(404)
    return send_from_directory(candidate.parent, candidate.name)


@app.post("/api/finance/attachments")
def upload_finance_attachment():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "Selecione um arquivo PDF ou imagem."}), 400

    mime_type = uploaded.mimetype or mimetypes.guess_type(uploaded.filename)[0] or "application/octet-stream"
    extension = Path(uploaded.filename).suffix.lower()
    is_allowed = mime_type.startswith("image/") or mime_type == "application/pdf" or extension == ".pdf"
    if not is_allowed:
        return jsonify({"error": "Somente PDF e imagens sao permitidos."}), 400

    content = uploaded.read()
    if not content:
        return jsonify({"error": "Arquivo vazio."}), 400
    if len(content) > FINANCE_ATTACHMENT_MAX_SIZE:
        return jsonify({"error": "Arquivo acima do limite permitido."}), 413

    metadata = save_finance_attachment(
        file_name=uploaded.filename,
        mime_type=mime_type,
        vencimento=as_text(request.form.get("vencimento")),
        conta_nome=as_text(request.form.get("contaNome")),
        pessoa=as_text(request.form.get("pessoa")),
        descricao=as_text(request.form.get("descricao")),
        content=content,
    )
    metadata["id"] = as_text(request.form.get("attachmentId"))

    return jsonify({"ok": True, "attachment": metadata})


@app.delete("/api/finance/attachments")
def delete_finance_attachment():
    relative_path = as_text(request.args.get("path"))
    if not relative_path:
        return jsonify({"error": "Informe o caminho do anexo."}), 400

    deleted = delete_finance_attachment_file(relative_path)
    if not deleted:
        abort(404)

    return ("", 204)


@app.post("/api/finance/attachments/decode")
def decode_finance_attachment():
    body = request.get_json(silent=True) or {}
    relative_path = as_text(body.get("path"))
    data_url = as_text(body.get("dataUrl"))
    mime_type = as_text(body.get("mime")).lower()
    file_name = ""

    if relative_path:
        candidate = ensure_within_directory(ATTACHMENTS_DIR, ATTACHMENTS_DIR / relative_path)
        if not candidate.exists() or not candidate.is_file():
            abort(404)
        content = candidate.read_bytes()
        file_name = candidate.name
    elif data_url:
        content = image_bytes_from_data_url(data_url)
    else:
        return jsonify({"error": "Informe o caminho ou a imagem do anexo."}), 400

    is_pdf = "pdf" in mime_type or file_name.lower().endswith(".pdf")
    codes = decode_pdf_codes(content) if is_pdf else decode_image_codes(content)
    return jsonify({"ok": True, "codes": codes})


@app.get("/api/tatoo/contracts/<path:contract_path>")
def get_tatoo_contract(contract_path: str):
    candidate = ensure_within_directory(CONTRACTS_DIR, CONTRACTS_DIR / contract_path)
    if not candidate.exists() or not candidate.is_file():
        abort(404)
    return send_from_directory(candidate.parent, candidate.name)


@app.post("/api/tatoo/contracts")
def create_tatoo_contract():
    body = request.get_json(silent=True) or {}
    metadata = save_tatoo_contract_pdf(body)
    return jsonify({"ok": True, "contract": metadata})


@app.post("/api/finance/reminders/run")
def trigger_finance_reminders():
    with SessionLocal() as session:
        with session.begin():
            result = run_finance_reminders(session)

    return jsonify(result)


@app.get("/api/finance/ai-status")
def finance_ai_status():
    probe = parse_bool(request.args.get("probe"), False)
    settings = finance_scraper_settings()
    return jsonify(
        build_scraper_diagnostic(
            allowed_domains=settings["allowed_domains"],
            timeout_seconds=settings["timeout_seconds"],
            max_offers=settings["max_offers"],
            probe=probe,
            user_agent=settings["user_agent"],
        )
    )


@app.post("/api/finance/purchase-research")
def finance_purchase_research():
    body = request.get_json(silent=True) or {}
    return jsonify(run_finance_purchase_research(body))


@app.get("/api/stores/<store_id>")
def get_store(store_id: str):
    with SessionLocal() as session:
        value = get_store_value(session, store_id)
        updated_at = get_store_updated_at(session, ensure_supported_store(store_id))

    return jsonify({"exists": True, "storeId": store_id, "updatedAt": updated_at, "value": value})


@app.put("/api/stores/<store_id>")
def put_store(store_id: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "value" not in body:
        return jsonify({"error": "Missing 'value' in request body."}), 400

    with SessionLocal() as session:
        with session.begin():
            updated_at = put_store_value(session, store_id, body["value"])

    return jsonify({"ok": True, "storeId": ensure_supported_store(store_id), "updatedAt": updated_at})


@app.delete("/api/stores/<store_id>")
def delete_store(store_id: str):
    with SessionLocal() as session:
        with session.begin():
            delete_store_value(session, store_id)

    return ("", 204)


@app.route("/", defaults={"requested_path": ""})
@app.route("/<path:requested_path>")
def serve_static(requested_path: str):
    if requested_path.startswith("api/"):
        abort(404)

    normalized_path = requested_path.strip("/")
    if normalized_path == "":
        normalized_path = "index.html"

    candidate = ensure_within_root(BASE_DIR / normalized_path)

    if candidate.is_dir():
        if requested_path and not requested_path.endswith("/"):
            return redirect(f"/{normalized_path}/", code=302)
        index_file = candidate / "index.html"
        if index_file.exists():
            return send_from_directory(index_file.parent, index_file.name)

    if candidate.exists() and candidate.is_file():
        return send_from_directory(candidate.parent, candidate.name)

    if not Path(normalized_path).suffix:
        html_candidate = ensure_within_root(BASE_DIR / f"{normalized_path}.html")
        if html_candidate.exists() and html_candidate.is_file():
            return send_from_directory(html_candidate.parent, html_candidate.name)

    abort(404)


@app.errorhandler(Exception)
def handle_error(error: Exception):
    if isinstance(error, AppError):
        status_code = error.status_code
        message = str(error)
    elif isinstance(error, HTTPException):
        status_code = error.code or 500
        message = error.description or "Request failed."
    elif isinstance(error, SQLAlchemyError):
        status_code = 500
        message = "Database error."
    else:
        status_code = 500
        message = "Internal server error."

    app.logger.exception(error)
    return jsonify({"error": message}), status_code


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
