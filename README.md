# Візуальна аналітика BI для KPI підприємства

Практична частина курсової роботи з дисципліни «Методи та засоби інженерії даних та знань».
Об'єкт: кадрове агентство, що надає працівників на виробничі й логістичні підприємства
(модель даних побудована за зразком системи обліку змін BC; усі дані синтетичні).

Що зроблено:

* **інтеграція набору даних** з трьох джерел: операційна БД PostgreSQL (OLTP), CSV-вивантаження
  бухгалтерії, REST API транспортної служби (JSON);
* **сховище даних** із шарами bronze → silver → gold (зірка Кімбалла, SCD2) і **дата-марти KPI**;
* **BI-дошка**: звіт Power BI (`powerbi/`) і запасна панель Plotly Dash (`dashboard/`);
* **ML**: прогноз відпрацьованих годин на 28 днів (сезонний наївний, Холт-Вінтерс, градієнтний бустинг,
  бектест із ковзним початком, реєстр моделей MLflow) і виявлення відхилень KPI (робастний z-score,
  Isolation Forest, оцінка на розмічених інцидентах);
* **якість даних**: Great Expectations на шарах silver, gold і mart;
* **оркестрація**: Apache Airflow, DAG `bc_kpi_pipeline`.

## Запуск

Потрібен Docker Desktop.

```bash
docker compose build
docker compose up -d source-db dwh mlflow
docker compose run --rm runner generate
docker compose up -d source-api airflow dashboard
docker compose run --rm runner all
```

| Сервіс | Адреса |
|---|---|
| Airflow (admin / admin) | http://localhost:8090 |
| MLflow | http://localhost:5050 |
| Панель Dash | http://localhost:8050 |
| Сховище PostgreSQL | localhost:55441, база `dwh` (BI: `bi_reader` / `bi_reader_pass`) |
| Операційна БД | localhost:55440, база `bc_ops` |
| API транспорту | http://localhost:8095/trips |

Кроки конвеєра можна запускати окремо: `docker compose run --rm runner extract_ops silver gold`.
Перелік кроків: `docker compose run --rm runner --help`.

## Структура

```
src/bikpi/sources/   генератор синтетичних джерел і REST API транспорту
src/bikpi/etl/       вилучення в bronze (водяні знаки), трансформації silver/gold/mart
src/bikpi/dq/        перевірки Great Expectations
src/bikpi/ml/        сховище ознак, прогноз, виявлення відхилень
sql/                 DDL шарів і SQL-трансформації
dags/                DAG Airflow
dashboard/           панель Plotly Dash
powerbi/             проєкт Power BI (PBIP) і генератор моделі та звіту
docs/                пояснювальна записка курсової роботи та рисунки
```
