"""Forecast of worked hours per workplace and day, 28 days ahead.

Three models are compared by rolling-origin backtesting:

* seasonal naive - plan fulfillment of the same weekday over the 8 weeks
  before the horizon;
* Holt-Winters exponential smoothing (statsmodels) on the fulfillment series
  of each workplace, weekly season;
* gradient boosting (scikit-learn HistGradientBoostingRegressor) on the
  feature store.

Every model is a run of the MLflow experiment; the boosting model is
registered as kpi_hours_forecaster and becomes the champion (alias) when it
beats the seasonal naive baseline and the current champion.
"""
from __future__ import annotations

import logging
import uuid
import warnings

import mlflow
import numpy as np
import pandas as pd
from mlflow.models import infer_signature
from mlflow.tracking import MlflowClient
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from bikpi.config import dwh_engine, settings
from bikpi.db import copy_frame
from bikpi.etl.batches import batch
from bikpi.ml.features import FEATURE_SET, FEATURES, HORIZON, read_features

log = logging.getLogger(__name__)

EXPERIMENT = "bc_kpi_forecast"
MODEL_NAME = "kpi_hours_forecaster"
ALIAS = "champion"
FOLDS = 8
COLUMNS = ["workplace_code", *FEATURES]
PARAMS = dict(max_iter=500, learning_rate=0.04, max_leaf_nodes=31, min_samples_leaf=30,
              l2_regularization=1.0, random_state=42)


def make_model() -> Pipeline:
    encode = ColumnTransformer(
        [("workplace", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1), ["workplace_code"])],
        remainder="passthrough", verbose_feature_names_out=False,
    )
    return Pipeline([("encode", encode),
                     ("boost", HistGradientBoostingRegressor(categorical_features=[0], **PARAMS))])


def matrix(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame[COLUMNS].copy()
    x["is_holiday"] = x["is_holiday"].astype(int)
    for column in FEATURES:
        x[column] = x[column].astype(float)
    return x


def metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    error = predicted - actual
    nonzero = actual > 0
    return {
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error ** 2))),
        "mape": float(np.mean(np.abs(error[nonzero]) / actual[nonzero])),
        "wape": float(np.sum(np.abs(error)) / np.sum(actual)),
    }


# ----------------------------------------------------------------- models
def predict_naive(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    fallback = train["fulfillment"].mean()
    ratio = test["fulfillment_dow_mean8"].fillna(test["fulfillment_lag28"]).fillna(fallback)
    return (ratio * test["planned_hours"]).to_numpy()


def predict_holt_winters(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    out = pd.Series(0.0, index=test.index)
    for code, part in test.groupby("workplace_code"):
        history = train.loc[train["workplace_code"] == code].sort_values("date")
        series = history["fulfillment"].astype(float).to_numpy()[-730:]
        period = int(round(len(history) / max(1, (history["date"].iloc[-1] - history["date"].iloc[0]).days) * 7))
        period = max(2, min(7, period))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fitted = ExponentialSmoothing(series, trend="add", damped_trend=True, seasonal="add",
                                          seasonal_periods=period).fit()
        ordered = part.sort_values("date")
        ratio = fitted.forecast(len(ordered))
        out.loc[ordered.index] = ratio * ordered["planned_hours"].to_numpy()
    return out.to_numpy()


def fit_boosting(train: pd.DataFrame) -> Pipeline:
    model = make_model()
    model.fit(matrix(train), train["fulfillment"].astype(float), boost__sample_weight=train["planned_hours"])
    return model


def predict_boosting(model: Pipeline, test: pd.DataFrame) -> np.ndarray:
    return model.predict(matrix(test)) * test["planned_hours"].to_numpy()


# ---------------------------------------------------------------- backtest
def backtest(history: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rolling origin: train up to a cutoff, predict the next 28 days."""
    last = history["date"].max()
    rows, predictions = [], []
    for fold in range(FOLDS, 0, -1):
        cutoff = last - pd.Timedelta(days=HORIZON * fold)
        train = history[history["date"] <= cutoff]
        test = history[(history["date"] > cutoff) & (history["date"] <= cutoff + pd.Timedelta(days=HORIZON))]
        train = train.dropna(subset=["fulfillment_lag28"])
        actual = test["worked_hours"].to_numpy(float)
        boosting = fit_boosting(train)
        for name, predicted in (
            ("seasonal_naive", predict_naive(train, test)),
            ("holt_winters", predict_holt_winters(train, test)),
            ("gradient_boosting", predict_boosting(boosting, test)),
        ):
            rows.append({"model": name, "fold": FOLDS - fold + 1, "cutoff": cutoff.date(), **metrics(actual, predicted)})
            predictions.append(pd.DataFrame({"model": name, "fold": FOLDS - fold + 1, "workplace_code": test["workplace_code"],
                                             "date": test["date"], "actual": actual, "predicted": predicted}))
    return pd.DataFrame(rows), pd.concat(predictions, ignore_index=True)


def train() -> str:
    mlflow.set_tracking_uri(settings().mlflow_tracking_uri)
    mlflow.set_experiment(EXPERIMENT)
    engine = dwh_engine()
    with batch("train") as current:
        features = read_features()
        features["date"] = pd.to_datetime(features["date"])
        history = features[~features["is_future"]].reset_index(drop=True)
        scores, predictions = backtest(history)
        summary = scores.groupby("model")[["mae", "rmse", "mape", "wape"]].mean()
        log.info("backtest:\n%s", summary.round(4))

        with mlflow.start_run(run_name="backtest") as parent:
            for name, row in summary.iterrows():
                with mlflow.start_run(run_name=name, nested=True):
                    mlflow.log_params({"model": name, "folds": FOLDS, "horizon_days": HORIZON,
                                       "feature_set": FEATURE_SET})
                    mlflow.log_metrics({f"backtest_{k}": float(v) for k, v in row.items()})
            boosting = predictions[predictions["model"] == "gradient_boosting"]
            relative = (boosting["actual"] - boosting["predicted"]) / boosting["predicted"].clip(lower=1)
            q10, q90 = float(relative.quantile(0.1)), float(relative.quantile(0.9))

            # The model of record is trained on the whole history.
            train_rows = history.dropna(subset=["fulfillment_lag28"])
            model = fit_boosting(train_rows)
            with mlflow.start_run(run_name="gradient_boosting_full", nested=True) as run:
                mlflow.log_params({**PARAMS, "feature_set": FEATURE_SET, "features": ",".join(FEATURES),
                                   "train_rows": len(train_rows),
                                   "train_until": str(history["date"].max().date())})
                mlflow.log_metrics({f"backtest_{k}": float(v) for k, v in summary.loc["gradient_boosting"].items()})
                mlflow.log_metrics({"residual_q10": q10, "residual_q90": q90})
                mlflow.log_table(summary.reset_index(), artifact_file="backtest_summary.json")
                x = matrix(train_rows.head(50))
                info = mlflow.sklearn.log_model(model, artifact_path="model", registered_model_name=MODEL_NAME,
                                                serialization_format="cloudpickle",
                                                signature=infer_signature(x, model.predict(x)),
                                                input_example=x.head(5))
                version = promote(info, summary, run.info.run_id)
            mlflow.set_tag("registered_version", version)

        scores.insert(0, "run_id", parent.info.run_id)
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM ml.backtest")
        copy_frame(engine, scores, "ml.backtest")
        export = settings().export_dir
        export.mkdir(parents=True, exist_ok=True)
        predictions.to_csv(export / "backtest_predictions.csv", index=False)
        current.rows = len(scores)
        return f"model version {version}, backtest WAPE {summary.loc['gradient_boosting', 'wape']:.4f}"


def promote(info, summary: pd.DataFrame, run_id: str) -> str:
    """Gives the champion alias to the new version if it is better."""
    client = MlflowClient()
    version = str(info.registered_model_version)
    wape = float(summary.loc["gradient_boosting", "wape"])
    if wape >= float(summary.loc["seasonal_naive", "wape"]):
        log.warning("gradient boosting does not beat the seasonal naive baseline; champion unchanged")
        return version
    try:
        champion = client.get_model_version_by_alias(MODEL_NAME, ALIAS)
        champion_wape = client.get_run(champion.run_id).data.metrics.get("backtest_wape", np.inf)
    except Exception:  # noqa: BLE001 - no champion yet
        champion_wape = np.inf
    if wape <= champion_wape:
        client.set_registered_model_alias(MODEL_NAME, ALIAS, version)
        client.set_model_version_tag(MODEL_NAME, version, "backtest_wape", f"{wape:.4f}")
        log.info("version %s is the champion (WAPE %.4f)", version, wape)
    return version


def predict() -> int:
    mlflow.set_tracking_uri(settings().mlflow_tracking_uri)
    client = MlflowClient()
    champion = client.get_model_version_by_alias(MODEL_NAME, ALIAS)
    metrics_ = client.get_run(champion.run_id).data.metrics
    model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{ALIAS}")
    engine = dwh_engine()
    with batch("predict") as current:
        features = read_features()
        future = features[features["is_future"]].copy()
        predicted = predict_boosting(model, future)
        run_id = uuid.uuid4().hex
        out = pd.DataFrame({
            "run_id": run_id, "model_name": MODEL_NAME, "model_version": champion.version,
            "workplace_code": future["workplace_code"], "date": future["date"],
            "planned_hours": future["planned_hours"], "predicted_hours": predicted.round(2),
            "lower_hours": (predicted * (1 + metrics_["residual_q10"])).round(2),
            "upper_hours": np.minimum(predicted * (1 + metrics_["residual_q90"]),
                                      future["planned_hours"] * 1.3).round(2),
        })
        # The marts show the latest forecast only.
        with engine.begin() as connection:
            connection.exec_driver_sql("TRUNCATE ml.forecast")
        current.rows = copy_frame(engine, out, "ml.forecast")
        return current.rows
