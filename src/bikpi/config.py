"""Settings of the pipeline, read from the environment (.env in docker compose)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class Settings:
    source_db_url: str
    dwh_url: str
    source_api_url: str
    mlflow_tracking_uri: str
    data_dir: Path
    sim_end_date: date
    sim_seed: int

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def ground_truth_dir(self) -> Path:
        return self.data_dir / "ground_truth"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "export"


@lru_cache
def settings() -> Settings:
    root = Path(__file__).resolve().parents[2]
    return Settings(
        source_db_url=os.environ.get("SOURCE_DB_URL", "postgresql+psycopg2://bc:bc_pass@localhost:55440/bc_ops"),
        dwh_url=os.environ.get("DWH_URL", "postgresql+psycopg2://dwh:dwh_pass@localhost:55441/dwh"),
        source_api_url=os.environ.get("SOURCE_API_URL", "http://localhost:8095"),
        mlflow_tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5050"),
        data_dir=Path(os.environ.get("DATA_DIR", root / "data")),
        sim_end_date=date.fromisoformat(os.environ.get("SIM_END_DATE", "2026-10-25")),
        sim_seed=int(os.environ.get("SIM_SEED", "42")),
    )


@lru_cache
def source_engine() -> Engine:
    return create_engine(settings().source_db_url, pool_pre_ping=True)


@lru_cache
def dwh_engine() -> Engine:
    return create_engine(settings().dwh_url, pool_pre_ping=True)
