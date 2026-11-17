-- silver -> gold: Kimball star schema. gold.dim_date is filled by Python
-- (bikpi.etl.transform) before this file runs, because it needs the calendar
-- of Czech public holidays.

-- Employer contributions in Czechia: social 24.8 % + health 9 %.
CREATE TEMP TABLE g_const ON COMMIT DROP AS
SELECT 1.338::numeric AS employer_cost_factor, 1.25::numeric AS overtime_pay_factor;

-- Dimensions (type 1) -----------------------------------------------------------
INSERT INTO gold.dim_workplace
SELECT workplace_id, code, name, client_name, city, is_active FROM silver.workplace
ON CONFLICT (workplace_key) DO UPDATE SET
  workplace_code = EXCLUDED.workplace_code, workplace_name = EXCLUDED.workplace_name,
  client_name = EXCLUDED.client_name, city = EXCLUDED.city, is_active = EXCLUDED.is_active;

INSERT INTO gold.dim_shift_type
SELECT shift_type_id, workplace_id, code, name, start_time, round(paid_minutes / 60.0, 2), agency_hourly_rate_czk
FROM silver.shift_type
ON CONFLICT (shift_type_key) DO UPDATE SET
  workplace_key = EXCLUDED.workplace_key, shift_code = EXCLUDED.shift_code, shift_name = EXCLUDED.shift_name,
  start_time = EXCLUDED.start_time, paid_hours = EXCLUDED.paid_hours,
  agency_hourly_rate_czk = EXCLUDED.agency_hourly_rate_czk;

-- Employee: SCD type 2 --------------------------------------------------------
-- Versions the history calls for: one per period of an hourly rate.
CREATE TEMP TABLE g_versions ON COMMIT DROP AS
SELECT e.employee_id, e.personal_no, w.code AS workplace_code, r.hourly_rate_czk, e.is_home, e.uses_transport,
       e.hired_on, e.left_on, greatest(r.valid_from, e.hired_on) AS valid_from, r.valid_to,
       md5(concat_ws('|', w.code, r.hourly_rate_czk, e.is_home, e.uses_transport)) AS row_hash
FROM silver.employee e
JOIN silver.employee_rate r USING (employee_id)
LEFT JOIN silver.workplace w USING (workplace_id)
WHERE r.valid_to >= e.hired_on;

-- Type 1 attributes are corrected in every version.
UPDATE gold.dim_employee d
SET personal_no = v.personal_no, hired_on = v.hired_on, left_on = v.left_on
FROM (SELECT DISTINCT employee_id, personal_no, hired_on, left_on FROM g_versions) v
WHERE d.employee_id = v.employee_id
  AND (d.left_on IS DISTINCT FROM v.left_on OR d.personal_no <> v.personal_no OR d.hired_on <> v.hired_on);

-- A version that changed its end (a newer one arrived) is closed.
UPDATE gold.dim_employee d
SET valid_to = v.valid_to, is_current = v.valid_to = date '9999-12-31'
FROM g_versions v
WHERE d.employee_id = v.employee_id AND d.valid_from = v.valid_from AND d.row_hash = v.row_hash
  AND d.valid_to <> v.valid_to;

-- New versions are added.
INSERT INTO gold.dim_employee (employee_id, personal_no, workplace_code, hourly_rate_czk, is_home, uses_transport,
                               hired_on, left_on, valid_from, valid_to, is_current, row_hash)
SELECT v.employee_id, v.personal_no, v.workplace_code, v.hourly_rate_czk, v.is_home, v.uses_transport,
       v.hired_on, v.left_on, v.valid_from, v.valid_to, v.valid_to = date '9999-12-31', v.row_hash
FROM g_versions v
WHERE NOT EXISTS (SELECT 1 FROM gold.dim_employee d
                  WHERE d.employee_id = v.employee_id AND d.valid_from = v.valid_from AND d.row_hash = v.row_hash);

-- Facts (rebuilt from silver) ---------------------------------------------------
TRUNCATE gold.fact_shift_day, gold.fact_trip, gold.fact_payroll_month, gold.fact_invoice, gold.fact_request;

INSERT INTO gold.fact_shift_day
SELECT to_char(a.work_date, 'YYYYMMDD')::integer, d.employee_key, st.workplace_id, a.shift_type_id,
       a.status, a.is_future,
       round(a.paid_minutes / 60.0, 2), round(a.worked_minutes / 60.0, 2), round(a.overtime_minutes / 60.0, 2),
       round(a.worked_minutes / 60.0 * st.agency_hourly_rate_czk, 2),
       round(((a.worked_minutes - a.overtime_minutes) + a.overtime_minutes * c.overtime_pay_factor) / 60.0
             * d.hourly_rate_czk * c.employer_cost_factor, 2)
FROM silver.attendance_day a
JOIN silver.shift_type st USING (shift_type_id)
JOIN gold.dim_employee d ON d.employee_id = a.employee_id AND a.work_date BETWEEN d.valid_from AND d.valid_to
CROSS JOIN g_const c;

INSERT INTO gold.fact_trip
SELECT t.trip_id, to_char(t.service_date, 'YYYYMMDD')::integer, w.workplace_key, t.shift_code, t.direction,
       t.vehicle_code, t.driver_code, t.status, t.delay_min,
       CASE WHEN t.status = 'completed' THEN t.delay_min <= 5 END,
       t.capacity, t.passengers_planned, t.passengers_boarded
FROM silver.trip t
JOIN gold.dim_workplace w ON w.workplace_code = t.workplace_code;

INSERT INTO gold.fact_invoice
SELECT i.invoice_no, to_char(i.period_month, 'YYYYMMDD')::integer, w.workplace_key,
       to_char(i.issued_on, 'YYYYMMDD')::integer, i.hours, i.amount_czk, i.is_credit_note, i.amount_imputed
FROM silver.invoice i
JOIN gold.dim_workplace w ON w.workplace_code = i.workplace_code;

INSERT INTO gold.fact_request
SELECT r.request_id, to_char(r.created_at AT TIME ZONE 'Europe/Prague', 'YYYYMMDD')::integer, d.employee_key,
       w.workplace_key, r.kind, r.status, r.amount_czk, r.decision_hours
FROM silver.request r
JOIN gold.dim_employee d ON d.employee_id = r.employee_id
  AND (r.created_at AT TIME ZONE 'Europe/Prague')::date BETWEEN d.valid_from AND d.valid_to
JOIN gold.dim_workplace w ON w.workplace_code = d.workplace_code;

-- Payroll before taxes: pay for the hours (overtime at 125 %), bonuses,
-- deductions, housing and advances, per month and employee version.
INSERT INTO gold.fact_payroll_month
WITH worked AS (
  SELECT date_trunc('month', dd.date)::date AS month, f.employee_key, f.workplace_key,
         sum(f.worked_hours) AS hours,
         sum((f.worked_hours - f.overtime_hours + f.overtime_hours * c.overtime_pay_factor) * d.hourly_rate_czk) AS pay
  FROM gold.fact_shift_day f
  JOIN gold.dim_date dd USING (date_key)
  JOIN gold.dim_employee d USING (employee_key)
  CROSS JOIN g_const c
  WHERE NOT f.is_future AND f.worked_hours > 0
  GROUP BY 1, 2, 3
), keyed AS (
  -- The month's extras go to the version that worked last in the month.
  SELECT w.*, d.employee_id, d.is_home,
         row_number() OVER (PARTITION BY w.month, d.employee_id ORDER BY d.valid_from DESC) AS last_version
  FROM worked w JOIN gold.dim_employee d USING (employee_key)
), adjustments AS (
  SELECT employee_id, salary_month AS month,
         sum(amount_czk) FILTER (WHERE kind = 'addition') AS additions,
         sum(amount_czk) FILTER (WHERE kind = 'deduction') AS deductions
  FROM silver.salary_adjustment GROUP BY 1, 2
), advances AS (
  SELECT employee_id, date_trunc('month', created_at AT TIME ZONE 'Europe/Prague')::date AS month,
         sum(amount_czk) AS amount
  FROM silver.request WHERE kind = 'advance' AND status = 'approved' GROUP BY 1, 2
)
SELECT to_char(k.month, 'YYYYMMDD')::integer, k.employee_key, k.workplace_key, round(k.hours, 2), round(k.pay, 2),
       CASE WHEN k.last_version = 1 THEN coalesce(a.additions, 0) ELSE 0 END,
       CASE WHEN k.last_version = 1 THEN coalesce(a.deductions, 0) ELSE 0 END,
       CASE WHEN k.last_version = 1 AND k.is_home THEN e.housing_fee_czk ELSE 0 END,
       CASE WHEN k.last_version = 1 THEN coalesce(v.amount, 0) ELSE 0 END,
       round(k.pay, 2)
         + CASE WHEN k.last_version = 1
                THEN coalesce(a.additions, 0) - coalesce(a.deductions, 0)
                     - CASE WHEN k.is_home THEN e.housing_fee_czk ELSE 0 END - coalesce(v.amount, 0)
                ELSE 0 END
FROM keyed k
JOIN silver.employee e USING (employee_id)
LEFT JOIN adjustments a ON a.employee_id = k.employee_id AND a.month = k.month
LEFT JOIN advances v ON v.employee_id = k.employee_id AND v.month = k.month;
