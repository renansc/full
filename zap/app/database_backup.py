from sqlalchemy import create_engine, select

from .extensions import db


def _toggle_foreign_keys(connection, enabled: bool):
    dialect = connection.dialect.name
    if dialect == "sqlite":
        connection.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
    elif dialect in {"mysql", "mariadb"}:
        connection.exec_driver_sql(f"SET FOREIGN_KEY_CHECKS={'1' if enabled else '0'}")


def copy_database_contents(source_url: str, target_url: str):
    source_url = (source_url or "").strip()
    target_url = (target_url or "").strip()
    if not source_url or not target_url:
        return {"ok": False, "error": "Fonte ou destino do backup nao informados."}

    source_engine = create_engine(source_url, pool_pre_ping=True)
    target_engine = create_engine(target_url, pool_pre_ping=True)
    tables_copied = 0
    rows_copied = 0

    try:
        db.metadata.create_all(bind=source_engine)
        db.metadata.create_all(bind=target_engine)

        table_data = []
        with source_engine.connect() as source_conn:
            for table in db.metadata.sorted_tables:
                statement = select(table)
                primary_key_columns = list(table.primary_key.columns)
                if primary_key_columns:
                    statement = statement.order_by(*primary_key_columns)
                rows = [dict(row) for row in source_conn.execute(statement).mappings().all()]
                table_data.append((table, rows))
                tables_copied += 1
                rows_copied += len(rows)

        if rows_copied == 0:
            return {"ok": False, "error": "Banco de origem vazio.", "tables_copied": tables_copied, "rows_copied": rows_copied}

        with target_engine.begin() as target_conn:
            _toggle_foreign_keys(target_conn, False)
            try:
                for table, rows in table_data:
                    target_conn.execute(table.delete())
                    if rows:
                        target_conn.execute(table.insert(), rows)
            finally:
                _toggle_foreign_keys(target_conn, True)

        return {"ok": True, "tables_copied": tables_copied, "rows_copied": rows_copied}
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "tables_copied": tables_copied,
            "rows_copied": rows_copied,
        }
    finally:
        source_engine.dispose()
        target_engine.dispose()
