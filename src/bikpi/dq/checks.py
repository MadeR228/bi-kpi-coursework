"""Expectation suites of the silver, gold and mart layers.

Every suite runs on a table read into pandas through an ephemeral Great
Expectations context; the outcome of each expectation goes to dq.results.
A failed expectation fails the step, so the DAG stops before bad data reaches
the marts or the models.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

import great_expectations as gx
import great_expectations.expectations as gxe
import pandas as pd
from great_expectations.data_context.types.base import ProgressBarsConfig

from bikpi.config import dwh_engine
from bikpi.db import copy_frame, read_sql
from bikpi.etl.batches import batch

log = logging.getLogger(__name__)

STATUSES = ["worked", "sick", "no_show", "vacation", "planned"]


class DataQualityError(RuntimeError):
    pass


def validate(layer: str, table: str, frame: pd.DataFrame, expectations: list, batch_id: int) -> list[dict]:
    context = gx.get_context(mode="ephemeral")
    context.variables.progress_bars = ProgressBarsConfig(globally=False)
    definition = (
        context.data_sources.add_pandas(name="dwh")
        .add_dataframe_asset(name=table)
        .add_batch_definition_whole_dataframe(name="whole")
    )
    suite = context.suites.add(gx.ExpectationSuite(name=f"{layer}.{table}"))
    for expectation in expectations:
        suite.add_expectation(expectation)
    validation = context.validation_definitions.add(
        gx.ValidationDefinition(name=f"{layer}.{table}", data=definition, suite=suite)
    )
    outcome = validation.run(batch_parameters={"dataframe": frame})
    rows = []
    for result in outcome.results:
        config = result.expectation_config
        kwargs = {k: v for k, v in config.kwargs.items() if k != "batch_id"}
        details = result.result or {}
        rows.append({
            "batch_id": batch_id, "layer": layer, "table_name": table, "expectation": config.type,
            "column_name": kwargs.get("column") or ",".join(kwargs.get("column_list", []))
            or ",".join(filter(None, [kwargs.get("column_A"), kwargs.get("column_B")])) or None,
            "success": bool(result.success),
            "unexpected_count": details.get("unexpected_count"),
            "element_count": details.get("element_count", len(frame)),
            "details": json.dumps({"kwargs": kwargs, "observed": details.get("observed_value")}, default=str),
        })
    return rows


def store(rows: list[dict]) -> None:
    frame = pd.DataFrame(rows)
    for column in ("unexpected_count", "element_count"):
        frame[column] = pd.to_numeric(frame[column]).round().astype("Int64")
    copy_frame(dwh_engine(), frame, "dq.results")
    failed = [f"{r['layer']}.{r['table_name']}: {r['expectation']} {r['column_name'] or ''}" for r in rows
              if not r["success"]]
    log.info("%d expectations, %d failed", len(rows), len(failed))
    if failed:
        raise DataQualityError("data quality checks failed:\n" + "\n".join(failed))


def check_silver() -> int:
    engine = dwh_engine()
    rows: list[dict] = []
    with batch("dq_silver") as current:
        workplaces = read_sql(engine, "SELECT code FROM silver.workplace")["code"].tolist()

        employees = read_sql(engine, "SELECT * FROM silver.employee")
        rows += validate("silver", "employee", employees, [
            gxe.ExpectTableRowCountToBeBetween(min_value=1),
            gxe.ExpectColumnValuesToBeUnique(column="personal_no"),
            gxe.ExpectColumnValuesToNotBeNull(column="hired_on"),
            gxe.ExpectColumnValuesToNotBeNull(column="workplace_id"),
            gxe.ExpectColumnPairValuesAToBeGreaterThanB(column_A="left_on", column_B="hired_on", or_equal=True,
                                                        ignore_row_if="either_value_is_missing"),
        ], current.id)

        attendance = read_sql(engine, "SELECT * FROM silver.attendance_day")
        rows += validate("silver", "attendance_day", attendance, [
            gxe.ExpectCompoundColumnsToBeUnique(column_list=["employee_id", "work_date"]),
            gxe.ExpectColumnValuesToNotBeNull(column="shift_type_id"),
            # A day lost by the time clock is allowed, but rarely (< 0.5 %).
            gxe.ExpectColumnValuesToBeInSet(column="status", value_set=STATUSES, mostly=0.995),
            gxe.ExpectColumnValuesToBeBetween(column="worked_minutes", min_value=0, max_value=16 * 60),
            gxe.ExpectColumnValuesToBeBetween(column="overtime_minutes", min_value=0, max_value=6 * 60),
            # Repaired clock records stay a small share.
            gxe.ExpectColumnMeanToBeBetween(column="clock_out_imputed", max_value=0.01),
        ], current.id)

        invoices = read_sql(engine, "SELECT * FROM silver.invoice")
        rows += validate("silver", "invoice", invoices, [
            gxe.ExpectColumnValuesToBeUnique(column="invoice_no"),
            gxe.ExpectColumnValuesToNotBeNull(column="amount_czk"),
            gxe.ExpectColumnValuesToBeInSet(column="workplace_code", value_set=workplaces),
            gxe.ExpectColumnValuesToMatchRegex(column="invoice_no", regex=r"^(?:FV|DB)\d{9}$"),
            gxe.ExpectColumnPairValuesAToBeGreaterThanB(column_A="issued_on", column_B="period_month"),
        ], current.id)

        trips = read_sql(engine, "SELECT * FROM silver.trip")
        rows += validate("silver", "trip", trips, [
            gxe.ExpectColumnValuesToBeUnique(column="trip_id"),
            gxe.ExpectColumnValuesToBeInSet(column="workplace_code", value_set=workplaces),
            gxe.ExpectColumnValuesToBeInSet(column="status", value_set=["completed", "cancelled"]),
            gxe.ExpectColumnValuesToBeBetween(column="delay_min", min_value=0, max_value=180),
            gxe.ExpectColumnPairValuesAToBeGreaterThanB(column_A="capacity", column_B="passengers_boarded",
                                                        or_equal=True),
            gxe.ExpectColumnValuesToNotBeNull(column="departure_actual", mostly=0.98),
        ], current.id)
        current.rows = len(rows)
        store(rows)
    return len(rows)


def check_gold() -> int:
    engine = dwh_engine()
    rows: list[dict] = []
    with batch("dq_gold") as current:
        silver_days = int(read_sql(engine, "SELECT count(*) AS n FROM silver.attendance_day")["n"][0])
        facts = read_sql(engine, """
            SELECT f.*, w.workplace_code FROM gold.fact_shift_day f JOIN gold.dim_workplace w USING (workplace_key)""")
        rows += validate("gold", "fact_shift_day", facts, [
            # Every planned day of silver found its dimension rows.
            gxe.ExpectTableRowCountToEqual(value=silver_days),
            gxe.ExpectColumnValuesToBeBetween(column="worked_hours", min_value=0, max_value=16),
            gxe.ExpectColumnValuesToBeBetween(column="revenue_czk", min_value=0),
            gxe.ExpectColumnValuesToBeBetween(column="labor_cost_czk", min_value=0),
        ], current.id)

        versions = read_sql(engine, """
            SELECT employee_id, count(*) FILTER (WHERE is_current) AS current_versions,
                   count(*) AS versions,
                   bool_and(valid_from <= valid_to) AS ordered,
                   count(*) - count(DISTINCT valid_from) AS repeated_starts
            FROM gold.dim_employee GROUP BY employee_id""")
        rows += validate("gold", "dim_employee", versions, [
            gxe.ExpectColumnValuesToBeBetween(column="current_versions", min_value=1, max_value=1),
            gxe.ExpectColumnValuesToBeInSet(column="ordered", value_set=[True]),
            gxe.ExpectColumnValuesToBeBetween(column="repeated_starts", min_value=0, max_value=0),
        ], current.id)

        days = read_sql(engine, "SELECT * FROM mart.kpi_workplace_day")
        as_of = days.loc[~days["is_future"], "date"].max()
        rate_columns = ["attendance_rate", "no_show_rate", "sick_rate", "unrecorded_rate", "overtime_share",
                        "on_time_rate", "seat_occupancy"]
        rows += validate("mart", "kpi_workplace_day", days, [
            gxe.ExpectCompoundColumnsToBeUnique(column_list=["date", "workplace_code"]),
            *[gxe.ExpectColumnValuesToBeBetween(column=c, min_value=0, max_value=1) for c in rate_columns],
            gxe.ExpectColumnValuesToBeBetween(column="plan_fulfillment", min_value=0.5, max_value=1.3, mostly=0.99),
            # Freshness: the records reach at least yesterday of the data date.
            gxe.ExpectColumnMaxToBeBetween(column="date", min_value=as_of - timedelta(days=1)),
            gxe.ExpectColumnSumToBeBetween(column="worked_hours", min_value=1),
        ], current.id)

        months = read_sql(engine, "SELECT * FROM mart.kpi_workplace_month WHERE invoiced_czk IS NOT NULL")
        rows += validate("mart", "kpi_workplace_month", months, [
            # Invoices and the hours of the time clock agree within 2 %.
            gxe.ExpectColumnValuesToBeBetween(column="invoice_gap_rate", min_value=-0.02, max_value=0.02, mostly=0.95),
            gxe.ExpectColumnValuesToBeBetween(column="margin_rate", min_value=0, max_value=0.4),
        ], current.id)
        current.rows = len(rows)
        store(rows)
    return len(rows)
