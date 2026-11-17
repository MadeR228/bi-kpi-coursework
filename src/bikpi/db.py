"""Database helpers: bulk loading with COPY and running SQL files."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def copy_frame(engine: Engine, frame: pd.DataFrame, table: str, *, truncate: bool = False) -> int:
    """Loads [frame] into an existing [table] (schema.name) with PostgreSQL COPY."""
    if truncate:
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {table}"))
    if frame.empty:
        return 0
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, header=False, na_rep="\\N")
    buffer.seek(0)
    columns = ", ".join(f'"{column}"' for column in frame.columns)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.copy_expert(
                f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, NULL '\\N')", buffer
            )
        raw.commit()
    finally:
        raw.close()
    return len(frame)


def run_sql_file(engine: Engine, name: str) -> None:
    """Runs one file of sql/ as a single transaction."""
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(sql)
        raw.commit()
    finally:
        raw.close()


def read_sql(engine: Engine, query: str, **params) -> pd.DataFrame:
    with engine.connect() as connection:
        return pd.read_sql(text(query), connection, params=params)


def execute(engine: Engine, statement: str, **params) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement), params)
