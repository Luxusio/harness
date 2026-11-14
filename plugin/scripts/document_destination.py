#!/usr/bin/env python3
"""Select document policy from project YAML and private injected authentication.

Read-only: no mise parsing, network requests, package installation or state.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from wiki_platform import safe_read


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

CONFIG_PARTS = ("doc", "harness", "manifest.yaml")
MAX_CONFIG_BYTES = 64 * 1024
CONFIG_FIELDS = frozenset(("enabled", "site", "spaces", "pollIntervalSeconds"))
SPACE_FIELDS = frozenset(("key", "rootPageId", "memoryPageId", "feedbackPageId", "watchCommentPages"))
MAX_SPACES = 32


def _project_root(cwd):
    """Nearest Git directory/gitfile boundary; without Git, use only startup cwd."""
    start = Path(os.getcwd() if cwd is None else cwd).resolve(strict=True)
    if not start.is_dir():
        raise ValueError("invalid project directory")
    for current in (start, *start.parents):
        git = current / ".git"
        try:
            marker = git.lstat()
        except FileNotFoundError:
            continue
        if not (stat.S_ISDIR(marker.st_mode) or stat.S_ISREG(marker.st_mode)):
            raise ValueError("invalid Git boundary")
        return current
    return start


def _manifest_bytes(root):
    """Read through directory descriptors so no manifest component follows links."""
    try:
        return safe_read(root.joinpath(*CONFIG_PARTS), MAX_CONFIG_BYTES)
    except FileNotFoundError:
        return None


def _proves_no_key(source, key):
    """Recognize ordinary root keys only; ambiguous YAML always needs its parser."""
    seen_key = False
    first = True
    for line in source.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            continue
        if first and line == "---":
            first = False
            continue
        first = False
        match = re.match(r"([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:(?:[ \t]|$)", line)
        if not match or match.group(1) == key:
            return False
        seen_key = True
    return seen_key


def _read_config(cwd):
    raw = _manifest_bytes(_project_root(cwd))
    if raw is None:
        return None
    source = raw.decode("utf-8", errors="strict")
    if _proves_no_key(source, "wiki"):
        return None
    manifest = _yaml_mapping(source)
    if "wiki" not in manifest:
        return None
    config = manifest["wiki"]
    if not isinstance(config, dict) or set(config) - (CONFIG_FIELDS | {"backend"}):
        raise ValueError("invalid project configuration")
    if ("backend" in config and config["backend"] != "atlassian") or (config.get("enabled") is True and "backend" not in config):
        raise ValueError("unsupported wiki backend")
    return {key: value for key, value in config.items() if key != "backend"}


def _yaml_mapping(source):
    """Bounded callers share strict YAML; ordinary no-wiki paths need no parser."""
    import yaml

    class ProjectLoader(yaml.SafeLoader):
        depth = 0

        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise ValueError("configuration aliases are unsupported")
            collection = self.check_event(yaml.MappingStartEvent, yaml.SequenceStartEvent)
            self.depth += int(collection)
            try:
                if self.depth > 20:
                    raise ValueError("configuration nesting limit")
                return super().compose_node(parent, index)
            finally:
                self.depth -= int(collection)

        def construct_mapping(self, node, deep=False):
            mapping = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                try:
                    duplicate = key in mapping
                except TypeError:
                    raise ValueError("unhashable configuration key") from None
                if duplicate:
                    raise ValueError("duplicate configuration key")
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping

    try:
        if any(isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken, yaml.tokens.TagToken))
               for token in yaml.scan(source)):
            raise ValueError("configuration tags and anchors are unsupported")
        manifest = yaml.load(source, Loader=ProjectLoader)
    except yaml.YAMLError:
        raise ValueError("invalid project configuration") from None
    if not isinstance(manifest, dict):
        raise ValueError("invalid project configuration")
    return manifest


def _origin(site):
    if not isinstance(site, str):
        raise ValueError("invalid origin")
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
        if not re.fullmatch(r"[A-Fa-f0-9:.]+(?:%[A-Za-z0-9_.]+)?", host):
            raise ValueError("invalid host")
    else:
        labels = host.removesuffix(".").split(".")
        if any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", part) for part in labels):
            raise ValueError("invalid host")
        if len(labels) > 1 and not re.match(r"[A-Za-z]", labels[-1]):
            if host.endswith(".") or len(labels) != 4 or any(not re.fullmatch(r"[0-9]+", part) or int(part) > 255 for part in labels):
                raise ValueError("invalid IPv4 host")
    return site.rstrip("/")


def _validate_config(config):
    if type(config.get("enabled")) is not bool:
        raise ValueError("invalid activation")
    if "site" in config:
        _origin(config["site"])
    poll = config.get("pollIntervalSeconds", 300)
    if type(poll) is not int or not 30 <= poll <= 86400:
        raise ValueError("invalid polling interval")
    if "spaces" in config:
        spaces = config["spaces"]
        if not isinstance(spaces, list) or not 1 <= len(spaces) <= MAX_SPACES:
            raise ValueError("invalid spaces")
        keys = set()
        for space in spaces:
            if not isinstance(space, dict) or set(space) - SPACE_FIELDS:
                raise ValueError("invalid space setting")
            key = space.get("key")
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key) or key in keys:
                raise ValueError("invalid or duplicate space key")
            keys.add(key)
            for field in ("rootPageId", "memoryPageId", "feedbackPageId"):
                if field in space and (not isinstance(space[field], str) or not re.fullmatch(r"[1-9][0-9]*", space[field])):
                    raise ValueError("invalid native ID")
            if "watchCommentPages" in space:
                selection = space["watchCommentPages"]
                if not isinstance(selection, str) or len(selection) > 329:
                    raise ValueError("invalid comment watch pages")
                pages = selection.split(",")
                if len(pages) > 10 or len(set(pages)) != len(pages) or any(
                    not re.fullmatch(r"[1-9][0-9]{0,31}", page) for page in pages
                ):
                    raise ValueError("invalid comment watch pages")
    if config["enabled"] and any(key not in config for key in ("site", "spaces")):
        raise ValueError("missing connection setting")
    return {**config, "pollIntervalSeconds": poll}


def select_destination(environment=None, cwd=None):
    """Select for the consuming cwd; old WIKI connection variables are ignored."""
    environment = os.environ if environment is None else environment
    try:
        config = _read_config(cwd)
        if config is None:
            return {"mode": "local", "notice": LOCAL_NOTICE}
        config = _validate_config(config)
        if not config["enabled"]:
            return {"mode": "local", "notice": LOCAL_NOTICE}
        for key in ("ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"):
            value = environment.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("missing credential")
        if _origin(environment.get("ATLASSIAN_BASE_URL")) != _origin(config["site"]):
            raise ValueError("credential origin mismatch")
        return {"mode": "external", "backend": "atlassian", "notice": REMOTE_NOTICE, "config": config}
    except (OSError, ValueError, TypeError, ImportError, RecursionError):
        # Never render exception text: parser diagnostics can contain YAML values.
        return {"mode": "unavailable", "backend": "atlassian", "notice": UNAVAILABLE_NOTICE}


def policy(environment=None, cwd=None):
    selected = select_destination(environment, cwd=cwd)
    if selected["mode"] == "local":
        guidance = "Business-document references are repository doc/<area>/<TYPE>__<name>.md paths. Read/write selected requirements before source implementation; local req_scaffold.py may help. Keep source provenance and use the effective WBS policy for document review."
    elif selected["mode"] == "external":
        guidance = "Business-document references are native document IDs/URLs with observed versions from atlassian MCP; use REQ: confluence:<page-id>@<version> in PLAN for requirement presence checks. Use that MCP for requirements, guidance, decisions, knowledge and reusable runbooks; provide original evidence to reviewers/QA. Do not create duplicate business-knowledge files in the repository. Repository product documentation, source assets, runtime instructions and WBS controls remain repository-owned. A failed/unavailable MCP does not authorize local fallback writes."
    else:
        guidance = "Business-document synchronization is sync_pending until the configured connection is usable and publication is confirmed. Report it separately from product review/QA, PR readiness and deployment; it does not by itself invalidate applicable product results or block PR readiness. An explicit current acceptance dependency on publication remains unfinished. Do not infer that an invalid connection means local mode or write a local fallback."
    return {**selected, "policy": guidance}


if __name__ == "__main__":
    print(json.dumps(policy(), ensure_ascii=False))
