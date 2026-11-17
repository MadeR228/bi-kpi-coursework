"""Command line of the pipeline: python -m bikpi.cli <step> [<step> ...].

The Airflow DAG runs the same steps one task each.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import time

STEPS = {
    "init": ("bikpi.etl.transform", "init", "create the tables of every layer"),
    "generate": ("bikpi.sources.generate", "main", "write the synthetic sources (OLTP, CSV, JSON)"),
    "extract_ops": ("bikpi.etl.extract", "extract_ops", "operational DB -> bronze (incremental)"),
    "extract_csv": ("bikpi.etl.extract", "extract_csv", "accounting CSV -> bronze"),
    "extract_api": ("bikpi.etl.extract", "extract_api", "transport REST API -> bronze"),
    "silver": ("bikpi.etl.transform", "build_silver", "bronze -> silver: clean, deduplicate, conform"),
    "dq_silver": ("bikpi.dq.checks", "check_silver", "Great Expectations on silver"),
    "gold": ("bikpi.etl.transform", "build_gold", "silver -> gold: star schema, SCD2"),
    "mart": ("bikpi.etl.transform", "build_marts", "gold -> KPI marts"),
    "dq_gold": ("bikpi.dq.checks", "check_gold", "Great Expectations on gold and marts"),
    "features": ("bikpi.ml.features", "build_features", "feature store of the forecast"),
    "train": ("bikpi.ml.forecast", "train", "backtest, train, register the forecaster in MLflow"),
    "predict": ("bikpi.ml.forecast", "predict", "forecast 28 days with the champion model"),
    "anomalies": ("bikpi.ml.anomalies", "detect", "detect KPI anomalies, evaluate them"),
    "export": ("bikpi.export", "export", "CSV of the marts for Power BI without a DB connection"),
}
PIPELINE = ["init", "extract_ops", "extract_csv", "extract_api", "silver", "dq_silver", "gold", "mart",
            "dq_gold", "features", "train", "predict", "anomalies", "export"]


def run(step: str) -> None:
    module, name, _ = STEPS[step]
    started = time.perf_counter()
    logging.info("step %s started", step)
    result = getattr(importlib.import_module(module), name)()
    logging.info("step %s done in %.1f s%s", step, time.perf_counter() - started,
                 "" if result is None else f": {result}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bikpi",
        description="BI KPI pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="steps:\n" + "\n".join(f"  {k:<12} {v[2]}" for k, v in STEPS.items())
               + "\n  all          " + " -> ".join(PIPELINE),
    )
    parser.add_argument("steps", nargs="+", choices=[*STEPS, "all"], metavar="step")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("great_expectations").setLevel(logging.WARNING)
    for step in args.steps:
        for one in PIPELINE if step == "all" else [step]:
            run(one)


if __name__ == "__main__":
    main()
