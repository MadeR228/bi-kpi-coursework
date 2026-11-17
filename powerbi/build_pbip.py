"""Writes the Power BI project (PBIP) of the KPI report: semantic model + report pages.

    python powerbi/build_pbip.py      ->  powerbi/BC_KPI.pbip (open in Power BI Desktop)

The semantic model (TMSL, model.bim) imports the marts from the warehouse with
the PostgreSQL connector of Power BI; the report (PBIR-legacy report.json) has
five pages. Both are generated from the definitions below, so a change of the
marts means editing this file and running it again.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "BC_KPI"

# ------------------------------------------------------------------- tables
# table -> (schema.object, [(column, type, format)], hidden columns)
D, S, I, F, B = "dateTime", "string", "int64", "double", "boolean"
PCT, MONEY, NUM, DATE = "0.0%", "#,0", "#,0.0", "dd.MM.yyyy"

TABLES = {
    "Календар": ("gold.dim_date", [
        ("date_key", I, None), ("date", D, DATE), ("year", I, "0"), ("quarter", I, "0"), ("month", I, "0"),
        ("month_name", S, None), ("month_start", D, "MMM yyyy"), ("iso_week", I, "0"), ("day_of_week", I, "0"),
        ("day_name", S, None), ("is_weekend", B, None), ("is_holiday", B, None), ("holiday_name", S, None),
    ]),
    "Об'єкти": ("gold.dim_workplace", [
        ("workplace_key", I, None), ("workplace_code", S, None), ("workplace_name", S, None),
        ("client_name", S, None), ("city", S, None), ("is_active", B, None),
    ]),
    "KPI день": ("mart.kpi_workplace_day", [
        ("date", D, DATE), ("workplace_code", S, None), ("is_future", B, None), ("headcount", I, MONEY),
        ("planned_shifts", I, MONEY), ("worked_shifts", I, MONEY), ("planned_hours", F, MONEY),
        ("worked_hours", F, MONEY), ("overtime_hours", F, MONEY), ("plan_fulfillment", F, PCT),
        ("attendance_rate", F, PCT), ("no_show_rate", F, PCT), ("sick_rate", F, PCT), ("unrecorded_rate", F, PCT),
        ("overtime_share", F, PCT), ("revenue_czk", F, MONEY), ("labor_cost_czk", F, MONEY),
        ("gross_margin_czk", F, MONEY), ("trips", I, MONEY), ("trips_cancelled", I, MONEY),
        ("on_time_rate", F, PCT), ("avg_delay_min", F, NUM), ("seat_occupancy", F, PCT), ("boarding_rate", F, PCT),
    ]),
    "KPI місяць": ("mart.kpi_workplace_month", [
        ("month", D, "MMM yyyy"), ("workplace_code", S, None), ("avg_headcount", F, MONEY), ("hires", I, MONEY),
        ("leavers", I, MONEY), ("turnover_rate", F, PCT), ("planned_hours", F, MONEY), ("worked_hours", F, MONEY),
        ("overtime_hours", F, MONEY), ("plan_fulfillment", F, PCT), ("no_show_rate", F, PCT), ("sick_rate", F, PCT),
        ("revenue_czk", F, MONEY), ("labor_cost_czk", F, MONEY), ("gross_margin_czk", F, MONEY),
        ("margin_rate", F, PCT), ("invoiced_czk", F, MONEY), ("invoice_gap_rate", F, PCT), ("on_time_rate", F, PCT),
        ("avg_delay_min", F, NUM), ("seat_occupancy", F, PCT), ("requests", I, MONEY),
        ("median_decision_hours", F, NUM), ("advances_czk", F, MONEY),
    ]),
    "Прогноз": ("ml.forecast", [
        ("run_id", S, None), ("model_name", S, None), ("model_version", S, None), ("workplace_code", S, None),
        ("date", D, DATE), ("planned_hours", F, MONEY), ("predicted_hours", F, MONEY), ("lower_hours", F, MONEY),
        ("upper_hours", F, MONEY), ("created_at", D, "dd.MM.yyyy HH:mm"),
    ]),
    "Відхилення": ("ml.anomalies", [
        ("workplace_code", S, None), ("date", D, DATE), ("kpi", S, None), ("value", F, "0.000"),
        ("expected", F, "0.000"), ("robust_z", F, "0.0"), ("iforest_score", F, "0.000"), ("method", S, None),
        ("severity", S, None), ("detected_at", D, "dd.MM.yyyy HH:mm"),
    ]),
    "Якість виявлення": ("ml.anomaly_evaluation", [
        ("run_id", S, None), ("period", S, None), ("method", S, None), ("threshold", F, "0.0"),
        ("true_positives", I, "0"), ("false_positives", I, "0"), ("false_negatives", I, "0"),
        ("precision", F, PCT), ("recall", F, PCT), ("f1", F, "0.000"), ("created_at", D, "dd.MM.yyyy HH:mm"),
    ]),
    "Бектест": ("ml.backtest", [
        ("run_id", S, None), ("model", S, None), ("fold", I, "0"), ("cutoff", D, DATE), ("mae", F, NUM),
        ("rmse", F, NUM), ("mape", F, PCT), ("wape", F, PCT), ("created_at", D, "dd.MM.yyyy HH:mm"),
    ]),
}

RELATIONSHIPS = [
    ("KPI день", "date", "Календар", "date"),
    ("KPI день", "workplace_code", "Об'єкти", "workplace_code"),
    ("KPI місяць", "month", "Календар", "date"),
    ("KPI місяць", "workplace_code", "Об'єкти", "workplace_code"),
    ("Прогноз", "date", "Календар", "date"),
    ("Прогноз", "workplace_code", "Об'єкти", "workplace_code"),
    ("Відхилення", "date", "Календар", "date"),
    ("Відхилення", "workplace_code", "Об'єкти", "workplace_code"),
]

# ----------------------------------------------------------------- measures
# table -> [(name, DAX, format)]
PAST = "'KPI день'[is_future] = FALSE()"
MEASURES = {
    "KPI день": [
        ("Години план", f"CALCULATE(SUM('KPI день'[planned_hours]), {PAST})", MONEY),
        ("Години факт", "SUM('KPI день'[worked_hours])", MONEY),
        ("Виконання плану", "DIVIDE([Години факт], [Години план])", PCT),
        ("Заплановані зміни", f"CALCULATE(SUM('KPI день'[planned_shifts]), {PAST})", MONEY),
        ("Невиходи", "DIVIDE(SUMX('KPI день', 'KPI день'[no_show_rate] * 'KPI день'[planned_shifts]), [Заплановані зміни])", PCT),
        ("Лікарняні", "DIVIDE(SUMX('KPI день', 'KPI день'[sick_rate] * 'KPI день'[planned_shifts]), [Заплановані зміни])", PCT),
        ("Понаднормові", "DIVIDE(SUM('KPI день'[overtime_hours]), [Години факт])", PCT),
        ("Середня чисельність", "AVERAGEX(VALUES('Календар'[date]), CALCULATE(SUM('KPI день'[headcount])))", MONEY),
        ("Виручка", "SUM('KPI день'[revenue_czk])", MONEY),
        ("Витрати на персонал", "SUM('KPI день'[labor_cost_czk])", MONEY),
        ("Валова маржа", "[Виручка] - [Витрати на персонал]", MONEY),
        ("Маржа %", "DIVIDE([Валова маржа], [Виручка])", PCT),
        ("Маржа % мин. рік", "CALCULATE([Маржа %], SAMEPERIODLASTYEAR('Календар'[date]))", PCT),
        ("Виручка зміна р/р", "VAR ly = CALCULATE([Виручка], SAMEPERIODLASTYEAR('Календар'[date])) "
                              "RETURN DIVIDE([Виручка] - ly, ly)", PCT),
        ("Рейси", "SUM('KPI день'[trips])", MONEY),
        ("Скасовані рейси", "SUM('KPI день'[trips_cancelled])", MONEY),
        ("Рейси вчасно", "DIVIDE(SUMX('KPI день', 'KPI день'[on_time_rate] * ('KPI день'[trips] - 'KPI день'[trips_cancelled])), "
                         "SUMX(FILTER('KPI день', NOT ISBLANK('KPI день'[on_time_rate])), 'KPI день'[trips] - 'KPI день'[trips_cancelled]))", PCT),
        ("Середня затримка, хв", "DIVIDE(SUMX('KPI день', 'KPI день'[avg_delay_min] * ('KPI день'[trips] - 'KPI день'[trips_cancelled])), "
                                 "SUMX(FILTER('KPI день', NOT ISBLANK('KPI день'[avg_delay_min])), 'KPI день'[trips] - 'KPI день'[trips_cancelled]))", NUM),
        ("Заповненість автобусів", "AVERAGE('KPI день'[seat_occupancy])", PCT),
        ("Факт за 120 днів",
         "VAR lastDay = CALCULATE(MAX('KPI день'[date]), ALL('KPI день'), 'KPI день'[is_future] = FALSE()) "
         "VAR d = MAX('Календар'[date]) "
         "RETURN IF(d > lastDay - 120 && d <= lastDay, [Години факт])", MONEY),
    ],
    "KPI місяць": [
        ("Прийнято", "SUM('KPI місяць'[hires])", MONEY),
        ("Звільнилось", "SUM('KPI місяць'[leavers])", MONEY),
        ("Плинність за місяць", "DIVIDE([Звільнилось], SUM('KPI місяць'[avg_headcount]))", PCT),
        ("Виставлено рахунків", "SUM('KPI місяць'[invoiced_czk])", MONEY),
        ("Розбіжність з рахунками",
         "VAR billed = FILTER('KPI місяць', NOT ISBLANK('KPI місяць'[invoiced_czk])) "
         "RETURN DIVIDE(SUMX(billed, 'KPI місяць'[invoiced_czk]) - SUMX(billed, 'KPI місяць'[revenue_czk]), "
         "SUMX(billed, 'KPI місяць'[revenue_czk]))", PCT),
        ("Медіана рішення, год", "AVERAGE('KPI місяць'[median_decision_hours])", NUM),
    ],
    "Прогноз": [
        ("Прогноз годин", "SUM('Прогноз'[predicted_hours])", MONEY),
        ("Прогноз мін", "SUM('Прогноз'[lower_hours])", MONEY),
        ("Прогноз макс", "SUM('Прогноз'[upper_hours])", MONEY),
        ("План на прогноз", "SUM('Прогноз'[planned_hours])", MONEY),
    ],
    "Відхилення": [
        ("Кількість відхилень", "COUNTROWS('Відхилення')", "0"),
    ],
    "Якість виявлення": [
        ("F1 (тест)", "CALCULATE(MAX('Якість виявлення'[f1]), 'Якість виявлення'[period] = \"test\")", "0.000"),
        ("Precision (тест)", "CALCULATE(MAX('Якість виявлення'[precision]), 'Якість виявлення'[period] = \"test\")", PCT),
        ("Recall (тест)", "CALCULATE(MAX('Якість виявлення'[recall]), 'Якість виявлення'[period] = \"test\")", PCT),
    ],
    "Бектест": [
        # Names of measures and columns are case-insensitive: not "WAPE" beside the column wape.
        ("WAPE середня", "AVERAGE('Бектест'[wape])", "0.00%"),
        ("WAPE бустинг", "CALCULATE([WAPE середня], 'Бектест'[model] = \"gradient_boosting\")", "0.00%"),
        ("WAPE наївний", "CALCULATE([WAPE середня], 'Бектест'[model] = \"seasonal_naive\")", "0.00%"),
        ("MAPE середня", "AVERAGE('Бектест'[mape])", "0.00%"),
        ("MAE, год", "AVERAGE('Бектест'[mae])", NUM),
    ],
}

HIDDEN = {"date_key", "workplace_key", "run_id", "is_future", "created_at", "detected_at"}


def m_query(source: str) -> str:
    schema, item = source.split(".")
    return "\n".join([
        "let",
        "    Source = PostgreSQL.Database(Server, Database),",
        f'    Data = Source{{[Schema="{schema}",Item="{item}"]}}[Data]',
        "in",
        "    Data",
    ])


def model() -> dict:
    tables = []
    for name, (source, columns) in TABLES.items():
        table = {
            "name": name,
            "columns": [
                {"name": column, "dataType": kind, "sourceColumn": column,
                 **({"formatString": fmt} if fmt else {}),
                 **({"isHidden": True} if column in HIDDEN else {}),
                 **({"summarizeBy": "none"} if kind in (I, F) and column.endswith(("_key", "year", "month", "quarter", "iso_week", "day_of_week", "fold")) else {})}
                for column, kind, fmt in columns
            ],
            "partitions": [{"name": name, "mode": "import", "source": {"type": "m", "expression": m_query(source)}}],
            "measures": [{"name": n, "expression": e, "formatString": f} for n, e, f in MEASURES.get(name, [])],
        }
        if name == "Календар":
            table["dataCategory"] = "Time"
            for column in table["columns"]:
                if column["name"] == "date":
                    column["isKey"] = True
                if column["name"] == "month_name":
                    column["sortByColumn"] = "month"
        tables.append(table)
    relationships = [
        {"name": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{a}.{b}->{c}.{d}")), "fromTable": a, "fromColumn": b,
         "toTable": c, "toColumn": d}
        for a, b, c, d in RELATIONSHIPS
    ]
    parameter = 'meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'
    return {
        "compatibilityLevel": 1567,
        "model": {
            "culture": "uk-UA",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "uk-UA",
            "dataAccessOptions": {"legacyRedirects": True, "returnErrorValuesAsNull": True},
            "tables": tables,
            "relationships": relationships,
            "expressions": [
                {"name": "Server", "kind": "m", "expression": f'"localhost:55441" {parameter}'},
                {"name": "Database", "kind": "m", "expression": f'"dwh" {parameter}'},
            ],
            "annotations": [{"name": "PBI_QueryOrder", "value": json.dumps(["Server", "Database", *TABLES])}],
        },
    }


# ------------------------------------------------------------------- report
def ref(table: str, field: str, measure: bool) -> tuple[dict, str]:
    alias = "t" + str(list(TABLES).index(table))
    kind = "Measure" if measure else "Column"
    return {kind: {"Expression": {"SourceRef": {"Source": alias}}, "Property": field},
            "Name": f"{table}.{field}"}, alias


def visual(kind: str, x: int, y: int, w: int, h: int, roles: dict[str, list[tuple[str, str, bool]]],
           title: str | None = None, selected: int | None = None) -> dict:
    """roles: role -> [(table, field, is_measure)]; selected: the value a slicer starts with."""
    select, sources, projections = [], {}, {}
    for role, fields in roles.items():
        projections[role] = []
        for table, field, measure in fields:
            item, alias = ref(table, field, measure)
            sources[alias] = table
            if item not in select:
                select.append(item)
            projections[role].append({"queryRef": f"{table}.{field}", **({} if measure else {"active": True})})
    name = uuid.uuid5(uuid.NAMESPACE_URL, f"{kind}/{x}/{y}/{title}/{select}").hex[:20]
    objects = {}
    if title:
        # A text literal of the report language: single quotes, doubled inside.
        literal = "'" + title.replace("'", "''") + "'"
        objects = {"title": [{"properties": {
            "show": {"expr": {"Literal": {"Value": "true"}}},
            "text": {"expr": {"Literal": {"Value": literal}}},
        }}]}
    config = {
        "name": name,
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": 0, "width": w, "height": h}}],
        "singleVisual": {
            "visualType": kind,
            "projections": projections,
            "prototypeQuery": {
                "Version": 2,
                "From": [{"Name": a, "Entity": t, "Type": 0} for a, t in sources.items()],
                "Select": select,
            },
            "drillFilterOtherVisuals": True,
            **({"vcObjects": objects} if objects else {}),
        },
    }
    if selected is not None:
        # The slicer's own filter: its single column IN (selected).
        alias, column = select[0]["Column"]["Expression"]["SourceRef"]["Source"], select[0]["Column"]["Property"]
        config["singleVisual"]["objects"] = {"general": [{"properties": {"filter": {"filter": {
            "Version": 2,
            "From": [{"Name": alias, "Entity": sources[alias], "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": alias}}, "Property": column}}],
                "Values": [[{"Literal": {"Value": f"{selected}L"}}]],
            }}}],
        }}}}]}
    return {"x": x, "y": y, "z": 0, "width": w, "height": h, "config": json.dumps(config, ensure_ascii=False),
            "filters": "[]"}


def cards(y: int, measures: list[tuple[str, str]]) -> list[dict]:
    width = (1240 - 10 * (len(measures) - 1)) // len(measures)
    return [visual("card", 20 + i * (width + 10), y, width, 110, {"Values": [(t, m, True)]})
            for i, (t, m) in enumerate(measures)]


DEFAULT_YEAR = 2026  # the report opens on the current year of the data


def slicers() -> list[dict]:
    return [
        visual("slicer", 20, 10, 300, 60, {"Values": [("Об'єкти", "workplace_code", False)]}, "Об'єкт"),
        visual("slicer", 330, 10, 300, 60, {"Values": [("Календар", "year", False)]}, "Рік", selected=DEFAULT_YEAR),
    ]


MONTH = ("Календар", "month_start", False)
K, M = "KPI день", "KPI місяць"

PAGES = {
    "Огляд": [
        *slicers(),
        *cards(80, [(K, "Години факт"), (K, "Виконання плану"), (K, "Виручка"), (K, "Маржа %"),
                    (K, "Невиходи"), (K, "Рейси вчасно")]),
        visual("clusteredColumnChart", 20, 200, 615, 500,
               {"Category": [MONTH], "Y": [(K, "Години план", True), (K, "Години факт", True)]},
               "Години: план і факт"),
        visual("lineChart", 645, 200, 615, 500,
               {"Category": [MONTH], "Y": [(K, "Маржа %", True), (K, "Маржа % мин. рік", True)]},
               "Валова маржа: поточний і минулий рік"),
    ],
    "Персонал": [
        *slicers(),
        *cards(80, [(K, "Середня чисельність"), (M, "Прийнято"), (M, "Звільнилось"), (M, "Плинність за місяць"),
                    (K, "Лікарняні"), (K, "Понаднормові")]),
        visual("lineChart", 20, 200, 615, 500,
               {"Category": [MONTH], "Y": [(K, "Лікарняні", True), (K, "Невиходи", True)]},
               "Відсутність, частка запланованих змін"),
        visual("clusteredColumnChart", 645, 200, 615, 500,
               {"Category": [MONTH], "Y": [(M, "Прийнято", True), (M, "Звільнилось", True)]},
               "Рух персоналу"),
    ],
    "Фінанси": [
        *slicers(),
        *cards(80, [(K, "Виручка"), (K, "Витрати на персонал"), (K, "Валова маржа"), (K, "Виручка зміна р/р"),
                    (M, "Розбіжність з рахунками")]),
        visual("clusteredColumnChart", 20, 200, 615, 500,
               {"Category": [MONTH], "Y": [(K, "Виручка", True), (K, "Витрати на персонал", True),
                                           (M, "Виставлено рахунків", True)]},
               "Виручка, витрати і рахунки"),
        visual("clusteredBarChart", 645, 200, 615, 500,
               {"Category": [("Об'єкти", "workplace_code", False)], "Y": [(K, "Маржа %", True)]},
               "Маржинальність об'єктів"),
    ],
    "Транспорт": [
        *slicers(),
        *cards(80, [(K, "Рейси"), (K, "Скасовані рейси"), (K, "Рейси вчасно"), (K, "Середня затримка, хв"),
                    (K, "Заповненість автобусів")]),
        visual("lineChart", 20, 200, 615, 500,
               {"Category": [MONTH], "Y": [(K, "Рейси вчасно", True)]}, "Пунктуальність рейсів"),
        visual("clusteredBarChart", 645, 200, 615, 500,
               {"Category": [("Об'єкти", "workplace_code", False)], "Y": [(K, "Заповненість автобусів", True)]},
               "Заповненість автобусів по об'єктах"),
    ],
    "Прогноз і відхилення": [
        visual("slicer", 20, 10, 300, 60, {"Values": [("Об'єкти", "workplace_code", False)]}, "Об'єкт"),
        *cards(80, [("Бектест", "WAPE бустинг"), ("Бектест", "WAPE наївний"), ("Якість виявлення", "F1 (тест)"),
                    ("Якість виявлення", "Precision (тест)"), ("Якість виявлення", "Recall (тест)"),
                    ("Відхилення", "Кількість відхилень")]),
        visual("lineChart", 20, 200, 760, 500,
               {"Category": [("Календар", "date", False)],
                "Y": [(K, "Факт за 120 днів", True), ("Прогноз", "Прогноз годин", True),
                      ("Прогноз", "Прогноз мін", True), ("Прогноз", "Прогноз макс", True),
                      ("Прогноз", "План на прогноз", True)]},
               "Відпрацьовані години: факт і прогноз на 28 днів"),
        visual("tableEx", 790, 200, 470, 500,
               {"Values": [("Відхилення", "date", False), ("Відхилення", "workplace_code", False),
                           ("Відхилення", "kpi", False), ("Відхилення", "value", False),
                           ("Відхилення", "expected", False), ("Відхилення", "robust_z", False),
                           ("Відхилення", "method", False)]},
               "Виявлені відхилення KPI"),
    ],
}


def report() -> dict:
    sections = []
    for index, (title, visuals) in enumerate(PAGES.items()):
        sections.append({
            "name": f"ReportSection{index:02d}",
            "displayName": title,
            "displayOption": 1,
            "width": 1280,
            "height": 720,
            "config": "{}",
            "filters": "[]",
            "visualContainers": visuals,
        })
    config = {"version": "5.43", "themeCollection": {}, "activeSectionIndex": 0,
              "defaultDrillFilterOtherVisuals": True, "linguisticSchemaSyncVersion": 0, "objects": {}}
    return {"config": json.dumps(config), "layoutOptimization": 0, "sections": sections}


def write(path: Path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")


def check_names() -> None:
    """Power BI refuses a model where a measure repeats a column of its table or another
    measure anywhere in the model; both comparisons ignore case."""
    problems = []
    measures = [(table, name) for table, items in MEASURES.items() for name, _, _ in items]
    seen: dict[str, str] = {}
    for table, name in measures:
        if name.casefold() in seen:
            problems.append(f"measure '{name}' in '{table}' repeats a measure of '{seen[name.casefold()]}'")
        seen[name.casefold()] = table
        columns = {column.casefold() for column, _, _ in TABLES[table][1]}
        if name.casefold() in columns:
            problems.append(f"measure '{name}' has the name of a column of '{table}'")
    if problems:
        raise SystemExit("model names clash:\n" + "\n".join(problems))


def main() -> None:
    check_names()
    write(HERE / f"{NAME}.pbip", {
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })
    write(HERE / f"{NAME}.SemanticModel" / "definition.pbism", {"version": "1.0", "settings": {}})
    write(HERE / f"{NAME}.SemanticModel" / "model.bim", model())
    write(HERE / f"{NAME}.Report" / "definition.pbir", {
        "version": "1.0",
        "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}, "byConnection": None},
    })
    write(HERE / f"{NAME}.Report" / "report.json", report())
    print(f"written {HERE / (NAME + '.pbip')}")


if __name__ == "__main__":
    main()
