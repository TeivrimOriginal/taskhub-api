-- =====================================================================================
-- Runs once, on an empty data directory, *before* the Alembic migrations.
--
-- Only extensions and server defaults belong here: at this point no application table
-- exists yet, so anything that references one would abort the bootstrap.
-- =====================================================================================

-- `citext` backs the case-insensitive e-mail/username columns.
CREATE EXTENSION IF NOT EXISTS citext;

-- `pg_trgm` powers the trigram indexes created by migration 0002.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Sensible defaults for a container that may sit on a shared CI runner: a forgotten
-- transaction cannot hold locks for ever, and no query may run for a minute.
ALTER DATABASE taskhub SET statement_timeout = '15s';
ALTER DATABASE taskhub SET idle_in_transaction_session_timeout = '30s';