#!/usr/bin/env python3
"""Symbol lane routing hints for the harness plugin."""
import json
import os
import sys

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import is_tooling_ready, is_profile_enabled

def get_hint(query_type):
    lsp_ready = is_tooling_ready("lsp_ready")
    cclsp_ready = is_tooling_ready("cclsp_ready")
    symbol_lane_enabled = is_profile_enabled("symbol_lane_enabled")

    if not symbol_lane_enabled:
        if cclsp_ready or lsp_ready:
            return {
                "available": True,
                "enabled": False,
                "hint": "Symbol lane tooling available but not enabled. Enable via profiles.symbol_lane_enabled in manifest.",
                "fallback": get_grep_hint(query_type)
            }
        return {
            "available": False,
            "enabled": False,
            "hint": "Symbol lane not available.",
            "fallback": get_grep_hint(query_type)
        }

    # Determine provider priority
    provider = "cclsp" if cclsp_ready else "lsp"

    hints = {
        "definition": {
            "hint": "Use lsp_goto_definition for precise definition lookup.",
            "tool": "lsp_goto_definition",
            "provider": provider
        },
        "references": {
            "hint": "Use lsp_find_references for all reference locations.",
            "tool": "lsp_find_references",
            "provider": provider
        },
        "rename": {
            "hint": "Use lsp_prepare_rename + lsp_rename for safe, project-wide rename.",
            "tool": "lsp_rename",
            "provider": provider
        },
        "callsite": {
            "hint": "Use lsp_find_references on the function to trace all call sites.",
            "tool": "lsp_find_references",
            "provider": provider
        },
        "type-usage": {
            "hint": "Use lsp_find_references on the type to find all usage locations.",
            "tool": "lsp_find_references",
            "provider": provider
        },
        "symbols": {
            "hint": "Use lsp_workspace_symbols to search for symbols by name across the workspace.",
            "tool": "lsp_workspace_symbols",
            "provider": provider
        },
        "diagnostics": {
            "hint": "Use lsp_diagnostics to get type errors and warnings for a file.",
            "tool": "lsp_diagnostics",
            "provider": provider
        }
    }

    if query_type in hints:
        return {"available": True, "enabled": True, **hints[query_type]}

    return {
        "available": True,
        "enabled": True,
        "hint": "Symbol lane active (provider: {}). Use LSP tools for precise navigation.".format(provider),
        "provider": provider
    }

def get_grep_hint(query_type):
    fallbacks = {
        "definition": "grep -rn 'function FUNCNAME\\|class FUNCNAME\\|const FUNCNAME\\|def FUNCNAME' .",
        "references": "grep -rn 'SYMBOL' . --include='*.ts' --include='*.py'",
        "rename": "grep -rn 'OLD_NAME' . to find all occurrences, then manual verification needed",
        "callsite": "grep -rn 'FUNCNAME(' . for call sites (may include false positives)",
        "type-usage": "grep -rn 'TypeName' . --include='*.ts' for type references",
        "symbols": "grep -rn 'function\\|class\\|interface\\|type ' . for symbol definitions",
        "diagnostics": "Run the project's type checker (tsc --noEmit, pyright, etc.)"
    }
    return fallbacks.get(query_type, "Use grep/rg for text-based search")

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "definition"
    try:
        result = get_hint(query)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"hint": "symbol lane error", "error": str(e)}))
    sys.exit(0)
