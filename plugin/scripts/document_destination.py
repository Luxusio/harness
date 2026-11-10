#!/usr/bin/env python3
"""Select documentation policy from the wiki plugin's injected environment.

This is a read-only selection, not authentication or a remote health check.
No repository paths, credentials or document contents are copied into state.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit


LOCAL_NOTICE = (
    "[harness-doc-gate] Durable decisions require doc/ update before final; "
    "if no doc applies, keep the PLAN durable-doc rationale specific."
)
REMOTE_NOTICE = (
    "[harness-doc-gate] Business knowledge: search/read/update with atlassian MCP. "
    "Keep WBS, runtime instructions and product assets in the repository."
)
UNAVAILABLE_NOTICE = (
    "[harness-doc-gate] Configured knowledge connection is unavailable: repair its "
    "configuration; business-document writes remain pending."
)


CONNECTION_FIELDS = ("SITE", "SPACE", "SPACE_ID", "ROOT_PAGE_ID", "MEMORY_PAGE_ID", "FEEDBACK_PAGE_ID", "POLL_INTERVAL_SECONDS")
PREFIX = "WIKI_ATLASSIAN_"


def select_destination(environment=None):
    """Select without reading configuration files, executing mise, or exposing values."""
    environment = dict(os.environ if environment is None else environment)
    try:
        enabled = environment.get(PREFIX + "ENABLED")
        if enabled == "false":
            return {"mode": "local", "notice": LOCAL_NOTICE}
        if enabled is None and not any(PREFIX + key in environment for key in CONNECTION_FIELDS):
            return {"mode": "local", "notice": LOCAL_NOTICE}
        if enabled != "true":
            raise ValueError("invalid activation")
        def text(key):
            value = environment.get(PREFIX + key)
            if not isinstance(value, str):
                raise ValueError("missing connection setting")
            return value
        site = text("SITE")
        origin = urlsplit(site)
        if (origin.scheme != "https" or not site.startswith("https://") or not origin.hostname
                or origin.username is not None or origin.password is not None
                or origin.path not in ("", "/") or "?" in site or "#" in site
                or any(c.isspace() or ord(c) < 32 for c in site) or "\\" in site
                or origin.netloc.endswith(":") or origin.port is not None and not 1 <= origin.port <= 65535):
            raise ValueError("invalid origin")
        # Match Java URI's server-authority rules, including DNS versus IPv4.
        host = origin.hostname
        if origin.netloc.startswith("["):
            # urlsplit validates IPv6; restrict its extra IPvFuture/zone forms
            # to Java URI's IPv6 address and scope-ID alphabet.
            if not re.fullmatch(r"[A-Fa-f0-9:.]+(?:%[A-Za-z0-9_.]+)?", host):
                raise ValueError("invalid host")
        else:
            labels = host.removesuffix(".").split(".")
            if any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", part) for part in labels):
                raise ValueError("invalid host")
            if len(labels) > 1 and not re.match(r"[A-Za-z]", labels[-1]):
                if host.endswith(".") or len(labels) != 4 or any(not re.fullmatch(r"[0-9]+", part) or int(part) > 255 for part in labels):
                    raise ValueError("invalid IPv4 host")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", text("SPACE")):
            raise ValueError("invalid space")
        for key in ("SPACE_ID", "ROOT_PAGE_ID") + tuple(k for k in ("MEMORY_PAGE_ID", "FEEDBACK_PAGE_ID") if PREFIX + k in environment):
            if not re.fullmatch(r"[1-9][0-9]*", text(key)):
                raise ValueError("invalid native ID")
        if PREFIX + "POLL_INTERVAL_SECONDS" in environment:
            poll = text("POLL_INTERVAL_SECONDS")
            if not re.fullmatch(r"[0-9]{1,20}", poll) or not 30 <= int(poll) <= 86400:
                raise ValueError("invalid polling interval")
        for key in ("ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"):
            value = environment.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("missing credential")
        return {"mode": "external", "backend": "atlassian", "notice": REMOTE_NOTICE}
    except (ValueError, TypeError):
        return {"mode": "unavailable", "backend": "atlassian", "notice": UNAVAILABLE_NOTICE}


def policy(environment=None):
    selected = select_destination(environment)
    if selected["mode"] == "local":
        guidance = "Business-document references are repository doc/<area>/<TYPE>__<name>.md paths. Read/write selected requirements before source implementation; local req_scaffold.py may help. Keep source provenance and independent document review."
    elif selected["mode"] == "external":
        guidance = "Business-document references are native document IDs/URLs with observed versions from atlassian MCP; use REQ: confluence:<page-id>@<version> in PLAN for requirement presence checks. Use that MCP for requirements, guidance, decisions, knowledge and reusable runbooks; provide original evidence to reviewers/QA. Do not create duplicate business-knowledge files in the repository. Repository product documentation, source assets, runtime instructions and WBS controls remain repository-owned. A failed/unavailable MCP does not authorize local fallback writes."
    else:
        guidance = "Business-document writes and required documentation completion are pending until the configured connection is usable. Continue independent product work where possible; retain actual unresolved documentation blockers. Do not infer that an invalid connection means local mode."
    return {**selected, "policy": guidance}


if __name__ == "__main__":
    print(json.dumps(policy(), ensure_ascii=False))
