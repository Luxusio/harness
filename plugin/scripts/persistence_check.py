#!/usr/bin/env python3
"""Persistence check — verify data persistence (DB connectivity)."""
import os
import shutil
import subprocess
import sys


def main():
    print("=== Persistence Check ===")

    checked = 0
    failed = 0

    # PostgreSQL check
    if shutil.which("psql") and os.environ.get("DATABASE_URL"):
        database_url = os.environ["DATABASE_URL"]
        print("Checking PostgreSQL...")
        checked += 1
        result = subprocess.run(
            ["psql", database_url, "-c", "SELECT 1"],
            capture_output=True,
        )
        if result.returncode == 0:
            print("  OK PostgreSQL connected")
            print("[EVIDENCE] persistence: PASS postgresql — connected via DATABASE_URL")
        else:
            print("  FAIL PostgreSQL connection failed")
            print("[EVIDENCE] persistence: FAIL postgresql — connection failed (DATABASE_URL set but psql returned error)")
            failed += 1

    # MongoDB check
    if shutil.which("mongosh") and os.environ.get("MONGODB_URI"):
        mongodb_uri = os.environ["MONGODB_URI"]
        print("Checking MongoDB...")
        checked += 1
        result = subprocess.run(
            ["mongosh", mongodb_uri, "--eval", "db.runCommand({ping:1})"],
            capture_output=True,
        )
        if result.returncode == 0:
            print("  OK MongoDB connected")
            print("[EVIDENCE] persistence: PASS mongodb — ping succeeded via MONGODB_URI")
        else:
            print("  FAIL MongoDB connection failed")
            print("[EVIDENCE] persistence: FAIL mongodb — ping failed (MONGODB_URI set but mongosh returned error)")
            failed += 1

    # .env file presence
    if os.path.isfile(".env") or os.path.isfile(".env.local"):
        print("Environment files found — check for DB connection strings")

    if checked == 0:
        print("SKIP: no persistence targets detected")
        print("[EVIDENCE] persistence: SKIP none — no DATABASE_URL or MONGODB_URI set")

    print("=== Persistence Check complete ===")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ERROR: {}".format(e))
        sys.exit(1)
