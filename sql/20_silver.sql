-- bronze -> silver: the latest version of every record, typed, deduplicated,
-- repaired where a rule allows it; what was dropped or repaired goes to
-- silver.rejected. Rebuilt in full on every run (idempotent).

TRUNCATE silver.rejected;

-- Reference data: the newest snapshot.
TRUNCATE silver.workplace;
INSERT INTO silver.workplace
SELECT DISTINCT ON (id) id, upper(trim(code)), trim(name), trim(client_name), trim(city), is_active
FROM bronze.ops_workplaces
ORDER BY id, _batch_id DESC, updated_at DESC;

TRUNCATE silver.shift_type;
INSERT INTO silver.shift_type
SELECT DISTINCT ON (id) id, workplace_id, code, name, start_time,
       duration_minutes - break_minutes, agency_hourly_rate_czk
FROM bronze.ops_shift_types
ORDER BY id, _batch_id DESC, updated_at DESC;

-- Names stay in bronze: the warehouse needs no personal data beyond the number.
TRUNCATE silver.employee;
INSERT INTO silver.employee
SELECT DISTINCT ON (id) id, personal_no, workplace_id, is_home, housing_fee_czk, uses_transport,
       hired_on, left_on, updated_at
FROM bronze.ops_employees
WHERE role = 'worker'
ORDER BY id, _batch_id DESC, updated_at DESC;

TRUNCATE silver.employee_rate;
INSERT INTO silver.employee_rate
SELECT employee_id, valid_from,
       coalesce(lead(valid_from) OVER (PARTITION BY employee_id ORDER BY valid_from) - 1, date '9999-12-31'),
       hourly_rate_czk
FROM bronze.ops_employee_rate_history
WHERE _batch_id = (SELECT max(_batch_id) FROM bronze.ops_employee_rate_history);

-- Event data -------------------------------------------------------------
CREATE TEMP TABLE s_planned ON COMMIT DROP AS
SELECT DISTINCT ON (employee_id, work_date) employee_id, work_date, shift_type_id
FROM (SELECT DISTINCT ON (id) * FROM bronze.ops_planned_shifts ORDER BY id, updated_at DESC, _batch_id DESC) latest
ORDER BY employee_id, work_date, updated_at DESC, id DESC;

CREATE TEMP TABLE s_logs_all ON COMMIT DROP AS
SELECT DISTINCT ON (id) * FROM bronze.ops_work_logs ORDER BY id, updated_at DESC, _batch_id DESC;

-- The terminal sometimes sends a record twice under a new id.
INSERT INTO silver.rejected (source, record_key, reason, action)
SELECT 'ops.work_logs', id::text, 'duplicate clock record', 'dropped'
FROM (SELECT id, row_number() OVER (PARTITION BY employee_id, work_date ORDER BY id) AS n FROM s_logs_all) d
WHERE n > 1;

CREATE TEMP TABLE s_logs ON COMMIT DROP AS
SELECT DISTINCT ON (employee_id, work_date) * FROM s_logs_all ORDER BY employee_id, work_date, id;

INSERT INTO silver.rejected (source, record_key, reason, action)
SELECT 'ops.work_logs', id::text, 'clock_out missing', 'worked time taken from the shift plan'
FROM s_logs WHERE clock_out IS NULL;

CREATE TEMP TABLE s_absence_day ON COMMIT DROP AS
SELECT DISTINCT ON (employee_id, day) employee_id, day::date AS day, reason
FROM (SELECT DISTINCT ON (id) * FROM bronze.ops_absences ORDER BY id, updated_at DESC, _batch_id DESC) a,
     generate_series(a.date_from, a.date_to, interval '1 day') AS day
ORDER BY employee_id, day,
         CASE reason WHEN 'sick_leave' THEN 1 WHEN 'vacation' THEN 2 ELSE 3 END;

CREATE TEMP TABLE s_overtime ON COMMIT DROP AS
SELECT employee_id, work_date, sum(minutes)::integer AS minutes
FROM (SELECT DISTINCT ON (id) * FROM bronze.ops_overtime ORDER BY id, updated_at DESC, _batch_id DESC) o
WHERE NOT cancelled
GROUP BY employee_id, work_date;

-- The day the records end; later plans are the future.
CREATE TEMP TABLE s_as_of ON COMMIT DROP AS SELECT max(work_date) AS day FROM s_logs;

TRUNCATE silver.attendance_day;
INSERT INTO silver.attendance_day
SELECT p.employee_id, p.work_date, p.shift_type_id,
       CASE
         WHEN p.work_date > (SELECT day FROM s_as_of) THEN 'planned'
         WHEN l.id IS NOT NULL THEN 'worked'
         WHEN a.reason = 'sick_leave' THEN 'sick'
         WHEN a.reason = 'no_show' THEN 'no_show'
         WHEN a.reason = 'vacation' THEN 'vacation'
         ELSE 'unrecorded'
       END,
       st.paid_minutes,
       CASE WHEN l.id IS NOT NULL THEN st.paid_minutes + coalesce(o.minutes, 0) ELSE 0 END,
       CASE WHEN l.id IS NOT NULL THEN coalesce(o.minutes, 0) ELSE 0 END,
       l.id IS NOT NULL AND l.clock_out IS NULL,
       p.work_date > (SELECT day FROM s_as_of)
FROM s_planned p
JOIN silver.shift_type st ON st.shift_type_id = p.shift_type_id
LEFT JOIN s_logs l ON l.employee_id = p.employee_id AND l.work_date = p.work_date
LEFT JOIN s_absence_day a ON a.employee_id = p.employee_id AND a.day = p.work_date
LEFT JOIN s_overtime o ON o.employee_id = p.employee_id AND o.work_date = p.work_date;

INSERT INTO silver.rejected (source, record_key, reason, action)
SELECT 'ops.work_logs', l.id::text, 'clock record without a planned shift', 'dropped'
FROM s_logs l
LEFT JOIN s_planned p ON p.employee_id = l.employee_id AND p.work_date = l.work_date
WHERE p.employee_id IS NULL;

TRUNCATE silver.request;
INSERT INTO silver.request
SELECT id, employee_id, kind, amount_czk, status, created_at, reviewed_at,
       round((extract(epoch FROM reviewed_at - created_at) / 3600)::numeric, 2)
FROM (SELECT DISTINCT ON (id) * FROM bronze.ops_requests ORDER BY id, updated_at DESC, _batch_id DESC) r;

TRUNCATE silver.salary_adjustment;
INSERT INTO silver.salary_adjustment
SELECT id, employee_id, date_trunc('month', salary_month)::date, kind, amount_czk
FROM (SELECT DISTINCT ON (id) * FROM bronze.ops_salary_adjustments ORDER BY id, updated_at DESC, _batch_id DESC) s;

-- Accounting CSV -----------------------------------------------------------
CREATE TEMP TABLE s_invoice_raw ON COMMIT DROP AS
SELECT trim(cislo_faktury) AS invoice_no, upper(trim(klient)) AS workplace_code,
       to_date(trim(obdobi) || '-01', 'YYYY-MM-DD') AS period_month,
       nullif(replace(trim(hodiny), ',', '.'), '')::numeric AS hours,
       nullif(replace(replace(trim(castka_czk), ' ', ''), ',', '.'), '')::numeric AS amount_czk,
       CASE WHEN trim(vystaveno) ~ '^\d{2}\.\d{2}\.\d{4}$' THEN to_date(trim(vystaveno), 'DD.MM.YYYY')
            ELSE trim(vystaveno)::date END AS issued_on,
       _source_file, _line_no
FROM bronze.acc_invoices;

INSERT INTO silver.rejected (source, record_key, reason, action)
SELECT 'csv.' || _source_file, invoice_no, 'invoice repeated in the export (line ' || _line_no || ')', 'dropped'
FROM (SELECT *, row_number() OVER (PARTITION BY invoice_no ORDER BY _line_no) AS n FROM s_invoice_raw) d
WHERE n > 1;

INSERT INTO silver.rejected (source, record_key, reason, action)
SELECT DISTINCT 'csv.' || _source_file, invoice_no, 'amount missing',
       'amount = hours x average agency rate of the workplace'
FROM s_invoice_raw WHERE amount_czk IS NULL;

TRUNCATE silver.invoice;
INSERT INTO silver.invoice
SELECT DISTINCT ON (r.invoice_no)
       r.invoice_no, r.workplace_code, r.period_month, r.hours,
       coalesce(r.amount_czk, round(r.hours * rate.avg_rate, 2)),
       r.amount_czk IS NULL,
       coalesce(r.amount_czk, 0) < 0 OR r.invoice_no LIKE 'DB%',
       r.issued_on, r._source_file
FROM s_invoice_raw r
LEFT JOIN (SELECT w.code, avg(st.agency_hourly_rate_czk) AS avg_rate
           FROM silver.shift_type st JOIN silver.workplace w USING (workplace_id)
           GROUP BY w.code) rate ON rate.code = r.workplace_code
ORDER BY r.invoice_no, r._line_no;

-- Transport API --------------------------------------------------------------
CREATE TEMP TABLE s_trip_raw ON COMMIT DROP AS
SELECT payload, _batch_id, _loaded_at FROM bronze.api_trips;

INSERT INTO silver.rejected (source, record_key, reason, action)
SELECT 'api.trips', payload ->> 'trip_id', 'trip received more than once', 'kept the latest'
FROM s_trip_raw
GROUP BY payload ->> 'trip_id', payload ->> 'service_date'
HAVING count(*) > 1 AND count(DISTINCT _batch_id) = 1;

TRUNCATE silver.trip;
INSERT INTO silver.trip
SELECT DISTINCT ON (payload ->> 'trip_id')
       payload ->> 'trip_id', (payload ->> 'service_date')::date, upper(payload ->> 'workplace_code'),
       payload ->> 'shift', payload ->> 'direction', payload ->> 'vehicle_code', payload ->> 'driver_code',
       (payload ->> 'capacity')::integer, (payload ->> 'departure_planned')::timestamptz,
       (payload ->> 'departure_actual')::timestamptz, (payload ->> 'delay_min')::numeric,
       payload ->> 'status', (payload ->> 'passengers_planned')::integer,
       (payload ->> 'passengers_boarded')::integer
FROM s_trip_raw
ORDER BY payload ->> 'trip_id', _batch_id DESC, _loaded_at DESC;
