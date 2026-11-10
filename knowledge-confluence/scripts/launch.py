#!/usr/bin/env python3
"""Start the standalone document MCP; stdout belongs exclusively to JSON-RPC."""
import os
from pathlib import Path
import shutil
import sys

def main():
    root = Path(__file__).resolve().parents[1]
    jar = root / "lib/knowledge-confluence.jar"
    if not jar.is_file():
        jar = root / "build/libs/knowledge-confluence.jar"
    if not jar.is_file():
        sys.stderr.write("knowledge-confluence: build the plugin before starting its MCP.\n")
        return 1
    explicit = os.environ.get("KNOWLEDGE_CONFLUENCE_JAVA")
    home = os.environ.get("JAVA_HOME")
    if explicit:
        java = Path(explicit)
        if not java.is_absolute() or not java.is_file():
            sys.stderr.write("knowledge-confluence: KNOWLEDGE_CONFLUENCE_JAVA must name an absolute Java executable.\n")
            return 1
    elif home:
        java = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if not java.is_absolute() or not java.is_file():
            sys.stderr.write("knowledge-confluence: JAVA_HOME must name an installed Java runtime.\n")
            return 1
    else:
        executable = shutil.which("java")
        if not executable:
            sys.stderr.write("knowledge-confluence: Java 21 or later is required.\n")
            return 1
        java = Path(executable)
    # Environment-manager shims must not resolve from the consuming project's cwd.
    os.chdir(root)
    os.execv(str(java), [str(java), "-jar", str(jar)])
    return 1

if __name__ == "__main__":
    raise SystemExit(main())

