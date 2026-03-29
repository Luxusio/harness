#!/usr/bin/env python3
"""ast-grep structural search lane routing hints."""
import json
import os
import sys

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import is_tooling_ready, is_profile_enabled

def get_hint(action, pattern=None):
    if not is_profile_enabled("ast_grep_enabled"):
        if is_tooling_ready("ast_grep_ready"):
            return {
                "hint": "ast-grep is available but not enabled. Enable via profiles.ast_grep_enabled in manifest.",
                "fallback": "Using rg for text search."
            }
        return {
            "hint": "ast-grep not available.",
            "fallback": "Using rg for text search. Install ast-grep for structural search."
        }

    hints = {
        "search": f"Use: sg scan --pattern '{pattern or '$PATTERN'}' to find structural matches across the codebase.",
        "replace": f"Use: sg scan --pattern '{pattern or '$PATTERN'}' --rewrite '$NEW' for structural replacement.",
        "lint": "Use: sg scan --rule <rule-file> to check for pattern violations.",
        "check-removal": f"Use: sg scan --pattern '{pattern or '$PATTERN'}' — if zero matches, pattern is fully removed."
    }

    return {
        "hint": hints.get(action, f"ast-grep available for action: {action}"),
        "ready": True
    }

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "search"
    pattern = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        result = get_hint(action, pattern)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"hint": "ast-grep lane error", "error": str(e)}))
    sys.exit(0)
