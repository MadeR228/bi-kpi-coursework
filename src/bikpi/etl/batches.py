"""Load batches and watermarks (schema meta)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text

from bikpi.config import dwh_engine


class Batch:
    def __init__(self, batch_id: int, step: str) -> None:
        self.id = batch_id
        self.step = step
        self.rows = 0


@contextmanager
def batch(step: str) -> Iterator[Batch]:
    """Registers a run of [step] in meta.batches with its outcome."""
    engine = dwh_engine()
    with engine.begin() as connection:
        batch_id = connection.execute(
            text("INSERT INTO meta.batches (step) VALUES (:step) RETURNING batch_id"), {"step": step}
        ).scalar_one()
    current = Batch(batch_id, step)
    try:
        yield current
    except Exception as error:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE meta.batches SET status = 'failed', finished_at = now(), message = :message "
                     "WHERE batch_id = :id"),
                {"id": batch_id, "message": str(error)[:2000]},
            )
        raise
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE meta.batches SET status = 'success', finished_at = now(), rows_loaded = :rows "
                 "WHERE batch_id = :id"),
            {"id": batch_id, "rows": current.rows},
        )


def watermark(source: str, name: str) -> str | None:
    with dwh_engine().connect() as connection:
        return connection.execute(
            text("SELECT value FROM meta.watermarks WHERE source = :source AND object = :name"),
            {"source": source, "name": name},
        ).scalar_one_or_none()


def set_watermark(source: str, name: str, value: str) -> None:
    with dwh_engine().begin() as connection:
        connection.execute(
            text("INSERT INTO meta.watermarks (source, object, value) VALUES (:source, :name, :value) "
                 "ON CONFLICT (source, object) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
            {"source": source, "name": name, "value": value},
        )
