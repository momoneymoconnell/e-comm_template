#!/usr/bin/env bash
# =============================================================================
# Container entrypoint for every Python service.
#
# Responsibilities, in order:
#   1. Wait for Postgres to accept queries (not just open a socket).
#   2. Apply that service's Alembic migrations.
#   3. Exec uvicorn.
#
# Running migrations here — rather than in a separate job — means the schema
# can never be older than the code that queries it, which is the failure mode
# that produces "column does not exist" at 3am. Alembic takes a Postgres
# advisory lock, so starting three replicas at once is safe: one migrates, the
# others wait and then find nothing to do.
# =============================================================================
set -euo pipefail

SERVICE_DIR="${SERVICE_DIR:?SERVICE_DIR must be set (e.g. /app/services/auth)}"
APP_MODULE="${APP_MODULE:?APP_MODULE must be set (e.g. ecom_auth.main:app)}"
PORT="${PORT:-8000}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"

log() { printf '{"event":"%s","service":"%s","ts":"%s"}\n' "$1" "${SERVICE_NAME:-unknown}" "$(date -u +%FT%TZ)"; }

# --- 1. Wait for Postgres ----------------------------------------------------
# Compose's `depends_on: service_healthy` already gates on pg_isready, but a
# service can also be started on its own with `docker compose run`, and cloud
# schedulers give no such guarantee. This loop makes the container correct in
# every case.
if [[ "${RUN_MIGRATIONS}" == "true" ]]; then
  log "waiting_for_database"
  for attempt in $(seq 1 60); do
    if python - <<'PYCHECK'
import os, sys
import psycopg
try:
    with psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "ecom"),
        user=os.environ.get("POSTGRES_USER", "ecom"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        connect_timeout=3,
    ) as conn:
        conn.execute("SELECT 1")
except Exception as exc:
    print(f"not ready: {exc}", file=sys.stderr)
    sys.exit(1)
PYCHECK
    then
      log "database_ready"
      break
    fi
    if [[ "${attempt}" == "60" ]]; then
      log "database_unreachable_giving_up"
      exit 1
    fi
    sleep 2
  done

  # --- 2. Migrate ------------------------------------------------------------
  log "running_migrations"
  cd "${SERVICE_DIR}"
  alembic upgrade head
  log "migrations_complete"
fi

# --- 3. Serve ----------------------------------------------------------------
cd "${SERVICE_DIR}"
log "starting_uvicorn"

# `exec` replaces this shell with uvicorn so it becomes PID 1 and receives
# SIGTERM directly. Without it, `docker stop` would signal bash, uvicorn would
# never shut down gracefully, and Docker would SIGKILL it 10 seconds later —
# cutting off in-flight requests.
exec uvicorn "${APP_MODULE}" \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --no-access-log \
  ${UVICORN_EXTRA_ARGS:-}
