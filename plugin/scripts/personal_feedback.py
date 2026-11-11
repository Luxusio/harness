#!/usr/bin/env python3
"""Private Harness feedback: inspect settings, check access, or send stdin once.

No hook, project configuration, local report storage or automatic publication.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from document_destination import _origin, _yaml_mapping


MAX_CONFIG = 65536
MAX_REPORT = 131072
MAX_RESPONSE = 1048576
CREDENTIALS = ("ATLASSIAN_BASE_URL", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN")
ID = re.compile(r"[1-9][0-9]{0,31}")


class FeedbackError(Exception):
    """Only fixed, nonsecret messages are returned to the caller."""


def _directory(home=None):
    return (Path.home() if home is None else Path(home)) / ".config" / "harness"


def _read_private(name, home=None):
    """Read bounded owner-private files through non-following directory FDs."""
    root = Path.home() if home is None else Path(home)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in (None, ".config", "harness"):
            if part is not None:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
                raise FeedbackError("Private settings directories must be owned by you and not writable by others.")
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > MAX_CONFIG):
                raise FeedbackError("Private settings must be regular files owned by you with mode 0600.")
            raw = stream.read(MAX_CONFIG + 1)
            if len(raw) > MAX_CONFIG:
                raise FeedbackError("Private settings exceed the supported size.")
            return raw
    finally:
        os.close(fd)


def _destination(raw):
    config = _yaml_mapping(raw.decode("utf-8", errors="strict"))
    if set(config) != {"backend", "page"} or config["backend"] != "atlassian":
        raise ValueError("invalid feedback schema")
    page = config["page"]
    if (not isinstance(page, str) or len(page) > 4096 or "\\" in page
            or any(c.isspace() or ord(c) < 32 for c in page)):
        raise ValueError("invalid page URL")
    url = urlsplit(page)
    site = _origin(f"{url.scheme}://{url.netloc}")
    if url.fragment:
        raise ValueError("use the page URL without a fragment")
    modern = re.fullmatch(r"/(?:wiki/)?spaces/~?[A-Za-z0-9_:-]{1,200}/pages/([1-9][0-9]{0,31})(?:/[^?#]*)?", url.path)
    if modern and not url.query:
        page_id = modern.group(1)
    elif url.path in ("/wiki/pages/viewpage.action", "/pages/viewpage.action"):
        query = parse_qs(url.query, keep_blank_values=True, strict_parsing=True)
        if set(query) != {"pageId"} or len(query["pageId"]) != 1 or not ID.fullmatch(query["pageId"][0]):
            raise ValueError("invalid page query")
        page_id = query["pageId"][0]
    else:
        raise ValueError("unsupported Confluence page URL")
    return {"backend": "atlassian", "site": site, "page_id": page_id,
            "page": f"{site}/wiki/pages/viewpage.action?pageId={page_id}"}


def load_destination(home=None):
    try:
        raw = _read_private("feedback.yaml", home)
    except FileNotFoundError:
        return {"status": "unconfigured", "message": "Configure ~/.config/harness/feedback.yaml; copyable reporting is still available."}
    except (OSError, FeedbackError, AttributeError):
        return {"status": "unavailable", "message": "Cannot safely read private feedback settings; check ownership, paths and mode 0600."}
    try:
        return {"status": "configured", **_destination(raw), "authentication": "not_checked"}
    except (ValueError, TypeError, ImportError, RecursionError):
        return {"status": "unavailable", "message": "Invalid private feedback YAML or missing PyYAML; use backend: atlassian and an HTTPS page URL."}


def _credentials(directory, environment):
    home = directory.parent.parent
    before = _read_private("mise.local.toml", home)
    mise = shutil.which("mise")
    if not mise:
        raise FeedbackError("mise is required for private feedback authentication.")
    # Never let the consuming project's activated mise environment choose credentials.
    child_env = {k: v for k, v in environment.items()
                 if not k.startswith(("ATLASSIAN_", "MISE_", "__MISE_"))}
    child_env["MISE_YES"] = "0"
    try:
        result = subprocess.run([mise, "env", "--json-extended"], cwd=directory,
                                env=child_env, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=20, check=False)
        if result.returncode or len(result.stdout.encode("utf-8")) > MAX_RESPONSE:
            raise ValueError("private mise evaluation failed")
        data = json.loads(result.stdout)
        credentials = {}
        expected = str(directory / "mise.local.toml")
        for name in CREDENTIALS:
            entry = data[name]
            if (entry["source"] != expected or not isinstance(entry["value"], str)
                    or not entry["value"].strip() or any(ord(c) < 32 for c in entry["value"])):
                raise ValueError("private source or credential unavailable")
            credentials[name] = entry["value"]
        if hashlib.sha256(before).digest() != hashlib.sha256(_read_private("mise.local.toml", home)).digest():
            raise ValueError("private auth changed")
        return credentials
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        raise FeedbackError("Private authentication unavailable. Configure and trust ~/.config/harness/mise.local.toml; all ATLASSIAN_* values must come from that file.") from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(site, credentials, method, path, body=None):
    token = base64.b64encode((credentials["ATLASSIAN_EMAIL"] + ":" + credentials["ATLASSIAN_API_TOKEN"]).encode()).decode("ascii")
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(site + path, data=raw, method=method,
                      headers={"Authorization": "Basic " + token, "Accept": "application/json", "Content-Type": "application/json"})
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=20) as response:
        if response.status != (201 if method == "POST" else 200):
            raise ValueError("unexpected response status")
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError("response too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid response")
    return value


def perform(action, report=None, home=None, environment=None):
    selected = load_destination(home)
    if action == "status" or selected["status"] != "configured":
        return selected
    if action not in ("check", "send"):
        return {"status": "unavailable", "message": "Unknown feedback action."}
    started = False
    comment_id = None
    try:
        if action == "send" and (not isinstance(report, str) or not report.strip()
                or len(report.encode("utf-8")) > MAX_REPORT or any(ord(c) < 32 and c not in "\n\t\r" for c in report)):
            raise FeedbackError("Send requires nonempty UTF-8 report text on stdin, at most 128 KiB.")
        credentials = _credentials(_directory(home), dict(os.environ if environment is None else environment))
        if _origin(credentials["ATLASSIAN_BASE_URL"]) != selected["site"]:
            raise FeedbackError("Private credential origin does not match the feedback page; no request was sent.")
        if ":" in credentials["ATLASSIAN_EMAIL"]:
            raise FeedbackError("Invalid private account email.")
        if action == "send" and credentials["ATLASSIAN_API_TOKEN"] in report:
            raise FeedbackError("Report contains the private token; redact it before sending.")
        site, page_id = selected["site"], selected["page_id"]
        user = _request(site, credentials, "GET", "/wiki/rest/api/user/current")
        account = user.get("accountId")
        if not isinstance(account, str) or not account.strip():
            raise FeedbackError("Authenticated account identity is unavailable; no write was sent.")
        page = _request(site, credentials, "GET", f"/wiki/api/v2/pages/{page_id}")
        if page.get("id") != page_id or page.get("status") != "current":
            raise FeedbackError("Feedback page is not the selected current page; no write was sent.")
        if action == "check":
            return {"status": "checked", "page": selected["page"], "publication": "not_attempted"}
        # Plain text only: never interpret report Markdown or embedded HTML as storage.
        body = "<p>" + html.escape(report.replace("\r\n", "\n").replace("\r", "\n"), quote=False).replace("\n", "<br />") + "</p>"
        started = True
        created = _request(site, credentials, "POST", "/wiki/api/v2/footer-comments",
                           {"pageId": page_id, "body": {"representation": "storage", "value": body}})
        value = created.get("id")
        if not isinstance(value, str) or not ID.fullmatch(value):
            raise ValueError("created comment ID missing")
        comment_id = value
        saved = _request(site, credentials, "GET", f"/wiki/api/v2/footer-comments/{comment_id}?body-format=storage")
        version = saved.get("version", {})
        storage = saved.get("body", {}).get("storage", {})
        if (saved.get("id") != comment_id or saved.get("pageId") != page_id
                or saved.get("status") != "current" or any(saved.get(k) is not None for k in ("parentCommentId", "blogPostId", "attachmentId", "customContentId"))
                or type(version.get("number")) is not int or version.get("number") != 1
                or version.get("authorId") != account or storage.get("representation") != "storage" or storage.get("value") != body):
            raise ValueError("created comment readback mismatch")
        return {"status": "sent", "page": selected["page"], "comment_id": comment_id}
    except (OSError, HTTPException, ValueError, TypeError, KeyError, AttributeError, FeedbackError, RecursionError) as error:
        if started:
            return {"status": "unknown", "page": selected["page"], "comment_id": comment_id,
                    "message": "The comment may exist. Inspect the page and known comment ID before another submission; do not automatically resend."}
        return {"status": "unavailable", "message": str(error) if isinstance(error, FeedbackError) else "Feedback access check failed; no write was sent. Check private credentials, page access and service availability."}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="""Setup (personal files only; never commit these):
  ~/.config/harness/feedback.yaml:
    backend: atlassian
    page: "https://YOUR-SITE.atlassian.net/wiki/spaces/TEAM/pages/123456"

  ~/.config/harness/mise.local.toml:
    [env]
    ATLASSIAN_BASE_URL = "https://YOUR-SITE.atlassian.net"
    ATLASSIAN_EMAIL = "YOUR_EMAIL"
    ATLASSIAN_API_TOKEN = "YOUR_TOKEN"

Use mode 0600 for both files; own their directories and prevent other users
from writing them (0700 for ~/.config/harness). Review the mise file, then run:
  mise trust ~/.config/harness/mise.local.toml
Requires a POSIX host, PyYAML in this Python and mise for check/send.
A compatible Confluence Cloud personal API token is required. Scoped gateway
tokens and Data Center are not supported. The private BASE_URL must match
the page origin. Project/global credentials never fill missing private values.

Page links: /wiki/spaces/KEY/pages/ID[/title] (including ~personal keys),
/spaces/KEY/pages/ID[/title], or /wiki/pages/viewpage.action?pageId=ID
(and /pages/viewpage.action?pageId=ID). Remove tracking queries/fragments;
short links need the full page URL. No destination environment override.

status: local settings only, no credentials/network. check: read-only access.
send: redacted UTF-8 report on stdin, <=128 KiB; appends one footer comment.
Configuration does not authorize sending. No automatic publication or retry.
Exit 0: success/status; 1: unavailable; 2: invalid CLI; 3: uncertain write.
If unknown, inspect the page/known comment ID before another submission.
""")
    parser.add_argument("action", nargs="?", default="status", choices=("status", "check", "send"))
    args = parser.parse_args()
    report = None
    if args.action == "send":
        try:
            raw = sys.stdin.buffer.read(MAX_REPORT + 1)
            if len(raw) > MAX_REPORT:
                raise ValueError()
            report = raw.decode("utf-8", errors="strict")
        except (ValueError, OSError):
            print(json.dumps({"status": "unavailable", "message": "Report must be UTF-8 and at most 128 KiB."}))
            return 1
    result = perform(args.action, report)
    print(json.dumps(result, ensure_ascii=False))
    return 3 if result["status"] == "unknown" else 1 if result["status"] == "unavailable" or args.action != "status" and result["status"] == "unconfigured" else 0


if __name__ == "__main__":
    raise SystemExit(main())
