#!/usr/bin/env bash
set -euo pipefail
# persistence-check.sh — Verify data persistence (DB connectivity)

echo "=== Persistence Check ==="

CHECKED=0
FAILED=0

if command -v psql &>/dev/null && [[ -n "${DATABASE_URL:-}" ]]; then
  echo "Checking PostgreSQL..."
  CHECKED=$((CHECKED + 1))
  if psql "$DATABASE_URL" -c "SELECT 1" > /dev/null 2>&1; then
    echo "  OK PostgreSQL connected"
    echo "[EVIDENCE] persistence: PASS postgresql — connected via DATABASE_URL"
  else
    echo "  FAIL PostgreSQL connection failed"
    echo "[EVIDENCE] persistence: FAIL postgresql — connection failed (DATABASE_URL set but psql returned error)"
    FAILED=$((FAILED + 1))
  fi
fi

if command -v mongosh &>/dev/null && [[ -n "${MONGODB_URI:-}" ]]; then
  echo "Checking MongoDB..."
  CHECKED=$((CHECKED + 1))
  if mongosh "$MONGODB_URI" --eval "db.runCommand({ping:1})" > /dev/null 2>&1; then
    echo "  OK MongoDB connected"
    echo "[EVIDENCE] persistence: PASS mongodb — ping succeeded via MONGODB_URI"
  else
    echo "  FAIL MongoDB connection failed"
    echo "[EVIDENCE] persistence: FAIL mongodb — ping failed (MONGODB_URI set but mongosh returned error)"
    FAILED=$((FAILED + 1))
  fi
fi

if [[ -f .env ]] || [[ -f .env.local ]]; then
  echo "Environment files found — check for DB connection strings"
fi

if [[ $CHECKED -eq 0 ]]; then
  echo "SKIP: no persistence targets detected"
  echo "[EVIDENCE] persistence: SKIP none — no DATABASE_URL or MONGODB_URI set"
fi

echo "=== Persistence Check complete ==="

[[ $FAILED -eq 0 ]]
