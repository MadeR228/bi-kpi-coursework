-- gold -> mart: KPI data marts of the BI report, one row per workplace and
-- day / month. Rates are shares of the planned shifts of past days.

TRUNCATE mart.kpi_workplace_day;
INSERT INTO mart.kpi_workplace_day
WITH shifts AS (
  SELECT dd.date, w.workplace_code, bool_and(f.is_future) AS is_future,
         count(*) AS planned_shifts,
         count(*) FILTER (WHERE f.status = 'worked') AS worked_shifts,
         count(*) FILTER (WHERE f.status = 'no_show') AS no_shows,
         count(*) FILTER (WHERE f.status = 'sick') AS sick,
         count(*) FILTER (WHERE f.status = 'unrecorded') AS unrecorded,
         sum(f.planned_hours) AS planned_hours, sum(f.worked_hours) AS worked_hours,
         sum(f.overtime_hours) AS overtime_hours, sum(f.revenue_czk) AS revenue,
         sum(f.labor_cost_czk) AS labor_cost
  FROM gold.fact_shift_day f
  JOIN gold.dim_date dd USING (date_key)
  JOIN gold.dim_workplace w USING (workplace_key)
  GROUP BY 1, 2
), trips AS (
  SELECT dd.date, w.workplace_code, count(*) AS trips,
         count(*) FILTER (WHERE t.status = 'cancelled') AS cancelled,
         avg(CASE WHEN t.is_on_time THEN 1.0 ELSE 0.0 END) FILTER (WHERE t.status = 'completed') AS on_time_rate,
         avg(t.delay_min) FILTER (WHERE t.status = 'completed') AS avg_delay,
         sum(t.passengers_boarded)::numeric / nullif(sum(t.capacity) FILTER (WHERE t.status = 'completed'), 0) AS occupancy,
         sum(t.passengers_boarded)::numeric / nullif(sum(t.passengers_planned), 0) AS boarding
  FROM gold.fact_trip t
  JOIN gold.dim_date dd USING (date_key)
  JOIN gold.dim_workplace w USING (workplace_key)
  GROUP BY 1, 2
), headcount AS (
  SELECT s.date, s.workplace_code, count(DISTINCT e.employee_id) AS people
  FROM shifts s
  JOIN gold.dim_employee e ON e.workplace_code = s.workplace_code AND e.is_current
   AND e.hired_on <= s.date AND (e.left_on IS NULL OR e.left_on > s.date)
  GROUP BY 1, 2
)
SELECT s.date, s.workplace_code, s.is_future, coalesce(h.people, 0), s.planned_shifts, s.worked_shifts,
       s.planned_hours, s.worked_hours, s.overtime_hours,
       CASE WHEN NOT s.is_future THEN round(s.worked_hours / nullif(s.planned_hours, 0), 4) END,
       CASE WHEN NOT s.is_future THEN round(s.worked_shifts::numeric / s.planned_shifts, 4) END,
       CASE WHEN NOT s.is_future THEN round(s.no_shows::numeric / s.planned_shifts, 4) END,
       CASE WHEN NOT s.is_future THEN round(s.sick::numeric / s.planned_shifts, 4) END,
       CASE WHEN NOT s.is_future THEN round(s.unrecorded::numeric / s.planned_shifts, 4) END,
       CASE WHEN NOT s.is_future THEN round(s.overtime_hours / nullif(s.worked_hours, 0), 4) END,
       s.revenue, s.labor_cost, s.revenue - s.labor_cost,
       coalesce(t.trips, 0), coalesce(t.cancelled, 0), round(t.on_time_rate, 4), round(t.avg_delay, 2),
       round(t.occupancy, 4), round(t.boarding, 4)
FROM shifts s
LEFT JOIN trips t USING (date, workplace_code)
LEFT JOIN headcount h USING (date, workplace_code);

TRUNCATE mart.kpi_workplace_month;
INSERT INTO mart.kpi_workplace_month
WITH days AS (
  SELECT date_trunc('month', date)::date AS month, workplace_code,
         avg(headcount) AS avg_headcount,
         sum(planned_hours) FILTER (WHERE NOT is_future) AS planned_hours,
         sum(worked_hours) AS worked_hours, sum(overtime_hours) AS overtime_hours,
         sum(planned_shifts) FILTER (WHERE NOT is_future) AS planned_shifts,
         sum(no_show_rate * planned_shifts) AS no_shows, sum(sick_rate * planned_shifts) AS sick,
         sum(revenue_czk) AS revenue, sum(labor_cost_czk) AS labor_cost,
         sum(on_time_rate * (trips - trips_cancelled)) / nullif(sum(trips - trips_cancelled) FILTER (WHERE on_time_rate IS NOT NULL), 0) AS on_time,
         sum(avg_delay_min * (trips - trips_cancelled)) / nullif(sum(trips - trips_cancelled) FILTER (WHERE avg_delay_min IS NOT NULL), 0) AS delay,
         avg(seat_occupancy) AS occupancy
  FROM mart.kpi_workplace_day
  GROUP BY 1, 2
  HAVING bool_or(NOT is_future)
), people AS (
  SELECT d.month, d.workplace_code,
         count(DISTINCT e.employee_id) FILTER (WHERE date_trunc('month', e.hired_on) = d.month) AS hires,
         count(DISTINCT e.employee_id) FILTER (WHERE date_trunc('month', e.left_on) = d.month) AS leavers
  FROM days d
  JOIN gold.dim_employee e ON e.workplace_code = d.workplace_code AND e.is_current
  GROUP BY 1, 2
), invoices AS (
  SELECT dd.month_start AS month, w.workplace_code, sum(i.amount_czk) AS invoiced
  FROM gold.fact_invoice i
  JOIN gold.dim_date dd ON dd.date_key = i.month_key
  JOIN gold.dim_workplace w USING (workplace_key)
  GROUP BY 1, 2
), requests AS (
  SELECT dd.month_start AS month, w.workplace_code, count(*) AS requests,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY r.decision_hours) AS median_hours,
         coalesce(sum(r.amount_czk) FILTER (WHERE r.kind = 'advance' AND r.status = 'approved'), 0) AS advances
  FROM gold.fact_request r
  JOIN gold.dim_date dd USING (date_key)
  JOIN gold.dim_workplace w USING (workplace_key)
  GROUP BY 1, 2
)
SELECT d.month, d.workplace_code, round(d.avg_headcount, 1), coalesce(p.hires, 0), coalesce(p.leavers, 0),
       round(coalesce(p.leavers, 0) / nullif(d.avg_headcount, 0), 4),
       coalesce(d.planned_hours, 0), d.worked_hours, d.overtime_hours,
       round(d.worked_hours / nullif(d.planned_hours, 0), 4),
       round(d.no_shows / nullif(d.planned_shifts, 0), 4), round(d.sick / nullif(d.planned_shifts, 0), 4),
       d.revenue, d.labor_cost, d.revenue - d.labor_cost, round((d.revenue - d.labor_cost) / nullif(d.revenue, 0), 4),
       i.invoiced, round((i.invoiced - d.revenue) / nullif(d.revenue, 0), 4),
       round(d.on_time, 4), round(d.delay, 2), round(d.occupancy, 4),
       coalesce(r.requests, 0), round(r.median_hours::numeric, 2), coalesce(r.advances, 0)
FROM days d
LEFT JOIN people p USING (month, workplace_code)
LEFT JOIN invoices i USING (month, workplace_code)
LEFT JOIN requests r USING (month, workplace_code);

-- Company-wide views for the first page of the report.
CREATE OR REPLACE VIEW mart.v_kpi_company_day AS
SELECT date, bool_and(is_future) AS is_future, sum(headcount) AS headcount,
       sum(planned_hours) AS planned_hours, sum(worked_hours) AS worked_hours,
       round(sum(worked_hours) / nullif(sum(planned_hours) FILTER (WHERE NOT is_future), 0), 4) AS plan_fulfillment,
       round(sum(no_show_rate * planned_shifts) / nullif(sum(planned_shifts) FILTER (WHERE NOT is_future), 0), 4) AS no_show_rate,
       sum(revenue_czk) AS revenue_czk, sum(labor_cost_czk) AS labor_cost_czk, sum(gross_margin_czk) AS gross_margin_czk
FROM mart.kpi_workplace_day
GROUP BY date;

CREATE OR REPLACE VIEW mart.v_kpi_company_month AS
SELECT month, sum(avg_headcount) AS avg_headcount, sum(hires) AS hires, sum(leavers) AS leavers,
       round(sum(leavers) / nullif(sum(avg_headcount), 0), 4) AS turnover_rate,
       sum(planned_hours) AS planned_hours, sum(worked_hours) AS worked_hours,
       round(sum(worked_hours) / nullif(sum(planned_hours), 0), 4) AS plan_fulfillment,
       sum(revenue_czk) AS revenue_czk, sum(labor_cost_czk) AS labor_cost_czk,
       sum(gross_margin_czk) AS gross_margin_czk,
       round(sum(gross_margin_czk) / nullif(sum(revenue_czk), 0), 4) AS margin_rate,
       sum(invoiced_czk) AS invoiced_czk
FROM mart.kpi_workplace_month
GROUP BY month;

GRANT SELECT ON ALL TABLES IN SCHEMA mart TO bi_reader;
