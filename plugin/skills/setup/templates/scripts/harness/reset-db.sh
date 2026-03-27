#!/usr/bin/env bash
set -euo pipefail

# Database reset and seed script.
# Drops and recreates the development database, then seeds initial data.
# Adapt to your project's database setup.
#
# {{RESET_DB_COMMANDS}}

echo "=== Database Reset ==="

# Example for common setups — uncomment and adapt:

# PostgreSQL with migrations:
# dropdb --if-exists myapp_dev
# createdb myapp_dev
# npm run migrate
# npm run seed

# SQLite:
# rm -f db/development.sqlite3
# npm run migrate
# npm run seed

# Prisma:
# npx prisma migrate reset --force

# Django:
# python manage.py flush --noinput
# python manage.py migrate
# python manage.py loaddata initial_data

echo "  - No database reset configured yet. Add commands for your project."
echo "  - If this project has no database, this script can be a no-op."
exit 0
