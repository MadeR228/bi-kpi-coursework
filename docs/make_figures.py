"""Figures and numbers of the explanatory note (docs/figures/*.png, docs/figures/stats.json).

    docker compose run --rm --entrypoint /opt/bikpi/bin/python runner docs/make_figures.py

Schemes are drawn with matplotlib; charts read the warehouse, so the note
shows the results of the last run of the pipeline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bikpi.config import dwh_engine, settings, source_engine  # noqa: E402
from bikpi.db import read_sql  # noqa: E402

OUT = Path(__file__).resolve().parent / "figures"
OUT.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "figure.dpi": 150})
BLUE, GREY, ORANGE, RED, GREEN = "#2a6fdb", "#9aa5b1", "#e8590c", "#c92a2a", "#2b8a3e"
LAYER = {"src": "#e7f0fd", "bronze": "#f3e1cf", "silver": "#eceff3", "gold": "#fff3bf", "mart": "#e6fcf5",
         "ml": "#f3f0ff", "tool": "#ffffff"}
STATS: dict = {}


def save(fig, name: str) -> None:
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ------------------------------------------------------------- drawing kit
def box(ax, x, y, w, h, text, color="#ffffff", size=9, bold=False, edge="#52606d"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                facecolor=color, edgecolor=edge, linewidth=1))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size,
            fontweight="bold" if bold else "normal", wrap=True)


def arrow(ax, a, b, text=None, style="-|>", color="#52606d", dashed=False, rad=0.0, size=8):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle=style, mutation_scale=12, color=color, linewidth=1,
                                 linestyle="--" if dashed else "-", connectionstyle=f"arc3,rad={rad}"))
    if text:
        ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 0.12, text, ha="center", va="bottom", fontsize=size,
                color="#323f4b")


def canvas(w, h, size):
    fig, ax = plt.subplots(figsize=size)
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.axis("off")
    return fig, ax


def table_box(ax, x, y, w, title, rows, color, line=0.32):
    h = line * (len(rows) + 1) + 0.1
    ax.add_patch(FancyBboxPatch((x, y - h), w, h, boxstyle="round,pad=0.01,rounding_size=0.05",
                                facecolor="white", edgecolor="#52606d", linewidth=1))
    ax.add_patch(FancyBboxPatch((x, y - line - 0.05), w, line + 0.05, boxstyle="round,pad=0.01,rounding_size=0.05",
                                facecolor=color, edgecolor="#52606d", linewidth=1))
    ax.text(x + w / 2, y - line / 2 - 0.02, title, ha="center", va="center", fontsize=8.5, fontweight="bold")
    for i, row in enumerate(rows):
        ax.text(x + 0.08, y - line * (i + 1.5) - 0.07, row, ha="left", va="center", fontsize=7.2,
                family="DejaVu Sans Mono")
    return (x, y - h, w, h)


# ----------------------------------------------------------------- schemes
def architecture():
    fig, ax = canvas(16, 9, (13, 7.3))
    ax.text(0.3, 8.6, "Джерела", fontsize=10, fontweight="bold")
    box(ax, 0.3, 6.6, 2.6, 1.5, "Операційна БД\nPostgreSQL (OLTP)\nзміни, табель,\nвідсутності", LAYER["src"])
    box(ax, 0.3, 4.6, 2.6, 1.5, "CSV бухгалтерії\nрахунки клієнтам\n(«;», кома,\nдва формати дат)", LAYER["src"])
    box(ax, 0.3, 2.6, 2.6, 1.5, "REST API транспорту\nJSON, пагінація,\nзбої 503", LAYER["src"])

    ax.text(3.6, 8.6, "Сховище даних PostgreSQL (ELT)", fontsize=10, fontweight="bold")
    box(ax, 3.6, 2.6, 2.2, 5.5, "Bronze\n\nсирі копії\n+ batch_id,\n_loaded_at\n\nводяні знаки\n(meta)", LAYER["bronze"], bold=False)
    box(ax, 6.3, 2.6, 2.2, 5.5, "Silver\n\nочищення,\nдедуплікація,\nтипізація,\nвідновлення\n\nattendance_day", LAYER["silver"])
    box(ax, 9.0, 2.6, 2.2, 5.5, "Gold\n\nзірка Кімбалла\n\n4 виміри\n(SCD2 для\nпрацівника)\n5 фактів", LAYER["gold"])
    box(ax, 11.7, 5.4, 2.0, 2.7, "Mart\nKPI день\nKPI місяць", LAYER["mart"])
    box(ax, 11.7, 2.6, 2.0, 2.5, "ML\nFeature Store\nпрогноз\nвідхилення", LAYER["ml"])
    for y in (7.35, 5.35, 3.35):
        arrow(ax, (2.9, y), (3.6, y))
    arrow(ax, (5.8, 5.35), (6.3, 5.35))
    arrow(ax, (8.5, 5.35), (9.0, 5.35))
    arrow(ax, (11.2, 6.7), (11.7, 6.7))
    arrow(ax, (11.2, 3.8), (11.7, 3.8))
    arrow(ax, (12.7, 5.4), (12.7, 5.1), style="<|-|>")

    ax.text(14.2, 8.6, "Споживачі", fontsize=10, fontweight="bold")
    box(ax, 14.2, 6.4, 1.6, 1.7, "Power BI\n(PBIP,\nDAX)", "#fff9db", bold=True)
    box(ax, 14.2, 4.4, 1.6, 1.5, "Панель\nPlotly Dash", "#fff9db")
    box(ax, 14.2, 2.6, 1.6, 1.4, "CSV-\nекспорт", "#fff9db")
    arrow(ax, (13.7, 6.9), (14.2, 7.2))
    arrow(ax, (13.7, 6.4), (14.2, 5.2))
    arrow(ax, (13.7, 3.6), (14.2, 6.6))
    arrow(ax, (13.7, 3.3), (14.2, 3.3))

    box(ax, 3.6, 0.6, 4.9, 1.2, "Great Expectations\nперевірки silver, gold, mart → dq.results", LAYER["tool"])
    box(ax, 9.0, 0.6, 4.7, 1.2, "MLflow: експерименти, реєстр моделей\nkpi_hours_forecaster@champion", LAYER["tool"])
    box(ax, 0.3, 0.6, 2.6, 1.2, "Apache Airflow\nDAG bc_kpi_pipeline", LAYER["tool"], bold=True)
    arrow(ax, (6.0, 1.8), (7.4, 2.6), dashed=True)
    arrow(ax, (11.5, 1.8), (12.7, 2.6), dashed=True, style="<|-|>")
    arrow(ax, (1.6, 1.8), (1.6, 2.6), dashed=True)
    ax.text(14.2, 1.2, "─ ─  керування\n         і контроль", fontsize=7.5, color="#52606d")
    save(fig, "fig_architecture")


def dfd():
    fig, ax = canvas(16, 7, (13, 5.8))
    ext = dict(color="#e7f0fd")
    box(ax, 0.2, 5.2, 2.2, 1.0, "Операційна БД", **ext)
    box(ax, 0.2, 3.0, 2.2, 1.0, "Бухгалтерія (CSV)", **ext)
    box(ax, 0.2, 0.8, 2.2, 1.0, "Транспорт (API)", **ext)
    procs = [(3.3, 5.0, "1. Вилучення\nінкрементне\n(updated_at)"), (3.3, 2.8, "2. Вилучення\nфайлів\n(sha-256)"),
             (3.3, 0.6, "3. Вилучення\nсторінок\n(повтори)")]
    for x, y, t in procs:
        ax.add_patch(plt.Circle((x + 1.0, y + 0.7), 0.85, facecolor="#fff", edgecolor="#52606d"))
        ax.text(x + 1.0, y + 0.7, t, ha="center", va="center", fontsize=7.5)
    arrow(ax, (2.4, 5.7), (3.45, 5.7), "рядки")
    arrow(ax, (2.4, 3.5), (3.45, 3.5), "файли")
    arrow(ax, (2.4, 1.3), (3.45, 1.3), "JSON")

    def store(x, y, w, t):
        ax.plot([x, x + w], [y + 0.55, y + 0.55], color="#52606d", lw=1)
        ax.plot([x, x + w], [y, y], color="#52606d", lw=1)
        ax.plot([x, x], [y, y + 0.55], color="#52606d", lw=1)
        ax.text(x + 0.12, y + 0.27, t, va="center", fontsize=8)

    store(5.6, 3.2, 2.1, "D1 bronze.*")
    for y in (5.7, 3.5, 1.3):
        arrow(ax, (5.15, y), (6.2, 3.8), rad=0.0)
    for x, y, t in [(8.4, 2.8, "4. Очищення\nі узгодження\n→ silver"), (10.9, 2.8, "5. Побудова\nзірки\n→ gold"),
                    (13.4, 2.8, "6. Агрегація\nKPI\n→ mart")]:
        ax.add_patch(plt.Circle((x + 0.8, y + 0.7), 0.85, facecolor="#fff", edgecolor="#52606d"))
        ax.text(x + 0.8, y + 0.7, t, ha="center", va="center", fontsize=7.5)
    arrow(ax, (7.7, 3.5), (8.35, 3.5))
    arrow(ax, (10.05, 3.5), (10.85, 3.5))
    arrow(ax, (12.55, 3.5), (13.35, 3.5))
    store(8.3, 5.4, 2.0, "D2 silver.rejected")
    arrow(ax, (9.2, 4.35), (9.2, 5.4), "відхилені")
    store(13.2, 5.4, 2.4, "D3 mart.kpi_*")
    arrow(ax, (14.2, 4.35), (14.2, 5.4))
    ax.add_patch(plt.Circle((11.7, 0.9), 0.85, facecolor="#fff", edgecolor="#52606d"))
    ax.text(11.7, 0.9, "7. Ознаки,\nпрогноз,\nвідхилення", ha="center", va="center", fontsize=7.5)
    arrow(ax, (13.6, 2.85), (12.5, 1.3))
    store(13.4, 0.6, 2.2, "D4 ml.*")
    arrow(ax, (12.55, 0.9), (13.4, 0.9))
    box(ax, 6.0, 0.4, 2.8, 1.0, "Керівник / аналітик\n(Power BI)", color="#fff9db")
    arrow(ax, (10.85, 0.9), (8.8, 0.9), "KPI, прогноз,\nвідхилення")
    save(fig, "fig_dfd")


def oltp_erd():
    fig, ax = canvas(16, 8.6, (13, 7))
    c = LAYER["src"]
    wp = table_box(ax, 0.3, 8.3, 3.3, "workplaces", ["PK id", "   code, name", "   client_name, city", "   updated_at"], c)
    st = table_box(ax, 0.3, 5.6, 3.3, "shift_types", ["PK id", "FK workplace_id", "   code, start_time", "   duration, break", "   agency_rate_czk"], c)
    em = table_box(ax, 4.5, 8.3, 3.4, "employees", ["PK id", "   personal_no", "FK workplace_id", "   hourly_rate_czk", "   is_home, uses_transport", "   hired_on, left_on"], c)
    rh = table_box(ax, 4.5, 3.9, 3.4, "employee_rate_history", ["PK employee_id, valid_from", "   hourly_rate_czk"], c)
    ps = table_box(ax, 8.8, 8.3, 3.2, "planned_shifts", ["PK id", "FK employee_id", "FK shift_type_id", "   work_date"], c)
    wl = table_box(ax, 8.8, 6.3, 3.2, "work_logs", ["PK id", "FK employee_id", "FK shift_type_id", "   clock_in, clock_out", "   status"], c)
    ab = table_box(ax, 12.6, 8.3, 3.2, "absences", ["PK id", "FK employee_id", "   date_from, date_to", "   reason"], c)
    ot = table_box(ax, 12.6, 6.3, 3.2, "overtime", ["PK id", "FK employee_id", "   work_date, minutes", "   cancelled"], c)
    rq = table_box(ax, 8.8, 3.6, 3.2, "requests", ["PK id", "FK employee_id", "   kind, amount_czk", "   status", "   created_at, reviewed_at"], c)
    sa = table_box(ax, 12.6, 4.3, 3.2, "salary_adjustments", ["PK id", "FK employee_id", "   salary_month, kind", "   amount_czk"], c)
    del rh, ab, ot, rq, sa, ps, wl
    arrow(ax, (1.9, wp[1]), (1.9, 5.6), "1 : N", style="-")
    arrow(ax, (3.6, 7.6), (4.5, 7.6), "1 : N", style="-")
    arrow(ax, (6.2, 8.3 - 2.34), (6.2, 3.9), "1 : N", style="-")
    arrow(ax, (7.9, 7.9), (8.8, 7.9), "1 : N", style="-")
    arrow(ax, (7.9, 6.9), (8.8, 5.7), style="-")
    arrow(ax, (7.9, 6.4), (8.8, 3.2), style="-")
    arrow(ax, (3.6, 4.6), (8.8, 5.2), "1 : N", style="-")
    arrow(ax, (12.0, 7.9), (12.6, 7.9), style="-")
    ax.text(12.0, 8.45, "employee_id →", fontsize=7, color="#52606d")
    ax.text(0.3, 0.5, "Усі таблиці подій мають updated_at — за ним працюють водяні знаки інкрементного вилучення.\n"
                      "Кожна подія належить працівнику (employees) і, через тип зміни, об'єкту (workplaces).",
            fontsize=8.5, color="#323f4b")
    save(fig, "fig_oltp_erd")


def star():
    fig, ax = canvas(16, 9.4, (13, 7.7))
    ax.set_ylim(-0.1, 9.4)
    g = LAYER["gold"]
    table_box(ax, 0.2, 9.2, 3.4, "dim_date", ["PK date_key (yyyymmdd)", "   date, year, quarter", "   month, month_name", "   iso_week, day_of_week", "   is_holiday, holiday_name"], "#eceff3")
    table_box(ax, 0.2, 4.6, 3.4, "dim_workplace", ["PK workplace_key", "   workplace_code", "   workplace_name", "   client_name, city"], "#eceff3")
    table_box(ax, 12.4, 9.2, 3.4, "dim_employee (SCD2)", ["PK employee_key", "   employee_id (NK)", "   workplace_code", "   hourly_rate_czk", "   valid_from, valid_to", "   is_current, row_hash"], "#eceff3")
    table_box(ax, 12.4, 4.6, 3.4, "dim_shift_type", ["PK shift_type_key", "FK workplace_key", "   shift_code, start_time", "   paid_hours", "   agency_rate_czk"], "#eceff3")
    table_box(ax, 5.0, 9.2, 6.0, "fact_shift_day  (зерно: працівник × день)", ["FK date_key, employee_key", "FK workplace_key, shift_type_key", "   status, is_future", "   planned_hours, worked_hours, overtime_hours", "   revenue_czk, labor_cost_czk"], g)
    table_box(ax, 5.0, 6.5, 2.9, "fact_trip", ["PK trip_id", "FK date_key, workplace_key", "   delay_min, is_on_time", "   capacity, boarded"], g)
    table_box(ax, 8.1, 6.5, 2.9, "fact_payroll_month", ["FK month_key, employee_key", "FK workplace_key", "   worked_hours, base_pay", "   additions, deductions", "   housing, advances, net"], g)
    table_box(ax, 5.0, 3.9, 2.9, "fact_invoice", ["PK invoice_no", "FK month_key, workplace_key", "   amount_czk, hours", "   is_credit_note"], g)
    table_box(ax, 8.1, 3.9, 2.9, "fact_request", ["PK request_id", "FK date_key, employee_key", "FK workplace_key", "   kind, status", "   decision_hours"], g)
    for a, b in [((3.6, 8.3), (5.0, 8.3)), ((3.6, 3.9), (5.0, 7.6)), ((12.4, 8.3), (11.0, 8.3)), ((12.4, 3.9), (11.0, 7.6)),
                 ((3.6, 7.6), (5.0, 5.9)), ((3.6, 3.6), (5.0, 3.3)), ((3.6, 7.3), (5.0, 3.0)), ((3.6, 4.2), (5.0, 5.6)),
                 ((12.4, 7.6), (11.0, 5.6)), ((12.4, 7.2), (11.0, 3.2))]:
        arrow(ax, a, b, style="-", color="#7b8794")
    mart_rows = ["date, workplace_code, headcount,", "planned / worked / overtime_hours,", "plan_fulfillment, no_show_rate, sick_rate,",
                 "overtime_share, revenue, margin, on_time_rate"]
    table_box(ax, 0.2, 1.75, 7.6, "mart.kpi_workplace_day  (зерно: об'єкт × день)", mart_rows[:2] + [mart_rows[2] + " ..."], LAYER["mart"], line=0.36)
    table_box(ax, 8.2, 1.75, 7.6, "mart.kpi_workplace_month  (зерно: об'єкт × місяць)",
              ["hires, leavers, turnover_rate, revenue,", "labor_cost, margin_rate, invoiced_czk,", "invoice_gap_rate, median_decision_hours ..."], LAYER["mart"], line=0.36)
    save(fig, "fig_star")


def dag():
    fig, ax = canvas(16.8, 5.6, (13.5, 4.5))
    nodes = {"init": (0.2, 2.2), "extract_ops": (1.9, 3.6), "extract_csv": (1.9, 2.2), "extract_api": (1.9, 0.8),
             "silver": (3.7, 2.2), "dq_silver": (5.3, 2.2), "gold": (6.9, 2.2), "mart": (8.5, 2.2),
             "dq_gold": (10.1, 2.2), "features": (11.8, 3.3), "retrain_today": (11.8, 1.0),
             "train": (13.5, 1.0), "predict": (13.5, 3.3), "anomalies": (11.8, 4.6), "export": (15.2, 3.3)}
    w, h = 1.35, 0.7
    colors = {"dq_silver": "#e6fcf5", "dq_gold": "#e6fcf5", "train": LAYER["ml"], "predict": LAYER["ml"],
              "anomalies": LAYER["ml"], "features": LAYER["ml"], "retrain_today": "#fff9db"}
    for name, (x, y) in nodes.items():
        box(ax, x, y, w, h, name, colors.get(name, "#ffffff"), size=7.5)
    edges = [("init", "extract_ops"), ("init", "extract_csv"), ("init", "extract_api"), ("extract_ops", "silver"),
             ("extract_csv", "silver"), ("extract_api", "silver"), ("silver", "dq_silver"), ("dq_silver", "gold"),
             ("gold", "mart"), ("mart", "dq_gold"), ("dq_gold", "features"), ("dq_gold", "anomalies"),
             ("features", "retrain_today"), ("retrain_today", "train"), ("train", "predict"), ("features", "predict"),
             ("predict", "export"), ("anomalies", "export")]
    for a, b in edges:
        (x1, y1), (x2, y2) = nodes[a], nodes[b]
        if x2 > x1:
            arrow(ax, (x1 + w, y1 + h / 2), (x2, y2 + h / 2))
        else:
            arrow(ax, (x1 + w / 2, y1 + (0 if y2 < y1 else h)), (x2 + w / 2, y2 + (h if y2 < y1 else 0)))
    ax.text(0.2, 0.1, "Розклад: щодня о 05:00; retrain_today пропускає навчання, крім понеділка або відсутності моделі champion.",
            fontsize=8, color="#323f4b")
    save(fig, "fig_dag")


def bi_reference():
    fig, ax = canvas(16, 6.2, (13, 5))
    layers = [
        (0.2, "Джерела", "ERP, CRM, облік\nчасу, файли, API", LAYER["src"]),
        (3.0, "Інтеграція", "ETL / ELT,\nоркестрація,\nякість даних", "#ffffff"),
        (5.8, "Зберігання", "DWH / Lakehouse\nшари, дата-марти\n(зірка Кімбалла)", LAYER["gold"]),
        (8.6, "Семантичний шар", "міри, ієрархії,\nправа доступу:\nDAX · LookML ·\nджерела Tableau", LAYER["mart"]),
        (11.4, "Подання", "дашборди, звіти,\nсповіщення,\nвбудована\nаналітика", "#fff9db"),
    ]
    for x, title, text, color in layers:
        ax.text(x + 1.2, 5.8, title, ha="center", fontsize=9.5, fontweight="bold")
        box(ax, x, 2.6, 2.4, 2.9, text, color)
    for x in (2.6, 5.4, 8.2, 11.0):
        arrow(ax, (x, 4.05), (x + 0.4, 4.05))
    box(ax, 14.0, 2.6, 1.8, 2.9, "Рішення\nкерівника", "#ffe3e3", bold=True)
    arrow(ax, (13.8, 4.05), (14.0, 4.05))
    box(ax, 5.8, 0.3, 5.2, 1.5, "ML-контур: ознаки (Feature Store) → навчання →\nреєстр моделей → прогнози й відхилення назад у сховище", LAYER["ml"])
    arrow(ax, (7.0, 2.6), (7.0, 1.8), style="<|-|>")
    arrow(ax, (14.9, 2.6), (12.0, 0.9), "зворотний зв'язок: нові цілі KPI", dashed=True, rad=-0.2)
    save(fig, "fig_bi_reference")


# ------------------------------------------------------------------ charts
def month_axis(ax):
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 7)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m.%Y"))
    ax.grid(axis="y", color="#e4e7eb", linewidth=0.8)


def charts():
    dwh = dwh_engine()
    month = read_sql(dwh, "SELECT * FROM mart.v_kpi_company_month ORDER BY month").astype({"month": "datetime64[ns]"})
    for c in month.columns[1:]:
        month[c] = month[c].astype(float)
    full = month[month["month"] < month["month"].max()]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(full["month"] - pd.Timedelta(days=7), full["planned_hours"] / 1e3, width=13, color=GREY, label="План, тис. год")
    ax.bar(full["month"] + pd.Timedelta(days=7), full["worked_hours"] / 1e3, width=13, color=BLUE, label="Факт, тис. год")
    ax2 = ax.twinx()
    ax2.plot(full["month"], full["plan_fulfillment"] * 100, color=ORANGE, marker="o", ms=3, label="Виконання плану, %")
    ax2.set_ylim(85, 102)
    ax2.spines["top"].set_visible(False)
    month_axis(ax)
    fig.legend(loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 1.02))
    save(fig, "fig_kpi_hours")

    places = read_sql(dwh, """
        SELECT workplace_code, extract(year FROM month)::int AS year,
               sum(gross_margin_czk) / sum(revenue_czk) AS margin
        FROM mart.kpi_workplace_month GROUP BY 1, 2 ORDER BY 1, 2""")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4), gridspec_kw={"width_ratios": [1.6, 1]})
    a1.plot(full["month"], full["margin_rate"] * 100, color=BLUE, marker="o", ms=3)
    a1.set_title("Валова маржа агентства, % виручки", fontsize=10)
    month_axis(a1)
    pivot = places.pivot(index="workplace_code", columns="year", values="margin").astype(float) * 100
    pivot.plot.barh(ax=a2, color=["#a5d8ff", "#4dabf7", "#1864ab"], width=0.8)
    a2.set_title("Маржа об'єктів за роками, %", fontsize=10)
    a2.set_ylabel("")
    a2.legend(frameon=False, fontsize=8)
    save(fig, "fig_margin")
    STATS["margin_by_year"] = {str(k): round(float(v), 4) for k, v in
                               places.groupby("year").apply(lambda g: g["margin"].astype(float).mean()).items()}
    STATS["margin_first"] = round(float(full["margin_rate"].iloc[0]), 4)
    STATS["margin_last"] = round(float(full["margin_rate"].iloc[-1]), 4)

    absence = read_sql(dwh, """
        SELECT date_trunc('month', date)::date AS month,
               sum(sick_rate * planned_shifts) / sum(planned_shifts) AS sick,
               sum(no_show_rate * planned_shifts) / sum(planned_shifts) AS no_show
        FROM mart.kpi_workplace_day WHERE NOT is_future GROUP BY 1 ORDER BY 1""").astype({"month": "datetime64[ns]"})
    fig, ax = plt.subplots(figsize=(10, 3.6))
    ax.plot(absence["month"], absence["sick"].astype(float) * 100, color=RED, marker="o", ms=3, label="Лікарняні")
    ax.plot(absence["month"], absence["no_show"].astype(float) * 100, color=GREY, marker="o", ms=3, label="Невиходи")
    ax.set_ylabel("% запланованих змін")
    month_axis(ax)
    ax.legend(frameon=False)
    save(fig, "fig_absence")

    transport = read_sql(dwh, """
        SELECT workplace_code, avg(seat_occupancy) AS occupancy,
               sum(on_time_rate * (trips - trips_cancelled)) / sum(trips - trips_cancelled) AS on_time
        FROM mart.kpi_workplace_day WHERE NOT is_future AND trips > 0 GROUP BY 1 ORDER BY 2""")
    fig, ax = plt.subplots(figsize=(8, 3.4))
    bars = ax.barh(transport["workplace_code"], transport["occupancy"].astype(float) * 100, color=BLUE)
    ax.bar_label(bars, fmt="%.0f %%", padding=3, fontsize=8)
    ax.set_xlabel("Середня заповненість автобусів, %")
    ax.set_xlim(0, 100)
    save(fig, "fig_transport")
    STATS["transport"] = {r.workplace_code: {"occupancy": round(float(r.occupancy), 3), "on_time": round(float(r.on_time), 3)}
                          for r in transport.itertuples()}

    # ML
    backtest = read_sql(dwh, "SELECT model, fold, cutoff, mae, rmse, mape, wape FROM ml.backtest ORDER BY fold")
    fig, ax = plt.subplots(figsize=(9, 3.6))
    names = {"seasonal_naive": ("Сезонний наївний", GREY), "holt_winters": ("Холт-Вінтерс", GREEN),
             "gradient_boosting": ("Градієнтний бустинг", ORANGE)}
    for model, (label, color) in names.items():
        part = backtest[backtest["model"] == model]
        ax.plot(part["fold"], part["wape"] * 100, marker="o", color=color, label=label)
    ax.set_xlabel("Блок бектесту (28 днів)")
    ax.set_ylabel("WAPE, %")
    ax.grid(axis="y", color="#e4e7eb")
    ax.legend(frameon=False)
    save(fig, "fig_backtest")
    summary = backtest.groupby("model")[["mae", "rmse", "mape", "wape"]].mean()
    STATS["backtest"] = {m: {k: round(float(v), 4) for k, v in row.items()} for m, row in summary.iterrows()}
    STATS["backtest_folds"] = int(backtest["fold"].max())
    STATS["backtest_first_cutoff"] = str(backtest["cutoff"].min())

    history = read_sql(dwh, """SELECT date, sum(worked_hours) AS worked, sum(planned_hours) AS planned
                               FROM mart.kpi_workplace_day WHERE NOT is_future GROUP BY 1 ORDER BY 1""")
    forecast = read_sql(dwh, """SELECT date, sum(predicted_hours) AS p, sum(lower_hours) AS lo, sum(upper_hours) AS hi,
                                       sum(planned_hours) AS planned FROM ml.forecast GROUP BY 1 ORDER BY 1""")
    history["date"] = pd.to_datetime(history["date"])
    forecast["date"] = pd.to_datetime(forecast["date"])
    recent = history[history["date"] > history["date"].max() - pd.Timedelta(days=90)]
    fig, ax = plt.subplots(figsize=(10, 3.8))
    ax.plot(recent["date"], recent["worked"].astype(float), color=BLUE, label="Факт")
    ax.fill_between(forecast["date"], forecast["lo"].astype(float), forecast["hi"].astype(float), color=ORANGE, alpha=0.18,
                    label="Інтервал 10–90 %")
    ax.plot(forecast["date"], forecast["p"].astype(float), color=ORANGE, linestyle="--", label="Прогноз")
    ax.plot(forecast["date"], forecast["planned"].astype(float), color=GREY, linestyle=":", label="План")
    ax.axvline(history["date"].max(), color="#7b8794", linewidth=0.8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax.set_ylabel("Години на день, усі об'єкти")
    ax.legend(frameon=False, ncol=4, loc="lower left")
    ax.grid(axis="y", color="#e4e7eb")
    save(fig, "fig_forecast")
    STATS["forecast_total_hours"] = round(float(forecast["p"].sum()), 0)
    STATS["forecast_planned_hours"] = round(float(forecast["planned"].sum()), 0)
    STATS["forecast_days"] = int(forecast["date"].nunique())

    # Anomalies of one workplace: no-shows with the found and the true incidents.
    days = read_sql(dwh, "SELECT date, no_show_rate FROM mart.kpi_workplace_day WHERE workplace_code = 'ELE-JIH' AND NOT is_future ORDER BY date")
    days["date"] = pd.to_datetime(days["date"])
    found = read_sql(dwh, "SELECT date, value FROM ml.anomalies WHERE workplace_code = 'ELE-JIH' AND kpi = 'no_show_rate'")
    found["date"] = pd.to_datetime(found["date"])
    truth = pd.read_csv(settings().ground_truth_dir / "incidents.csv", parse_dates=["date"])
    truth = truth[(truth["workplace_code"] == "ELE-JIH") & (truth["kind"] == "no_show_spike")]
    fig, ax = plt.subplots(figsize=(10, 3.6))
    ax.plot(days["date"], days["no_show_rate"].astype(float) * 100, color=GREY, linewidth=0.7, label="Невиходи, % змін")
    for d in truth["date"]:
        ax.axvline(d, color=GREEN, alpha=0.35, linewidth=3)
    ax.scatter(found["date"], found["value"].astype(float) * 100, color=RED, zorder=3, s=22, label="Виявлене відхилення")
    ax.plot([], [], color=GREEN, alpha=0.35, linewidth=3, label="Розмічений інцидент")
    month_axis(ax)
    ax.legend(frameon=False, loc="upper left")
    save(fig, "fig_anomalies")

    evaluation = read_sql(dwh, "SELECT * FROM ml.anomaly_evaluation ORDER BY period, method, threshold")
    curve = evaluation[evaluation["period"] == "calibration"]
    fig, ax = plt.subplots(figsize=(8, 3.4))
    for col, label, color in (("precision", "Precision", BLUE), ("recall", "Recall", GREEN), ("f1", "F1", ORANGE)):
        ax.plot(curve["threshold"], curve[col], marker="o", ms=3, color=color, label=label)
    chosen = float(evaluation.loc[evaluation["period"] == "test", "threshold"].dropna().iloc[0])
    ax.axvline(chosen, color="#7b8794", linestyle="--", linewidth=0.8)
    ax.set_xlabel("Поріг робастного z-score")
    ax.set_ylim(0, 1.05)
    ax.legend(frameon=False)
    ax.grid(axis="y", color="#e4e7eb")
    save(fig, "fig_calibration")
    STATS["anomaly_threshold"] = chosen
    STATS["anomaly_test"] = {r.method: {"tp": int(r.true_positives), "fp": int(r.false_positives), "fn": int(r.false_negatives),
                                        "precision": round(float(r.precision), 3), "recall": round(float(r.recall), 3),
                                        "f1": round(float(r.f1), 3)}
                             for r in evaluation[evaluation["period"] == "test"].itertuples()}
    STATS["anomaly_rows"] = int(read_sql(dwh, "SELECT count(*) AS n FROM ml.anomalies")["n"][0])


def importance():
    from sklearn.inspection import permutation_importance

    import mlflow

    from bikpi.ml.features import FEATURES, read_features
    from bikpi.ml.forecast import ALIAS, MODEL_NAME, matrix

    mlflow.set_tracking_uri(settings().mlflow_tracking_uri)
    model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{ALIAS}")
    features = read_features()
    features = features[~features["is_future"]].dropna(subset=["fulfillment_lag28"]).tail(6 * 180)
    x = matrix(features)
    result = permutation_importance(model, x, features["fulfillment"].astype(float), n_repeats=8, random_state=42,
                                    scoring="neg_mean_absolute_error")
    order = np.argsort(result.importances_mean)
    names = np.array(x.columns)[order]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.barh(names, result.importances_mean[order] * 1000, xerr=result.importances_std[order] * 1000, color=BLUE)
    ax.set_xlabel("Зростання MAE частки виконання плану при перестановці ознаки, ×10⁻³")
    save(fig, "fig_importance")
    STATS["importance_top"] = list(names[::-1][:5])
    del FEATURES


def numbers():
    dwh, src = dwh_engine(), source_engine()
    q = lambda e, s: read_sql(e, s).iloc[0].to_dict()  # noqa: E731
    STATS["source"] = q(src, """SELECT (SELECT count(*) FROM employees) AS employees,
        (SELECT count(*) FROM planned_shifts) AS planned_shifts, (SELECT count(*) FROM work_logs) AS work_logs,
        (SELECT count(*) FROM absences) AS absences, (SELECT count(*) FROM overtime) AS overtime,
        (SELECT count(*) FROM requests) AS requests, (SELECT count(*) FROM salary_adjustments) AS adjustments""")
    STATS["files"] = {"invoice_files": len(list((settings().raw_dir / "accounting").glob("*.csv"))),
                      "trips_json_mb": round((settings().raw_dir / "transport" / "trips.json").stat().st_size / 2**20, 1)}
    STATS["layers"] = q(dwh, """SELECT
        (SELECT count(*) FROM bronze.ops_work_logs) AS bronze_work_logs, (SELECT count(*) FROM bronze.api_trips) AS bronze_trips,
        (SELECT count(*) FROM bronze.acc_invoices) AS bronze_invoices, (SELECT count(*) FROM silver.attendance_day) AS attendance_day,
        (SELECT count(*) FROM silver.trip) AS trips, (SELECT count(*) FROM silver.invoice) AS invoices,
        (SELECT count(*) FROM gold.dim_employee) AS employee_versions, (SELECT count(DISTINCT employee_id) FROM gold.dim_employee) AS employees,
        (SELECT count(*) FROM gold.fact_shift_day) AS fact_shift_day, (SELECT count(*) FROM gold.fact_payroll_month) AS payroll,
        (SELECT count(*) FROM gold.dim_date) AS dim_date, (SELECT count(*) FROM mart.kpi_workplace_day) AS mart_day,
        (SELECT count(*) FROM mart.kpi_workplace_month) AS mart_month, (SELECT count(*) FROM ml.features_workplace_day) AS features""")
    STATS["rejected"] = read_sql(dwh, """SELECT split_part(source, '.', 1) AS source, reason, count(*) AS n
        FROM silver.rejected GROUP BY 1, 2 ORDER BY 1, 2""").to_dict("records")
    STATS["status"] = read_sql(dwh, "SELECT status, count(*) AS n FROM silver.attendance_day GROUP BY 1 ORDER BY 2 DESC").to_dict("records")
    STATS["dq"] = read_sql(dwh, """SELECT layer, table_name, count(*) AS expectations, count(*) FILTER (WHERE success) AS passed
        FROM dq.results
        WHERE batch_id IN ((SELECT max(batch_id) FROM dq.results WHERE layer = 'silver'),
                           (SELECT max(batch_id) FROM dq.results WHERE layer IN ('gold', 'mart')))
        GROUP BY 1, 2 ORDER BY 1, 2""").to_dict("records")
    STATS["batches"] = read_sql(dwh, """SELECT DISTINCT ON (step) step, rows_loaded,
        round(extract(epoch FROM finished_at - started_at)::numeric, 1) AS seconds
        FROM meta.batches WHERE status = 'success' ORDER BY step, batch_id""").to_dict("records")
    STATS["kpi_totals"] = q(dwh, """SELECT sum(worked_hours) AS worked_hours, sum(worked_hours) / sum(planned_hours) AS fulfillment,
        sum(revenue_czk) AS revenue, sum(gross_margin_czk) / sum(revenue_czk) AS margin,
        sum(no_show_rate * planned_shifts) / sum(planned_shifts) AS no_show, sum(sick_rate * planned_shifts) / sum(planned_shifts) AS sick,
        sum(overtime_hours) / sum(worked_hours) AS overtime, sum(trips) AS trips
        FROM mart.kpi_workplace_day WHERE NOT is_future""")
    STATS["invoice_gap"] = q(dwh, """SELECT avg(abs(invoice_gap_rate)) AS mean_abs, max(abs(invoice_gap_rate)) AS max_abs
        FROM mart.kpi_workplace_month WHERE invoiced_czk IS NOT NULL""")
    STATS["turnover"] = q(dwh, "SELECT avg(turnover_rate) AS monthly FROM mart.v_kpi_company_month")
    STATS["incidents"] = pd.read_csv(settings().ground_truth_dir / "incidents.csv")["kind"].value_counts().to_dict()


if __name__ == "__main__":
    for step in (bi_reference, architecture, dfd, oltp_erd, star, dag, charts, importance, numbers):
        step()
        print("done", step.__name__)
    (OUT / "stats.json").write_text(json.dumps(STATS, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
