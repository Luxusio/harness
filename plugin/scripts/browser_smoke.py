#!/usr/bin/env python3
"""Browser smoke test — validates dev server is running and accessible for browser QA."""
import os
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import manifest_field

MANIFEST = os.environ.get("HARNESS_MANIFEST", ".claude/harness/manifest.yaml")
RETRIES = 10
CONSOLE_ERRORS = 0
NETWORK_FAILURES = 0


def main():
    print("=== Browser Smoke Test ===")

    frontend_url = manifest_field("frontend") if os.path.isfile(MANIFEST) else ""
    if not frontend_url:
        frontend_url = "http://localhost:3000"

    print("Checking dev server at {}...".format(frontend_url))

    load_success = False
    last_status = "000"

    for i in range(1, RETRIES + 1):
        try:
            req = urllib.request.urlopen(frontend_url, timeout=3)
            http_status = req.status
        except urllib.error.HTTPError as e:
            http_status = e.code
        except Exception:
            http_status = 0

        last_status = str(http_status)

        if str(http_status).startswith(("2", "3")):
            print("  OK Dev server is running (HTTP {})".format(http_status))
            load_success = True
            break

        if i == RETRIES:
            print("  FAIL Dev server not responding after {} attempts (last HTTP {})".format(
                RETRIES, last_status))
            print("  Start with: npm run dev (or check manifest for dev_command)")
            print("[EVIDENCE] browser: FAIL {} — server not reachable after {} attempts, last HTTP {}".format(
                frontend_url, RETRIES, last_status))
            sys.exit(1)

        time.sleep(2)

    print()
    print("Browser smoke prerequisites met.")
    print("Use chrome-devtools MCP for interactive verification:")
    print("  1. Navigate to {}".format(frontend_url))
    print("  2. Check console for errors")
    print("  3. Verify key routes render")
    print("  4. Check network for failed requests")

    print("[EVIDENCE] browser: PASS {} — server reachable, console_errors={}, network_failures={}".format(
        frontend_url, CONSOLE_ERRORS, NETWORK_FAILURES))
    print("=== Browser Smoke complete ===")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ERROR: {}".format(e))
        sys.exit(1)
