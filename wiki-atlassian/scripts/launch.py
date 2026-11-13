#!/usr/bin/env python3
"""Start the standalone document MCP; stdout belongs exclusively to JSON-RPC."""
from pathlib import Path
import os
import sys

def main():
    root = Path(__file__).resolve().parents[1]
    helpers = root / 'scripts'
    if not (helpers / 'personal_feedback.py').is_file():
        helpers = root.parent / 'plugin/scripts'
    # Explicit trusted code roots are required because installed hosts use -I.
    # Keep consuming cwd for project selection.
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(root), str(helpers)]
    if sys.argv[1:2] == ['--mise']:
        # Raw source and portable payloads share the exact installer policy.
        # Validate before importing any product code, including when mise exec
        # used PATH Python because the requested tool was absent.
        from mise_python import select_python, MisePythonError
        try:
            interpreter = select_python(require_yaml=True, _requested_version='3.12')
            os.execv(interpreter, [interpreter, '-I', '-B', str(Path(__file__).resolve()), *sys.argv[2:]])
        except MisePythonError as error:
            print(str(error), file=sys.stderr)
            return 1
        except OSError:
            print('The selected mise Python could not start the wiki plugin.', file=sys.stderr)
            return 1
    from wiki_atlassian.main import main as serve
    return serve()

if __name__ == "__main__":
    raise SystemExit(main())
