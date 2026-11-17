"""Python dashboard of the KPI marts (Plotly Dash): the same pages as the Power BI report.

    python dashboard/app.py        ->  http://localhost:8050
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bikpi.config import dwh_engine  # noqa: E402
from bikpi.db import read_sql  # noqa: E402

ALL = "Усі об'єкти"
COLORS = {"fact": "#2a6fdb", "plan": "#9aa5b1", "forecast": "#e8590c", "band": "rgba(232,89,12,0.18)",
          "bad": "#c92a2a"}
TEMPLATE = "plotly_white"
TEXT_COLUMNS = {"workplace_code", "kpi", "method", "severity", "model", "run_id", "model_name", "model_version",
                "period"}


def load() -> dict[str, pd.DataFrame]:
    engine = dwh_engine()
    data = {
        "day": read_sql(engine, "SELECT * FROM mart.kpi_workplace_day"),
        "month": read_sql(engine, "SELECT * FROM mart.kpi_workplace_month"),
        "forecast": read_sql(engine, "SELECT * FROM ml.forecast"),
        "anomalies": read_sql(engine, "SELECT * FROM ml.anomalies"),
        "evaluation": read_sql(engine, "SELECT * FROM ml.anomaly_evaluation"),
        "backtest": read_sql(engine, "SELECT model, avg(wape) AS wape, avg(mape) AS mape, avg(mae) AS mae "
                                     "FROM ml.backtest GROUP BY model ORDER BY wape"),
    }
    for name in ("day", "forecast", "anomalies"):
        data[name]["date"] = pd.to_datetime(data[name]["date"])
    data["month"]["month"] = pd.to_datetime(data["month"]["month"])
    # numeric columns arrive as Decimal objects
    for frame in data.values():
        for column in frame.columns:
            if frame[column].dtype == object and column not in TEXT_COLUMNS:
                try:
                    frame[column] = pd.to_numeric(frame[column])
                except (TypeError, ValueError):
                    pass
    data["evaluation"] = data["evaluation"][data["evaluation"]["period"] == "test"]
    return data


DATA = load()
WORKPLACES = sorted(DATA["day"]["workplace_code"].unique())


def card(title: str, value: str, note: str = "") -> html.Div:
    return html.Div([html.Div(title, className="t"), html.Div(value, className="v"), html.Div(note, className="n")],
                    className="card")


def money(value: float) -> str:
    return f"{value / 1e6:,.1f} млн Kč".replace(",", " ")


def pct(value: float) -> str:
    return "—" if pd.isna(value) else f"{value * 100:.1f} %"


def pick(frame: pd.DataFrame, workplace: str) -> pd.DataFrame:
    return frame if workplace == ALL else frame[frame["workplace_code"] == workplace]


app = Dash(__name__, title="BI KPI агентства")
app.index_string = app.index_string.replace("</head>", """<style>
body{font-family:Segoe UI,Roboto,sans-serif;margin:0;background:#f5f6f8;color:#1f2933}
.wrap{max-width:1280px;margin:0 auto;padding:16px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:12px 0}
.card{background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.card .t{font-size:12px;color:#616e7c}.card .v{font-size:24px;font-weight:600;margin-top:4px}
.card .n{font-size:12px;color:#7b8794}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(480px,1fr));gap:12px}
.panel{background:#fff;border-radius:8px;padding:8px}
</style></head>""")

app.layout = html.Div(className="wrap", children=[
    html.H2("Візуальна аналітика KPI кадрового агентства"),
    html.Div(style={"display": "flex", "gap": "12px", "flexWrap": "wrap"}, children=[
        dcc.Dropdown([ALL, *WORKPLACES], ALL, id="workplace", clearable=False, style={"width": "260px"}),
        dcc.DatePickerRange(id="period", display_format="DD.MM.YYYY", first_day_of_week=1,
                            start_date=(DATA["day"]["date"].max() - pd.DateOffset(months=12)).date(),
                            end_date=DATA["day"]["date"].max().date()),
    ]),
    dcc.Tabs(id="tab", value="overview", children=[
        dcc.Tab(label="Огляд", value="overview"),
        dcc.Tab(label="Персонал", value="people"),
        dcc.Tab(label="Фінанси", value="finance"),
        dcc.Tab(label="Транспорт", value="transport"),
        dcc.Tab(label="Прогноз і відхилення", value="ml"),
    ]),
    html.Div(id="page"),
])


def chart(figure: go.Figure) -> html.Div:
    figure.update_layout(template=TEMPLATE, margin=dict(l=40, r=16, t=48, b=32), height=340,
                         legend=dict(orientation="h", y=-0.15))
    return html.Div(dcc.Graph(figure=figure, config={"displaylogo": False}), className="panel")


@app.callback(Output("page", "children"), Input("tab", "value"), Input("workplace", "value"),
              Input("period", "start_date"), Input("period", "end_date"))
def page(tab: str, workplace: str, start: str, end: str):
    days = pick(DATA["day"], workplace)
    past = days[~days["is_future"] & days["date"].between(start, end)]
    months = pick(DATA["month"], workplace)
    months = months[months["month"].between(pd.Timestamp(start).to_period("M").start_time, end)]
    daily = past.groupby("date").sum(numeric_only=True).reset_index()
    weights = past["planned_shifts"].sum()

    if tab == "overview":
        monthly = months.groupby("month").sum(numeric_only=True).reset_index()
        cards = html.Div(className="cards", children=[
            card("Відпрацьовано годин", f"{past['worked_hours'].sum():,.0f}".replace(",", " ")),
            card("Виконання плану", pct(past["worked_hours"].sum() / past["planned_hours"].sum())),
            card("Виручка", money(past["revenue_czk"].sum())),
            card("Валова маржа", pct(past["gross_margin_czk"].sum() / past["revenue_czk"].sum())),
            card("Невиходи", pct((past["no_show_rate"] * past["planned_shifts"]).sum() / weights)),
            card("Рейси вчасно", pct((past["on_time_rate"] * past["trips"]).sum() / past["trips"].sum())),
        ])
        hours = go.Figure([go.Bar(x=monthly["month"], y=monthly["planned_hours"], name="План", marker_color=COLORS["plan"]),
                           go.Bar(x=monthly["month"], y=monthly["worked_hours"], name="Факт", marker_color=COLORS["fact"])])
        hours.update_layout(title="Години: план і факт за місяцями", barmode="group")
        margin = go.Figure([go.Scatter(x=monthly["month"], y=monthly["gross_margin_czk"] / monthly["revenue_czk"],
                                       mode="lines+markers", name="Маржа", line_color=COLORS["fact"])])
        margin.update_layout(title="Валова маржа, частка виручки", yaxis_tickformat=".0%")
        return [cards, html.Div(className="grid", children=[chart(hours), chart(margin)])]

    if tab == "people":
        monthly = months.groupby("month").sum(numeric_only=True).reset_index()
        cards = html.Div(className="cards", children=[
            card("Середня чисельність", f"{months.groupby('month')['avg_headcount'].sum().mean():,.0f}"),
            card("Прийнято", f"{months['hires'].sum():,.0f}"),
            card("Звільнилось", f"{months['leavers'].sum():,.0f}"),
            card("Плинність / міс.", pct(monthly["leavers"].sum() / monthly["avg_headcount"].sum())),
            card("Лікарняні", pct((past["sick_rate"] * past["planned_shifts"]).sum() / weights)),
            card("Понаднормові", pct(past["overtime_hours"].sum() / past["worked_hours"].sum())),
        ])
        rates = past.assign(month=past["date"].dt.to_period("M").dt.start_time).groupby("month").apply(
            lambda g: pd.Series({
                "Лікарняні": (g["sick_rate"] * g["planned_shifts"]).sum() / g["planned_shifts"].sum(),
                "Невиходи": (g["no_show_rate"] * g["planned_shifts"]).sum() / g["planned_shifts"].sum(),
            }), include_groups=False).reset_index()
        absence = px.line(rates, x="month", y=["Лікарняні", "Невиходи"], markers=True,
                          title="Відсутність, частка запланованих змін")
        absence.update_layout(yaxis_tickformat=".1%", yaxis_title=None, xaxis_title=None, legend_title=None)
        flow = go.Figure([go.Bar(x=monthly["month"], y=monthly["hires"], name="Прийнято", marker_color=COLORS["fact"]),
                          go.Bar(x=monthly["month"], y=-monthly["leavers"], name="Звільнилось", marker_color=COLORS["bad"])])
        flow.update_layout(title="Рух персоналу", barmode="relative")
        return [cards, html.Div(className="grid", children=[chart(absence), chart(flow)])]

    if tab == "finance":
        by_place = past.groupby("workplace_code")[["revenue_czk", "labor_cost_czk", "gross_margin_czk"]].sum()
        by_place["rate"] = by_place["gross_margin_czk"] / by_place["revenue_czk"]
        cards = html.Div(className="cards", children=[
            card("Виручка", money(past["revenue_czk"].sum())),
            card("Витрати на персонал", money(past["labor_cost_czk"].sum())),
            card("Валова маржа", money(past["gross_margin_czk"].sum())),
            card("Розбіжність з рахунками", pct((months["invoiced_czk"].sum() - months.loc[months["invoiced_czk"].notna(), "revenue_czk"].sum())
                                               / months.loc[months["invoiced_czk"].notna(), "revenue_czk"].sum())),
        ])
        places = px.bar(by_place.reset_index().sort_values("rate"), x="rate", y="workplace_code", orientation="h",
                        title="Маржинальність об'єктів", text_auto=".1%")
        places.update_layout(xaxis_tickformat=".0%", xaxis_title=None, yaxis_title=None)
        monthly = months.groupby("month").sum(numeric_only=True).reset_index()
        flows = go.Figure([go.Bar(x=monthly["month"], y=monthly["revenue_czk"], name="Виручка", marker_color=COLORS["fact"]),
                           go.Bar(x=monthly["month"], y=monthly["labor_cost_czk"], name="Витрати", marker_color=COLORS["plan"]),
                           go.Scatter(x=monthly["month"], y=monthly["invoiced_czk"], name="Виставлено рахунків",
                                      mode="markers", marker=dict(color=COLORS["forecast"], size=8))])
        flows.update_layout(title="Виручка, витрати, рахунки", barmode="group")
        return [cards, html.Div(className="grid", children=[chart(flows), chart(places)])]

    if tab == "transport":
        trips = past[past["trips"] > 0]
        cards = html.Div(className="cards", children=[
            card("Рейсів", f"{trips['trips'].sum():,.0f}".replace(",", " ")),
            card("Скасовано", f"{trips['trips_cancelled'].sum():,.0f}"),
            card("Вчасно (≤ 5 хв)", pct((trips["on_time_rate"] * trips["trips"]).sum() / trips["trips"].sum())),
            card("Середня затримка", f"{(trips['avg_delay_min'] * trips['trips']).sum() / trips['trips'].sum():.1f} хв"),
            card("Заповненість автобусів", pct(trips["seat_occupancy"].mean())),
        ])
        weekly = trips.set_index("date").resample("W").apply(
            lambda g: (g["on_time_rate"] * g["trips"]).sum() / max(g["trips"].sum(), 1)).rename("Вчасно").reset_index()
        punctual = px.line(weekly, x="date", y="Вчасно", title="Пунктуальність рейсів за тижнями")
        punctual.update_layout(yaxis_tickformat=".0%", xaxis_title=None)
        occupancy = px.box(trips, x="workplace_code", y="seat_occupancy", title="Заповненість автобусів по об'єктах")
        occupancy.update_layout(yaxis_tickformat=".0%", xaxis_title=None, yaxis_title=None)
        return [cards, html.Div(className="grid", children=[chart(punctual), chart(occupancy)])]

    # Forecast and anomalies
    forecast = pick(DATA["forecast"], workplace).groupby("date").sum(numeric_only=True).reset_index()
    history = days[~days["is_future"]].groupby("date").sum(numeric_only=True).reset_index().tail(90)
    figure = go.Figure([
        go.Scatter(x=history["date"], y=history["worked_hours"], name="Факт", line_color=COLORS["fact"]),
        go.Scatter(x=forecast["date"], y=forecast["upper_hours"], line_width=0, showlegend=False, hoverinfo="skip"),
        go.Scatter(x=forecast["date"], y=forecast["lower_hours"], fill="tonexty", fillcolor=COLORS["band"],
                   line_width=0, name="Інтервал 10–90 %"),
        go.Scatter(x=forecast["date"], y=forecast["predicted_hours"], name="Прогноз", line=dict(color=COLORS["forecast"], dash="dash")),
        go.Scatter(x=forecast["date"], y=forecast["planned_hours"], name="План", line=dict(color=COLORS["plan"], dash="dot")),
    ])
    figure.update_layout(title="Відпрацьовані години: факт і прогноз на 28 днів")
    anomalies = pick(DATA["anomalies"], workplace)
    anomalies = anomalies[anomalies["date"].between(start, end)]
    found = px.scatter(anomalies, x="date", y="workplace_code", color="kpi", symbol="method",
                       title="Виявлені відхилення KPI", hover_data=["value", "expected", "robust_z"])
    found.update_layout(xaxis_title=None, yaxis_title=None, legend_title=None)
    evaluation = DATA["evaluation"]
    cards = html.Div(className="cards", children=[
        *[card(f"WAPE: {row.model}", pct(row.wape), f"MAPE {pct(row.mape)}") for row in DATA["backtest"].itertuples()],
        *[card(f"F1: {row.method}", f"{row.f1:.2f}", f"precision {row.precision:.2f}, recall {row.recall:.2f}")
          for row in evaluation.itertuples()],
    ])
    return [cards, html.Div(className="grid", children=[chart(figure), chart(found)])]


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
