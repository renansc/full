from __future__ import annotations

import io
import time
import uuid
from functools import wraps
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file, session
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import scoped_session, sessionmaker
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from .config import Config
from .models import Base, Employee, PointJustification, PunchRecord
from .services import (
    bootstrap_payload,
    clean_digits,
    clean_text,
    create_bank_adjustment,
    create_holiday,
    create_leave,
    create_point_justification,
    employee_hours_report,
    ensure_runtime_schema,
    generate_afd,
    generate_employee_hours_report_pdf,
    generate_fiscal_export_zip,
    generate_justification_pdf,
    generate_receipt_pdf,
    get_or_create_config,
    email_integration_status,
    register_punch,
    reset_employee_password_with_code,
    seed_default_data,
    save_report_missing_punches,
    send_email_integration_test,
    send_password_recovery_code,
    send_receipt_email,
    serialize_punch,
    upsert_employee,
    upsert_settings,
    verify_employee_password,
)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH
    upload_dir = Path(app.config.get("ATESTADO_UPLOAD_DIR") or Config.ATESTADO_UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    if app.config["DATABASE_URL"].startswith("sqlite:///"):
        sqlite_path = Path(app.config["DATABASE_URL"].replace("sqlite:///", "", 1))
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(app.config["DATABASE_URL"], future=True, pool_pre_ping=True)
    session_factory = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False))

    for attempt in range(10):
        try:
            Base.metadata.create_all(engine)
            break
        except OperationalError:
            if attempt == 9:
                raise
            time.sleep(1)

    ensure_runtime_schema(engine)

    with session_factory() as bootstrap_db:
        seed_default_data(bootstrap_db)
        bootstrap_db.commit()

    app.extensions["db_session_factory"] = session_factory

    @app.teardown_appcontext
    def remove_session(_: BaseException | None) -> None:
        session_factory.remove()

    def db_session():
        return session_factory()

    def current_auth() -> dict | None:
        role = session.get("role")
        if role not in {"admin", "employee"}:
            return None
        auth = {"role": role}
        if role == "employee":
            auth["employee_id"] = session.get("employee_id")
            auth["employee_name"] = session.get("employee_name", "")
            auth["employee_cpf"] = session.get("employee_cpf", "")
        else:
            auth["employee_id"] = None
            auth["employee_name"] = "Administrador"
            auth["employee_cpf"] = ""
        return auth

    def require_auth(role: str | None = None):
        def decorator(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                auth = current_auth()
                if auth is None:
                    return jsonify({"error": "Sessao expirada. Faca login novamente."}), 401
                if role and auth["role"] != role:
                    return jsonify({"error": "Acesso negado para este perfil."}), 403
                return view(*args, **kwargs)

            return wrapped

        return decorator

    def filtered_bootstrap(db, auth: dict) -> dict:
        payload = bootstrap_payload(db)
        payload["current_user"] = auth
        if auth["role"] == "admin":
            return payload

        employee_id = auth.get("employee_id")
        payload["employees"] = [item for item in payload["employees"] if item["id"] == employee_id]
        payload["recent_punches"] = [item for item in payload["recent_punches"] if item["employee_id"] == employee_id]
        payload["bank_summaries"] = [item for item in payload["bank_summaries"] if item["employee_id"] == employee_id]
        payload["leaves"] = [item for item in payload["leaves"] if item["employee_id"] == employee_id]
        payload["adjustments"] = [item for item in payload["adjustments"] if item["employee_id"] == employee_id]
        payload["justifications"] = [item for item in payload["justifications"] if item["employee_id"] == employee_id]
        payload["holidays"] = []
        payload["compliance"] = []
        return payload

    def request_payload() -> dict:
        if request.files or request.form:
            return dict(request.form)
        return request.get_json(force=True)

    def save_justification_attachment(justification: PointJustification, attachment: FileStorage | None) -> None:
        if not attachment or not attachment.filename:
            return
        original_name = secure_filename(attachment.filename)
        extension = Path(original_name).suffix.lower()
        allowed_extensions = {".pdf", ".jpg", ".jpeg", ".png"}
        if extension not in allowed_extensions:
            raise ValueError("Envie o atestado em PDF, JPG ou PNG.")
        stored_name = f"justificativa-{justification.id:04d}-{uuid.uuid4().hex}{extension}"
        attachment.save(upload_dir / stored_name)
        justification.attachment_original_name = original_name
        justification.attachment_stored_name = stored_name
        justification.attachment_mime = clean_text(attachment.mimetype) or "application/octet-stream"

    @app.get("/")
    def index() -> str:
        with db_session() as db:
            config = get_or_create_config(db)
            db.commit()
            return render_template("index.html", app_name=config.app_name)

    @app.get("/health")
    def health() -> Response:
        return jsonify({"status": "ok", "application": app.config["APP_NAME"]})

    @app.get("/api/auth/me")
    def api_auth_me() -> Response:
        auth = current_auth()
        if auth is None:
            return jsonify({"authenticated": False, "current_user": None})
        return jsonify({"authenticated": True, "current_user": auth})

    @app.post("/api/auth/login")
    def api_auth_login() -> Response:
        payload = request.get_json(force=True)
        role = payload.get("role")
        password = clean_text(payload.get("password"))
        with db_session() as db:
            config = get_or_create_config(db)
            if role == "admin":
                username = str(payload.get("username", "")).strip().lower()
                if username != "admin":
                    return jsonify({"error": "Usuario admin invalido."}), 401
                if clean_digits(password) != clean_digits(config.employer_document):
                    return jsonify({"error": "Senha do administrador invalida."}), 401
                session.clear()
                session["role"] = "admin"
                db.commit()
                return jsonify({"authenticated": True, "current_user": current_auth()})

            if role == "employee":
                username = clean_digits(payload.get("username"))
                employee = db.scalar(select(Employee).where(Employee.cpf == username).where(Employee.active.is_(True)))
                if employee is None:
                    return jsonify({"error": "CPF do usuario nao encontrado."}), 401
                if not verify_employee_password(employee, password):
                    return jsonify({"error": "Senha do usuario invalida."}), 401
                session.clear()
                session["role"] = "employee"
                session["employee_id"] = employee.id
                session["employee_name"] = employee.full_name
                session["employee_cpf"] = employee.cpf
                db.commit()
                return jsonify({"authenticated": True, "current_user": current_auth()})

            return jsonify({"error": "Perfil de acesso invalido."}), 400

    @app.post("/api/auth/password-recovery/request")
    def api_password_recovery_request() -> Response:
        payload = request.get_json(force=True)
        cpf = clean_digits(payload.get("cpf") or payload.get("username"))
        if not cpf:
            return jsonify({"error": "Informe o CPF do usuario para recuperar a senha."}), 400
        with db_session() as db:
            employee = db.scalar(select(Employee).where(Employee.cpf == cpf).where(Employee.active.is_(True)))
            if employee is None:
                return jsonify({"error": "CPF do usuario nao encontrado."}), 404
            result = send_password_recovery_code(db, employee)
            db.commit()
            response_payload = {
                "status": result["status"],
                "message": result["message"],
                "recipient_hint": result.get("recipient_hint", ""),
            }
            if app.testing and result.get("reset_code"):
                response_payload["debug_reset_code"] = result["reset_code"]
            if result["status"] != "sent":
                return jsonify(response_payload), 503
            return jsonify(response_payload)

    @app.post("/api/auth/password-recovery/confirm")
    def api_password_recovery_confirm() -> Response:
        payload = request.get_json(force=True)
        cpf = clean_digits(payload.get("cpf"))
        code = clean_text(payload.get("code"))
        new_password = clean_text(payload.get("new_password"))
        if not cpf or not code or not new_password:
            return jsonify({"error": "Informe CPF, codigo de recuperacao e nova senha."}), 400
        with db_session() as db:
            employee = reset_employee_password_with_code(db, cpf, code, new_password)
            db.commit()
            return jsonify(
                {
                    "message": "Senha redefinida com sucesso. Use a nova senha no proximo acesso.",
                    "employee_id": employee.id,
                }
            )

    @app.post("/api/auth/logout")
    def api_auth_logout() -> Response:
        session.clear()
        return jsonify({"authenticated": False, "current_user": None})

    @app.get("/api/bootstrap")
    @require_auth()
    def api_bootstrap() -> Response:
        auth = current_auth()
        with db_session() as db:
            payload = filtered_bootstrap(db, auth)
            db.commit()
            return jsonify(payload)

    @app.post("/api/settings")
    @require_auth("admin")
    def api_settings() -> Response:
        payload = request.get_json(force=True)
        with db_session() as db:
            config = upsert_settings(db, payload, actor="rh-web")
            db.commit()
            return jsonify({"config": filtered_bootstrap(db, current_auth())["config"], "message": f"{config.app_name} atualizado."})

    @app.post("/api/employees")
    @require_auth("admin")
    def api_employees() -> Response:
        payload = request.get_json(force=True)
        with db_session() as db:
            employee = upsert_employee(db, payload, actor="rh-web")
            db.commit()
            return jsonify({"employee": filtered_bootstrap(db, current_auth())["employees"], "saved_id": employee.id})

    @app.post("/api/punches")
    @require_auth()
    def api_punches() -> Response:
        payload = request.get_json(force=True)
        auth = current_auth()
        if auth["role"] == "employee":
            payload["employee_id"] = auth["employee_id"]
            payload.pop("badge_id", None)
            payload["actor"] = auth["employee_name"]
        with db_session() as db:
            punch = register_punch(db, payload, actor=payload.get("actor", "worker"))
            auto_email: dict | None = None
            employee = db.get(Employee, punch.employee_id)
            recipient = clean_text(employee.email if employee else "")
            if recipient:
                auto_email = send_receipt_email(db, punch, recipient)
            db.commit()
            message = "Marcacao registrada com sucesso."
            if auto_email:
                if auto_email.get("status") == "sent":
                    message = f"{message} Comprovante enviado automaticamente para o e-mail cadastrado."
                else:
                    message = f"{message} Nao foi possivel enviar o comprovante automaticamente: {auto_email.get('message', '')}"
            return jsonify({"message": message, "punch": serialize_punch(db, punch), "auto_email": auto_email})

    @app.get("/api/punches/<punch_id>")
    @require_auth()
    def api_punch_detail(punch_id: str) -> Response:
        auth = current_auth()
        with db_session() as db:
            punch = db.get(PunchRecord, punch_id)
            if punch is None:
                return jsonify({"error": "Comprovante nao encontrado."}), 404
            if auth["role"] == "employee" and punch.employee_id != auth["employee_id"]:
                return jsonify({"error": "Acesso negado ao comprovante."}), 403
            db.commit()
            return jsonify(serialize_punch(db, punch))

    @app.get("/api/receipts/<punch_id>.pdf")
    @require_auth()
    def api_receipt_pdf(punch_id: str):
        auth = current_auth()
        with db_session() as db:
            punch = db.get(PunchRecord, punch_id)
            if punch is None:
                return jsonify({"error": "Comprovante nao encontrado."}), 404
            if auth["role"] == "employee" and punch.employee_id != auth["employee_id"]:
                return jsonify({"error": "Acesso negado ao comprovante."}), 403
            pdf_bytes = generate_receipt_pdf(db, punch)
            db.commit()
            return send_file(
                io.BytesIO(pdf_bytes),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=f"comprovante-{punch.nsr:09d}.pdf",
            )

    @app.post("/api/receipts/<punch_id>/email")
    @require_auth()
    def api_receipt_email(punch_id: str) -> Response:
        payload = request.get_json(force=True)
        auth = current_auth()
        with db_session() as db:
            punch = db.get(PunchRecord, punch_id)
            if punch is None:
                return jsonify({"error": "Comprovante nao encontrado."}), 404
            if auth["role"] == "employee" and punch.employee_id != auth["employee_id"]:
                return jsonify({"error": "Acesso negado ao comprovante."}), 403
            result = send_receipt_email(db, punch, payload["recipient"])
            db.commit()
            return jsonify(result)

    @app.get("/api/integrations/email/status")
    @require_auth("admin")
    def api_email_status() -> Response:
        with db_session() as db:
            return jsonify(email_integration_status(db))

    @app.post("/api/integrations/email/test")
    @require_auth("admin")
    def api_email_test() -> Response:
        payload = request.get_json(force=True)
        recipient = str(payload.get("recipient", "")).strip()
        if not recipient:
            return jsonify({"error": "Informe um destinatario para o teste."}), 400
        with db_session() as db:
            result = send_email_integration_test(db, recipient)
            db.commit()
            return jsonify(result)

    @app.post("/api/holidays")
    @require_auth("admin")
    def api_holidays() -> Response:
        payload = request.get_json(force=True)
        with db_session() as db:
            holiday = create_holiday(db, payload, actor="rh-web")
            db.commit()
            return jsonify({"holiday_id": holiday.id})

    @app.post("/api/leaves")
    @require_auth("admin")
    def api_leaves() -> Response:
        payload = request.get_json(force=True)
        with db_session() as db:
            leave = create_leave(db, payload, actor="rh-web")
            db.commit()
            return jsonify({"leave_id": leave.id})

    @app.post("/api/bank-adjustments")
    @require_auth("admin")
    def api_adjustments() -> Response:
        payload = request.get_json(force=True)
        with db_session() as db:
            adjustment = create_bank_adjustment(db, payload, actor="rh-web")
            db.commit()
            return jsonify({"adjustment_id": adjustment.id})

    @app.post("/api/justifications")
    @require_auth()
    def api_justifications() -> Response:
        payload = request_payload()
        auth = current_auth()
        if auth["role"] == "employee":
            payload["employee_id"] = auth["employee_id"]
        reference_date = date.fromisoformat(payload["reference_date"]) if payload.get("reference_date") else date.today()
        end_date = date.fromisoformat(payload["end_date"]) if payload.get("end_date") else reference_date
        if reference_date > end_date:
            return jsonify({"error": "A data inicial do atestado nao pode ser maior que a data final."}), 400
        with db_session() as db:
            justifications = []
            cursor = reference_date
            while cursor <= end_date:
                day_payload = dict(payload)
                day_payload["reference_date"] = cursor.isoformat()
                justification = create_point_justification(
                    db,
                    day_payload,
                    actor="rh-web" if auth["role"] == "admin" else auth["employee_name"],
                )
                justifications.append(justification)
                cursor += timedelta(days=1)

            attachment = request.files.get("attachment")
            if attachment and attachment.filename:
                save_justification_attachment(justifications[0], attachment)
                for justification in justifications[1:]:
                    justification.attachment_original_name = justifications[0].attachment_original_name
                    justification.attachment_stored_name = justifications[0].attachment_stored_name
                    justification.attachment_mime = justifications[0].attachment_mime
            db.commit()
            return jsonify(
                {
                    "justification_id": justifications[0].id,
                    "justification_ids": [justification.id for justification in justifications],
                }
            )

    @app.post("/api/medical-certificates")
    @require_auth()
    def api_medical_certificates() -> Response:
        payload = request_payload()
        auth = current_auth()
        if auth["role"] == "employee":
            payload["employee_id"] = auth["employee_id"]
        payload["occurrence_type"] = "atestado_medico"
        payload["reason"] = clean_text(payload.get("reason")) or "Atestado medico"
        payload["details"] = clean_text(payload.get("details")) or "Periodo coberto por atestado medico."
        payload["informed_time"] = ""
        reference_date = date.fromisoformat(payload["reference_date"]) if payload.get("reference_date") else date.today()
        end_date = date.fromisoformat(payload["end_date"]) if payload.get("end_date") else reference_date
        if reference_date > end_date:
            return jsonify({"error": "A data inicial do atestado nao pode ser maior que a data final."}), 400
        attachment = request.files.get("attachment")
        if not attachment or not attachment.filename:
            return jsonify({"error": "Anexe o arquivo do atestado medico."}), 400

        with db_session() as db:
            justifications = []
            cursor = reference_date
            while cursor <= end_date:
                day_payload = dict(payload)
                day_payload["reference_date"] = cursor.isoformat()
                justification = create_point_justification(
                    db,
                    day_payload,
                    actor="rh-web" if auth["role"] == "admin" else auth["employee_name"],
                )
                justifications.append(justification)
                cursor += timedelta(days=1)

            save_justification_attachment(justifications[0], attachment)
            for justification in justifications[1:]:
                justification.attachment_original_name = justifications[0].attachment_original_name
                justification.attachment_stored_name = justifications[0].attachment_stored_name
                justification.attachment_mime = justifications[0].attachment_mime
            db.commit()
            return jsonify(
                {
                    "certificate_id": justifications[0].id,
                    "certificate_ids": [justification.id for justification in justifications],
                }
            )

    @app.post("/api/reports/hours/missing-punches")
    @require_auth()
    def api_report_missing_punches() -> Response:
        payload = request.get_json(force=True)
        auth = current_auth()
        if auth["role"] == "employee":
            payload["employee_id"] = auth["employee_id"]
            actor = auth["employee_name"]
        else:
            actor = "rh-web"
        if not payload.get("employee_id"):
            return jsonify({"error": "Selecione um funcionario para salvar as batidas nao lancadas."}), 400
        with db_session() as db:
            justification = save_report_missing_punches(db, payload, actor=actor)
            report = employee_hours_report(
                db,
                int(payload["employee_id"]),
                date.fromisoformat(payload["period_start"]) if payload.get("period_start") else date.today().replace(day=1),
                date.fromisoformat(payload["period_end"]) if payload.get("period_end") else date.today(),
            )
            db.commit()
            return jsonify(
                {
                    "message": "Batidas nao lancadas atualizadas no espelho de ponto.",
                    "justification_id": justification.id if justification else None,
                    "report": report,
                }
            )

    @app.get("/api/justifications/<int:justification_id>.pdf")
    @require_auth()
    def api_justification_pdf(justification_id: int):
        auth = current_auth()
        with db_session() as db:
            justification = db.get(PointJustification, justification_id)
            if justification is None:
                return jsonify({"error": "Justificativa nao encontrada."}), 404
            if auth["role"] == "employee" and justification.employee_id != auth["employee_id"]:
                return jsonify({"error": "Acesso negado a justificativa."}), 403
            pdf_bytes = generate_justification_pdf(db, justification)
            db.commit()
            return send_file(
                io.BytesIO(pdf_bytes),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=f"justificativa-ponto-{justification.id:04d}.pdf",
            )

    @app.get("/api/justifications/<int:justification_id>/attachment")
    @require_auth()
    def api_justification_attachment(justification_id: int):
        auth = current_auth()
        with db_session() as db:
            justification = db.get(PointJustification, justification_id)
            if justification is None:
                return jsonify({"error": "Justificativa nao encontrada."}), 404
            if auth["role"] == "employee" and justification.employee_id != auth["employee_id"]:
                return jsonify({"error": "Acesso negado ao atestado."}), 403
            if not justification.attachment_stored_name:
                return jsonify({"error": "Esta justificativa nao possui atestado anexado."}), 404
            attachment_path = upload_dir / justification.attachment_stored_name
            if not attachment_path.exists():
                return jsonify({"error": "Arquivo do atestado nao encontrado no servidor."}), 404
            db.commit()
            return send_file(
                attachment_path,
                mimetype=justification.attachment_mime or None,
                as_attachment=False,
                download_name=justification.attachment_original_name or attachment_path.name,
            )

    @app.get("/api/reports/hours")
    @require_auth()
    def api_hours_report() -> Response:
        auth = current_auth()
        employee_id = request.args.get("employee_id", type=int)
        if auth["role"] == "employee":
            employee_id = int(auth["employee_id"])
        if not employee_id:
            return jsonify({"error": "Selecione um funcionario para consultar as batidas."}), 400
        start = date.fromisoformat(request.args["start"]) if request.args.get("start") else date.today().replace(day=1)
        end = date.fromisoformat(request.args["end"]) if request.args.get("end") else date.today()
        with db_session() as db:
            report = employee_hours_report(db, employee_id, start, end)
            db.commit()
            return jsonify(report)

    @app.get("/api/reports/hours.pdf")
    @require_auth()
    def api_hours_report_pdf():
        auth = current_auth()
        employee_id = request.args.get("employee_id", type=int)
        if auth["role"] == "employee":
            employee_id = int(auth["employee_id"])
        if not employee_id:
            return jsonify({"error": "Selecione um funcionario para gerar o documento."}), 400
        start = date.fromisoformat(request.args["start"]) if request.args.get("start") else date.today().replace(day=1)
        end = date.fromisoformat(request.args["end"]) if request.args.get("end") else date.today()
        with db_session() as db:
            report = employee_hours_report(db, employee_id, start, end)
            pdf_bytes = generate_employee_hours_report_pdf(db, employee_id, start, end)
            db.commit()
            return send_file(
                io.BytesIO(pdf_bytes),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=(
                    f"espelho-ponto-{clean_digits(report['employee']['cpf'])}-"
                    f"{report['period_start']}-{report['period_end']}.pdf"
                ),
            )

    @app.get("/api/afd.txt")
    @require_auth("admin")
    def api_afd() -> Response:
        start = date.fromisoformat(request.args["start"]) if request.args.get("start") else None
        end = date.fromisoformat(request.args["end"]) if request.args.get("end") else None
        with db_session() as db:
            filename, body = generate_afd(db, start, end)
            db.commit()
            return Response(
                body.encode("latin-1", errors="replace"),
                mimetype="text/plain; charset=iso-8859-1",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

    @app.get("/api/fiscalizacao.zip")
    @require_auth("admin")
    def api_fiscalizacao() -> Response:
        start = date.fromisoformat(request.args["start"]) if request.args.get("start") else None
        end = date.fromisoformat(request.args["end"]) if request.args.get("end") else None
        with db_session() as db:
            filename, body = generate_fiscal_export_zip(db, start, end)
            db.commit()
            return Response(
                body,
                mimetype="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

    @app.errorhandler(ValueError)
    def handle_value_error(error: ValueError):
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(RuntimeError)
    def handle_runtime_error(error: RuntimeError):
        return jsonify({"error": str(error)}), 503

    return app
