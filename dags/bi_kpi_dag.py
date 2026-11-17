"""Daily ETL + ML pipeline of the BI KPI warehouse.

Each task runs one step of the bikpi command line in the project's own
virtual environment (/opt/bikpi), so the libraries of the pipeline do not mix
with those of Airflow. The model is retrained on Mondays; the other days use
the registered champion.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import ShortCircuitOperator

PYTHON = os.environ.get("BIKPI_PYTHON", "/opt/bikpi/bin/python")


def step(name: str, **kwargs) -> BashOperator:
    return BashOperator(task_id=name, bash_command=f"{PYTHON} -m bikpi.cli {name}", **kwargs)


def retrain_today(logical_date=None, **_) -> bool:
    """Monday, or no champion model yet."""
    if logical_date is not None and logical_date.weekday() == 0:
        return True
    import subprocess

    check = subprocess.run(
        [PYTHON, "-c", "from mlflow.tracking import MlflowClient as C; "
                       "C().get_model_version_by_alias('kpi_hours_forecaster', 'champion')"],
        capture_output=True,
    )
    return check.returncode != 0


with DAG(
    dag_id="bc_kpi_pipeline",
    description="Sources -> bronze -> silver -> gold -> marts -> ML -> Power BI",
    start_date=datetime(2026, 10, 1),
    schedule="0 5 * * *",
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["coursework", "bi", "kpi"],
) as dag:
    init = step("init")
    extract = [step("extract_ops"), step("extract_csv"), step("extract_api")]
    silver = step("silver")
    dq_silver = step("dq_silver", retries=0)
    gold = step("gold")
    mart = step("mart")
    dq_gold = step("dq_gold", retries=0)
    features = step("features")
    should_train = ShortCircuitOperator(task_id="retrain_today", python_callable=retrain_today,
                                        ignore_downstream_trigger_rules=False)
    train = step("train")
    predict = step("predict", trigger_rule="none_failed")
    anomalies = step("anomalies")
    export = step("export", trigger_rule="none_failed")
    done = EmptyOperator(task_id="done")

    init >> extract >> silver >> dq_silver >> gold >> mart >> dq_gold >> features
    features >> should_train >> train >> predict
    features >> predict
    dq_gold >> anomalies
    [predict, anomalies] >> export >> done
