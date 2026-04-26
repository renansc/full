from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AppConfig(Base):
    __tablename__ = "app_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    app_name: Mapped[str] = mapped_column(String(80), default="NanoPonto")
    employer_name: Mapped[str] = mapped_column(String(150), default="Empresa Homologacao")
    employer_document: Mapped[str] = mapped_column(String(20), default="00000000000000")
    employer_document_type: Mapped[str] = mapped_column(String(1), default="1")
    cno_caepf: Mapped[str] = mapped_column(String(20), default="")
    workplace: Mapped[str] = mapped_column(String(150), default="Matriz")
    developer_name: Mapped[str] = mapped_column(String(150), default="NanoPonto Tecnologia")
    developer_document: Mapped[str] = mapped_column(String(20), default="00000000000000")
    developer_document_type: Mapped[str] = mapped_column(String(1), default="1")
    developer_inpi: Mapped[str] = mapped_column(String(30), default="")
    legal_responsible_name: Mapped[str] = mapped_column(String(120), default="")
    legal_responsible_cpf: Mapped[str] = mapped_column(String(14), default="")
    technical_responsible_name: Mapped[str] = mapped_column(String(120), default="")
    technical_responsible_cpf: Mapped[str] = mapped_column(String(14), default="")
    technical_responsible_registry: Mapped[str] = mapped_column(String(60), default="")
    timezone_name: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    allow_system_time_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    service_status: Mapped[str] = mapped_column(String(20), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_code: Mapped[str] = mapped_column(String(30), unique=True)
    badge_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    cpf: Mapped[str] = mapped_column(String(14), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    reset_code_hash: Mapped[str] = mapped_column(String(64), default="")
    reset_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reset_code_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    department: Mapped[str] = mapped_column(String(80), default="")
    admission_date: Mapped[date] = mapped_column(Date)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    daily_minutes: Mapped[int] = mapped_column(Integer, default=480)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    punches: Mapped[list["PunchRecord"]] = relationship(back_populates="employee")


class RepEvent(Base):
    __tablename__ = "rep_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nsr: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    record_type: Mapped[str] = mapped_column(String(1), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PunchRecord(Base):
    __tablename__ = "punch_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    nsr: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    collector_code: Mapped[str] = mapped_column(String(2))
    collector_label: Mapped[str] = mapped_column(String(60))
    is_offline: Mapped[bool] = mapped_column(Boolean, default=False)
    punch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    previous_hash: Mapped[str] = mapped_column(String(64), default="")
    hash_code: Mapped[str] = mapped_column(String(64))
    ntp_server: Mapped[str] = mapped_column(String(64), default="")
    ntp_offset_ms: Mapped[int] = mapped_column(Integer, default=0)
    time_source: Mapped[str] = mapped_column(String(30), default="ntp")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    employee: Mapped[Employee] = relationship(back_populates="punches")


class Holiday(Base):
    __tablename__ = "holidays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    holiday_date: Mapped[date] = mapped_column(Date, unique=True)
    name: Mapped[str] = mapped_column(String(120))
    scope: Mapped[str] = mapped_column(String(40), default="empresa")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    leave_type: Mapped[str] = mapped_column(String(30), default="ferias")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BankAdjustment(Base):
    __tablename__ = "bank_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    reference_date: Mapped[date] = mapped_column(Date)
    minutes_delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PointJustification(Base):
    __tablename__ = "point_justifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    reference_date: Mapped[date] = mapped_column(Date, index=True)
    occurrence_type: Mapped[str] = mapped_column(String(40), default="esquecimento")
    informed_time: Mapped[str] = mapped_column(String(30), default="")
    reason: Mapped[str] = mapped_column(String(160))
    details: Mapped[str] = mapped_column(Text, default="")
    attachment_original_name: Mapped[str] = mapped_column(String(255), default="")
    attachment_stored_name: Mapped[str] = mapped_column(String(255), default="")
    attachment_mime: Mapped[str] = mapped_column(String(120), default="")
    signature_status: Mapped[str] = mapped_column(String(30), default="pendente_assinatura")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(60))
    actor: Mapped[str] = mapped_column(String(80), default="system")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EmailDispatch(Base):
    __tablename__ = "email_dispatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    punch_id: Mapped[str] = mapped_column(String(36), index=True)
    recipient: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
