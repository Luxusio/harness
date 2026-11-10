#!/usr/bin/env python3
"""Select documentation policy from the knowledge plugin's global activation.

This is a read-only selection, not authentication or a remote health check.
No repository paths, credentials or document contents are copied into state.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit


LOCAL_NOTICE = (
    "[harness-doc-gate] Durable decisions require doc/ update before final; "
    "if no doc applies, keep the PLAN durable-doc rationale specific."
)
REMOTE_NOTICE = (
    "[harness-doc-gate] Business knowledge: search/read/update with confluence MCP. "
    "Keep WBS, runtime instructions and product assets in the repository."
)
UNAVAILABLE_NOTICE = (
    "[harness-doc-gate] Configured knowledge connection is unavailable: repair its "
    "configuration; business-document writes remain pending."
)


def _config_path(environment):
    value = environment.get("KNOWLEDGE_CONFLUENCE_CONFIG")
    if value is not None:
        path = Path(value)
        if not value or not path.is_absolute() or str(path) != value or ".." in path.parts:
            raise ValueError("invalid config path")
        return path
    return Path(environment.get("HOME") or str(Path.home())) / ".config/knowledge-confluence/config.json"


def select_destination(environment=None):
    """Return local/external/unavailable without exposing untrusted config text."""
    environment = os.environ if environment is None else environment
    try:
        path = _config_path(environment)
        # Read config through no-follow descriptors: a symlink or special file
        # must not turn an advisory hook into an arbitrary home-file reader.
        if not os.path.lexists(path):
            if "KNOWLEDGE_CONFLUENCE_CONFIG" in environment:
                raise ValueError("explicit config is missing")
            return {"mode": "local", "notice": LOCAL_NOTICE}
        for parent in path.parents:
            if parent.is_symlink():
                raise ValueError("symlink config parent")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_mode & 0o022 or before.st_size > 65536:
                raise ValueError("unsafe config")
            data = stream.read(65537)
            after = os.fstat(stream.fileno())
            if len(data) > 65536 or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("config changed")
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("config replaced")
        document = json.loads(data)
        if not isinstance(document, dict) or type(document.get("schemaVersion", 1)) is not int or document.get("schemaVersion", 1) != 1:
            raise ValueError("invalid schema")
        enabled = document.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("invalid activation")
        if not enabled:
            return {"mode": "local", "notice": LOCAL_NOTICE}
        site = document.get("site")
        if not isinstance(site, str):
            raise ValueError("invalid origin")
        origin = urlsplit(site)
        if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.path not in ("", "/") or origin.query or origin.fragment:
            raise ValueError("invalid origin")
        if not isinstance(document.get("space"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", document["space"]):
            raise ValueError("invalid space")
        for key in ("spaceId", "rootPageId"):
            if not isinstance(document.get(key), str) or not re.fullmatch(r"[1-9][0-9]*", document[key]):
                raise ValueError("invalid root")
        return {"mode": "external", "backend": "confluence", "notice": REMOTE_NOTICE}
    except (OSError, ValueError, TypeError):
        return {"mode": "unavailable", "backend": "confluence", "notice": UNAVAILABLE_NOTICE}


def policy(environment=None):
    selected = select_destination(environment)
    if selected["mode"] == "local":
        guidance = "Business-document references are repository doc/<area>/<TYPE>__<name>.md paths. Read/write selected requirements before source implementation; local req_scaffold.py may help. Keep source provenance and independent document review."
    elif selected["mode"] == "external":
        guidance = "Business-document references are native document IDs/URLs with observed versions from confluence MCP; use REQ: confluence:<page-id>@<version> in PLAN for requirement presence checks. Use that MCP for requirements, guidance, decisions, knowledge and reusable runbooks; provide original evidence to reviewers/QA. Do not create duplicate business-knowledge files in the repository. Repository product documentation, source assets, runtime instructions and WBS controls remain repository-owned. A failed/unavailable MCP does not authorize local fallback writes."
    else:
        guidance = "Business-document writes and required documentation completion are pending until the configured connection is usable. Continue independent product work where possible; retain actual unresolved documentation blockers. Do not infer that an invalid connection means local mode."
    return {**selected, "policy": guidance}


if __name__ == "__main__":
    print(json.dumps(policy(), ensure_ascii=False))
