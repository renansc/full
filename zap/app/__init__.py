from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, current_app, jsonify, request
from flask.cli import with_appcontext
from flask_login import LoginManager
from sqlalchemy import inspect, text
from werkzeug.exceptions import HTTPException

from .config import get_config
from .extensions import db
from .models import Department, Label, QuickReply, User, WorkflowState
from .routes import bp as main_bp


login_manager = LoginManager()
login_manager.login_view = "main.login"


def create_app():
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config())

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    app.register_blueprint(main_bp)
    app.cli.add_command(init_db_command)

    def _is_api_request():
        path = request.path or ""
        return path.startswith("/api/") or "/api/" in path

    @app.errorhandler(HTTPException)
    def handle_http_exception(error):
        if _is_api_request():
            return jsonify({"ok": False, "error": error.description or error.name}), error.code
        return error

    @app.errorhandler(Exception)
    def handle_unexpected_exception(error):
        if _is_api_request():
            current_app.logger.exception("Unexpected API error")
            return jsonify({"ok": False, "error": "Erro interno do servidor."}), 500
        raise error

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    with app.app_context():
        db.create_all()
        ensure_schema()
        seed_defaults()

    return app


def _set_default_row(rows, target_row):
    for row in rows:
        row.is_default = row.id == target_row.id


def seed_defaults():
    if not User.query.first():
        from werkzeug.security import generate_password_hash

        db.session.add(
            User(
                name=current_app.config.get("BOOTSTRAP_ADMIN_NAME", "Administrador"),
                email=current_app.config.get("BOOTSTRAP_ADMIN_EMAIL", "admin@empresa.com"),
                password_hash=generate_password_hash(current_app.config.get("BOOTSTRAP_ADMIN_PASSWORD", "admin123")),
                role="admin",
            )
        )

    departments = Department.query.order_by(Department.id.asc()).all()
    if not departments:
        db.session.add_all(
            [
                Department(name="Triagem", color="#38bdf8", is_default=True),
                Department(name="Comercial", color="#25d366"),
                Department(name="Suporte", color="#f59e0b"),
                Department(name="Financeiro", color="#a78bfa"),
                Department(name="Pos-venda", color="#10b981"),
            ]
        )
    else:
        default_department = Department.query.filter_by(is_default=True).first()
        if not default_department:
            fallback = Department.query.filter(db.func.lower(Department.name) == "triagem").first() or departments[0]
            _set_default_row(departments, fallback)

    states = WorkflowState.query.order_by(WorkflowState.order_index.asc(), WorkflowState.id.asc()).all()
    if not states:
        db.session.add_all(
            [
                WorkflowState(name="Aguardando", order_index=1, color="#0ea5e9", is_default=True),
                WorkflowState(name="Em andamento", order_index=2, color="#f59e0b"),
                WorkflowState(name="Concluido", order_index=3, color="#10b981", is_closed=True),
                WorkflowState(name="Cancelado", order_index=4, color="#ef4444", is_closed=True),
            ]
        )
    else:
        default_state = WorkflowState.query.filter_by(is_default=True).first()
        awaiting_state = WorkflowState.query.filter(db.func.lower(WorkflowState.name).in_(["aguardando", "novo"])).first()
        if awaiting_state and not default_state:
            _set_default_row(states, awaiting_state)

    if not Label.query.first():
        db.session.add_all(
            [
                Label(name="Prioridade alta", color="#ef4444"),
                Label(name="Financeiro", color="#f97316"),
                Label(name="Agendado", color="#14b8a6"),
            ]
        )

    if not QuickReply.query.first():
        db.session.add_all(
            [
                QuickReply(title="Boas-vindas", shortcut="/bomdia", body="Oi! Recebemos sua mensagem e ja vamos te atender."),
                QuickReply(title="Aguardando retorno", shortcut="/retorno", body="Ficamos no aguardo do seu retorno para seguir com o atendimento."),
            ]
        )

    db.session.commit()


def _add_column_if_missing(inspector, table_name, column_name, ddl):
    tables = set(inspector.get_table_names())
    if table_name not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in columns:
        return
    with db.engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))


def ensure_schema():
    inspector = inspect(db.engine)
    _add_column_if_missing(inspector, "ticket", "department_id", "department_id INTEGER")
    _add_column_if_missing(inspector, "ticket", "closed_at", "closed_at DATETIME")
    _add_column_if_missing(inspector, "user", "department_id", "department_id INTEGER")
    _add_column_if_missing(inspector, "conversation", "contact_name", "contact_name VARCHAR(140) NOT NULL DEFAULT ''")
    _add_column_if_missing(inspector, "conversation", "unread_incoming_count", "unread_incoming_count INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(inspector, "message", "external_id", "external_id VARCHAR(255)")


@click.command("init-db")
@with_appcontext
def init_db_command():
    db.create_all()
    ensure_schema()
    seed_defaults()
    click.echo("Banco inicializado com sucesso.")
