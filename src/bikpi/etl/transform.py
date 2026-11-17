"""Transformations inside the warehouse (T of ELT): silver, gold, marts."""
from __future__ import annotations

from datetime import date

import holidays
import pandas as pd

from bikpi.config import dwh_engine
from bikpi.db import copy_frame, read_sql, run_sql_file
from bikpi.etl.batches import batch

MONTHS_UK = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень", "Липень", "Серпень",
             "Вересень", "Жовтень", "Листопад", "Грудень"]
DAYS_UK = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


def init() -> None:
    engine = dwh_engine()
    run_sql_file(engine, "00_init.sql")
    run_sql_file(engine, "10_tables.sql")


def build_silver() -> None:
    with batch("silver") as current:
        run_sql_file(dwh_engine(), "20_silver.sql")
        current.rows = int(read_sql(dwh_engine(), "SELECT count(*) AS n FROM silver.attendance_day")["n"][0])


def date_dimension(first: date, last: date) -> pd.DataFrame:
    czech = holidays.country_holidays("CZ", years=range(first.year, last.year + 1))
    days = pd.date_range(first, last, freq="D")
    return pd.DataFrame({
        "date_key": days.strftime("%Y%m%d").astype(int),
        "date": days.date,
        "year": days.year,
        "quarter": days.quarter,
        "month": days.month,
        "month_name": [MONTHS_UK[m - 1] for m in days.month],
        "month_start": days.to_period("M").start_time.date,
        "iso_week": days.isocalendar().week.astype(int).to_numpy(),
        "day_of_week": days.dayofweek + 1,
        "day_name": [DAYS_UK[d] for d in days.dayofweek],
        "is_weekend": days.dayofweek >= 5,
        "is_holiday": [d in czech for d in days.date],
        "holiday_name": [czech.get(d) for d in days.date],
    })


def build_gold() -> None:
    engine = dwh_engine()
    with batch("gold") as current:
        bounds = read_sql(engine, """
            SELECT least(min(work_date), (SELECT min(service_date) FROM silver.trip)) AS first,
                   max(work_date) AS last
            FROM silver.attendance_day""")
        first = date(bounds["first"][0].year, 1, 1)
        last = date(bounds["last"][0].year, 12, 31)
        known = read_sql(engine, "SELECT count(*) AS n FROM gold.dim_date")["n"][0]
        frame = date_dimension(first, last)
        if known != len(frame):
            with engine.begin() as connection:
                connection.exec_driver_sql("TRUNCATE gold.dim_date CASCADE")
            copy_frame(engine, frame, "gold.dim_date")
        run_sql_file(engine, "30_gold.sql")
        current.rows = int(read_sql(engine, "SELECT count(*) AS n FROM gold.fact_shift_day")["n"][0])


def build_marts() -> None:
    with batch("mart") as current:
        run_sql_file(dwh_engine(), "40_mart.sql")
        current.rows = int(read_sql(dwh_engine(), "SELECT count(*) AS n FROM mart.kpi_workplace_day")["n"][0])
