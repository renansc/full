from __future__ import annotations

import threading
from typing import Iterable

from flask import current_app
from sqlalchemy import create_engine, delete, func, inspect, select, text
from sqlalchemy.pool import NullPool

from .extensions import db


_ENGINE_CACHE: dict[str, object] = {}
_ENGINE_CACHE_LOCK = threading.Lock()
_SYNC_LOCK = threading.Lock()
_SYNC_PENDING = threading.Event()
_SYNC_HOOK_REGISTERED = False


def _app_object(app=None):
    if app is not None:
        return app
    return current_app._get_current_object()


def backup_database_url(app=None):
    app = _app_object(app)
    backup_url = (app.config.get("BACKUP_DATABASE_URL") or "").strip()
    primary_url = (app.config.get("SQLALCHEMY_DATABASE_URI") or "").strip()
    if not backup_url or backup_url == primary_url:
        return ""
    return backup_url


def _engine_for(url: str):
    with _ENGINE_CACHE_LOCK:
        engine = _ENGINE_CACHE.get(url)
        if engine is None:
            engine = create_engine(url, future=True, pool_pre_ping=True, poolclass=NullPool)
            _ENGINE_CACHE[url] = engine
        return engine


def _add_column_if_missing(engine, table_name, column_name, ddl):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if table_name not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in columns:
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))


def ensure_engine_schema(engine):
    db.metadata.create_all(bind=engine)
    _add_column_if_missing(engine, "ticket", "department_id", "department_id INTEGER")
    _add_column_if_missing(engine, "ticket", "closed_at", "closed_at DATETIME")
    _add_column_if_missing(engine, "user", "department_id", "department_id INTEGER")
    _add_column_if_missing(engine, "conversation", "contact_name", "contact_name VARCHAR(140) NOT NULL DEFAULT ''")
    _add_column_if_missing(engine, "conversation", "unread_incoming_count", "unread_incoming_count INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(engine, "message", "external_id", "external_id VARCHAR(255)")
    _add_column_if_missing(engine, "message", "sender_department", "sender_department VARCHAR(140) NOT NULL DEFAULT ''")


def _snapshot_tables(engine):
    ensure_engine_schema(engine)
    snapshot = []
    with engine.connect() as connection:
        for table in db.metadata.sorted_tables:
            rows = [dict(row._mapping) for row in connection.execute(select(table))]
            snapshot.append((table, rows))
    return snapshot


def _database_has_rows(engine):
    ensure_engine_schema(engine)
    with engine.connect() as connection:
        for table in db.metadata.sorted_tables:
            if connection.execute(select(table).limit(1)).first():
                return True
    return False


def _reset_postgres_sequences(connection, tables: Iterable):
    if connection.dialect.name != "postgresql":
        return
    for table in tables:
        primary_keys = list(table.primary_key.columns)
        if len(primary_keys) != 1:
            continue
        column = primary_keys[0]
        if column.autoincrement is False:
            continue
        sequence_name = connection.execute(
            text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
            {"table_name": table.name, "column_name": column.name},
        ).scalar()
        if not sequence_name:
            continue
        max_value = connection.execute(select(func.max(column))).scalar()
        if max_value is None:
            connection.execute(text("SELECT setval(:sequence_name, 1, false)"), {"sequence_name": sequence_name})
        else:
            connection.execute(
                text("SELECT setval(:sequence_name, :value, true)"),
                {"sequence_name": sequence_name, "value": int(max_value)},
            )


def mirror_database(source_engine, target_engine):
    ensure_engine_schema(source_engine)
    ensure_engine_schema(target_engine)
    snapshot = _snapshot_tables(source_engine)
    copied_rows = sum(len(rows) for _, rows in snapshot)
    with target_engine.begin() as connection:
        for table in reversed(db.metadata.sorted_tables):
            connection.execute(delete(table))
        for table, rows in snapshot:
            if rows:
                connection.execute(table.insert(), rows)
        _reset_postgres_sequences(connection, db.metadata.sorted_tables)
    return copied_rows


def restore_backup_to_local_if_empty(app=None):
    app = _app_object(app)
    backup_url = backup_database_url(app)
    if not backup_url:
        return 0
    local_has_rows = _database_has_rows(db.engine)
    if local_has_rows:
        return 0
    try:
        return mirror_database(_engine_for(backup_url), db.engine)
    except Exception:
        app.logger.exception("Nao foi possivel restaurar o banco local a partir do backup externo.")
        return 0


def bootstrap_database_pair(app=None):
    app = _app_object(app)
    backup_url = backup_database_url(app)
    if not backup_url:
        return {"action": "none", "copied_rows": 0}

    try:
        backup_engine = _engine_for(backup_url)
        local_has_rows = _database_has_rows(db.engine)
        backup_has_rows = _database_has_rows(backup_engine)

        if local_has_rows:
            try:
                copied_rows = mirror_database(db.engine, backup_engine)
                return {"action": "synced_local_to_backup", "copied_rows": copied_rows}
            except Exception:
                app.logger.exception("Nao foi possivel sincronizar o banco local para o backup externo.")
                return {"action": "error", "copied_rows": 0}

        if backup_has_rows:
            try:
                copied_rows = mirror_database(backup_engine, db.engine)
                return {"action": "restored_backup_to_local", "copied_rows": copied_rows}
            except Exception:
                app.logger.exception("Nao foi possivel restaurar o banco local a partir do backup externo.")
                return {"action": "error", "copied_rows": 0}
    except Exception:
        app.logger.exception("Nao foi possivel preparar o espelho do banco externo.")
        return {"action": "error", "copied_rows": 0}

    return {"action": "none", "copied_rows": 0}


def _sync_worker(app, reason):
    try:
        while True:
            _SYNC_PENDING.clear()
            with app.app_context():
                backup_url = backup_database_url(app)
                if not backup_url:
                    return
                copied_rows = mirror_database(db.engine, _engine_for(backup_url))
                app.logger.info(
                    "backup_sync_completed reason=%s copied_rows=%s backup_url=%s",
                    reason,
                    copied_rows,
                    backup_url,
                )
            if not _SYNC_PENDING.is_set():
                break
    except Exception:
        app.logger.exception("Falha ao sincronizar o backup externo.")
    finally:
        _SYNC_LOCK.release()


def schedule_backup_sync(app=None, reason="commit"):
    app = _app_object(app)
    backup_url = backup_database_url(app)
    if not backup_url:
        return False
    if not _SYNC_LOCK.acquire(blocking=False):
        _SYNC_PENDING.set()
        return False
    thread = threading.Thread(target=_sync_worker, args=(app, reason), daemon=True)
    thread.start()
    return True


def register_backup_sync_hook(app):
    global _SYNC_HOOK_REGISTERED
    if _SYNC_HOOK_REGISTERED:
        return

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    @event.listens_for(Session, "after_commit")
    def _after_commit(_session):
        try:
            active_app = current_app._get_current_object()
        except RuntimeError:
            active_app = app
        try:
            bind = _session.get_bind()
        except Exception:
            return
        if bind is None:
            return
        try:
            if bind != db.engine:
                return
        except Exception:
            return
        schedule_backup_sync(active_app, reason="commit")

    _SYNC_HOOK_REGISTERED = True
