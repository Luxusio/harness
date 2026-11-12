#!/usr/bin/env python3
"""Start the standalone document MCP; stdout belongs exclusively to JSON-RPC."""
from pathlib import Path
import sys

def main():
    root = Path(__file__).resolve().parents[1]
    helpers = root / 'scripts'
    if not (helpers / 'personal_feedback.py').is_file():
        helpers = root.parent / 'plugin/scripts'
    # Explicit trusted code roots are required because installed hosts use -I.
    # Keep consuming cwd for project selection; no shell or interpreter re-exec.
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(root), str(helpers)]
    from wiki_atlassian.main import main as serve
    return serve()

if __name__ == "__main__":
    raise SystemExit(main())
