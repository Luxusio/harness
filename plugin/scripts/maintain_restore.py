#!/usr/bin/env python3
"""Compatibility wrapper for hygiene_restore.py.

Older docs and commit messages may still reference:
  python3 plugin/scripts/maintain_restore.py <archive-path>

Keep this wrapper until at least one compatibility release after the canonical
`hygiene_restore.py` command is installed.
"""
from __future__ import annotations

import sys
from hygiene_restore import _strip_sha7_suffix, main, restore


if __name__ == "__main__":
    sys.exit(main())
