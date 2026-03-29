#!/usr/bin/env python3
"""Detect native LSP server readiness for the harness plugin."""
import json
import os
import shutil
import sys

def detect():
    result = {
        "lsp_ready": False,
        "servers_found": [],
        "languages": [],
        "configs_found": [],
        "details": []
    }

    # Language detection via project files
    lang_indicators = {
        "typescript": ["tsconfig.json", "package.json"],
        "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"],
        "go": ["go.mod"],
        "rust": ["Cargo.toml"],
        "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "csharp": ["*.csproj", "*.sln"],
    }

    for lang, indicators in lang_indicators.items():
        for ind in indicators:
            if ind.startswith("*"):
                # glob pattern
                import glob as g
                if g.glob(ind):
                    result["languages"].append(lang)
                    break
            elif os.path.isfile(ind):
                result["languages"].append(lang)
                break

    # Check for LSP server binaries
    server_binaries = {
        "typescript-language-server": "typescript",
        "tsserver": "typescript",
        "pyright-langserver": "python",
        "pylsp": "python",
        "gopls": "go",
        "rust-analyzer": "rust",
        "jdtls": "java",
    }

    for binary, lang in server_binaries.items():
        path = shutil.which(binary)
        if path:
            result["servers_found"].append({"name": binary, "language": lang, "path": path})
            result["details"].append("Server found: {} ({})".format(binary, path))

    # Check for LSP-related configs
    lsp_configs = [
        ".vscode/settings.json",
        "pyrightconfig.json",
        "tsconfig.json",
        ".eslintrc.json",
        ".eslintrc.js",
        ".eslintrc.yml",
        "gopls.json",
    ]

    for config in lsp_configs:
        if os.path.isfile(config):
            result["configs_found"].append(config)

    # Ready if any server found
    result["lsp_ready"] = len(result["servers_found"]) > 0
    if result["lsp_ready"]:
        result["details"].append("LSP ready with {} server(s)".format(len(result["servers_found"])))
    else:
        result["details"].append("No LSP servers found on PATH")

    # Deduplicate languages
    result["languages"] = sorted(set(result["languages"]))

    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    try:
        detect()
    except Exception as e:
        print(json.dumps({"lsp_ready": False, "error": str(e)}))
    sys.exit(0)
