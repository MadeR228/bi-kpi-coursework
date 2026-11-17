"""CSV copies of the marts (data/export) for opening the report without the database."""
from __future__ import annotations

from bikpi.config import dwh_engine, settings
from bikpi.db import read_sql

TABLES = {
    "kpi_workplace_day": "SELECT * FROM mart.kpi_workplace_day ORDER BY date, workplace_code",
    "kpi_workplace_month": "SELECT * FROM mart.kpi_workplace_month ORDER BY month, workplace_code",
    "forecast": "SELECT * FROM ml.forecast ORDER BY workplace_code, date",
    "anomalies": "SELECT * FROM ml.anomalies ORDER BY date, workplace_code",
    "anomaly_evaluation": "SELECT * FROM ml.anomaly_evaluation",
    "backtest": "SELECT * FROM ml.backtest ORDER BY model, fold",
    "dim_date": "SELECT * FROM gold.dim_date",
    "dim_workplace": "SELECT * FROM gold.dim_workplace",
    "dq_results": "SELECT * FROM dq.results WHERE run_at > now() - interval '1 day' ORDER BY run_at",
}


def export() -> int:
    folder = settings().export_dir
    folder.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, query in TABLES.items():
        frame = read_sql(dwh_engine(), query)
        frame.to_csv(folder / f"{name}.csv", index=False, encoding="utf-8")
        total += len(frame)
    return total
