-- First start of the warehouse: the layers as schemas and a read-only role
-- for the BI tools. The tables are created by sql/10_tables.sql (run by the
-- pipeline, step "init").
CREATE SCHEMA IF NOT EXISTS bronze;   -- raw data as it came from the sources
CREATE SCHEMA IF NOT EXISTS silver;   -- cleaned, typed, deduplicated, conformed
CREATE SCHEMA IF NOT EXISTS gold;     -- Kimball star schema: dimensions and facts
CREATE SCHEMA IF NOT EXISTS mart;     -- KPI data marts for Power BI
CREATE SCHEMA IF NOT EXISTS ml;       -- feature store, forecasts, anomalies
CREATE SCHEMA IF NOT EXISTS dq;       -- data quality results
CREATE SCHEMA IF NOT EXISTS meta;     -- load batches and watermarks

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_reader') THEN
    CREATE ROLE bi_reader LOGIN PASSWORD 'bi_reader_pass';
  END IF;
END $$;

GRANT CONNECT ON DATABASE dwh TO bi_reader;
GRANT USAGE ON SCHEMA gold, mart, ml, dq TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE dwh IN SCHEMA gold, mart, ml, dq
  GRANT SELECT ON TABLES TO bi_reader;
