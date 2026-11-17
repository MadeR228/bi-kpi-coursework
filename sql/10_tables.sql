-- Tables of every layer of the warehouse. Safe to run again.

------------------------------------------------------------------ meta
CREATE TABLE IF NOT EXISTS meta.batches (
  batch_id     bigserial PRIMARY KEY,
  step         text NOT NULL,
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  status       text NOT NULL DEFAULT 'running',  -- running | success | failed
  rows_loaded  bigint,
  message      text
);

CREATE TABLE IF NOT EXISTS meta.watermarks (
  source      text NOT NULL,
  object      text NOT NULL,
  value       text NOT NULL,           -- last updated_at / date / file name loaded
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source, object)
);

---------------------------------------------------------------- bronze
-- Copies of the operational tables as they were read, plus lineage columns.
CREATE TABLE IF NOT EXISTS bronze.ops_workplaces (
  id integer, code text, name text, client_name text, city text, address text, is_active boolean,
  updated_at timestamptz, _batch_id bigint, _loaded_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS bronze.ops_shift_types (
  id integer, workplace_id integer, code text, name text, start_time time, duration_minutes integer,
  break_minutes integer, agency_hourly_rate_czk numeric, updated_at timestamptz,
  _batch_id bigint, _loaded_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS bronze.ops_employees (
  id integer, personal_no text, full_name text, role text, workplace_id integer, hourly_rate_czk numeric,
  is_home boolean, housing_fee_czk numeric, uses_transport boolean, hired_on date, left_on date,
  updated_at timestamptz, _batch_id bigint, _loaded_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS bronze.ops_employee_rate_history (
  employee_id integer, valid_from date, hourly_rate_czk numeric,
  _batch_id bigint, _loaded_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS bronze.ops_planned_shifts (
  id bigint, employee_id integer, work_date date, shift_type_id integer, updated_at timestamptz,
  _batch_id bigint, _loaded_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS bronze.ops_work_logs (
  id bigint, employee_id integer, work_date date, shift_type_id integer, clock_in timestamptz,
  clock_out timestamptz, status text, updated_at timestamptz, _batch_id bigint, _loaded_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS bronze.ops_absences (
  id bigint, employee_id integer, date_from date, date_to date, reason text, updated_at timestamptz,
  _batch_id bigint, _loaded_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS bronze.ops_overtime (
  id bigint, employee_id integer, work_date date, minutes integer, cancelled boolean, updated_at timestamptz,
  _batch_id bigint, _loaded_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS bronze.ops_requests (
  id bigint, employee_id integer, kind text, amount_czk numeric, status text, created_at timestamptz,
  reviewed_at timestamptz, updated_at timestamptz, _batch_id bigint, _loaded_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS bronze.ops_salary_adjustments (
  id bigint, employee_id integer, salary_month date, kind text, amount_czk numeric, comment text,
  created_at timestamptz, updated_at timestamptz, _batch_id bigint, _loaded_at timestamptz DEFAULT now());

-- Accounting CSV files: every value kept as text, exactly as in the file.
CREATE TABLE IF NOT EXISTS bronze.acc_invoices (
  cislo_faktury text, klient text, obdobi text, hodiny text, castka_czk text, vystaveno text,
  _source_file text, _line_no integer, _batch_id bigint, _loaded_at timestamptz DEFAULT now());

-- Transport API: the JSON documents as received.
CREATE TABLE IF NOT EXISTS bronze.api_trips (
  payload jsonb NOT NULL, _batch_id bigint, _loaded_at timestamptz DEFAULT now());

---------------------------------------------------------------- silver
CREATE TABLE IF NOT EXISTS silver.workplace (
  workplace_id integer PRIMARY KEY, code text NOT NULL UNIQUE, name text NOT NULL, client_name text NOT NULL,
  city text, is_active boolean NOT NULL);
CREATE TABLE IF NOT EXISTS silver.shift_type (
  shift_type_id integer PRIMARY KEY, workplace_id integer NOT NULL, code text NOT NULL, name text NOT NULL,
  start_time time NOT NULL, paid_minutes integer NOT NULL, agency_hourly_rate_czk numeric(10,2) NOT NULL);
CREATE TABLE IF NOT EXISTS silver.employee (
  employee_id integer PRIMARY KEY, personal_no text NOT NULL, workplace_id integer, is_home boolean NOT NULL,
  housing_fee_czk numeric(10,2) NOT NULL, uses_transport boolean NOT NULL, hired_on date NOT NULL,
  left_on date, updated_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS silver.employee_rate (
  employee_id integer NOT NULL, valid_from date NOT NULL, valid_to date NOT NULL,
  hourly_rate_czk numeric(10,2) NOT NULL, PRIMARY KEY (employee_id, valid_from));
-- One row per employee and planned day, with what happened.
CREATE TABLE IF NOT EXISTS silver.attendance_day (
  employee_id integer NOT NULL, work_date date NOT NULL, shift_type_id integer NOT NULL,
  status text NOT NULL,  -- worked | sick | no_show | vacation | unrecorded
  paid_minutes integer NOT NULL, worked_minutes integer NOT NULL, overtime_minutes integer NOT NULL,
  clock_out_imputed boolean NOT NULL, is_future boolean NOT NULL,
  PRIMARY KEY (employee_id, work_date));
CREATE TABLE IF NOT EXISTS silver.request (
  request_id bigint PRIMARY KEY, employee_id integer NOT NULL, kind text NOT NULL, amount_czk numeric(10,2),
  status text NOT NULL, created_at timestamptz NOT NULL, reviewed_at timestamptz, decision_hours numeric(10,2));
CREATE TABLE IF NOT EXISTS silver.salary_adjustment (
  adjustment_id bigint PRIMARY KEY, employee_id integer NOT NULL, salary_month date NOT NULL,
  kind text NOT NULL, amount_czk numeric(10,2) NOT NULL);
CREATE TABLE IF NOT EXISTS silver.invoice (
  invoice_no text PRIMARY KEY, workplace_code text NOT NULL, period_month date NOT NULL,
  hours numeric(10,1), amount_czk numeric(14,2), amount_imputed boolean NOT NULL, is_credit_note boolean NOT NULL,
  issued_on date NOT NULL, source_file text NOT NULL);
CREATE TABLE IF NOT EXISTS silver.trip (
  trip_id text PRIMARY KEY, service_date date NOT NULL, workplace_code text NOT NULL, shift_code text NOT NULL,
  direction text NOT NULL, vehicle_code text NOT NULL, driver_code text NOT NULL, capacity integer NOT NULL,
  departure_planned timestamptz NOT NULL, departure_actual timestamptz, delay_min numeric(6,1),
  status text NOT NULL, passengers_planned integer NOT NULL, passengers_boarded integer NOT NULL);
-- Rows the cleaning rejected or repaired, for the data quality report.
CREATE TABLE IF NOT EXISTS silver.rejected (
  batch_id bigint, source text NOT NULL, record_key text, reason text NOT NULL, action text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now());

------------------------------------------------------------------ gold
CREATE TABLE IF NOT EXISTS gold.dim_date (
  date_key integer PRIMARY KEY,          -- yyyymmdd
  date date NOT NULL UNIQUE, year integer NOT NULL, quarter integer NOT NULL, month integer NOT NULL,
  month_name text NOT NULL, month_start date NOT NULL, iso_week integer NOT NULL, day_of_week integer NOT NULL,
  day_name text NOT NULL, is_weekend boolean NOT NULL, is_holiday boolean NOT NULL, holiday_name text);
CREATE TABLE IF NOT EXISTS gold.dim_workplace (
  workplace_key integer PRIMARY KEY, workplace_code text NOT NULL UNIQUE, workplace_name text NOT NULL,
  client_name text NOT NULL, city text, is_active boolean NOT NULL);
CREATE TABLE IF NOT EXISTS gold.dim_shift_type (
  shift_type_key integer PRIMARY KEY, workplace_key integer NOT NULL REFERENCES gold.dim_workplace,
  shift_code text NOT NULL, shift_name text NOT NULL, start_time time NOT NULL, paid_hours numeric(4,2) NOT NULL,
  agency_hourly_rate_czk numeric(10,2) NOT NULL);
-- Slowly changing dimension type 2: a new version on a change of rate, workplace or housing.
CREATE TABLE IF NOT EXISTS gold.dim_employee (
  employee_key bigserial PRIMARY KEY, employee_id integer NOT NULL, personal_no text NOT NULL,
  workplace_code text, hourly_rate_czk numeric(10,2) NOT NULL, is_home boolean NOT NULL,
  uses_transport boolean NOT NULL, hired_on date NOT NULL, left_on date,
  valid_from date NOT NULL, valid_to date NOT NULL, is_current boolean NOT NULL, row_hash text NOT NULL);
CREATE INDEX IF NOT EXISTS dim_employee_lookup ON gold.dim_employee (employee_id, valid_from, valid_to);

CREATE TABLE IF NOT EXISTS gold.fact_shift_day (
  date_key integer NOT NULL REFERENCES gold.dim_date, employee_key bigint NOT NULL REFERENCES gold.dim_employee,
  workplace_key integer NOT NULL REFERENCES gold.dim_workplace,
  shift_type_key integer NOT NULL REFERENCES gold.dim_shift_type,
  status text NOT NULL, is_future boolean NOT NULL,
  planned_hours numeric(6,2) NOT NULL, worked_hours numeric(6,2) NOT NULL, overtime_hours numeric(6,2) NOT NULL,
  revenue_czk numeric(12,2) NOT NULL, labor_cost_czk numeric(12,2) NOT NULL,
  PRIMARY KEY (date_key, employee_key));
CREATE INDEX IF NOT EXISTS fact_shift_day_workplace ON gold.fact_shift_day (workplace_key, date_key);
CREATE TABLE IF NOT EXISTS gold.fact_trip (
  trip_id text PRIMARY KEY, date_key integer NOT NULL REFERENCES gold.dim_date,
  workplace_key integer NOT NULL REFERENCES gold.dim_workplace, shift_code text NOT NULL, direction text NOT NULL,
  vehicle_code text NOT NULL, driver_code text NOT NULL, status text NOT NULL, delay_min numeric(6,1),
  is_on_time boolean, capacity integer NOT NULL, passengers_planned integer NOT NULL,
  passengers_boarded integer NOT NULL);
CREATE TABLE IF NOT EXISTS gold.fact_payroll_month (
  month_key integer NOT NULL REFERENCES gold.dim_date, employee_key bigint NOT NULL REFERENCES gold.dim_employee,
  workplace_key integer NOT NULL REFERENCES gold.dim_workplace,
  worked_hours numeric(8,2) NOT NULL, base_pay_czk numeric(12,2) NOT NULL, additions_czk numeric(12,2) NOT NULL,
  deductions_czk numeric(12,2) NOT NULL, housing_czk numeric(12,2) NOT NULL, advances_czk numeric(12,2) NOT NULL,
  net_pay_czk numeric(12,2) NOT NULL, PRIMARY KEY (month_key, employee_key));
CREATE TABLE IF NOT EXISTS gold.fact_invoice (
  invoice_no text PRIMARY KEY, month_key integer NOT NULL REFERENCES gold.dim_date,
  workplace_key integer NOT NULL REFERENCES gold.dim_workplace, issued_date_key integer NOT NULL,
  hours numeric(10,1), amount_czk numeric(14,2) NOT NULL, is_credit_note boolean NOT NULL,
  amount_imputed boolean NOT NULL);
CREATE TABLE IF NOT EXISTS gold.fact_request (
  request_id bigint PRIMARY KEY, date_key integer NOT NULL REFERENCES gold.dim_date,
  employee_key bigint NOT NULL REFERENCES gold.dim_employee,
  workplace_key integer NOT NULL REFERENCES gold.dim_workplace, kind text NOT NULL, status text NOT NULL,
  amount_czk numeric(10,2), decision_hours numeric(10,2));

------------------------------------------------------------------ mart
CREATE TABLE IF NOT EXISTS mart.kpi_workplace_day (
  date date NOT NULL, workplace_code text NOT NULL, is_future boolean NOT NULL,
  headcount integer NOT NULL, planned_shifts integer NOT NULL, worked_shifts integer NOT NULL,
  planned_hours numeric(10,2) NOT NULL, worked_hours numeric(10,2) NOT NULL, overtime_hours numeric(10,2) NOT NULL,
  plan_fulfillment numeric(6,4), attendance_rate numeric(6,4), no_show_rate numeric(6,4),
  sick_rate numeric(6,4), unrecorded_rate numeric(6,4), overtime_share numeric(6,4),
  revenue_czk numeric(14,2) NOT NULL, labor_cost_czk numeric(14,2) NOT NULL, gross_margin_czk numeric(14,2) NOT NULL,
  trips integer NOT NULL, trips_cancelled integer NOT NULL, on_time_rate numeric(6,4), avg_delay_min numeric(6,2),
  seat_occupancy numeric(6,4), boarding_rate numeric(6,4),
  PRIMARY KEY (date, workplace_code));
CREATE TABLE IF NOT EXISTS mart.kpi_workplace_month (
  month date NOT NULL, workplace_code text NOT NULL,
  avg_headcount numeric(8,1), hires integer NOT NULL, leavers integer NOT NULL, turnover_rate numeric(6,4),
  planned_hours numeric(12,2) NOT NULL, worked_hours numeric(12,2) NOT NULL, overtime_hours numeric(12,2) NOT NULL,
  plan_fulfillment numeric(6,4), no_show_rate numeric(6,4), sick_rate numeric(6,4),
  revenue_czk numeric(14,2) NOT NULL, labor_cost_czk numeric(14,2) NOT NULL, gross_margin_czk numeric(14,2) NOT NULL,
  margin_rate numeric(6,4), invoiced_czk numeric(14,2), invoice_gap_rate numeric(6,4),
  on_time_rate numeric(6,4), avg_delay_min numeric(6,2), seat_occupancy numeric(6,4),
  requests integer NOT NULL, median_decision_hours numeric(8,2), advances_czk numeric(14,2) NOT NULL,
  PRIMARY KEY (month, workplace_code));

-------------------------------------------------------------------- ml
-- Feature store: features of a workplace-day, versioned by feature set.
CREATE TABLE IF NOT EXISTS ml.features_workplace_day (
  feature_set text NOT NULL, workplace_code text NOT NULL, date date NOT NULL, is_future boolean NOT NULL,
  planned_hours numeric(10,2) NOT NULL, worked_hours numeric(10,2), fulfillment numeric(8,5),
  day_of_week integer NOT NULL, month integer NOT NULL, is_holiday boolean NOT NULL,
  doy_sin double precision NOT NULL, doy_cos double precision NOT NULL, days_to_holiday integer NOT NULL,
  fulfillment_lag28 numeric(8,5), fulfillment_lag35 numeric(8,5), fulfillment_lag364 numeric(8,5),
  fulfillment_dow_mean8 numeric(8,5), fulfillment_mean28 numeric(8,5), fulfillment_std28 numeric(8,5),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (feature_set, workplace_code, date));
CREATE TABLE IF NOT EXISTS ml.backtest (
  run_id text NOT NULL, model text NOT NULL, fold integer NOT NULL, cutoff date NOT NULL,
  mae double precision, rmse double precision, mape double precision, wape double precision,
  created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ml.forecast (
  run_id text NOT NULL, model_name text NOT NULL, model_version text NOT NULL,
  workplace_code text NOT NULL, date date NOT NULL, planned_hours numeric(10,2) NOT NULL,
  predicted_hours numeric(10,2) NOT NULL, lower_hours numeric(10,2) NOT NULL, upper_hours numeric(10,2) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, workplace_code, date));
CREATE TABLE IF NOT EXISTS ml.anomalies (
  workplace_code text NOT NULL, date date NOT NULL, kpi text NOT NULL, value double precision,
  expected double precision, robust_z double precision, iforest_score double precision,
  method text NOT NULL, severity text NOT NULL, detected_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workplace_code, date, kpi));
CREATE TABLE IF NOT EXISTS ml.anomaly_evaluation (
  run_id text NOT NULL, period text NOT NULL,  -- calibration | test
  method text NOT NULL, threshold double precision, true_positives integer, false_positives integer,
  false_negatives integer, precision double precision, recall double precision, f1 double precision,
  created_at timestamptz NOT NULL DEFAULT now());

-------------------------------------------------------------------- dq
CREATE TABLE IF NOT EXISTS dq.results (
  run_at timestamptz NOT NULL DEFAULT now(), batch_id bigint, layer text NOT NULL, table_name text NOT NULL,
  expectation text NOT NULL, column_name text, success boolean NOT NULL,
  unexpected_count bigint, element_count bigint, details jsonb);

-- The BI role reads what Power BI shows.
GRANT SELECT ON ALL TABLES IN SCHEMA gold, mart, ml, dq TO bi_reader;
