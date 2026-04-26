from __future__ import annotations

import hashlib
import io
import json
import math
import re
import secrets
import smtplib
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
import ssl
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import ntplib
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from .config import Config
from .models import AppConfig, AuditLog, BankAdjustment, EmailDispatch, Employee, Holiday, LeaveRequest, PointJustification, PunchRecord, RepEvent, utc_now


COLLECTOR_OPTIONS = {
    "01": "Aplicativo mobile",
    "02": "Browser",
    "03": "Aplicativo desktop",
    "04": "Dispositivo eletronico",
    "05": "Outro dispositivo",
}

JUSTIFICATION_TYPE_LABELS = {
    "atestado_medico": "Atestado medico",
    "esquecimento": "Esquecimento de ponto",
    "fora_horario": "Ponto fora do horario",
    "problema_tecnico": "Problema tecnico",
    "ajuste_manual": "Ajuste manual",
    "outro": "Outro motivo",
}

WEEKDAY_LABELS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]
PASSWORD_RESET_EXPIRATION_MINUTES = 15


@dataclass
class OfficialTime:
    instant: datetime
    server: str
    offset_ms: int
    source: str


def json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def json_loads(payload: str) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def clean_digits(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def mask_email(value: str) -> str:
    email = clean_text(value)
    if "@" not in email:
        return email
    local_part, domain = email.split("@", 1)
    if len(local_part) <= 2:
        masked_local = f"{local_part[:1]}***"
    else:
        masked_local = f"{local_part[:2]}***{local_part[-1:]}"
    return f"{masked_local}@{domain}"


def get_zoneinfo(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def now_local(tz_name: str) -> datetime:
    return datetime.now(get_zoneinfo(tz_name))


def ensure_data_dir() -> None:
    Config.DATA_DIR = getattr(Config, "DATA_DIR", None)


def ensure_runtime_schema(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    statements: list[str] = []

    if "employees" in table_names:
        employee_columns = {column["name"] for column in inspector.get_columns("employees")}
        if "password_hash" not in employee_columns:
            statements.append("ALTER TABLE employees ADD COLUMN password_hash VARCHAR(255) DEFAULT ''")
        if "reset_code_hash" not in employee_columns:
            statements.append("ALTER TABLE employees ADD COLUMN reset_code_hash VARCHAR(64) DEFAULT ''")
        if "reset_code_expires_at" not in employee_columns:
            statements.append("ALTER TABLE employees ADD COLUMN reset_code_expires_at DATETIME")
        if "reset_code_sent_at" not in employee_columns:
            statements.append("ALTER TABLE employees ADD COLUMN reset_code_sent_at DATETIME")
        if "password_updated_at" not in employee_columns:
            statements.append("ALTER TABLE employees ADD COLUMN password_updated_at DATETIME")

    if "point_justifications" in table_names:
        justification_columns = {column["name"] for column in inspector.get_columns("point_justifications")}
        if "attachment_original_name" not in justification_columns:
            statements.append("ALTER TABLE point_justifications ADD COLUMN attachment_original_name VARCHAR(255) DEFAULT ''")
        if "attachment_stored_name" not in justification_columns:
            statements.append("ALTER TABLE point_justifications ADD COLUMN attachment_stored_name VARCHAR(255) DEFAULT ''")
        if "attachment_mime" not in justification_columns:
            statements.append("ALTER TABLE point_justifications ADD COLUMN attachment_mime VARCHAR(120) DEFAULT ''")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _flush_or_load_existing(session: Session, factory, lookup):
    instance = factory()
    savepoint = session.begin_nested()
    try:
        session.add(instance)
        session.flush()
    except IntegrityError:
        savepoint.rollback()
        session.expire_all()
        instance = lookup()
    else:
        savepoint.commit()
    return instance


def get_or_create_config(session: Session) -> AppConfig:
    config = session.get(AppConfig, 1)
    if config is None:
        config = _flush_or_load_existing(
            session,
            lambda: AppConfig(
                id=1,
                app_name=Config.APP_NAME,
                timezone_name=Config.DEFAULT_TIMEZONE,
                allow_system_time_fallback=Config.ALLOW_SYSTEM_TIME_FALLBACK,
            ),
            lambda: session.get(AppConfig, 1),
        )
    return config


def write_audit_log(session: Session, action: str, entity_type: str, entity_id: str, actor: str, payload: dict[str, Any]) -> None:
    session.add(
        AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            actor=actor,
            payload=json_dumps(payload),
        )
    )


def next_nsr(session: Session) -> int:
    current = session.scalar(select(func.max(RepEvent.nsr)))
    return int(current or 0) + 1


def parse_iso_date(raw: str | None, default: date | None = None) -> date | None:
    if not raw:
        return default
    return date.fromisoformat(raw)


def format_document(value: str, size: int) -> str:
    digits = clean_digits(value)
    return digits[:size].rjust(size, "0")


def format_alpha(value: str, size: int) -> str:
    safe = (value or "")[:size]
    return safe.ljust(size, " ")


def format_minutes_label(minutes: int, signed: bool = False) -> str:
    sign = ""
    if minutes < 0:
        sign = "-"
    elif signed and minutes > 0:
        sign = "+"
    absolute_minutes = abs(int(minutes))
    hours, remainder = divmod(absolute_minutes, 60)
    return f"{sign}{hours:02d}h{remainder:02d}"


def format_dh(value: datetime, tz_name: str) -> str:
    local_value = value.astimezone(get_zoneinfo(tz_name))
    return local_value.strftime("%Y-%m-%dT%H:%M:00%z")


def format_d(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def crc16_kermit(value: str) -> str:
    crc = 0x0000
    for byte in value.encode("latin-1", errors="replace"):
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    return f"{crc & 0xFFFF:04X}"


def _hash_password_reset_code(code: str) -> str:
    return hashlib.sha256(clean_text(code).encode("utf-8")).hexdigest()


def verify_employee_password(employee: Employee, password: str | None) -> bool:
    candidate = clean_text(password)
    if employee.password_hash:
        return check_password_hash(employee.password_hash, candidate)
    return clean_digits(candidate) == clean_digits(employee.cpf)


def set_employee_password(employee: Employee, password: str) -> None:
    normalized_password = clean_text(password)
    if len(normalized_password) < 6:
        raise ValueError("A nova senha deve ter pelo menos 6 caracteres.")
    employee.password_hash = generate_password_hash(normalized_password)
    employee.password_updated_at = utc_now()


def clear_employee_recovery_code(employee: Employee) -> None:
    employee.reset_code_hash = ""
    employee.reset_code_expires_at = None
    employee.reset_code_sent_at = None


def as_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def append_rep_event(session: Session, record_type: str, occurred_at: datetime, payload: dict[str, Any]) -> RepEvent:
    event = RepEvent(
        nsr=next_nsr(session),
        record_type=record_type,
        occurred_at=occurred_at.astimezone(timezone.utc),
        payload=json_dumps(payload),
    )
    session.add(event)
    session.flush()
    return event


def _record_service_transition(session: Session, config: AppConfig, new_status: str, occurred_at: datetime) -> None:
    if config.service_status == new_status:
        return
    config.service_status = new_status
    if new_status == "available":
        append_rep_event(session, "6", occurred_at, {"event_code": "07"})
    elif new_status == "unavailable":
        append_rep_event(session, "6", occurred_at, {"event_code": "08"})


def get_official_time(session: Session, strict: bool = True) -> OfficialTime:
    config = get_or_create_config(session)
    client = ntplib.NTPClient()
    last_error = ""
    for server in Config.NTP_SERVERS:
        try:
            response = client.request(server, version=3, timeout=2)
            instant = datetime.fromtimestamp(response.tx_time, tz=timezone.utc)
            _record_service_transition(session, config, "available", instant)
            return OfficialTime(
                instant=instant,
                server=server,
                offset_ms=int(response.offset * 1000),
                source="ntp",
            )
        except Exception as exc:  # pragma: no cover - network variance
            last_error = str(exc)

    fallback_allowed = config.allow_system_time_fallback or Config.ALLOW_SYSTEM_TIME_FALLBACK
    fallback_time = datetime.now(timezone.utc)
    _record_service_transition(session, config, "unavailable", fallback_time)
    if strict and not fallback_allowed:
        raise RuntimeError(
            f"Falha ao consultar a Hora Legal Brasileira via NTP do ON. Ultimo erro: {last_error or 'indisponivel'}"
        )
    return OfficialTime(instant=fallback_time, server="system-clock", offset_ms=0, source="system-fallback")


def compute_punch_hash(
    nsr: int,
    employee_cpf: str,
    punch_at: datetime,
    recorded_at: datetime,
    collector_code: str,
    is_offline: bool,
    previous_hash: str,
    tz_name: str,
) -> str:
    raw = (
        f"{nsr:09d}"
        f"7"
        f"{format_dh(punch_at, tz_name)}"
        f"{format_document(employee_cpf, 12)}"
        f"{format_dh(recorded_at, tz_name)}"
        f"{collector_code}"
        f"{'1' if is_offline else '0'}"
        f"{previous_hash}"
    )
    return hashlib.sha256(raw.encode("latin-1", errors="replace")).hexdigest().upper()


def serialize_employee(employee: Employee) -> dict[str, Any]:
    return {
        "id": employee.id,
        "employee_code": employee.employee_code,
        "badge_id": employee.badge_id,
        "full_name": employee.full_name,
        "cpf": employee.cpf,
        "email": employee.email,
        "department": employee.department,
        "admission_date": employee.admission_date.isoformat(),
        "termination_date": employee.termination_date.isoformat() if employee.termination_date else "",
        "daily_minutes": employee.daily_minutes,
        "active": employee.active,
    }


def serialize_punch(session: Session, punch: PunchRecord) -> dict[str, Any]:
    employee = session.get(Employee, punch.employee_id)
    return {
        "id": punch.id,
        "nsr": punch.nsr,
        "employee_id": punch.employee_id,
        "employee_name": employee.full_name if employee else "Desconhecido",
        "employee_cpf": employee.cpf if employee else "",
        "collector_code": punch.collector_code,
        "collector_label": punch.collector_label,
        "is_offline": punch.is_offline,
        "punch_at": punch.punch_at.isoformat(),
        "recorded_at": punch.recorded_at.isoformat(),
        "hash_code": punch.hash_code,
        "time_source": punch.time_source,
        "ntp_server": punch.ntp_server,
    }


def serialize_email_dispatch(dispatch: EmailDispatch) -> dict[str, Any]:
    return {
        "id": dispatch.id,
        "punch_id": dispatch.punch_id,
        "recipient": dispatch.recipient,
        "status": dispatch.status,
        "message": dispatch.message,
        "created_at": dispatch.created_at.isoformat() if dispatch.created_at else "",
    }


def serialize_justification(session: Session, justification: PointJustification) -> dict[str, Any]:
    employee = session.get(Employee, justification.employee_id)
    occurrence_type = clean_text(justification.occurrence_type) or "outro"
    return {
        "id": justification.id,
        "employee_id": justification.employee_id,
        "employee_name": employee.full_name if employee else "Desconhecido",
        "employee_cpf": employee.cpf if employee else "",
        "reference_date": justification.reference_date.isoformat(),
        "occurrence_type": occurrence_type,
        "occurrence_label": JUSTIFICATION_TYPE_LABELS.get(occurrence_type, JUSTIFICATION_TYPE_LABELS["outro"]),
        "informed_time": clean_text(justification.informed_time),
        "reason": clean_text(justification.reason),
        "details": clean_text(justification.details),
        "attachment_original_name": clean_text(justification.attachment_original_name),
        "attachment_mime": clean_text(justification.attachment_mime),
        "signature_status": clean_text(justification.signature_status) or "pendente_assinatura",
        "created_at": justification.created_at.isoformat() if justification.created_at else "",
    }


def serialize_config(config: AppConfig) -> dict[str, Any]:
    return {
        "app_name": clean_text(config.app_name),
        "employer_name": clean_text(config.employer_name),
        "employer_document": clean_text(config.employer_document),
        "employer_document_type": clean_text(config.employer_document_type) or "1",
        "cno_caepf": clean_text(config.cno_caepf),
        "workplace": clean_text(config.workplace),
        "developer_name": clean_text(config.developer_name),
        "developer_document": clean_text(config.developer_document),
        "developer_document_type": clean_text(config.developer_document_type) or "1",
        "developer_inpi": clean_text(config.developer_inpi),
        "legal_responsible_name": clean_text(config.legal_responsible_name),
        "legal_responsible_cpf": clean_text(config.legal_responsible_cpf),
        "technical_responsible_name": clean_text(config.technical_responsible_name),
        "technical_responsible_cpf": clean_text(config.technical_responsible_cpf),
        "technical_responsible_registry": clean_text(config.technical_responsible_registry),
        "timezone_name": clean_text(config.timezone_name) or Config.DEFAULT_TIMEZONE,
        "allow_system_time_fallback": config.allow_system_time_fallback,
        "service_status": clean_text(config.service_status) or "unknown",
    }


def upsert_settings(session: Session, data: dict[str, Any], actor: str) -> AppConfig:
    config = get_or_create_config(session)
    tracked_fields = (
        "app_name",
        "employer_name",
        "employer_document",
        "employer_document_type",
        "cno_caepf",
        "workplace",
        "developer_name",
        "developer_document",
        "developer_document_type",
        "developer_inpi",
        "legal_responsible_name",
        "legal_responsible_cpf",
        "technical_responsible_name",
        "technical_responsible_cpf",
        "technical_responsible_registry",
        "timezone_name",
        "allow_system_time_fallback",
    )
    before = serialize_config(config)
    for field in tracked_fields:
        if field in data:
            if field == "allow_system_time_fallback":
                setattr(config, field, bool(data[field]))
            else:
                setattr(config, field, clean_text(data[field]))
    official = get_official_time(session, strict=False)
    payload = {
        "responsible_cpf": clean_digits(config.technical_responsible_cpf or config.legal_responsible_cpf),
        "employer_document_type": config.employer_document_type,
        "employer_document": clean_digits(config.employer_document),
        "cno_caepf": clean_digits(config.cno_caepf),
        "employer_name": config.employer_name,
        "workplace": config.workplace,
    }
    append_rep_event(session, "2", official.instant, payload)
    write_audit_log(session, "settings.updated", "app_config", "1", actor, {"before": before, "after": serialize_config(config)})
    return config


def upsert_employee(session: Session, data: dict[str, Any], actor: str) -> Employee:
    employee_id = data.get("id")
    employee = session.get(Employee, int(employee_id)) if employee_id else None
    if employee is None:
        cpf = clean_digits(data.get("cpf"))
        employee_code = str(data.get("employee_code", "")).strip()
        if cpf:
            employee = session.scalar(select(Employee).where(Employee.cpf == cpf))
        if employee is None and employee_code:
            employee = session.scalar(select(Employee).where(Employee.employee_code == employee_code))
    is_new = employee is None
    before = serialize_employee(employee) if employee else {}

    if employee is None:
        employee = Employee(
            employee_code=data["employee_code"],
            full_name=data["full_name"],
            cpf=clean_digits(data["cpf"]),
            badge_id=data.get("badge_id", ""),
            email=data.get("email", ""),
            department=data.get("department", ""),
            admission_date=parse_iso_date(data.get("admission_date")) or date.today(),
            daily_minutes=int(data.get("daily_minutes") or 480),
            active=bool(data.get("active", True)),
        )
        session.add(employee)
        session.flush()
    else:
        employee.employee_code = data.get("employee_code", employee.employee_code)
        employee.full_name = data.get("full_name", employee.full_name)
        employee.cpf = clean_digits(data.get("cpf", employee.cpf))
        employee.badge_id = data.get("badge_id", employee.badge_id)
        employee.email = data.get("email", employee.email)
        employee.department = data.get("department", employee.department)
        employee.admission_date = parse_iso_date(data.get("admission_date"), employee.admission_date) or employee.admission_date
        employee.termination_date = parse_iso_date(data.get("termination_date"), employee.termination_date)
        employee.daily_minutes = int(data.get("daily_minutes") or employee.daily_minutes)
        employee.active = bool(data.get("active", employee.active))

    config = get_or_create_config(session)
    official = get_official_time(session, strict=False)
    operation = "I" if is_new else ("E" if not employee.active and employee.termination_date else "A")
    append_rep_event(
        session,
        "5",
        official.instant,
        {
            "operation": operation,
            "employee_cpf": clean_digits(employee.cpf),
            "employee_name": employee.full_name,
            "employee_code": employee.employee_code[:4],
            "responsible_cpf": clean_digits(config.technical_responsible_cpf or config.legal_responsible_cpf),
        },
    )
    write_audit_log(
        session,
        "employee.created" if is_new else "employee.updated",
        "employee",
        str(employee.id),
        actor,
        {"before": before, "after": serialize_employee(employee)},
    )
    return employee


def create_holiday(session: Session, data: dict[str, Any], actor: str) -> Holiday:
    holiday = Holiday(
        holiday_date=parse_iso_date(data["holiday_date"]) or date.today(),
        name=data["name"],
        scope=data.get("scope", "empresa"),
    )
    session.add(holiday)
    session.flush()
    write_audit_log(session, "holiday.created", "holiday", str(holiday.id), actor, {"holiday_date": holiday.holiday_date.isoformat(), "name": holiday.name})
    return holiday


def create_leave(session: Session, data: dict[str, Any], actor: str) -> LeaveRequest:
    leave = LeaveRequest(
        employee_id=int(data["employee_id"]),
        leave_type=data.get("leave_type", "ferias"),
        start_date=parse_iso_date(data["start_date"]) or date.today(),
        end_date=parse_iso_date(data["end_date"]) or date.today(),
        notes=data.get("notes", ""),
    )
    session.add(leave)
    session.flush()
    write_audit_log(session, "leave.created", "leave_request", str(leave.id), actor, data)
    return leave


def create_bank_adjustment(session: Session, data: dict[str, Any], actor: str) -> BankAdjustment:
    adjustment = BankAdjustment(
        employee_id=int(data["employee_id"]),
        reference_date=parse_iso_date(data["reference_date"]) or date.today(),
        minutes_delta=int(data["minutes_delta"]),
        reason=data["reason"],
    )
    session.add(adjustment)
    session.flush()
    write_audit_log(session, "bank_adjustment.created", "bank_adjustment", str(adjustment.id), actor, data)
    return adjustment


def create_point_justification(session: Session, data: dict[str, Any], actor: str) -> PointJustification:
    employee_id = int(data.get("employee_id") or 0)
    employee = session.get(Employee, employee_id) if employee_id else None
    if employee is None or not employee.active:
        raise ValueError("Funcionario nao encontrado ou inativo.")

    justification = PointJustification(
        employee_id=employee.id,
        reference_date=parse_iso_date(data["reference_date"]) or date.today(),
        occurrence_type=clean_text(data.get("occurrence_type")) or "esquecimento",
        informed_time=clean_text(data.get("informed_time")),
        reason=clean_text(data.get("reason")),
        details=clean_text(data.get("details")),
        signature_status="pendente_assinatura",
    )
    if justification.occurrence_type not in JUSTIFICATION_TYPE_LABELS:
        raise ValueError("Tipo de justificativa invalido.")
    if not justification.reason:
        raise ValueError("Informe o motivo da justificativa.")
    session.add(justification)
    session.flush()
    write_audit_log(
        session,
        "point_justification.created",
        "point_justification",
        str(justification.id),
        actor,
        serialize_justification(session, justification),
    )
    return justification


def save_report_missing_punches(session: Session, data: dict[str, Any], actor: str) -> PointJustification | None:
    employee_id = int(data.get("employee_id") or 0)
    employee = session.get(Employee, employee_id) if employee_id else None
    if employee is None or not employee.active:
        raise ValueError("Funcionario nao encontrado ou inativo.")

    reference_date = parse_iso_date(data.get("reference_date")) or date.today()
    informed_time = " / ".join(_parse_informed_times(data.get("informed_time")))
    reason = clean_text(data.get("reason")) or "Batidas nao lancadas informadas no espelho de ponto."
    details = clean_text(data.get("details")) or "Ajuste informado diretamente no espelho de ponto."
    justification = session.scalar(
        select(PointJustification)
        .where(PointJustification.employee_id == employee_id)
        .where(PointJustification.reference_date == reference_date)
        .where(PointJustification.occurrence_type == "ajuste_manual")
        .order_by(PointJustification.id.desc())
    )

    if not informed_time:
        if justification is not None:
            write_audit_log(
                session,
                "point_justification.deleted",
                "point_justification",
                str(justification.id),
                actor,
                serialize_justification(session, justification),
            )
            session.delete(justification)
        return None

    if justification is None:
        justification = PointJustification(
            employee_id=employee_id,
            reference_date=reference_date,
            occurrence_type="ajuste_manual",
            informed_time=informed_time,
            reason=reason,
            details=details,
            signature_status="pendente_assinatura",
        )
        session.add(justification)
        session.flush()
        write_audit_log(
            session,
            "point_justification.created",
            "point_justification",
            str(justification.id),
            actor,
            serialize_justification(session, justification),
        )
        return justification

    justification.informed_time = informed_time
    justification.reason = reason
    justification.details = details
    justification.signature_status = "pendente_assinatura"
    session.flush()
    write_audit_log(
        session,
        "point_justification.updated",
        "point_justification",
        str(justification.id),
        actor,
        serialize_justification(session, justification),
    )
    return justification


def resolve_employee_for_punch(session: Session, data: dict[str, Any]) -> Employee:
    employee = None
    if data.get("employee_id"):
        employee = session.get(Employee, int(data["employee_id"]))
    elif data.get("badge_id"):
        employee = session.scalar(select(Employee).where(Employee.badge_id == data["badge_id"]))
    elif data.get("cpf"):
        employee = session.scalar(select(Employee).where(Employee.cpf == clean_digits(data["cpf"])))
    if employee is None or not employee.active:
        raise ValueError("Funcionario nao encontrado ou inativo.")
    return employee


def register_punch(session: Session, data: dict[str, Any], actor: str) -> PunchRecord:
    employee = resolve_employee_for_punch(session, data)
    config = get_or_create_config(session)
    official = get_official_time(session, strict=True)
    collector_code = data.get("collector_code", "01")
    if collector_code not in COLLECTOR_OPTIONS:
        raise ValueError("Coletor invalido.")
    previous_hash = session.scalar(select(PunchRecord.hash_code).order_by(PunchRecord.nsr.desc()).limit(1)) or ""
    nsr = next_nsr(session)
    hash_code = compute_punch_hash(
        nsr=nsr,
        employee_cpf=employee.cpf,
        punch_at=official.instant,
        recorded_at=official.instant,
        collector_code=collector_code,
        is_offline=bool(data.get("is_offline", False)),
        previous_hash=previous_hash,
        tz_name=config.timezone_name,
    )
    punch = PunchRecord(
        id=str(uuid.uuid4()),
        employee_id=employee.id,
        nsr=nsr,
        collector_code=collector_code,
        collector_label=COLLECTOR_OPTIONS[collector_code],
        is_offline=bool(data.get("is_offline", False)),
        punch_at=official.instant,
        recorded_at=official.instant,
        previous_hash=previous_hash,
        hash_code=hash_code,
        ntp_server=official.server,
        ntp_offset_ms=official.offset_ms,
        time_source=official.source,
    )
    session.add(punch)
    append_rep_event(
        session,
        "7",
        official.instant,
        {
            "employee_cpf": clean_digits(employee.cpf),
            "recorded_at": official.instant.isoformat(),
            "collector_code": collector_code,
            "is_offline": 1 if punch.is_offline else 0,
            "hash_code": hash_code,
        },
    )
    write_audit_log(
        session,
        "punch.created",
        "punch_record",
        punch.id,
        actor,
        {
            "employee_id": employee.id,
            "employee_name": employee.full_name,
            "nsr": nsr,
            "collector_code": collector_code,
            "hash_code": hash_code,
            "ntp_server": official.server,
        },
    )
    return punch


def _pair_minutes(punches: list[datetime]) -> int:
    total = 0
    ordered = sorted(punches)
    for index in range(0, len(ordered) - 1, 2):
        total += int((ordered[index + 1] - ordered[index]).total_seconds() // 60)
    return total


def _is_leave_day(leaves: list[LeaveRequest], current_day: date) -> bool:
    return any(leave.start_date <= current_day <= leave.end_date for leave in leaves)


def _parse_informed_times(raw_value: str) -> list[str]:
    found = re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", clean_text(raw_value))
    unique_values: list[str] = []
    for item in found:
        normalized = item if len(item) == 5 else f"0{item}"
        if normalized not in unique_values:
            unique_values.append(normalized)
    return unique_values


def employee_bank_summary(session: Session, employee: Employee, start: date, end: date, tz_name: str) -> dict[str, Any]:
    holiday_dates = {holiday.holiday_date for holiday in session.scalars(select(Holiday)).all()}
    leaves = session.scalars(select(LeaveRequest).where(LeaveRequest.employee_id == employee.id)).all()
    adjustments = session.scalars(select(BankAdjustment).where(BankAdjustment.employee_id == employee.id)).all()
    punches = session.scalars(
        select(PunchRecord)
        .where(PunchRecord.employee_id == employee.id)
        .where(PunchRecord.punch_at >= datetime.combine(start, datetime.min.time(), tzinfo=get_zoneinfo(tz_name)).astimezone(timezone.utc))
        .where(PunchRecord.punch_at <= datetime.combine(end, datetime.max.time(), tzinfo=get_zoneinfo(tz_name)).astimezone(timezone.utc))
        .order_by(PunchRecord.punch_at.asc())
    ).all()

    by_day: dict[date, list[datetime]] = {}
    for punch in punches:
        local_zone = get_zoneinfo(tz_name)
        local_day = punch.punch_at.astimezone(local_zone).date()
        by_day.setdefault(local_day, []).append(punch.punch_at.astimezone(local_zone))

    worked_minutes = 0
    expected_minutes = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in holiday_dates and not _is_leave_day(leaves, cursor):
            expected_minutes += employee.daily_minutes
        worked_minutes += _pair_minutes(by_day.get(cursor, []))
        cursor += timedelta(days=1)

    adjustment_minutes = sum(adj.minutes_delta for adj in adjustments if start <= adj.reference_date <= end)
    balance = worked_minutes - expected_minutes + adjustment_minutes
    return {
        "employee_id": employee.id,
        "employee_name": employee.full_name,
        "worked_minutes": worked_minutes,
        "expected_minutes": expected_minutes,
        "adjustment_minutes": adjustment_minutes,
        "balance_minutes": balance,
    }


def employee_hours_report(session: Session, employee_id: int, start: date, end: date) -> dict[str, Any]:
    if start > end:
        raise ValueError("A data inicial nao pode ser maior que a data final.")

    employee = session.get(Employee, int(employee_id))
    if employee is None:
        raise ValueError("Funcionario nao encontrado.")

    config = get_or_create_config(session)
    tz_name = config.timezone_name
    local_zone = get_zoneinfo(tz_name)
    holiday_map = {holiday.holiday_date: holiday.name for holiday in session.scalars(select(Holiday)).all()}
    leaves = session.scalars(select(LeaveRequest).where(LeaveRequest.employee_id == employee.id)).all()
    justifications = session.scalars(
        select(PointJustification)
        .where(PointJustification.employee_id == employee.id)
        .where(PointJustification.reference_date >= start)
        .where(PointJustification.reference_date <= end)
        .order_by(PointJustification.reference_date.asc(), PointJustification.id.asc())
    ).all()
    adjustments = session.scalars(
        select(BankAdjustment)
        .where(BankAdjustment.employee_id == employee.id)
        .where(BankAdjustment.reference_date >= start)
        .where(BankAdjustment.reference_date <= end)
        .order_by(BankAdjustment.reference_date.asc(), BankAdjustment.id.asc())
    ).all()
    punches = session.scalars(
        select(PunchRecord)
        .where(PunchRecord.employee_id == employee.id)
        .where(PunchRecord.punch_at >= datetime.combine(start, datetime.min.time(), tzinfo=local_zone).astimezone(timezone.utc))
        .where(PunchRecord.punch_at <= datetime.combine(end, datetime.max.time(), tzinfo=local_zone).astimezone(timezone.utc))
        .order_by(PunchRecord.punch_at.asc(), PunchRecord.nsr.asc())
    ).all()

    punches_by_day: dict[date, list[datetime]] = {}
    for punch in punches:
        local_punch = punch.punch_at.astimezone(local_zone)
        punches_by_day.setdefault(local_punch.date(), []).append(local_punch)

    adjustments_by_day: dict[date, list[BankAdjustment]] = {}
    for adjustment in adjustments:
        adjustments_by_day.setdefault(adjustment.reference_date, []).append(adjustment)

    justifications_by_day: dict[date, list[PointJustification]] = {}
    for justification in justifications:
        justifications_by_day.setdefault(justification.reference_date, []).append(justification)

    rows: list[dict[str, Any]] = []
    worked_total = 0
    expected_total = 0
    adjustment_total = 0
    balance_total = 0
    cursor = start
    while cursor <= end:
        day_punches = punches_by_day.get(cursor, [])
        day_justifications = justifications_by_day.get(cursor, [])
        day_adjustments = adjustments_by_day.get(cursor, [])
        adjustment_minutes = sum(item.minutes_delta for item in day_adjustments)
        leave = next((item for item in leaves if item.start_date <= cursor <= item.end_date), None)
        holiday_name = holiday_map.get(cursor)
        effective_punches = list(day_punches)
        justified_labels: list[str] = []
        justified_times_for_edit: list[str] = []
        report_justifications: list[dict[str, Any]] = []
        notes: list[str] = []

        for justification in day_justifications:
            parsed_times = _parse_informed_times(justification.informed_time)
            for raw_time in parsed_times:
                hour, minute = map(int, raw_time.split(":"))
                local_adjustment = datetime.combine(cursor, datetime.min.time(), tzinfo=local_zone).replace(hour=hour, minute=minute)
                if all(item.strftime("%H:%M") != local_adjustment.strftime("%H:%M") for item in effective_punches):
                    effective_punches.append(local_adjustment)
                if raw_time not in justified_times_for_edit:
                    justified_times_for_edit.append(raw_time)
                display_label = f"{raw_time} (just.)"
                if display_label not in justified_labels:
                    justified_labels.append(display_label)

            justification_label = JUSTIFICATION_TYPE_LABELS.get(
                clean_text(justification.occurrence_type) or "outro",
                JUSTIFICATION_TYPE_LABELS["outro"],
            )
            note = f"Justificativa: {justification_label}"
            if clean_text(justification.reason):
                note = f"{note} - {clean_text(justification.reason)}"
            notes.append(note)
            report_justifications.append(
                {
                    "id": justification.id,
                    "occurrence_type": clean_text(justification.occurrence_type) or "outro",
                    "reason": clean_text(justification.reason),
                    "details": clean_text(justification.details),
                    "attachment_original_name": clean_text(justification.attachment_original_name),
                    "attachment_mime": clean_text(justification.attachment_mime),
                }
            )

        worked_minutes = _pair_minutes(sorted(effective_punches))

        expected_minutes = 0
        if leave:
            notes.append(f"{leave.leave_type.title()} em vigor")
        elif holiday_name:
            notes.append(f"Feriado: {holiday_name}")
        elif cursor.weekday() >= 5:
            notes.append("Fim de semana")
        else:
            expected_minutes = employee.daily_minutes

        if not day_punches and not justified_labels:
            notes.append("Sem marcacoes")
        if len(effective_punches) % 2 == 1:
            notes.append("Batidas incompletas")
        if day_adjustments:
            reasons = ", ".join(clean_text(item.reason) for item in day_adjustments if clean_text(item.reason))
            if reasons:
                notes.append(f"Ajustes: {reasons}")

        balance_minutes = worked_minutes - expected_minutes + adjustment_minutes
        worked_total += worked_minutes
        expected_total += expected_minutes
        adjustment_total += adjustment_minutes
        balance_total += balance_minutes

        rows.append(
            {
                "date": cursor.isoformat(),
                "weekday_label": WEEKDAY_LABELS[cursor.weekday()],
                "punches": [item.strftime("%H:%M") for item in day_punches],
                "justified_punches": justified_labels,
                "justifications": report_justifications,
                "editable_times": " / ".join(justified_times_for_edit),
                "punches_label": " | ".join([item.strftime("%H:%M") for item in day_punches] + justified_labels) or "-",
                "expected_minutes": expected_minutes,
                "expected_label": format_minutes_label(expected_minutes),
                "worked_minutes": worked_minutes,
                "worked_label": format_minutes_label(worked_minutes),
                "adjustment_minutes": adjustment_minutes,
                "adjustment_label": format_minutes_label(adjustment_minutes, signed=True),
                "balance_minutes": balance_minutes,
                "balance_label": format_minutes_label(balance_minutes, signed=True),
                "notes": " | ".join(notes) or "Jornada regular",
            }
        )
        cursor += timedelta(days=1)

    return {
        "employee": serialize_employee(employee),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "generated_at": now_local(tz_name).isoformat(),
        "rows": rows,
        "totals": {
            "worked_minutes": worked_total,
            "worked_label": format_minutes_label(worked_total),
            "expected_minutes": expected_total,
            "expected_label": format_minutes_label(expected_total),
            "adjustment_minutes": adjustment_total,
            "adjustment_label": format_minutes_label(adjustment_total, signed=True),
            "balance_minutes": balance_total,
            "balance_label": format_minutes_label(balance_total, signed=True),
        },
    }


def compliance_snapshot(config: AppConfig) -> list[dict[str, str]]:
    return [
        {"item": "REP-P com marcação imutavel, NSR e hash SHA-256", "status": "implementado"},
        {"item": "AFD em texto ASCII com leiaute 003 e assinatura destacada no arquivo", "status": "implementado"},
        {"item": "Comprovante PDF via portal e envio por e-mail", "status": "implementado"},
        {"item": "Hora oficial via servidores NTP publicos do Observatorio Nacional", "status": "implementado"},
        {"item": "Assinatura ICP-Brasil PAdES/CAdES", "status": "pendente de certificado e integracao externa"},
        {"item": "Certificado de registro do programa no INPI (art. 91)", "status": "dependencia externa"},
        {"item": "Atestado Tecnico e Termo de Responsabilidade assinado por responsaveis", "status": "template pronto para assinatura externa"},
        {"item": "Homologacao final juridico-trabalhista", "status": "pendente de validacao formal da empresa"},
    ]


def email_integration_status(session: Session) -> dict[str, Any]:
    recent_dispatches = session.scalars(select(EmailDispatch).order_by(EmailDispatch.created_at.desc(), EmailDispatch.id.desc()).limit(8)).all()
    has_config = bool(Config.SMTP_HOST and Config.SMTP_FROM)
    status = "pending_config"
    if has_config:
        if recent_dispatches:
            latest_status = recent_dispatches[0].status
            if latest_status == "sent":
                status = "operational"
            elif latest_status == "error":
                status = "error"
            elif latest_status == "pending_config":
                status = "pending_config"
            else:
                status = latest_status
        else:
            status = "ready"

    return {
        "configured": has_config,
        "status": status,
        "smtp_host": Config.SMTP_HOST,
        "smtp_port": Config.SMTP_PORT,
        "smtp_user": Config.SMTP_USER,
        "smtp_from": Config.SMTP_FROM,
        "auth_enabled": bool(Config.SMTP_USER and Config.SMTP_PASSWORD),
        "recent_dispatches": [serialize_email_dispatch(item) for item in recent_dispatches],
        "last_dispatch": serialize_email_dispatch(recent_dispatches[0]) if recent_dispatches else None,
    }


def send_password_recovery_code(session: Session, employee: Employee) -> dict[str, Any]:
    recipient = clean_text(employee.email)
    if not recipient:
        raise ValueError("O funcionario nao possui e-mail cadastrado para recuperacao.")

    reset_code = f"{secrets.randbelow(1000000):06d}"
    employee.reset_code_hash = _hash_password_reset_code(reset_code)
    employee.reset_code_expires_at = utc_now() + timedelta(minutes=PASSWORD_RESET_EXPIRATION_MINUTES)
    employee.reset_code_sent_at = utc_now()

    subject = "NanoPonto - codigo de recuperacao de senha"
    body = (
        f"Ola, {employee.full_name}.\n\n"
        f"Seu codigo de recuperacao de senha e: {reset_code}\n"
        f"Validade: {PASSWORD_RESET_EXPIRATION_MINUTES} minutos.\n\n"
        "Se voce nao solicitou esta alteracao, ignore este e-mail."
    )
    result = _send_email_message(
        session,
        punch_id=f"password-reset-{employee.id}",
        recipient=recipient,
        subject=subject,
        body=body,
    )
    if result["status"] != "sent":
        clear_employee_recovery_code(employee)
        return {
            "status": result["status"],
            "message": f"Nao foi possivel enviar o codigo de recuperacao: {result['message']}",
            "recipient_hint": mask_email(recipient),
        }

    write_audit_log(
        session,
        "employee.password_recovery.requested",
        "employee",
        str(employee.id),
        "password-recovery",
        {"recipient": mask_email(recipient)},
    )
    return {
        "status": result["status"],
        "message": "Codigo de recuperacao enviado com sucesso.",
        "recipient_hint": mask_email(recipient),
        "reset_code": reset_code,
    }


def reset_employee_password_with_code(session: Session, cpf: str, code: str, new_password: str) -> Employee:
    employee = session.scalar(select(Employee).where(Employee.cpf == clean_digits(cpf)).where(Employee.active.is_(True)))
    if employee is None:
        raise ValueError("CPF do usuario nao encontrado.")
    if not employee.reset_code_hash or employee.reset_code_expires_at is None:
        raise ValueError("Solicite um novo codigo de recuperacao.")
    expires_at = as_utc_datetime(employee.reset_code_expires_at)
    if expires_at is None:
        raise ValueError("Solicite um novo codigo de recuperacao.")
    if expires_at < utc_now():
        clear_employee_recovery_code(employee)
        raise ValueError("O codigo de recuperacao expirou. Solicite um novo.")
    if _hash_password_reset_code(code) != employee.reset_code_hash:
        raise ValueError("Codigo de recuperacao invalido.")

    set_employee_password(employee, new_password)
    clear_employee_recovery_code(employee)
    write_audit_log(
        session,
        "employee.password_recovery.completed",
        "employee",
        str(employee.id),
        "password-recovery",
        {"password_updated_at": employee.password_updated_at.isoformat() if employee.password_updated_at else ""},
    )
    return employee


def bootstrap_payload(session: Session) -> dict[str, Any]:
    config = get_or_create_config(session)
    employees = session.scalars(select(Employee).order_by(Employee.full_name.asc())).all()
    recent_punches = session.scalars(select(PunchRecord).order_by(PunchRecord.nsr.desc()).limit(12)).all()
    holidays = session.scalars(select(Holiday).order_by(Holiday.holiday_date.asc())).all()
    leaves = session.scalars(select(LeaveRequest).order_by(LeaveRequest.start_date.asc())).all()
    adjustments = session.scalars(select(BankAdjustment).order_by(BankAdjustment.reference_date.desc())).all()
    justifications = session.scalars(
        select(PointJustification).order_by(PointJustification.reference_date.desc(), PointJustification.id.desc()).limit(24)
    ).all()
    today = now_local(config.timezone_name).date()
    month_start = today.replace(day=1)
    summaries = [employee_bank_summary(session, employee, month_start, today, config.timezone_name) for employee in employees]
    return {
        "config": serialize_config(config),
        "employees": [serialize_employee(employee) for employee in employees],
        "recent_punches": [serialize_punch(session, punch) for punch in recent_punches],
        "holidays": [{"id": item.id, "holiday_date": item.holiday_date.isoformat(), "name": item.name, "scope": item.scope} for item in holidays],
        "leaves": [
            {
                "id": item.id,
                "employee_id": item.employee_id,
                "employee_name": session.get(Employee, item.employee_id).full_name if session.get(Employee, item.employee_id) else "",
                "leave_type": item.leave_type,
                "start_date": item.start_date.isoformat(),
                "end_date": item.end_date.isoformat(),
                "notes": item.notes,
            }
            for item in leaves
        ],
        "adjustments": [
            {
                "id": item.id,
                "employee_id": item.employee_id,
                "employee_name": session.get(Employee, item.employee_id).full_name if session.get(Employee, item.employee_id) else "",
                "reference_date": item.reference_date.isoformat(),
                "minutes_delta": item.minutes_delta,
                "reason": item.reason,
            }
            for item in adjustments
        ],
        "justifications": [serialize_justification(session, item) for item in justifications],
        "bank_summaries": summaries,
        "compliance": compliance_snapshot(config),
        "email_integration": email_integration_status(session),
    }


def seed_default_data(session: Session) -> None:
    created = session.get(AppConfig, 1) is None
    config = get_or_create_config(session)

    if not config.app_name:
        config.app_name = Config.APP_NAME
    if created or not config.employer_name:
        config.employer_name = Config.DEFAULT_EMPLOYER_NAME
    if created or not clean_digits(config.employer_document):
        config.employer_document = Config.DEFAULT_EMPLOYER_DOCUMENT
    if created or not config.employer_document_type:
        config.employer_document_type = Config.DEFAULT_EMPLOYER_DOCUMENT_TYPE
    if created or not config.workplace:
        config.workplace = Config.DEFAULT_EMPLOYER_WORKPLACE
    if created or not config.developer_name:
        config.developer_name = "NanoPonto Tecnologia"
    if created or not config.developer_document:
        config.developer_document = "00000000000000"
    if created or not config.developer_document_type:
        config.developer_document_type = "1"
    if created or not config.timezone_name:
        config.timezone_name = Config.DEFAULT_TIMEZONE

    default_employee = session.scalar(select(Employee).where(Employee.cpf == Config.DEFAULT_EMPLOYEE_CPF))
    if default_employee is None:
        _flush_or_load_existing(
            session,
            lambda: Employee(
                employee_code=Config.DEFAULT_EMPLOYEE_CODE,
                badge_id=Config.DEFAULT_EMPLOYEE_BADGE_ID,
                full_name=Config.DEFAULT_EMPLOYEE_NAME,
                cpf=Config.DEFAULT_EMPLOYEE_CPF,
                email=Config.DEFAULT_EMPLOYEE_EMAIL,
                department="",
                admission_date=date.today(),
                daily_minutes=480,
                active=True,
            ),
            lambda: session.scalar(select(Employee).where(Employee.cpf == Config.DEFAULT_EMPLOYEE_CPF)),
        )
    elif not clean_text(default_employee.email) and clean_text(Config.DEFAULT_EMPLOYEE_EMAIL):
        default_employee.email = clean_text(Config.DEFAULT_EMPLOYEE_EMAIL)


def generate_receipt_pdf(session: Session, punch: PunchRecord) -> bytes:
    config = get_or_create_config(session)
    employee = session.get(Employee, punch.employee_id)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    left = 18 * mm
    top = height - 22 * mm

    pdf.setTitle(f"Comprovante de Registro de Ponto - {punch.id}")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(left, top, "Comprovante de Registro de Ponto do Trabalhador")
    pdf.setFont("Helvetica", 10)
    lines = [
        f"NSR: {punch.nsr:09d}",
        f"Empregador: {config.employer_name} | Documento: {clean_digits(config.employer_document)}",
        f"CEI/CAEPF/CNO: {clean_digits(config.cno_caepf) or 'nao informado'}",
        f"Local da prestacao: {config.workplace}",
        f"Trabalhador: {employee.full_name if employee else 'Desconhecido'} | CPF: {clean_digits(employee.cpf if employee else '')}",
        f"Data e horario do registro: {format_dh(punch.punch_at, config.timezone_name)}",
        f"Registro INPI do REP-P: {clean_digits(config.developer_inpi) or 'PENDENTE_INPI'}",
        f"Codigo hash SHA-256: {punch.hash_code}",
        "Assinatura PAdES: pendente de integracao com certificado ICP-Brasil para homologacao formal.",
    ]
    y = top - 14 * mm
    for line in lines:
        pdf.drawString(left, y, line)
        y -= 8 * mm

    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawString(left, y - 4 * mm, "Documento operacional gerado pelo NanoPonto para consulta imediata, portal e envio por e-mail.")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def generate_justification_pdf(session: Session, justification: PointJustification) -> bytes:
    config = get_or_create_config(session)
    employee = session.get(Employee, justification.employee_id)
    if employee is None:
        raise ValueError("Funcionario da justificativa nao encontrado.")

    payload = serialize_justification(session, justification)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    left = 18 * mm
    right = width - 18 * mm
    top = height - 22 * mm

    pdf.setTitle(f"Justificativa de Ponto - {justification.id}")
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(left, top, "Relatorio de Justificativa de Marcacao de Ponto")

    pdf.setFont("Helvetica", 10)
    lines = [
        f"Empresa: {config.employer_name}",
        f"CNPJ/CPF: {clean_digits(config.employer_document)}",
        f"Funcionario: {employee.full_name}",
        f"CPF: {clean_digits(employee.cpf)}",
        f"Data da ocorrencia: {payload['reference_date']}",
        f"Tipo: {payload['occurrence_label']}",
        f"Horario informado: {payload['informed_time'] or 'nao informado'}",
        f"Motivo resumido: {payload['reason']}",
        f"Atestado anexado: {payload['attachment_original_name'] or 'nao anexado'}",
        f"Status documental: {payload['signature_status']}",
    ]
    y = top - 12 * mm
    for line in lines:
        pdf.drawString(left, y, line)
        y -= 7 * mm

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(left, y - 1 * mm, "Descricao da justificativa")
    y -= 8 * mm
    pdf.setFont("Helvetica", 10)
    detail_lines = pdf.beginText(left, y)
    detail_lines.setLeading(14)
    detail_lines.textLines(payload["details"] or "Funcionario declara que a marcacao nao ocorreu e solicita regularizacao pelo RH.")
    pdf.drawText(detail_lines)

    y -= 28 * mm
    pdf.setFont("Helvetica", 10)
    pdf.drawString(left, y, "Declaracao: Confirmo que as informacoes acima representam a jornada efetivamente realizada.")
    y -= 18 * mm

    signature_width = 60 * mm
    pdf.line(left, y, left + signature_width, y)
    pdf.line((left + right) / 2, y, (left + right) / 2 + signature_width, y)
    pdf.drawString(left, y - 5 * mm, "Assinatura do funcionario")
    pdf.drawString((left + right) / 2, y - 5 * mm, "Assinatura do RH / responsavel")

    y -= 18 * mm
    pdf.line(left, y, left + signature_width, y)
    pdf.drawString(left, y - 5 * mm, "Data da assinatura")

    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawString(left, 15 * mm, "Documento para assinatura manual e arquivo no prontuario funcional.")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def generate_employee_hours_report_pdf(session: Session, employee_id: int, start: date, end: date) -> bytes:
    report = employee_hours_report(session, employee_id, start, end)
    config = get_or_create_config(session)
    employee = report["employee"]
    rows = report["rows"]

    buffer = io.BytesIO()
    page_size = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    width, height = page_size
    left = 14 * mm
    top = height - 16 * mm
    bottom = 18 * mm
    row_height = 7 * mm
    column_widths = [22 * mm, 16 * mm, 58 * mm, 24 * mm, 24 * mm, 24 * mm, 24 * mm]
    column_headers = ["Data", "Dia", "Batidas", "Previsto", "Trabalhado", "Ajuste", "Saldo"]
    notes_width = width - (left * 2) - sum(column_widths)

    def draw_page_header() -> float:
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(left, top, "Espelho de Ponto para Conferencia e Assinatura")
        pdf.setFont("Helvetica", 9)
        pdf.drawString(left, top - 7 * mm, f"Empresa: {config.employer_name} | Documento: {clean_digits(config.employer_document)}")
        pdf.drawString(
            left,
            top - 12 * mm,
            (
                f"Funcionario: {employee['full_name']} | CPF: {clean_digits(employee['cpf'])} | "
                f"Setor: {employee['department'] or 'nao informado'}"
            ),
        )
        pdf.drawString(
            left,
            top - 17 * mm,
            f"Periodo: {report['period_start']} a {report['period_end']} | Emitido em: {report['generated_at'][:19].replace('T', ' ')}",
        )
        header_y = top - 26 * mm
        pdf.setFont("Helvetica-Bold", 8)
        cursor_x = left
        for index, title in enumerate(column_headers):
            pdf.drawString(cursor_x + 1.5 * mm, header_y, title)
            cursor_x += column_widths[index]
        pdf.drawString(cursor_x + 1.5 * mm, header_y, "Observacoes")
        pdf.line(left, header_y - 1.5 * mm, width - left, header_y - 1.5 * mm)
        return header_y - 5 * mm

    def draw_row(y: float, row: dict[str, Any]) -> float:
        values = [
            row["date"],
            row["weekday_label"],
            row["punches_label"],
            row["expected_label"],
            row["worked_label"],
            row["adjustment_label"],
            row["balance_label"],
        ]
        pdf.setFont("Helvetica", 8)
        cursor_x = left
        for index, value in enumerate(values):
            pdf.drawString(cursor_x + 1.5 * mm, y, clean_text(value)[: max(1, math.floor(column_widths[index] / (2.4 * mm)))])
            cursor_x += column_widths[index]
        pdf.drawString(cursor_x + 1.5 * mm, y, clean_text(row["notes"])[: max(1, math.floor(notes_width / (2.2 * mm)))])
        pdf.line(left, y - 1.2 * mm, width - left, y - 1.2 * mm)
        return y - row_height

    y = draw_page_header()
    for row in rows:
        if y <= bottom + 18 * mm:
            pdf.showPage()
            y = draw_page_header()
        y = draw_row(y, row)

    if y <= bottom + 28 * mm:
        pdf.showPage()
        y = draw_page_header()

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(left, y - 2 * mm, "Totais do periodo")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(
        left,
        y - 9 * mm,
        (
            f"Previsto: {report['totals']['expected_label']} | Trabalhado: {report['totals']['worked_label']} | "
            f"Ajustes: {report['totals']['adjustment_label']} | Saldo: {report['totals']['balance_label']}"
        ),
    )
    pdf.drawString(
        left,
        y - 16 * mm,
        "Declaracao: confirmo a conferencia das marcacoes e do saldo de horas do periodo acima informado.",
    )

    signature_y = y - 30 * mm
    signature_width = 70 * mm
    pdf.line(left, signature_y, left + signature_width, signature_y)
    pdf.line(width - left - signature_width, signature_y, width - left, signature_y)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(left, signature_y - 5 * mm, "Assinatura do funcionario")
    pdf.drawString(width - left - signature_width, signature_y - 5 * mm, "Assinatura do RH / responsavel")
    pdf.line(left, signature_y - 16 * mm, left + 40 * mm, signature_y - 16 * mm)
    pdf.drawString(left, signature_y - 21 * mm, "Data")
    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawString(left, 10 * mm, "Documento emitido pelo NanoPonto para arquivo funcional e assinatura manual.")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _send_email_message(
    session: Session,
    *,
    punch_id: str,
    recipient: str,
    subject: str,
    body: str,
    attachment: bytes | None = None,
    attachment_name: str | None = None,
) -> dict[str, Any]:
    email_log = EmailDispatch(punch_id=punch_id, recipient=recipient, status="pending", message="")

    if not Config.SMTP_HOST or not Config.SMTP_FROM:
        email_log.status = "pending_config"
        email_log.message = "SMTP nao configurado. Envio permanece pendente."
        session.add(email_log)
        session.flush()
        write_audit_log(session, "email.pending_config", "email_dispatch", str(email_log.id), "system", {"recipient": recipient, "subject": subject})
        return {"status": email_log.status, "message": email_log.message}

    message = EmailMessage()
    message["From"] = Config.SMTP_FROM
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    if attachment is not None and attachment_name:
        message.add_attachment(attachment, maintype="application", subtype="pdf", filename=attachment_name)

    try:
        if Config.SMTP_PORT == 465:
            context = ssl.create_default_context()
            smtp_client = smtplib.SMTP_SSL(Config.SMTP_HOST, Config.SMTP_PORT, timeout=10, context=context)
        else:
            smtp_client = smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=10)
        with smtp_client as smtp:
            smtp.ehlo()
            if Config.SMTP_PORT != 465:
                try:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                except smtplib.SMTPException:
                    pass
            if Config.SMTP_USER and Config.SMTP_PASSWORD:
                smtp.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            smtp.send_message(message)
        email_log.status = "sent"
        email_log.message = "Enviado com sucesso."
    except Exception as exc:  # pragma: no cover - depends on SMTP infra
        email_log.status = "error"
        email_log.message = str(exc)

    session.add(email_log)
    session.flush()
    write_audit_log(session, "email.send", "email_dispatch", str(email_log.id), "system", {"recipient": recipient, "subject": subject, "status": email_log.status, "message": email_log.message})
    return {"status": email_log.status, "message": email_log.message, "dispatch": serialize_email_dispatch(email_log)}


def send_receipt_email(session: Session, punch: PunchRecord, recipient: str) -> dict[str, Any]:
    pdf_bytes = generate_receipt_pdf(session, punch)
    subject = f"NanoPonto - comprovante NSR {punch.nsr:09d}"
    body = "Segue o comprovante de registro de ponto gerado pelo NanoPonto."
    return _send_email_message(
        session,
        punch_id=punch.id,
        recipient=recipient,
        subject=subject,
        body=body,
        attachment=pdf_bytes,
        attachment_name=f"comprovante-{punch.nsr:09d}.pdf",
    )


def send_email_integration_test(session: Session, recipient: str) -> dict[str, Any]:
    subject = "NanoPonto - teste de integracao de e-mail"
    body = (
        "Este e-mail confirma que a integracao de SMTP do NanoPonto esta funcionando.\n\n"
        f"Gerado em: {datetime.now(timezone.utc).isoformat()}"
    )
    return _send_email_message(
        session,
        punch_id="integration-test",
        recipient=recipient,
        subject=subject,
        body=body,
    )


def _header_record(config: AppConfig, start: date, end: date, generated_at: datetime) -> str:
    base = (
        "000000000"
        "1"
        f"{config.employer_document_type}"
        f"{format_document(config.employer_document, 14)}"
        f"{format_document(config.cno_caepf, 14)}"
        f"{format_alpha(config.employer_name, 150)}"
        f"{format_alpha(clean_digits(config.developer_inpi) or '00000000000000000', 17)}"
        f"{format_d(start)}"
        f"{format_d(end)}"
        f"{format_dh(generated_at, config.timezone_name)}"
        "003"
        f"{config.developer_document_type}"
        f"{format_document(config.developer_document, 14)}"
        f"{format_alpha('', 30)}"
    )
    return base + crc16_kermit(base)


def _record_type_2(config: AppConfig, event: RepEvent) -> str:
    payload = json_loads(event.payload)
    base = (
        f"{event.nsr:09d}"
        "2"
        f"{format_dh(event.occurred_at, config.timezone_name)}"
        f"{format_document(payload.get('responsible_cpf', ''), 14)}"
        f"{payload.get('employer_document_type', config.employer_document_type)}"
        f"{format_document(payload.get('employer_document', config.employer_document), 14)}"
        f"{format_document(payload.get('cno_caepf', config.cno_caepf), 14)}"
        f"{format_alpha(payload.get('employer_name', config.employer_name), 150)}"
        f"{format_alpha(payload.get('workplace', config.workplace), 100)}"
    )
    return base + crc16_kermit(base)


def _record_type_5(config: AppConfig, event: RepEvent) -> str:
    payload = json_loads(event.payload)
    base = (
        f"{event.nsr:09d}"
        "5"
        f"{format_dh(event.occurred_at, config.timezone_name)}"
        f"{payload.get('operation', 'A')[:1]}"
        f"{format_document(payload.get('employee_cpf', ''), 12)}"
        f"{format_alpha(payload.get('employee_name', ''), 52)}"
        f"{format_alpha(payload.get('employee_code', ''), 4)}"
        f"{format_document(payload.get('responsible_cpf', ''), 11)}"
    )
    return base + crc16_kermit(base)


def _record_type_6(config: AppConfig, event: RepEvent) -> str:
    payload = json_loads(event.payload)
    return (
        f"{event.nsr:09d}"
        "6"
        f"{format_dh(event.occurred_at, config.timezone_name)}"
        f"{payload.get('event_code', '07')[:2]}"
    )


def _record_type_7(config: AppConfig, punch: PunchRecord, employee: Employee) -> str:
    return (
        f"{punch.nsr:09d}"
        "7"
        f"{format_dh(punch.punch_at, config.timezone_name)}"
        f"{format_document(employee.cpf, 12)}"
        f"{format_dh(punch.recorded_at, config.timezone_name)}"
        f"{punch.collector_code}"
        f"{'1' if punch.is_offline else '0'}"
        f"{punch.hash_code}"
    )


def generate_afd(session: Session, start: date | None = None, end: date | None = None) -> tuple[str, str]:
    config = get_or_create_config(session)
    tz = get_zoneinfo(config.timezone_name)
    all_punches = session.scalars(select(PunchRecord).order_by(PunchRecord.nsr.asc())).all()
    if all_punches and start is None:
        start = all_punches[0].punch_at.astimezone(tz).date()
    if all_punches and end is None:
        end = all_punches[-1].punch_at.astimezone(tz).date()
    start = start or now_local(config.timezone_name).date()
    end = end or start

    generated_at = get_official_time(session, strict=False).instant
    records: list[str] = [_header_record(config, start, end, generated_at)]

    event_start = datetime.combine(start, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    event_end = datetime.combine(end, datetime.max.time(), tzinfo=tz).astimezone(timezone.utc)
    events = session.scalars(
        select(RepEvent)
        .where(RepEvent.occurred_at >= event_start)
        .where(RepEvent.occurred_at <= event_end)
        .order_by(RepEvent.nsr.asc())
    ).all()
    counts = {"2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "7": 0}
    for event in events:
        if event.record_type == "2":
            records.append(_record_type_2(config, event))
            counts["2"] += 1
        elif event.record_type == "5":
            records.append(_record_type_5(config, event))
            counts["5"] += 1
        elif event.record_type == "6":
            records.append(_record_type_6(config, event))
            counts["6"] += 1

    punches = session.scalars(
        select(PunchRecord)
        .where(PunchRecord.punch_at >= event_start)
        .where(PunchRecord.punch_at <= event_end)
        .order_by(PunchRecord.nsr.asc())
    ).all()
    for punch in punches:
        employee = session.get(Employee, punch.employee_id)
        if employee is None:
            continue
        records.append(_record_type_7(config, punch, employee))
        counts["7"] += 1

    trailer = (
        "999999999"
        f"{counts['2']:09d}"
        f"{counts['3']:09d}"
        f"{counts['4']:09d}"
        f"{counts['5']:09d}"
        f"{counts['6']:09d}"
        f"{counts['7']:09d}"
        "9"
    )
    records.append(trailer)
    records.append(format_alpha("ASSINATURA_DIGITAL_EM_ARQUIVO_P7S", 100))

    inpi = clean_digits(config.developer_inpi) or "00000000000000000"
    employer_doc = clean_digits(config.employer_document) or "00000000000000"
    filename = f"AFD{inpi}{employer_doc}REP_P.txt"
    body = "\r\n".join(records) + "\r\n"
    return filename, body


def generate_fiscal_export_zip(session: Session, start: date | None = None, end: date | None = None) -> tuple[str, bytes]:
    config = get_or_create_config(session)
    filename, afd_body = generate_afd(session, start, end)
    hash_value = hashlib.sha256(afd_body.encode("latin-1", errors="replace")).hexdigest().upper()
    manifest = {
        "application": config.app_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "afd_file": filename,
        "afd_sha256": hash_value,
        "compliance": compliance_snapshot(config),
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, afd_body.encode("latin-1", errors="replace"))
        archive.writestr(f"{filename}.sha256", f"{hash_value}  {filename}\n")
        archive.writestr("manifesto-fiscalizacao.json", json_dumps(manifest))

    export_name = f"fiscalizacao-{now_local(config.timezone_name).strftime('%Y%m%d-%H%M%S')}.zip"
    return export_name, zip_buffer.getvalue()
