from __future__ import annotations

from io import BytesIO
from datetime import date
from pathlib import Path

import pytest

import nanoponto.services as services_module
from nanoponto import create_app
from nanoponto.services import crc16_kermit


@pytest.fixture(autouse=True)
def use_local_time_fallback(monkeypatch):
    services_module.Config.NTP_SERVERS = ()
    services_module.Config.ALLOW_SYSTEM_TIME_FALLBACK = True
    monkeypatch.setattr(services_module.Config, "NTP_SERVERS", ())
    monkeypatch.setattr(services_module.Config, "ALLOW_SYSTEM_TIME_FALLBACK", True)


def make_app(tmp_path: Path):
    db_path = tmp_path / "test.db"
    return create_app({"TESTING": True, "DATABASE_URL": f"sqlite:///{db_path}", "ATESTADO_UPLOAD_DIR": tmp_path / "atestados"})


def fake_send_email_message(_session, **_kwargs):
    return {"status": "sent", "message": "Enviado com sucesso."}


def test_crc16_kermit_reference():
    assert crc16_kermit("123456789") == "2189"


def test_create_app_is_idempotent_for_existing_database(tmp_path):
    db_path = tmp_path / "test.db"
    create_app({"TESTING": True, "DATABASE_URL": f"sqlite:///{db_path}"})

    app = create_app({"TESTING": True, "DATABASE_URL": f"sqlite:///{db_path}"})
    client = app.test_client()
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_register_punch_and_generate_afd(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()

    admin_login = client.post("/api/auth/login", json={"role": "admin", "username": "admin", "password": "4625190000100"})
    assert admin_login.status_code == 200

    settings_payload = {
        "employer_name": "Empresa Teste",
        "employer_document": "12345678000199",
        "employer_document_type": "1",
        "workplace": "Matriz",
        "developer_inpi": "5120221234567",
        "technical_responsible_name": "Responsavel Teste",
        "technical_responsible_cpf": "12345678901",
        "allow_system_time_fallback": True,
    }
    employee_payload = {
        "employee_code": "E002",
        "full_name": "Ana Silva",
        "cpf": "12345678901",
        "badge_id": "RFID-1",
        "admission_date": "2026-04-22",
        "daily_minutes": 480,
    }

    settings_response = client.post("/api/settings", json=settings_payload)
    assert settings_response.status_code == 200

    employee_response = client.post("/api/employees", json=employee_payload)
    assert employee_response.status_code == 200

    punch_response = client.post("/api/punches", json={"badge_id": "RFID-1", "collector_code": "04"})
    assert punch_response.status_code == 200
    punch_payload = punch_response.get_json()
    assert punch_payload["punch"]["hash_code"]

    afd_response = client.get("/api/afd.txt")
    assert afd_response.status_code == 200
    afd_text = afd_response.data.decode("latin-1")
    assert "ASSINATURA_DIGITAL_EM_ARQUIVO_P7S" in afd_text
    assert "0000000017" in afd_text or "7" in afd_text


def test_settings_accept_blank_cno_caepf(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    admin_login = client.post("/api/auth/login", json={"role": "admin", "username": "admin", "password": "4625190000100"})
    assert admin_login.status_code == 200

    response = client.post(
        "/api/settings",
        json={
            "employer_name": "Empresa Sem CAEPF",
            "employer_document": "12345678000199",
            "employer_document_type": "1",
            "cno_caepf": None,
            "workplace": "Matriz",
            "allow_system_time_fallback": True,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["config"]["cno_caepf"] == ""


def test_admin_and_employee_auth_scope(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()

    initial_admin_login = client.post("/api/auth/login", json={"role": "admin", "username": "admin", "password": "4625190000100"})
    assert initial_admin_login.status_code == 200

    client.post(
        "/api/settings",
        json={
            "employer_name": "Nanotech",
            "employer_document": "4625190000100",
            "employer_document_type": "1",
            "workplace": "Matriz",
            "allow_system_time_fallback": True,
        },
    )
    client.post("/api/auth/logout")

    admin_login_after_logout = client.post(
        "/api/auth/login",
        json={"role": "admin", "username": "admin", "password": "4625190000100"},
    )
    assert admin_login_after_logout.status_code == 200
    employee_create = client.post(
        "/api/employees",
        json={
            "employee_code": "E002",
            "full_name": "Ana Silva",
            "cpf": "12345678901",
            "admission_date": "2026-04-22",
            "daily_minutes": 480,
        },
    )
    assert employee_create.status_code == 200
    client.post("/api/auth/logout")

    unauth_bootstrap = client.get("/api/bootstrap")
    assert unauth_bootstrap.status_code == 401

    admin_login = client.post("/api/auth/login", json={"role": "admin", "username": "admin", "password": "46025190000100"})
    assert admin_login.status_code == 401
    admin_bootstrap = client.get("/api/bootstrap")
    assert admin_bootstrap.status_code == 401

    admin_login = client.post("/api/auth/login", json={"role": "admin", "username": "admin", "password": "4625190000100"})
    assert admin_login.status_code == 200
    admin_bootstrap = client.get("/api/bootstrap")
    assert admin_bootstrap.status_code == 200
    assert admin_bootstrap.get_json()["current_user"]["role"] == "admin"
    assert any(item["cpf"] == "06587583903" for item in admin_bootstrap.get_json()["employees"])
    client.post("/api/auth/logout")

    employee_login = client.post("/api/auth/login", json={"role": "employee", "username": "06587583903", "password": "06587583903"})
    assert employee_login.status_code == 200
    employee_bootstrap = client.get("/api/bootstrap")
    assert employee_bootstrap.status_code == 200
    employee_payload = employee_bootstrap.get_json()
    assert employee_payload["current_user"]["role"] == "employee"
    assert len(employee_payload["employees"]) == 1
    assert employee_payload["employees"][0]["cpf"] == "06587583903"
    employee_report = client.get(f"/api/reports/hours?employee_id=999&start={date.today().isoformat()}&end={date.today().isoformat()}")
    assert employee_report.status_code == 200
    assert employee_report.get_json()["employee"]["cpf"] == "06587583903"

    employee_cannot_change_settings = client.post(
        "/api/settings",
        json={"employer_name": "Outra Empresa", "employer_document": "11111111000111", "employer_document_type": "1"},
    )
    assert employee_cannot_change_settings.status_code == 403

    employee_punch = client.post("/api/punches", json={"employee_id": 999, "collector_code": "01"})
    assert employee_punch.status_code == 200
    assert employee_punch.get_json()["punch"]["employee_cpf"] == "06587583903"


def test_email_integration_status_and_test_endpoint(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()

    admin_login = client.post("/api/auth/login", json={"role": "admin", "username": "admin", "password": "4625190000100"})
    assert admin_login.status_code == 200

    status_response = client.get("/api/integrations/email/status")
    assert status_response.status_code == 200
    status_payload = status_response.get_json()
    assert "status" in status_payload

    test_response = client.post("/api/integrations/email/test", json={"recipient": "teste@example.com"})
    assert test_response.status_code == 200
    assert test_response.get_json()["status"] in {"pending_config", "sent", "error"}


def test_register_punch_sends_automatic_email(tmp_path, monkeypatch):
    monkeypatch.setattr(services_module, "_send_email_message", fake_send_email_message)

    app = make_app(tmp_path)
    client = app.test_client()

    admin_login = client.post("/api/auth/login", json={"role": "admin", "username": "admin", "password": "4625190000100"})
    assert admin_login.status_code == 200

    employee_response = client.post(
        "/api/employees",
        json={
            "employee_code": "E002",
            "full_name": "Ana Silva",
            "cpf": "12345678901",
            "email": "ana@example.com",
            "admission_date": "2026-04-22",
            "daily_minutes": 480,
        },
    )
    assert employee_response.status_code == 200
    employee_id = employee_response.get_json()["saved_id"]

    punch_response = client.post("/api/punches", json={"employee_id": employee_id, "collector_code": "02"})
    assert punch_response.status_code == 200
    payload = punch_response.get_json()
    assert payload["auto_email"]["status"] == "sent"
    assert "automaticamente" in payload["message"]


def test_employee_password_recovery_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(services_module.Config, "DEFAULT_EMPLOYEE_EMAIL", "solucoestecnologicasrenan@gmail.com")
    monkeypatch.setattr(services_module, "_send_email_message", fake_send_email_message)

    app = make_app(tmp_path)
    client = app.test_client()

    recovery_request = client.post("/api/auth/password-recovery/request", json={"cpf": "06587583903"})
    assert recovery_request.status_code == 200
    recovery_payload = recovery_request.get_json()
    assert recovery_payload["status"] == "sent"
    assert recovery_payload["recipient_hint"]
    assert recovery_payload["debug_reset_code"]

    recovery_confirm = client.post(
        "/api/auth/password-recovery/confirm",
        json={
            "cpf": "06587583903",
            "code": recovery_payload["debug_reset_code"],
            "new_password": "NovaSenha123",
        },
    )
    assert recovery_confirm.status_code == 200

    old_login = client.post("/api/auth/login", json={"role": "employee", "username": "06587583903", "password": "06587583903"})
    assert old_login.status_code == 401

    new_login = client.post("/api/auth/login", json={"role": "employee", "username": "06587583903", "password": "NovaSenha123"})
    assert new_login.status_code == 200


def test_point_justification_report_and_scope(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()

    admin_login = client.post("/api/auth/login", json={"role": "admin", "username": "admin", "password": "4625190000100"})
    assert admin_login.status_code == 200

    employee_response = client.post(
        "/api/employees",
        json={
            "employee_code": "E002",
            "full_name": "Ana Silva",
            "cpf": "12345678901",
            "admission_date": "2026-04-22",
            "daily_minutes": 480,
        },
    )
    assert employee_response.status_code == 200

    justification_response = client.post(
        "/api/justifications",
        json={
            "employee_id": 2,
            "reference_date": "2026-04-22",
            "occurrence_type": "problema_tecnico",
            "informed_time": "08:00 / 18:00",
            "reason": "Relogio sem conexao",
            "details": "Funcionario compareceu no horario normal, mas o terminal estava indisponivel.",
        },
    )
    assert justification_response.status_code == 200
    justification_id = justification_response.get_json()["justification_id"]

    bootstrap_response = client.get("/api/bootstrap")
    assert bootstrap_response.status_code == 200
    justifications = bootstrap_response.get_json()["justifications"]
    assert len(justifications) == 1
    assert justifications[0]["occurrence_type"] == "problema_tecnico"

    pdf_response = client.get(f"/api/justifications/{justification_id}.pdf")
    assert pdf_response.status_code == 200
    assert pdf_response.mimetype == "application/pdf"

    client.post("/api/auth/logout")
    employee_login = client.post("/api/auth/login", json={"role": "employee", "username": "12345678901", "password": "12345678901"})
    assert employee_login.status_code == 200

    employee_create_justification = client.post(
        "/api/justifications",
        json={
            "reference_date": "2026-04-23",
            "occurrence_type": "fora_horario",
            "informed_time": "19:30",
            "reason": "Atendimento emergencial",
            "details": "Funcionario ficou apos o expediente para finalizar atendimento.",
        },
    )
    assert employee_create_justification.status_code == 200

    employee_bootstrap = client.get("/api/bootstrap")
    assert employee_bootstrap.status_code == 200
    employee_justifications = employee_bootstrap.get_json()["justifications"]
    assert len(employee_justifications) == 2
    assert employee_justifications[0]["employee_id"] == 2

    employee_report = client.get("/api/reports/hours?start=2026-04-22&end=2026-04-22")
    assert employee_report.status_code == 200
    report_payload = employee_report.get_json()
    assert len(report_payload["rows"]) == 1
    assert report_payload["rows"][0]["justified_punches"] == ["08:00 (just.)", "18:00 (just.)"]
    assert "Justificativa:" in report_payload["rows"][0]["notes"]

    employee_pdf_response = client.get(f"/api/justifications/{justification_id}.pdf")
    assert employee_pdf_response.status_code == 200


def test_point_justification_requires_valid_employee(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()

    admin_login = client.post("/api/auth/login", json={"role": "admin", "username": "admin", "password": "4625190000100"})
    assert admin_login.status_code == 200

    response = client.post(
        "/api/justifications",
        json={
            "employee_id": 999,
            "reference_date": "2026-04-22",
            "occurrence_type": "esquecimento",
            "informed_time": "08:00",
            "reason": "Teste com funcionario inexistente",
        },
    )

    assert response.status_code == 400
    assert "Funcionario nao encontrado" in response.get_json()["error"]


def test_employee_can_upload_and_download_medical_certificate(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()

    employee_login = client.post("/api/auth/login", json={"role": "employee", "username": "06587583903", "password": "06587583903"})
    assert employee_login.status_code == 200

    response = client.post(
        "/api/justifications",
        data={
            "reference_date": "2026-04-22",
            "occurrence_type": "problema_tecnico",
            "informed_time": "08:00 / 18:00",
            "reason": "Atestado medico",
            "details": "Funcionario anexou atestado medico para validacao do RH.",
            "attachment": (BytesIO(b"%PDF-1.4\natestato\n"), "atestado.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    justification_id = response.get_json()["justification_id"]

    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.status_code == 200
    justification = bootstrap.get_json()["justifications"][0]
    assert justification["attachment_original_name"] == "atestado.pdf"

    attachment_response = client.get(f"/api/justifications/{justification_id}/attachment")
    assert attachment_response.status_code == 200
    assert attachment_response.data.startswith(b"%PDF-1.4")


def test_medical_certificate_range_appears_in_hours_report(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()

    employee_login = client.post("/api/auth/login", json={"role": "employee", "username": "06587583903", "password": "06587583903"})
    assert employee_login.status_code == 200

    response = client.post(
        "/api/medical-certificates",
        data={
            "reference_date": "2026-04-22",
            "end_date": "2026-04-24",
            "reason": "Atestado medico de tres dias",
            "details": "Afastamento medico informado pelo funcionario.",
            "attachment": (BytesIO(b"%PDF-1.4\natestato\n"), "atestado-3-dias.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert len(response.get_json()["certificate_ids"]) == 3

    report_response = client.get("/api/reports/hours?start=2026-04-22&end=2026-04-24")
    assert report_response.status_code == 200
    rows = report_response.get_json()["rows"]
    assert [row["date"] for row in rows] == ["2026-04-22", "2026-04-23", "2026-04-24"]
    assert all(row["justifications"][0]["occurrence_type"] == "atestado_medico" for row in rows)
    assert all(row["justifications"][0]["attachment_original_name"] == "atestado-3-dias.pdf" for row in rows)
    assert all("Atestado medico de tres dias" in row["notes"] for row in rows)

    attachment_response = client.get(f"/api/justifications/{response.get_json()['certificate_ids'][1]}/attachment")
    assert attachment_response.status_code == 200
    assert attachment_response.headers["Content-Disposition"].startswith("inline;")


def test_admin_hours_report_and_signature_pdf(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()

    admin_login = client.post("/api/auth/login", json={"role": "admin", "username": "admin", "password": "4625190000100"})
    assert admin_login.status_code == 200

    settings_response = client.post(
        "/api/settings",
        json={
            "employer_name": "Empresa Teste",
            "employer_document": "12345678000199",
            "employer_document_type": "1",
            "workplace": "Matriz",
            "allow_system_time_fallback": True,
        },
    )
    assert settings_response.status_code == 200

    employee_response = client.post(
        "/api/employees",
        json={
            "employee_code": "E002",
            "full_name": "Ana Silva",
            "cpf": "12345678901",
            "email": "ana@example.com",
            "admission_date": "2026-04-22",
            "daily_minutes": 480,
        },
    )
    assert employee_response.status_code == 200
    employee_id = employee_response.get_json()["saved_id"]

    first_punch = client.post("/api/punches", json={"employee_id": employee_id, "collector_code": "02"})
    second_punch = client.post("/api/punches", json={"employee_id": employee_id, "collector_code": "02"})
    assert first_punch.status_code == 200
    assert second_punch.status_code == 200

    today = date.today().isoformat()
    report_response = client.get(f"/api/reports/hours?employee_id={employee_id}&start={today}&end={today}")
    assert report_response.status_code == 200
    report_payload = report_response.get_json()
    assert report_payload["employee"]["id"] == employee_id
    assert len(report_payload["rows"]) == 1
    assert report_payload["rows"][0]["date"] == today
    assert len(report_payload["rows"][0]["punches"]) >= 2

    pdf_response = client.get(f"/api/reports/hours.pdf?employee_id={employee_id}&start={today}&end={today}")
    assert pdf_response.status_code == 200
    assert pdf_response.mimetype == "application/pdf"

    missing_punch_response = client.post(
        "/api/reports/hours/missing-punches",
        json={
            "employee_id": employee_id,
            "reference_date": today,
            "informed_time": "08:00 / 18:00",
            "period_start": today,
            "period_end": today,
        },
    )
    assert missing_punch_response.status_code == 200
    updated_report = missing_punch_response.get_json()["report"]
    assert updated_report["rows"][0]["editable_times"] == "08:00 / 18:00"
    assert updated_report["rows"][0]["justified_punches"] == ["08:00 (just.)", "18:00 (just.)"]
