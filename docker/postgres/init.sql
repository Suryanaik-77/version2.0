-- VLSI Interview Platform V2
-- PostgreSQL initialization script.
-- Runs once when the postgres container is first created.
--
-- Purpose: set connection limits and enable required extensions.
-- All schema creation is handled by Alembic migrations, not here.

-- Enable pg_stat_statements for query monitoring (optional, non-blocking if unavailable)
-- CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Set timezone to UTC for all connections (matches application timestamps)
ALTER DATABASE vlsi_interview SET timezone TO 'UTC';

-- Application user already exists from POSTGRES_USER env var.
-- No additional grants needed — app connects as that user.

SELECT 'VLSI Interview Platform V2 - initialization complete' AS status;
