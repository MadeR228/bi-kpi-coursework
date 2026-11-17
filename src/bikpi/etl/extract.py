"""Extraction (E of ELT) from the three sources into the bronze layer.

* Operational DB: incremental by updated_at watermarks for the event tables,
  full snapshots for the small reference tables.
* Accounting CSV: every new or changed file, values kept as text.
* Transport API: pages of JSON since the last service date (with a few days of
  overlap for late corrections), retried on 5xx.
"""
from __future__ import annotations

import csv
import hashlib
import json

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from sqlalchemy import text
from urllib3.util.retry import Retry

from bikpi.config import dwh_engine, settings, source_engine
from bikpi.db import copy_frame
from bikpi.etl.batches import batch, set_watermark, watermark

SNAPSHOT_TABLES = ("workplaces", "shift_types", "employees", "employee_rate_history")
INCREMENTAL_TABLES = ("planned_shifts", "work_logs", "absences", "overtime", "requests", "salary_adjustments")
CHUNK_ROWS = 200_000
API_OVERLAP_DAYS = 3


def extract_ops() -> int:
    source = source_engine()
    dwh = dwh_engine()
    with batch("extract_ops") as current:
        for table in SNAPSHOT_TABLES:
            frame = pd.read_sql(text(f"SELECT * FROM {table}"), source)
            current.rows += copy_frame(dwh, frame.assign(_batch_id=current.id), f"bronze.ops_{table}")
        for table in INCREMENTAL_TABLES:
            since = watermark("ops", table) or "1900-01-01T00:00:00+00:00"
            newest = None
            query = text(f"SELECT * FROM {table} WHERE updated_at > :since ORDER BY updated_at")
            with source.connect().execution_options(stream_results=True) as connection:
                for chunk in pd.read_sql(query, connection, params={"since": since}, chunksize=CHUNK_ROWS):
                    if chunk.empty:  # pandas yields one empty chunk when nothing changed
                        continue
                    current.rows += copy_frame(dwh, chunk.assign(_batch_id=current.id), f"bronze.ops_{table}")
                    newest = chunk["updated_at"].max()
            if newest is not None:
                set_watermark("ops", table, pd.Timestamp(newest).isoformat())
        return current.rows


def extract_csv() -> int:
    folder = settings().raw_dir / "accounting"
    dwh = dwh_engine()
    with batch("extract_csv") as current:
        for path in sorted(folder.glob("invoices_*.csv")):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if watermark("csv", path.name) == digest:
                continue
            with path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file, delimiter=";"))
            frame = pd.DataFrame(rows, dtype=str)
            frame["_source_file"] = path.name
            frame["_line_no"] = range(2, len(frame) + 2)
            # A changed file replaces what was loaded from it before.
            with dwh.begin() as connection:
                connection.execute(text("DELETE FROM bronze.acc_invoices WHERE _source_file = :name"),
                                   {"name": path.name})
            current.rows += copy_frame(dwh, frame.assign(_batch_id=current.id), "bronze.acc_invoices")
            set_watermark("csv", path.name, digest)
        return current.rows


def api_session() -> requests.Session:
    retry = Retry(total=6, backoff_factor=0.5, status_forcelist=(500, 502, 503, 504), allowed_methods=("GET",))
    session = requests.Session()
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def extract_api() -> int:
    dwh = dwh_engine()
    last = watermark("api", "trips")
    since = (pd.Timestamp(last) - pd.Timedelta(days=API_OVERLAP_DAYS)).date().isoformat() if last else ""
    session = api_session()
    with batch("extract_api") as current:
        page, newest = 1, last
        while True:
            response = session.get(
                f"{settings().source_api_url}/trips",
                params={"updated_since": since, "page": page, "page_size": 5000},
                timeout=60,
            )
            response.raise_for_status()
            body = response.json()
            items = body["items"]
            if items:
                frame = pd.DataFrame({"payload": [json.dumps(item, ensure_ascii=False) for item in items]})
                current.rows += copy_frame(dwh, frame.assign(_batch_id=current.id), "bronze.api_trips")
                latest = max(item["service_date"] for item in items)
                newest = max(newest or latest, latest)
            if not body["has_more"]:
                break
            page += 1
        if newest:
            set_watermark("api", "trips", newest)
        return current.rows
