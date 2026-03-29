#!/usr/bin/env python3
"""Detect ast-grep readiness for the harness plugin."""
import json
import os
import shutil
import sys
import glob as globmod

def detect():
    result = {
        "ast_grep_ready": False,
        "binary_path": None,
        "config_found": False,
        "rules_found": 0,
        "details": []
    }

    # Check for ast-grep binary
    for name in ["ast-grep", "sg"]:
        path = shutil.which(name)
        if path:
            result["binary_path"] = path
            result["details"].append(f"Binary found: {path}")
            break

    if not result["binary_path"]:
        result["details"].append("No ast-grep/sg binary found on PATH")
        print(json.dumps(result))
        return

    # Check for config files
    for config in ["sgconfig.yml", "sgconfig.yaml", ".ast-grep/sgconfig.yml"]:
        if os.path.isfile(config):
            result["config_found"] = True
            result["details"].append(f"Config found: {config}")
            break

    # Check for existing rules
    rule_dirs = [".ast-grep/rules", "rules", "sgconfig/rules"]
    for rd in rule_dirs:
        if os.path.isdir(rd):
            rules = globmod.glob(os.path.join(rd, "**/*.yml"), recursive=True)
            rules += globmod.glob(os.path.join(rd, "**/*.yaml"), recursive=True)
            result["rules_found"] += len(rules)

    # Ready if binary exists (config/rules are optional)
    result["ast_grep_ready"] = True
    result["details"].append(f"Rules found: {result['rules_found']}")

    print(json.dumps(result))

if __name__ == "__main__":
    try:
        detect()
    except Exception as e:
        print(json.dumps({"ast_grep_ready": False, "error": str(e)}))
    sys.exit(0)
