from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from .extensions import db
from .models import Conversation, Department, Label, Message, QuickReply, ReminderLog, Setting, Ticket, User, WorkflowState
from .services import (
    iso_now,
    make_google_auth_url,
    mark_whatsapp_message_read,
    preview_sheet_rows,
    normalize_whatsapp_phone_number,
    send_whatsapp_contact,
    send_whatsapp_interactive,
    send_whatsapp_location,
    send_whatsapp_media,
    send_whatsapp_template,
    send_whatsapp_text,
    sync_tickets_to_sheet,
)


bp = Blueprint("main", __name__)


def _login_payload():
    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "role": current_user.role,
        "department_id": current_user.department_id,
        "department_name": current_user.department.name if current_user.department else "",
    }


def _settings_map():
    return {row.key: row.value for row in Setting.query.all()}


def _public_base_url():
    base_url = current_app.config.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    return base_url


def _whatsapp_webhook_url():
    return url_for("main.whatsapp_webhook", _external=True)


def _normalized_phone(value):
    return normalize_whatsapp_phone_number(value)


def _setting_bool(settings_map, key, default=False):
    raw = str(settings_map.get(key, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "sim"}


def _parse_datetime_local(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_label_ids(payload):
    raw = payload.get("label_ids", [])
    if isinstance(raw, str):
        raw = [raw]
    return [int(label_id) for label_id in raw if str(label_id).strip().isdigit()]


def _default_state():
    return WorkflowState.query.filter_by(is_default=True).first() or WorkflowState.query.order_by(WorkflowState.order_index.asc(), WorkflowState.id.asc()).first()


def _default_department():
    return Department.query.filter_by(is_default=True, is_active=True).first() or Department.query.filter_by(is_active=True).order_by(Department.id.asc()).first() or Department.query.order_by(Department.id.asc()).first()


def _ticket_department_id(payload):
    raw = payload.get("department_id")
    if raw and str(raw).strip().isdigit():
        department = db.session.get(Department, int(raw))
        if department and department.is_active:
            return department.id
    if getattr(current_user, "department_id", None):
        return current_user.department_id
    department = _default_department()
    return department.id if department else None


def _visible_ticket(ticket, cutoff):
    if not ticket.status or not ticket.status.is_closed:
        return True
    closed_moment = ticket.closed_at or ticket.updated_at or ticket.created_at
    return closed_moment >= cutoff


def _integration_status(settings_map=None):
    settings_map = settings_map or _settings_map()
    database_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    backend = database_uri.split(":", 1)[0] if ":" in database_uri else database_uri
    whatsapp_ready = bool(current_app.config.get("WHATSAPP_TOKEN") and current_app.config.get("WHATSAPP_PHONE_NUMBER_ID"))
    google_ready = bool(settings_map.get("GOOGLE_SERVICE_ACCOUNT_JSON") and settings_map.get("GOOGLE_SHEETS_SPREADSHEET_ID"))
    reminders_ready = _setting_bool(settings_map, "REMINDER_SEND_WHATSAPP", True) and bool(settings_map.get("REMINDER_MINUTES"))
    departments = Department.query.filter_by(is_active=True).count()
    users = User.query.count()
    return [
        {
            "name": "Banco de dados",
            "status": "ok" if backend else "warn",
            "detail": backend or "Nao configurado",
        },
        {
            "name": "WhatsApp Cloud API",
            "status": "ok" if whatsapp_ready else "warn",
            "detail": "Credenciais definidas" if whatsapp_ready else "Faltam token ou phone number id",
        },
        {
            "name": "Webhook WhatsApp",
            "status": "ok" if current_app.config.get("WHATSAPP_VERIFY_TOKEN") else "warn",
            "detail": "Verify token definido" if current_app.config.get("WHATSAPP_VERIFY_TOKEN") else "Defina WHATSAPP_VERIFY_TOKEN",
        },
        {
            "name": "Google Sheets",
            "status": "ok" if google_ready else "warn",
            "detail": "Planilha conectada" if google_ready else "Faltam credenciais ou spreadsheet id",
        },
        {
            "name": "Lembretes",
            "status": "ok" if reminders_ready else "warn",
            "detail": f"{settings_map.get('REMINDER_MINUTES', '120')} minutos" if reminders_ready else "Configurar intervalo de lembrete",
        },
        {
            "name": "Multiusuario",
            "status": "ok" if users > 1 and departments > 0 else "warn",
            "detail": f"{users} usuarios e {departments} departamentos",
        },
    ]


def _ensure_default_admin():
    if User.query.first():
        return
    admin = User(
        name=current_app.config["BOOTSTRAP_ADMIN_NAME"],
        email=current_app.config["BOOTSTRAP_ADMIN_EMAIL"],
        password_hash=generate_password_hash(current_app.config["BOOTSTRAP_ADMIN_PASSWORD"]),
        role="admin",
    )
    db.session.add(admin)
    db.session.commit()


def _require_admin():
    if current_user.role != "admin":
        abort(403, "Permissao negada.")


def _sync_agenda_sheet_best_effort():
    settings_map = _settings_map()
    if not _setting_bool(settings_map, "GOOGLE_SHEETS_SYNC_ENABLED", True):
        return {"ok": False, "skipped": True, "reason": "sync_disabled"}
    spreadsheet_id = settings_map.get("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    if not spreadsheet_id:
        return {"ok": False, "skipped": True, "reason": "spreadsheet_missing"}
    try:
        return sync_tickets_to_sheet(
            settings_map.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
            spreadsheet_id,
            settings_map.get("GOOGLE_SHEETS_TAB_NAME", "Agenda"),
            Ticket.query.order_by(Ticket.due_at.asc().nullslast(), Ticket.created_at.desc()).all(),
        )
    except Exception as exc:
        current_app.logger.warning("Agenda sync failed: %s", exc)
        return {"ok": False, "error": str(exc)}


@bp.route("/")
@login_required
def index():
    states = WorkflowState.query.order_by(WorkflowState.order_index.asc(), WorkflowState.id.asc()).all()
    labels = Label.query.order_by(Label.name.asc()).all()
    quick_replies = QuickReply.query.order_by(QuickReply.title.asc()).all()
    departments = Department.query.filter_by(is_active=True).order_by(Department.name.asc()).all()
    return render_template(
        "dashboard.html",
        states=states,
        labels=labels,
        departments=departments,
        quick_replies=quick_replies,
        user_payload=_login_payload(),
    )


@bp.route("/login", methods=["GET", "POST"])
def login():
    _ensure_default_admin()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter(db.func.lower(User.email) == email).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Credenciais invalidas.", "error")
            return render_template("login.html")
        login_user(user)
        return redirect(url_for("main.index"))
    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.login"))


@bp.route("/settings")
@login_required
def settings():
    settings_map = _settings_map()
    states = WorkflowState.query.order_by(WorkflowState.order_index.asc(), WorkflowState.id.asc()).all()
    labels = Label.query.order_by(Label.name.asc()).all()
    quick_replies = QuickReply.query.order_by(QuickReply.title.asc()).all()
    settings_rows = Setting.query.order_by(Setting.key.asc()).all()
    departments = Department.query.order_by(Department.name.asc()).all()
    users = [
        {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "department_id": user.department_id,
            "department_name": user.department.name if user.department else "",
        }
        for user in User.query.order_by(User.name.asc()).all()
    ]
    return render_template(
        "settings.html",
        states=states,
        labels=labels,
        quick_replies=quick_replies,
        settings_rows=settings_rows,
        settings_map=settings_map,
        users=users,
        departments=departments,
        integration_status=_integration_status(settings_map),
        public_base_url=_public_base_url(),
        whatsapp_webhook_url=_whatsapp_webhook_url(),
        is_admin=current_user.role == "admin",
        user_payload=_login_payload(),
    )


@bp.route("/calendar")
@bp.route("/agenda")
@login_required
def calendar():
    settings_map = _settings_map()
    auth_url = (
        make_google_auth_url(
            current_app.config["GOOGLE_CLIENT_ID"],
            current_app.config["GOOGLE_REDIRECT_URI"],
            ["https://www.googleapis.com/auth/calendar"],
            state=str(current_user.id),
        )
        if current_app.config["GOOGLE_CLIENT_ID"] and current_app.config["GOOGLE_REDIRECT_URI"]
        else None
    )
    preview = {"ok": False, "rows": [], "error": None}
    if settings_map.get("GOOGLE_SHEETS_SPREADSHEET_ID"):
        preview = preview_sheet_rows(
            settings_map.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
            settings_map.get("GOOGLE_SHEETS_SPREADSHEET_ID", ""),
            settings_map.get("GOOGLE_SHEETS_TAB_NAME", "Agenda"),
        )
    return render_template(
        "calendar.html",
        auth_url=auth_url,
        settings_map=settings_map,
        preview=preview,
        user_payload=_login_payload(),
    )


@bp.route("/docs")
@login_required
def docs():
    return render_template("docs.html", user_payload=_login_payload())


@bp.route("/api/dashboard")
@login_required
def api_dashboard():
    states = WorkflowState.query.order_by(WorkflowState.order_index.asc(), WorkflowState.id.asc()).all()
    labels = Label.query.order_by(Label.name.asc()).all()
    departments = Department.query.filter_by(is_active=True).order_by(Department.name.asc()).all()
    tickets = Ticket.query.order_by(Ticket.created_at.desc()).all()
    archive_cutoff = datetime.utcnow() - timedelta(days=current_app.config.get("TICKET_ARCHIVE_DAYS", 2))
    tickets = [ticket for ticket in tickets if _visible_ticket(ticket, archive_cutoff)]
    return jsonify(
        {
            "states": [
                {"id": state.id, "name": state.name, "color": state.color, "is_closed": state.is_closed, "is_default": state.is_default}
                for state in states
            ],
            "labels": [{"id": label.id, "name": label.name, "color": label.color} for label in labels],
            "departments": [
                {"id": department.id, "name": department.name, "color": department.color, "is_default": department.is_default}
                for department in departments
            ],
            "tickets": [
                {
                    "id": ticket.id,
                    "title": ticket.title,
                    "client_name": ticket.client_name,
                    "client_phone": ticket.client_phone,
                    "company": ticket.company,
                    "service": ticket.service,
                    "description": ticket.description,
                    "due_at": ticket.due_at.isoformat() if ticket.due_at else "",
                    "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else "",
                    "status_id": ticket.status_id,
                    "status_name": ticket.status.name if ticket.status else "",
                    "status_color": ticket.status.color if ticket.status else "#4f46e5",
                    "department_id": ticket.department_id,
                    "department_name": ticket.department.name if ticket.department else "",
                    "labels": [{"id": label.id, "name": label.name, "color": label.color} for label in ticket.labels],
                    "assigned_to": ticket.assigned_to.name if ticket.assigned_to else "",
                    "created_at": ticket.created_at.isoformat(),
                }
                for ticket in tickets
            ],
            "user": _login_payload(),
            "archive_days": current_app.config.get("TICKET_ARCHIVE_DAYS", 2),
        }
    )


@bp.route("/api/integrations/status")
@login_required
def api_integration_status():
    return jsonify({"ok": True, "items": _integration_status()})


@bp.route("/api/tickets", methods=["POST"])
@login_required
def api_create_ticket():
    payload = request.get_json(force=True)
    state = _default_state()
    if not state:
        abort(400, "Nenhum estado disponivel.")
    client_name = (payload.get("client_name") or "").strip()
    client_phone = _normalized_phone(payload.get("client_phone") or "")
    if not client_name or not client_phone:
        abort(400, "Cliente e telefone sao obrigatorios.")
    ticket = Ticket(
        title=(payload.get("title") or "Novo atendimento").strip(),
        client_name=client_name,
        client_phone=client_phone,
        company=(payload.get("company") or "").strip(),
        service=(payload.get("service") or "").strip(),
        description=(payload.get("description") or "").strip(),
        status_id=state.id,
        assigned_to_id=current_user.id,
        department_id=_ticket_department_id(payload),
        due_at=_parse_datetime_local(payload.get("due_at")),
        closed_at=None,
    )
    db.session.add(ticket)
    db.session.flush()
    conversation = Conversation(
        ticket_id=ticket.id,
        wa_chat_id=_normalized_phone(payload.get("wa_chat_id") or client_phone),
        contact_name=client_name,
        last_message_at=datetime.utcnow(),
    )
    db.session.add(conversation)
    label_ids = _parse_label_ids(payload)
    if label_ids:
        ticket.labels = [db.session.get(Label, label_id) for label_id in label_ids if db.session.get(Label, label_id)]
    db.session.commit()
    _sync_agenda_sheet_best_effort()
    return jsonify({"ok": True, "ticket_id": ticket.id})


@bp.route("/api/tickets/<int:ticket_id>", methods=["PATCH"])
@login_required
def api_update_ticket(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    payload = request.get_json(force=True)
    for field in ["title", "client_name", "client_phone", "company", "service", "description"]:
        if field in payload:
            value = (payload.get(field) or "").strip()
            if field == "client_phone":
                value = _normalized_phone(value)
            setattr(ticket, field, value)
    if "due_at" in payload:
        ticket.due_at = _parse_datetime_local(payload.get("due_at"))
    if "status_id" in payload:
        state = db.session.get(WorkflowState, payload["status_id"])
        if state:
            ticket.status_id = state.id
            ticket.closed_at = datetime.utcnow() if state.is_closed else None
    if "assigned_to_id" in payload:
        ticket.assigned_to_id = payload["assigned_to_id"] or None
    if "department_id" in payload:
        ticket.department_id = _ticket_department_id(payload) if payload.get("department_id") else None
    if "label_ids" in payload:
        label_ids = _parse_label_ids(payload)
        ticket.labels = [db.session.get(Label, label_id) for label_id in label_ids if db.session.get(Label, label_id)]
    db.session.commit()
    _sync_agenda_sheet_best_effort()
    return jsonify({"ok": True})


@bp.route("/api/tickets/<int:ticket_id>", methods=["DELETE"])
@login_required
def api_delete_ticket(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    conversation = ticket.conversation
    if conversation:
        Message.query.filter_by(conversation_id=conversation.id).delete(synchronize_session=False)
        db.session.delete(conversation)
    ReminderLog.query.filter_by(ticket_id=ticket.id).delete(synchronize_session=False)
    ticket.labels = []
    db.session.delete(ticket)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/tickets/<int:ticket_id>", methods=["GET"])
@login_required
def api_get_ticket(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    conversation = ticket.conversation
    return jsonify(
        {
            "ok": True,
            "ticket": {
                "id": ticket.id,
                "title": ticket.title,
                "client_name": ticket.client_name,
                "client_phone": ticket.client_phone,
                "company": ticket.company,
                "service": ticket.service,
                "description": ticket.description,
                "due_at": ticket.due_at.isoformat() if ticket.due_at else "",
                "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else "",
                "status_id": ticket.status_id,
                "assigned_to_id": ticket.assigned_to_id,
                "department_id": ticket.department_id,
                "labels": [{"id": label.id, "name": label.name, "color": label.color} for label in ticket.labels],
            },
            "department": {
                "id": ticket.department.id if ticket.department else None,
                "name": ticket.department.name if ticket.department else "",
                "color": ticket.department.color if ticket.department else "#38bdf8",
            },
            "conversation": {
                "id": conversation.id if conversation else None,
                "contact_name": conversation.contact_name if conversation else "",
                "last_message_at": conversation.last_message_at.isoformat() if conversation and conversation.last_message_at else None,
                "messages": [
                    {
                        "id": message.id,
                        "direction": message.direction,
                        "sender_name": message.sender_name,
                        "content": message.content,
                        "media_url": message.media_url,
                        "created_at": message.created_at.isoformat(),
                    }
                    for message in (conversation.messages.order_by(Message.created_at.asc()).all() if conversation else [])
                ],
            },
            "departments": [
                {"id": department.id, "name": department.name, "color": department.color, "is_default": department.is_default}
                for department in Department.query.filter_by(is_active=True).order_by(Department.name.asc()).all()
            ],
        }
    )


@bp.route("/api/tickets/<int:ticket_id>/labels", methods=["POST"])
@login_required
def api_ticket_labels(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    payload = request.get_json(force=True)
    label_ids = _parse_label_ids(payload)
    ticket.labels = [db.session.get(Label, label_id) for label_id in label_ids if db.session.get(Label, label_id)]
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/messages", methods=["POST"])
@login_required
def api_add_message():
    payload = request.get_json(force=True)
    conversation = db.session.get(Conversation, payload.get("conversation_id")) or abort(404)
    message = Message(
        conversation_id=conversation.id,
        direction=payload.get("direction", "outgoing"),
        sender_name=payload.get("sender_name", current_user.name),
        content=payload.get("content", "").strip(),
        media_url=payload.get("media_url", "").strip(),
    )
    if not message.content and not message.media_url:
        abort(400, "Mensagem vazia.")
    db.session.add(message)
    conversation.last_message_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "message_id": message.id})


@bp.route("/api/tickets/<int:ticket_id>/messages", methods=["POST"])
@login_required
def api_ticket_message(ticket_id):
    ticket = db.session.get(Ticket, ticket_id) or abort(404)
    conversation = ticket.conversation
    if not conversation:
        conversation = Conversation(ticket_id=ticket.id, wa_chat_id=ticket.client_phone or "", contact_name=ticket.client_name, last_message_at=datetime.utcnow())
        db.session.add(conversation)
        db.session.flush()
    payload = request.get_json(force=True)
    text = (payload.get("content") or "").strip()
    media_url = (payload.get("media_url") or "").strip()
    if not text and not media_url:
        abort(400, "Mensagem vazia.")

    message = Message(
        conversation_id=conversation.id,
        direction="outgoing",
        sender_name=current_user.name,
        content=text or media_url,
        media_url=media_url,
    )
    db.session.add(message)
    conversation.last_message_at = datetime.utcnow()

    send_result = {"ok": True, "skipped": True}
    if text and ticket.client_phone:
        send_result = send_whatsapp_text(
            current_app.config["WHATSAPP_TOKEN"],
            current_app.config["WHATSAPP_API_VERSION"],
            current_app.config["WHATSAPP_PHONE_NUMBER_ID"],
            to=_normalized_phone(ticket.client_phone),
            body=text,
        )
    elif text and not ticket.client_phone:
        send_result = {"ok": False, "error": "Telefone do cliente nao cadastrado.", "data": {}}
    if send_result.get("ok"):
        messages = send_result.get("data", {}).get("messages", [])
        if isinstance(messages, list) and messages:
            message.external_id = messages[0].get("id")
    db.session.commit()
    if not send_result.get("ok"):
        return jsonify({"ok": False, "error": send_result.get("error", "Falha no envio do WhatsApp."), "message_id": message.id, "whatsapp": send_result}), 502
    return jsonify({"ok": True, "message_id": message.id, "whatsapp": send_result})


@bp.route("/api/uploads", methods=["POST"])
@login_required
def api_upload():
    file = request.files.get("file") or abort(400, "Arquivo nao enviado.")
    filename = secure_filename(file.filename or "arquivo")
    if not filename:
        abort(400, "Nome de arquivo invalido.")
    upload_dir = Path(current_app.instance_path) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    file.save(str(upload_dir / filename))
    return jsonify({"ok": True, "url": url_for("main.uploaded_file", filename=filename)})


@bp.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    upload_dir = Path(current_app.instance_path) / "uploads"
    return send_from_directory(upload_dir, filename)


@bp.route("/api/messages/poll")
@login_required
def api_poll_messages():
    since = request.args.get("since")
    query = Message.query.order_by(Message.created_at.desc()).limit(20)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            query = Message.query.filter(Message.created_at > since_dt).order_by(Message.created_at.asc())
        except ValueError:
            pass
    messages = query.all()
    return jsonify(
        {
            "ok": True,
            "messages": [
                {
                    "id": message.id,
                    "conversation_id": message.conversation_id,
                    "direction": message.direction,
                    "sender_name": message.sender_name,
                    "content": message.content,
                    "media_url": message.media_url,
                    "created_at": message.created_at.isoformat() + "Z",
                }
                for message in messages
            ],
            "server_time": iso_now(),
        }
    )


@bp.route("/api/settings", methods=["POST"])
@login_required
def api_save_setting():
    payload = request.get_json(force=True)
    key = (payload.get("key") or "").strip()
    value = (payload.get("value") or "").strip()
    if not key:
        abort(400, "Chave obrigatoria.")
    row = Setting.query.filter_by(key=key).first() or Setting(key=key, value=value)
    row.value = value
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/settings/bulk", methods=["POST"])
@login_required
def api_save_settings_bulk():
    payload = request.get_json(force=True)
    items = payload.get("settings", {})
    if not isinstance(items, dict):
        abort(400, "Formato invalido.")
    for key, value in items.items():
        row = Setting.query.filter_by(key=key).first() or Setting(key=key, value="")
        if isinstance(value, bool):
            row.value = "true" if value else "false"
        else:
            row.value = "" if value is None else str(value)
        db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "saved": len(items)})


@bp.route("/api/users", methods=["POST"])
@login_required
def api_create_user():
    _require_admin()
    payload = request.get_json(force=True)
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    password = (payload.get("password") or "").strip()
    role = (payload.get("role") or "operator").strip() or "operator"
    department_id = _ticket_department_id(payload) if payload.get("department_id") else None
    if not name or not email or not password:
        abort(400, "Nome, e-mail e senha sao obrigatorios.")
    if User.query.filter(db.func.lower(User.email) == email).first():
        abort(400, "Ja existe um usuario com esse e-mail.")
    user = User(
        name=name,
        email=email,
        password_hash=generate_password_hash(password),
        role=role,
        department_id=department_id,
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({"ok": True, "id": user.id})


@bp.route("/api/users/<int:user_id>", methods=["PATCH"])
@login_required
def api_update_user(user_id):
    _require_admin()
    payload = request.get_json(force=True)
    user = db.session.get(User, user_id) or abort(404)
    name = (payload.get("name") or user.name).strip()
    email = (payload.get("email") or user.email).strip().lower()
    role = (payload.get("role") or user.role).strip() or user.role
    password = (payload.get("password") or "").strip()
    if not name or not email:
        abort(400, "Nome e e-mail sao obrigatorios.")
    existing = User.query.filter(db.func.lower(User.email) == email, User.id != user.id).first()
    if existing:
        abort(400, "Ja existe outro usuario com esse e-mail.")
    user.name = name
    user.email = email
    user.role = role
    if "department_id" in payload:
        user.department_id = _ticket_department_id(payload) if payload.get("department_id") else None
    if password:
        user.password_hash = generate_password_hash(password)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/config/states", methods=["POST"])
@login_required
def api_save_state():
    payload = request.get_json(force=True)
    state = WorkflowState(
        name=(payload.get("name") or "").strip(),
        order_index=int(payload.get("order_index") or 0),
        color=(payload.get("color") or "#4f46e5").strip(),
        is_closed=bool(payload.get("is_closed")),
        is_default=bool(payload.get("is_default")),
    )
    if not state.name:
        abort(400, "Nome obrigatorio.")
    if state.is_default:
        for row in WorkflowState.query.all():
            row.is_default = False
    db.session.add(state)
    db.session.commit()
    return jsonify({"ok": True, "id": state.id})


@bp.route("/api/config/departments", methods=["POST"])
@login_required
def api_save_department():
    payload = request.get_json(force=True)
    department = Department(
        name=(payload.get("name") or "").strip(),
        color=(payload.get("color") or "#38bdf8").strip(),
        is_default=bool(payload.get("is_default")),
    )
    if not department.name:
        abort(400, "Nome obrigatorio.")
    if department.is_default:
        for row in Department.query.all():
            row.is_default = False
    db.session.add(department)
    db.session.commit()
    return jsonify({"ok": True, "id": department.id})


@bp.route("/api/config/labels", methods=["POST"])
@login_required
def api_save_label():
    payload = request.get_json(force=True)
    label = Label(name=(payload.get("name") or "").strip(), color=(payload.get("color") or "#64748b").strip())
    if not label.name:
        abort(400, "Nome obrigatorio.")
    db.session.add(label)
    db.session.commit()
    return jsonify({"ok": True, "id": label.id})


@bp.route("/api/config/quick-replies", methods=["POST"])
@login_required
def api_save_quick_reply():
    payload = request.get_json(force=True)
    reply = QuickReply(
        title=(payload.get("title") or "").strip(),
        shortcut=(payload.get("shortcut") or "").strip(),
        body=(payload.get("body") or "").strip(),
    )
    if not reply.title or not reply.shortcut:
        abort(400, "Titulo e atalho sao obrigatorios.")
    db.session.add(reply)
    db.session.commit()
    return jsonify({"ok": True, "id": reply.id})


@bp.route("/api/agenda/sync", methods=["POST"])
@login_required
def api_agenda_sync():
    settings_map = _settings_map()
    if not _setting_bool(settings_map, "GOOGLE_SHEETS_SYNC_ENABLED", True):
        abort(400, "Sincronizacao da agenda desativada.")
    result = sync_tickets_to_sheet(
        settings_map.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
        settings_map.get("GOOGLE_SHEETS_SPREADSHEET_ID", ""),
        settings_map.get("GOOGLE_SHEETS_TAB_NAME", "Agenda"),
        Ticket.query.order_by(Ticket.due_at.asc().nullslast(), Ticket.created_at.desc()).all(),
    )
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@bp.route("/api/agenda/preview")
@login_required
def api_agenda_preview():
    settings_map = _settings_map()
    result = preview_sheet_rows(
        settings_map.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
        settings_map.get("GOOGLE_SHEETS_SPREADSHEET_ID", ""),
        settings_map.get("GOOGLE_SHEETS_TAB_NAME", "Agenda"),
    )
    return jsonify(result), 200 if result.get("ok") else 400


@bp.route("/api/reminders/run", methods=["POST"])
@login_required
def api_run_reminders():
    settings_map = _settings_map()
    reminder_minutes = int(settings_map.get("REMINDER_MINUTES", "120") or 120)
    send_via_whatsapp = _setting_bool(settings_map, "REMINDER_SEND_WHATSAPP", True)
    now = datetime.utcnow()
    reminders_sent = 0
    tickets = Ticket.query.filter(Ticket.due_at.isnot(None)).all()
    for ticket in tickets:
        if ticket.status and ticket.status.is_closed:
            continue
        target = ticket.due_at - timedelta(minutes=reminder_minutes)
        if abs((target - now).total_seconds()) > 60:
            continue
        already_sent = ReminderLog.query.filter_by(ticket_id=ticket.id, reminder_type="appointment").first()
        if already_sent:
            continue
        message = f"Oi, {ticket.client_name}! Lembrando do seu atendimento em {ticket.due_at.strftime('%d/%m/%Y %H:%M')}."
        reminder_status = "sent"
        if send_via_whatsapp and ticket.client_phone:
            reminder_result = send_whatsapp_text(
                settings_map.get("WHATSAPP_TOKEN", current_app.config["WHATSAPP_TOKEN"]),
                settings_map.get("WHATSAPP_API_VERSION", current_app.config["WHATSAPP_API_VERSION"]),
                settings_map.get("WHATSAPP_PHONE_NUMBER_ID", current_app.config["WHATSAPP_PHONE_NUMBER_ID"]),
                to=_normalized_phone(ticket.client_phone),
                body=message,
            )
            if not reminder_result.get("ok"):
                reminder_status = "failed"
                current_app.logger.warning("Reminder WhatsApp send failed for ticket %s: %s", ticket.id, reminder_result.get("error", "unknown"))
        db.session.add(
            ReminderLog(
                ticket_id=ticket.id,
                reminder_type="appointment",
                scheduled_for=target,
                sent_at=now,
                channel="whatsapp" if send_via_whatsapp else "internal",
                status=reminder_status,
            )
        )
        if reminder_status == "sent":
            reminders_sent += 1
    db.session.commit()
    return jsonify({"ok": True, "reminders_sent": reminders_sent, "reminder_minutes": reminder_minutes})


@bp.route("/api/whatsapp/send", methods=["POST"])
@login_required
def api_whatsapp_send():
    payload = request.get_json(force=True)
    to = _normalized_phone(payload.get("to") or "")
    message_type = (payload.get("type") or "text").strip().lower()
    token = current_app.config["WHATSAPP_TOKEN"]
    version = current_app.config["WHATSAPP_API_VERSION"]
    phone_number_id = current_app.config["WHATSAPP_PHONE_NUMBER_ID"]

    if message_type != "read" and not to:
        abort(400, "Telefone de destino obrigatorio.")

    if message_type == "media":
        result = send_whatsapp_media(
            token,
            version,
            phone_number_id,
            to=to,
            media_type=(payload.get("media_type") or "image").strip(),
            link=(payload.get("link") or "").strip(),
            caption=(payload.get("caption") or "").strip(),
        )
    elif message_type == "template":
        result = send_whatsapp_template(
            token,
            version,
            phone_number_id,
            to=to,
            template_name=(payload.get("template_name") or "").strip(),
            language_code=(payload.get("language_code") or "pt_BR").strip(),
            components=payload.get("components") or None,
        )
    elif message_type == "interactive":
        result = send_whatsapp_interactive(
            token,
            version,
            phone_number_id,
            to=to,
            interactive=payload.get("interactive") or {},
        )
    elif message_type == "location":
        result = send_whatsapp_location(
            token,
            version,
            phone_number_id,
            to=to,
            latitude=float(payload.get("latitude")),
            longitude=float(payload.get("longitude")),
            name=(payload.get("name") or "").strip(),
            address=(payload.get("address") or "").strip(),
        )
    elif message_type == "contact":
        result = send_whatsapp_contact(
            token,
            version,
            phone_number_id,
            to=to,
            contact=payload.get("contact") or {},
        )
    elif message_type == "read":
        result = mark_whatsapp_message_read(
            token,
            version,
            phone_number_id,
            message_id=(payload.get("message_id") or "").strip(),
        )
    else:
        result = send_whatsapp_text(
            token,
            version,
            phone_number_id,
            to=to,
            body=(payload.get("body") or "").strip(),
        )
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "Falha no envio do WhatsApp."), "whatsapp": result}), 502
    return jsonify(result)


@bp.route("/integrations/google/callback")
@login_required
def google_callback():
    return render_template(
        "integration_callback.html",
        title="Google Calendar",
        message="Callback recebido. Troque o code por tokens no backend.",
        user_payload=_login_payload(),
    )


@bp.route("/webhooks/whatsapp", methods=["GET", "POST"])
def whatsapp_webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == current_app.config["WHATSAPP_VERIFY_TOKEN"]:
            return request.args.get("hub.challenge", ""), 200
        return "Unauthorized", 403

    payload = request.get_json(force=True, silent=True) or {}
    default_state = _default_state()
    default_department = _default_department()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message_data in value.get("messages", []):
                wa_chat_id = message_data.get("from", "")
                wa_chat_id = _normalized_phone(wa_chat_id)
                text = message_data.get("text", {}).get("body", "")
                contact_name = "WhatsApp"
                if value.get("contacts"):
                    contact_name = value["contacts"][0].get("profile", {}).get("name", "WhatsApp")
                conversation = Conversation.query.filter_by(wa_chat_id=wa_chat_id).first()
                if not conversation:
                    ticket = Ticket.query.filter_by(client_phone=wa_chat_id).first()
                    if not ticket:
                        ticket = Ticket(
                            title="Contato WhatsApp",
                            client_name=contact_name,
                            client_phone=wa_chat_id,
                            company="",
                            service="",
                            description="Recebido por webhook do WhatsApp",
                            status_id=default_state.id if default_state else WorkflowState.query.first().id,
                            assigned_to_id=None,
                            department_id=default_department.id if default_department else None,
                            closed_at=None,
                        )
                        db.session.add(ticket)
                        db.session.flush()
                    conversation = Conversation(ticket_id=ticket.id, wa_chat_id=wa_chat_id, contact_name=contact_name, last_message_at=datetime.utcnow())
                    db.session.add(conversation)
                    db.session.flush()
                if conversation.ticket and conversation.ticket.client_name in {"", "WhatsApp"}:
                    conversation.ticket.client_name = contact_name
                message = Message(
                    conversation_id=conversation.id,
                    direction="incoming",
                    sender_name=contact_name,
                    content=text or "[midia]",
                    media_url="",
                )
                db.session.add(message)
                conversation.last_message_at = datetime.utcnow()
                conversation.contact_name = contact_name
            for status in value.get("statuses", []):
                message_id = status.get("id")
                if not message_id:
                    continue
                message = Message.query.filter_by(external_id=message_id).first()
                if not message:
                    continue
                status_name = status.get("status", "")
                timestamp = datetime.utcnow()
                if status_name == "delivered":
                    message.delivered_at = timestamp
                elif status_name == "read":
                    message.read_at = timestamp
    db.session.commit()
    return jsonify({"ok": True})
