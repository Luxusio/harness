#!/usr/bin/env python3
"""Detect cclsp (Claude Code LSP) readiness for the harness plugin."""
import json
import os
import shutil
import sys

def detect():
    result = {
        "cclsp_ready": False,
        "binary_found": False,
        "mcp_registered": False,
        "config_found": False,
        "details": []
    }

    # Check for cclsp binary
    cclsp_path = shutil.which("cclsp")
    if cclsp_path:
        result["binary_found"] = True
        result["details"].append("cclsp binary found: {}".format(cclsp_path))

    # Check for cclsp config
    for config in [".cclsp.json", ".cclsp.yaml", ".cclsp.yml"]:
        if os.path.isfile(config):
            result["config_found"] = True
            result["details"].append("Config found: {}".format(config))
            break

    # Check if MCP LSP tools are available (look for .mcp.json with lsp references)
    mcp_config = ".mcp.json"
    if os.path.isfile(mcp_config):
        try:
            with open(mcp_config) as f:
                content = f.read()
            if "lsp" in content.lower():
                result["mcp_registered"] = True
                result["details"].append("LSP tools found in MCP config")
        except Exception:
            pass

    # Ready if binary found or MCP tools registered
    result["cclsp_ready"] = result["binary_found"] or result["mcp_registered"]
    if result["cclsp_ready"]:
        result["details"].append("cclsp ready")
    else:
        result["details"].append("cclsp not available")

    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    try:
        detect()
    except Exception as e:
        print(json.dumps({"cclsp_ready": False, "error": str(e)}))
    sys.exit(0)
