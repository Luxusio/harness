#!/usr/bin/env python3
"""Unified tooling/capability probe for structural and symbol-navigation helpers.

Subcommands:
  lsp-detect
  cclsp-detect
  ast-grep-detect
  symbol-hint <query-type>
  ast-grep-hint <action> [pattern]
"""

from __future__ import annotations

import argparse
import glob as globmod
import json
import os
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from _lib import is_profile_enabled, is_tooling_ready


def detect_lsp() -> dict:
    result = {
        "lsp_ready": False,
        "servers_found": [],
        "languages": [],
        "configs_found": [],
        "details": [],
    }

    lang_indicators = {
        "typescript": ["tsconfig.json", "package.json"],
        "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"],
        "go": ["go.mod"],
        "rust": ["Cargo.toml"],
        "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "csharp": ["*.csproj", "*.sln"],
    }
    for language, indicators in lang_indicators.items():
        for indicator in indicators:
            if indicator.startswith("*"):
                if globmod.glob(indicator):
                    result["languages"].append(language)
                    break
            elif os.path.isfile(indicator):
                result["languages"].append(language)
                break

    server_binaries = {
        "typescript-language-server": "typescript",
        "tsserver": "typescript",
        "pyright-langserver": "python",
        "pylsp": "python",
        "gopls": "go",
        "rust-analyzer": "rust",
        "jdtls": "java",
    }
    for binary, language in server_binaries.items():
        path = shutil.which(binary)
        if path:
            result["servers_found"].append({"name": binary, "language": language, "path": path})
            result["details"].append(f"Server found: {binary} ({path})")

    for config in [
        ".vscode/settings.json",
        "pyrightconfig.json",
        "tsconfig.json",
        ".eslintrc.json",
        ".eslintrc.js",
        ".eslintrc.yml",
        "gopls.json",
    ]:
        if os.path.isfile(config):
            result["configs_found"].append(config)

    result["lsp_ready"] = len(result["servers_found"]) > 0
    if result["lsp_ready"]:
        result["details"].append(f"LSP ready with {len(result['servers_found'])} server(s)")
    else:
        result["details"].append("No LSP servers found on PATH")
    result["languages"] = sorted(set(result["languages"]))
    return result


def detect_ast_grep() -> dict:
    result = {
        "ast_grep_ready": False,
        "binary_path": None,
        "config_found": False,
        "rules_found": 0,
        "details": [],
    }

    for name in ["ast-grep", "sg"]:
        path = shutil.which(name)
        if path:
            result["binary_path"] = path
            result["details"].append(f"Binary found: {path}")
            break
    if not result["binary_path"]:
        result["details"].append("No ast-grep/sg binary found on PATH")
        return result

    for config in ["sgconfig.yml", "sgconfig.yaml", ".ast-grep/sgconfig.yml"]:
        if os.path.isfile(config):
            result["config_found"] = True
            result["details"].append(f"Config found: {config}")
            break

    for rule_dir in [".ast-grep/rules", "rules", "sgconfig/rules"]:
        if not os.path.isdir(rule_dir):
            continue
        rules = globmod.glob(os.path.join(rule_dir, "**/*.yml"), recursive=True)
        rules += globmod.glob(os.path.join(rule_dir, "**/*.yaml"), recursive=True)
        result["rules_found"] += len(rules)

    result["ast_grep_ready"] = True
    result["details"].append(f"Rules found: {result['rules_found']}")
    return result


def detect_cclsp() -> dict:
    result = {
        "cclsp_ready": False,
        "binary_found": False,
        "mcp_registered": False,
        "config_found": False,
        "details": [],
    }

    cclsp_path = shutil.which("cclsp")
    if cclsp_path:
        result["binary_found"] = True
        result["details"].append(f"cclsp binary found: {cclsp_path}")

    for config in [".cclsp.json", ".cclsp.yaml", ".cclsp.yml"]:
        if os.path.isfile(config):
            result["config_found"] = True
            result["details"].append(f"Config found: {config}")
            break

    mcp_config = ".mcp.json"
    if os.path.isfile(mcp_config):
        try:
            with open(mcp_config, encoding="utf-8") as handle:
                content = handle.read()
            if "lsp" in content.lower():
                result["mcp_registered"] = True
                result["details"].append("LSP tools found in MCP config")
        except Exception:
            pass

    result["cclsp_ready"] = result["binary_found"] or result["mcp_registered"]
    result["details"].append("cclsp ready" if result["cclsp_ready"] else "cclsp not available")
    return result


def ast_grep_hint(action: str, pattern: str | None = None) -> dict:
    if not is_profile_enabled("ast_grep_enabled"):
        if is_tooling_ready("ast_grep_ready"):
            return {
                "hint": "ast-grep is available but not enabled. Enable via profiles.ast_grep_enabled in manifest.",
                "fallback": "Using rg for text search.",
            }
        return {
            "hint": "ast-grep not available.",
            "fallback": "Using rg for text search. Install ast-grep for structural search.",
        }

    hints = {
        "search": f"Use: sg scan --pattern '{pattern or '$PATTERN'}' to find structural matches across the codebase.",
        "replace": f"Use: sg scan --pattern '{pattern or '$PATTERN'}' --rewrite '$NEW' for structural replacement.",
        "lint": "Use: sg scan --rule <rule-file> to check for pattern violations.",
        "check-removal": f"Use: sg scan --pattern '{pattern or '$PATTERN'}' — if zero matches, pattern is fully removed.",
    }
    return {"hint": hints.get(action, f"ast-grep available for action: {action}"), "ready": True}


def _grep_fallback(query_type: str) -> str:
    fallbacks = {
        "definition": "grep -rn 'function FUNCNAME\\|class FUNCNAME\\|const FUNCNAME\\|def FUNCNAME' .",
        "references": "grep -rn 'SYMBOL' . --include='*.ts' --include='*.py'",
        "rename": "grep -rn 'OLD_NAME' . to find all occurrences, then manual verification needed",
        "callsite": "grep -rn 'FUNCNAME(' . for call sites (may include false positives)",
        "type-usage": "grep -rn 'TypeName' . --include='*.ts' for type references",
        "symbols": "grep -rn 'function\\|class\\|interface\\|type ' . for symbol definitions",
        "diagnostics": "Run the project's type checker (tsc --noEmit, pyright, etc.)",
    }
    return fallbacks.get(query_type, "Use grep/rg for text-based search")


def symbol_hint(query_type: str) -> dict:
    lsp_ready = is_tooling_ready("lsp_ready")
    cclsp_ready = is_tooling_ready("cclsp_ready")
    symbol_lane_enabled = is_profile_enabled("symbol_lane_enabled")

    if not symbol_lane_enabled:
        if cclsp_ready or lsp_ready:
            return {
                "available": True,
                "enabled": False,
                "hint": "Symbol lane tooling available but not enabled. Enable via profiles.symbol_lane_enabled in manifest.",
                "fallback": _grep_fallback(query_type),
            }
        return {
            "available": False,
            "enabled": False,
            "hint": "Symbol lane not available.",
            "fallback": _grep_fallback(query_type),
        }

    provider = "cclsp" if cclsp_ready else "lsp"
    hints = {
        "definition": {
            "hint": "Use lsp_goto_definition for precise definition lookup.",
            "tool": "lsp_goto_definition",
            "provider": provider,
        },
        "references": {
            "hint": "Use lsp_find_references for all reference locations.",
            "tool": "lsp_find_references",
            "provider": provider,
        },
        "rename": {
            "hint": "Use lsp_prepare_rename + lsp_rename for safe, project-wide rename.",
            "tool": "lsp_rename",
            "provider": provider,
        },
        "callsite": {
            "hint": "Use lsp_find_references on the function to trace all call sites.",
            "tool": "lsp_find_references",
            "provider": provider,
        },
        "type-usage": {
            "hint": "Use lsp_find_references on the type to find all usage locations.",
            "tool": "lsp_find_references",
            "provider": provider,
        },
        "symbols": {
            "hint": "Use lsp_workspace_symbols to search for symbols by name across the workspace.",
            "tool": "lsp_workspace_symbols",
            "provider": provider,
        },
        "diagnostics": {
            "hint": "Use lsp_diagnostics to get type errors and warnings for a file.",
            "tool": "lsp_diagnostics",
            "provider": provider,
        },
    }
    if query_type in hints:
        return {"available": True, "enabled": True, **hints[query_type]}
    return {
        "available": True,
        "enabled": True,
        "hint": f"Symbol lane active (provider: {provider}). Use LSP tools for precise navigation.",
        "provider": provider,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Harness tooling probe")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("lsp-detect")
    subparsers.add_parser("cclsp-detect")
    subparsers.add_parser("ast-grep-detect")

    symbol_parser = subparsers.add_parser("symbol-hint")
    symbol_parser.add_argument("query_type", nargs="?", default="definition")

    ast_parser = subparsers.add_parser("ast-grep-hint")
    ast_parser.add_argument("action", nargs="?", default="search")
    ast_parser.add_argument("pattern", nargs="?", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "lsp-detect":
        print(json.dumps(detect_lsp(), indent=2))
        return 0
    if args.command == "cclsp-detect":
        print(json.dumps(detect_cclsp(), indent=2))
        return 0
    if args.command == "ast-grep-detect":
        print(json.dumps(detect_ast_grep(), indent=2))
        return 0
    if args.command == "symbol-hint":
        print(json.dumps(symbol_hint(args.query_type), indent=2))
        return 0
    if args.command == "ast-grep-hint":
        print(json.dumps(ast_grep_hint(args.action, args.pattern), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(0)
