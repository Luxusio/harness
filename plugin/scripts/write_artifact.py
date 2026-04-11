#!/usr/bin/env python3
"""Shim: delegates to plugin-legacy/scripts/write_artifact.py.

This file is the plugin/scripts/ entry point for the write_artifact CLI.
During the plugin-legacy -> plugin transition it simply loads the legacy
implementation via importlib and re-exports main().

When the legacy tree is removed this shim will be replaced by a direct copy
or reorganisation of the source.  Until then, callers may invoke either:

  python3 plugin/scripts/write_artifact.py  <subcommand> ...
  python3 plugin-legacy/scripts/write_artifact.py  <subcommand> ...

Both paths expose the same subcommand surface and behave identically.
"""

from __future__ import annotations

import importlib.util
import os
import sys


def _load_legacy_module():
    shim_dir = os.path.dirname(os.path.abspath(__file__))
    # Resolve repo root: plugin/scripts/ -> plugin/ -> repo root
    repo_root = os.path.dirname(os.path.dirname(shim_dir))
    legacy_path = os.path.join(repo_root, "plugin-legacy", "scripts", "write_artifact.py")
    if not os.path.isfile(legacy_path):
        print(
            f"ERROR: legacy write_artifact.py not found at {legacy_path}",
            file=sys.stderr,
        )
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("write_artifact_legacy", legacy_path)
    module = importlib.util.module_from_spec(spec)
    # Ensure the legacy script directory is on sys.path so its own imports work
    legacy_dir = os.path.dirname(legacy_path)
    if legacy_dir not in sys.path:
        sys.path.insert(0, legacy_dir)
    spec.loader.exec_module(module)
    return module


_legacy = _load_legacy_module()
main = _legacy.main

if __name__ == "__main__":
    main()
