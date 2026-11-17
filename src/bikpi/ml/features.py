"""Feature store of the hours forecast (ml.features_workplace_day).

The target is the share of planned hours actually worked (plan fulfillment);
worked hours = fulfillment x planned hours, and the plan is known four weeks
ahead. Every lag is at least 28 days, the forecast horizon, so the same
features exist for each of the 28 future days without recursion.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bikpi.config import dwh_engine
from bikpi.db import copy_frame, read_sql
from bikpi.etl.batches import batch

FEATURE_SET = "fulfillment_v1"
HORIZON = 28
FEATURES = [
    "day_of_week", "month", "is_holiday", "doy_sin", "doy_cos", "days_to_holiday", "planned_hours",
    "fulfillment_lag28", "fulfillment_lag35", "fulfillment_lag364", "fulfillment_dow_mean8",
    "fulfillment_mean28", "fulfillment_std28",
]


def compute(days: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
    """[days]: workplace_code, date, is_future, planned_hours, worked_hours."""
    holidays = pd.to_datetime(calendar.loc[calendar["is_holiday"], "date"]).sort_values().to_numpy()
    frames = []
    for code, part in days.groupby("workplace_code"):
        # A continuous calendar, so that shift(k) means k days back.
        part = part.set_index(pd.to_datetime(part["date"])).drop(columns=["date", "workplace_code"])
        part = part.reindex(pd.date_range(part.index.min(), part.index.max(), freq="D"))
        frame = pd.DataFrame(index=part.index)
        frame["is_future"] = part["is_future"]
        frame["planned_hours"] = part["planned_hours"]
        frame["worked_hours"] = part["worked_hours"].where(~part["is_future"].fillna(False).astype(bool))
        frame["fulfillment"] = frame["worked_hours"] / frame["planned_hours"]
        ratio = frame["fulfillment"]
        frame["fulfillment_lag28"] = ratio.shift(28)
        frame["fulfillment_lag35"] = ratio.shift(35)
        frame["fulfillment_lag364"] = ratio.shift(364)
        # The same weekday over the 8 weeks before the horizon.
        same_day = pd.concat([ratio.shift(28 + 7 * k) for k in range(8)], axis=1)
        frame["fulfillment_dow_mean8"] = same_day.mean(axis=1)
        window = ratio.shift(28).rolling(28, min_periods=10)
        frame["fulfillment_mean28"] = window.mean()
        frame["fulfillment_std28"] = window.std()
        frame["workplace_code"] = code
        frames.append(frame[frame["planned_hours"].notna()])
    out = pd.concat(frames).rename_axis("date").reset_index()
    dates = out["date"]
    doy = dates.dt.dayofyear
    out["day_of_week"] = dates.dt.dayofweek + 1
    out["month"] = dates.dt.month
    out["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    out["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    holiday_set = set(pd.DatetimeIndex(holidays))
    out["is_holiday"] = dates.isin(holiday_set)
    position = np.searchsorted(holidays, dates.to_numpy())
    next_holiday = holidays[np.minimum(position, len(holidays) - 1)]
    out["days_to_holiday"] = np.clip((next_holiday - dates.to_numpy()) / np.timedelta64(1, "D"), 0, 60).astype(int)
    out["date"] = dates.dt.date
    out["is_future"] = out["is_future"].astype(bool)
    return out


def load_days() -> pd.DataFrame:
    return read_sql(dwh_engine(), """
        SELECT workplace_code, date, is_future, planned_hours::float, worked_hours::float
        FROM mart.kpi_workplace_day ORDER BY workplace_code, date""")


def load_calendar() -> pd.DataFrame:
    return read_sql(dwh_engine(), "SELECT date, is_holiday FROM gold.dim_date")


def build_features() -> int:
    engine = dwh_engine()
    with batch("features") as current:
        features = compute(load_days(), load_calendar())
        features.insert(0, "feature_set", FEATURE_SET)
        with engine.begin() as connection:
            connection.exec_driver_sql(f"DELETE FROM ml.features_workplace_day WHERE feature_set = '{FEATURE_SET}'")
        columns = ["feature_set", "workplace_code", "date", "is_future", "planned_hours", "worked_hours",
                   "fulfillment", *[c for c in FEATURES if c != "planned_hours"]]
        current.rows = copy_frame(engine, features[columns].round(5), "ml.features_workplace_day")
        return current.rows


def read_features() -> pd.DataFrame:
    return read_sql(dwh_engine(), "SELECT * FROM ml.features_workplace_day WHERE feature_set = :name ORDER BY date",
                    name=FEATURE_SET)
