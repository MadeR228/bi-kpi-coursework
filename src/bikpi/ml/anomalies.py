"""Detection of anomalous days in the KPIs of each workplace.

* Robust z-score: the value against the median of the previous 8 weeks; the
  scale is the larger of the MAD and the binomial standard error of a rate,
  so that one absent person at a small plant is not an alarm. Only rises are
  bad for these KPIs, so the test is one-sided.
* Isolation Forest (scikit-learn) on the vector of the robust scores of a day:
  catches days that are unusual in several KPIs at once.

The threshold of the z-score is calibrated on the first 40 % of the history
against the labelled incidents of the generator (data/ground_truth); the
quality of each method is then measured on the remaining 60 %, which the
calibration did not see. Precision, recall and F1 go to MLflow and
ml.anomaly_evaluation. Without the labels the threshold is 3.5, the usual
value for the modified z-score (Iglewicz and Hoaglin).
"""
from __future__ import annotations

import logging

import mlflow
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from bikpi.config import dwh_engine, settings
from bikpi.db import copy_frame, read_sql
from bikpi.etl.batches import batch

log = logging.getLogger(__name__)

EXPERIMENT = "bc_kpi_anomalies"
WINDOW = 56
DEFAULT_THRESHOLD = 3.5
THRESHOLDS = [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0, 10.0]
CALIBRATION_SHARE = 0.4
CONTAMINATION = 0.01
# KPI -> (kind of value, minimal scale)
KPIS = {
    "no_show_rate": ("rate", 0.004),
    "unrecorded_rate": ("rate", 0.002),
    "overtime_share": ("value", 0.006),
    "avg_delay_min": ("value", 0.8),
}


def robust_scores(days: pd.DataFrame) -> pd.DataFrame:
    """Adds <kpi>_expected and <kpi>_z for every KPI, per workplace."""
    out = []
    for _, part in days.groupby("workplace_code"):
        part = part.sort_values("date").copy()
        for kpi, (kind, floor) in KPIS.items():
            past = part[kpi].shift(1).rolling(WINDOW, min_periods=20)
            median = past.median()
            mad = (part[kpi].shift(1) - median).abs().rolling(WINDOW, min_periods=20).median() * 1.4826
            scale = np.maximum(mad.fillna(0), floor)
            if kind == "rate":
                p = np.maximum(past.mean(), floor)
                scale = np.maximum(scale, np.sqrt(p * (1 - p) / part["planned_shifts"].clip(lower=1)))
            part[f"{kpi}_expected"] = median
            part[f"{kpi}_z"] = (part[kpi] - median) / scale
        out.append(part)
    scored = pd.concat(out, ignore_index=True)
    scored["z_max"] = scored[[f"{kpi}_z" for kpi in KPIS]].max(axis=1)
    return scored


def isolation_scores(scored: pd.DataFrame) -> pd.Series:
    columns = [f"{kpi}_z" for kpi in KPIS]
    result = pd.Series(np.nan, index=scored.index)
    for _, part in scored.groupby("workplace_code"):
        x = part[columns].clip(-10, 30).fillna(0).to_numpy()
        forest = IsolationForest(n_estimators=300, contamination=CONTAMINATION, random_state=42).fit(x)
        # Positive = more anomalous; the threshold of the forest is at 0.
        result.loc[part.index] = -forest.decision_function(x)
    return result


def evaluate(flagged: pd.Series, truth: pd.Series) -> dict[str, float]:
    tp = int((flagged & truth).sum())
    fp = int((flagged & ~truth).sum())
    fn = int((~flagged & truth).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": precision, "recall": recall, "f1": f1}


def load_truth(scored: pd.DataFrame) -> pd.Series | None:
    path = settings().ground_truth_dir / "incidents.csv"
    if not path.exists():
        return None
    incidents = pd.read_csv(path, parse_dates=["date"])
    keys = set(zip(incidents["workplace_code"], incidents["date"].dt.date))
    return pd.Series([key in keys for key in zip(scored["workplace_code"], scored["date"])], index=scored.index)


def anomaly_rows(scored: pd.DataFrame, by_z: pd.Series, by_forest: pd.Series, threshold: float) -> pd.DataFrame:
    rows = []
    for index in scored.index[by_z | by_forest]:
        day = scored.loc[index]
        method = "both" if by_z[index] and by_forest[index] else "robust_z" if by_z[index] else "isolation_forest"
        raised = [kpi for kpi in KPIS if day[f"{kpi}_z"] > threshold] or \
                 [max(KPIS, key=lambda kpi: day[f"{kpi}_z"] if pd.notna(day[f"{kpi}_z"]) else -np.inf)]
        for kpi in raised:
            z = day[f"{kpi}_z"]
            rows.append({"workplace_code": day["workplace_code"], "date": day["date"], "kpi": kpi,
                         "value": day[kpi], "expected": day[f"{kpi}_expected"], "robust_z": z,
                         "iforest_score": day["iforest_score"], "method": method,
                         "severity": "high" if method == "both" or (pd.notna(z) and z > 2 * threshold) else "medium"})
    return pd.DataFrame(rows).round(4)


def detect() -> str:
    engine = dwh_engine()
    mlflow.set_tracking_uri(settings().mlflow_tracking_uri)
    mlflow.set_experiment(EXPERIMENT)
    with batch("anomalies") as current, mlflow.start_run(run_name="anomaly_detection") as run:
        days = read_sql(engine, f"""
            SELECT date, workplace_code, planned_shifts, {", ".join(f"{k}::float AS {k}" for k in KPIS)}
            FROM mart.kpi_workplace_day WHERE NOT is_future""")
        days["avg_delay_min"] = days["avg_delay_min"].fillna(0)
        scored = robust_scores(days)
        scored["iforest_score"] = isolation_scores(scored)
        by_forest = scored["iforest_score"] > 0
        truth = load_truth(scored)

        threshold, evaluation = DEFAULT_THRESHOLD, []
        if truth is not None:
            dates = pd.to_datetime(scored["date"])
            split = dates.quantile(CALIBRATION_SHARE)
            calibration, test = dates <= split, dates > split
            # The threshold with the best F1 on the calibration period.
            curve = []
            for value in THRESHOLDS:
                result = evaluate((scored["z_max"] > value)[calibration], truth[calibration])
                curve.append({"run_id": run.info.run_id, "period": "calibration", "method": "robust_z",
                              "threshold": value, **result})
            threshold = max(curve, key=lambda row: (row["f1"], -row["threshold"]))["threshold"]
            evaluation += curve
            by_z = scored["z_max"] > threshold
            for method, flagged in (("robust_z", by_z), ("isolation_forest", by_forest), ("both", by_z & by_forest)):
                result = evaluate(flagged[test], truth[test])
                evaluation.append({"run_id": run.info.run_id, "period": "test", "method": method,
                                   "threshold": threshold if method != "isolation_forest" else None, **result})
                mlflow.log_metrics({f"test_{method}_{k}": float(v) for k, v in result.items()})
                log.info("test %s: %s", method, {k: round(v, 3) for k, v in result.items()})
            mlflow.log_params({"calibration_until": str(split.date()), "test_incidents": int(truth[test].sum())})
        by_z = scored["z_max"] > threshold
        mlflow.log_params({"window_days": WINDOW, "z_threshold": threshold, "contamination": CONTAMINATION,
                           "kpis": ",".join(KPIS), "days": len(scored)})

        anomalies = anomaly_rows(scored, by_z, by_forest, threshold)
        with engine.begin() as connection:
            connection.exec_driver_sql("TRUNCATE ml.anomalies")
        current.rows = copy_frame(engine, anomalies, "ml.anomalies")
        mlflow.log_metric("anomaly_rows", len(anomalies))
        if not evaluation:
            return f"{len(anomalies)} anomaly rows, threshold {threshold}"
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM ml.anomaly_evaluation")
        copy_frame(engine, pd.DataFrame(evaluation), "ml.anomaly_evaluation")
        mlflow.log_table(pd.DataFrame(evaluation), artifact_file="evaluation.json")
        best = max((e for e in evaluation if e["period"] == "test"), key=lambda e: e["f1"])
        return f"{len(anomalies)} anomaly rows, threshold {threshold}; best on test: {best['method']} F1 {best['f1']:.3f}"
