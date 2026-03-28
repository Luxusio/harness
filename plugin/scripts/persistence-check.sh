#!/usr/bin/env bash
set -euo pipefail
# persistence-check.sh — Verify data persistence (DB connectivity)

echo "=== Persistence Check ==="

if command -v psql &>/dev/null && [[ -n "${DATABASE_URL:-}" ]]; then
  echo "Checking PostgreSQL..."
  psql "$DATABASE_URL" -c "SELECT 1" > /dev/null 2>&1 && echo "  ✓ PostgreSQL connected" || echo "  ✗ PostgreSQL connection failed"
fi

if command -v mongosh &>/dev/null && [[ -n "${MONGODB_URI:-}" ]]; then
  echo "Checking MongoDB..."
  mongosh "$MONGODB_URI" --eval "db.runCommand({ping:1})" > /dev/null 2>&1 && echo "  ✓ MongoDB connected" || echo "  ✗ MongoDB connection failed"
fi

if [[ -f .env ]] || [[ -f .env.local ]]; then
  echo "Environment files found — check for DB connection strings"
fi

echo "=== Persistence Check complete ==="
