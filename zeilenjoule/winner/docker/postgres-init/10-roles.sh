#!/usr/bin/env bash
# First start of an empty data volume only (docker-entrypoint-initdb.d), as deploy/compose/postgres-init: the
# router's SQL runs as zj_ask (member of zj_reader: SELECT on the view meldungen only, granted by migration 0001),
# read-only with a 10 s statement timeout.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -v pw="$ZJ_READER_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE zj_reader NOLOGIN;
CREATE ROLE zj_ask LOGIN PASSWORD :'pw' IN ROLE zj_reader;
ALTER ROLE zj_ask SET statement_timeout = '10s';
ALTER ROLE zj_ask SET default_transaction_read_only = on;
ALTER ROLE zj_ask SET search_path = public;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
