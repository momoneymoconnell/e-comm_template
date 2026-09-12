#!/usr/bin/env bash
# Probe every service's /health endpoint and print a compact status table.
# Used by `make health`. Exits non-zero if anything is unhealthy, so it is also
# usable as a CI smoke test after `docker compose up`.
set -uo pipefail

declare -a SERVICES=(
  "gateway|8080"
  "auth|8001"
  "catalog|8002"
  "orders|8003"
  "payments|8004"
  "analytics|8005"
  "notifications|8006"
)

printf "%-16s %-8s %-10s %s\n" "SERVICE" "PORT" "STATUS" "DATABASE"
printf "%-16s %-8s %-10s %s\n" "-------" "----" "------" "--------"

failures=0
for entry in "${SERVICES[@]}"; do
  name="${entry%%|*}"
  port="${entry##*|}"
  body=$(curl -fsS --max-time 4 "http://localhost:${port}/health" 2>/dev/null) || body=""

  if [[ -z "$body" ]]; then
    printf "%-16s %-8s %-10s %s\n" "$name" "$port" "DOWN" "-"
    failures=$((failures + 1))
    continue
  fi

  # Parse without requiring jq, which is not installed everywhere.
  status=$(printf '%s' "$body" | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')
  database=$(printf '%s' "$body" | sed -n 's/.*"database":\([a-z]*\).*/\1/p')
  printf "%-16s %-8s %-10s %s\n" "$name" "$port" "${status:-?}" "${database:-?}"
  [[ "$status" == "ok" ]] || failures=$((failures + 1))
done

echo ""
if [[ $failures -eq 0 ]]; then
  echo "All services healthy."
else
  echo "$failures service(s) unhealthy. Inspect with: make logs SVC=<name>"
fi
exit $failures
