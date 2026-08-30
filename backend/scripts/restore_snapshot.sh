#!/usr/bin/env bash
# Restore backend/seed/data/suwapath_snapshot.dump onto the compose Postgres.
# Run from /opt/suwapath on the VPS (or any host with docker compose + .env):
#
#   cat backend/seed/data/suwapath_snapshot.dump | bash backend/scripts/restore_snapshot.sh
#
# Drops and recreates the database — destructive. Intended for cloning a known
# local snapshot onto production during demo prep.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"

set -a
source .env
set +a

echo "Stopping backend..."
docker compose stop backend

echo "Recreating ${POSTGRES_DB}..."
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS ${POSTGRES_DB};
CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};
SQL

echo "Restoring snapshot from stdin..."
docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl

echo "Starting backend..."
docker compose up -d backend
