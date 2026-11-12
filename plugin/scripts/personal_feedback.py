#!/usr/bin/env python3
"""Private Harness feedback: inspect settings, check access, or send stdin once.

No hook, project configuration, local report storage or automatic publication.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
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
from urllib.parse import parse_qs, quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parent))
from document_destination import _origin, _yaml_mapping
from wiki_platform import run_bounded, safe_read, same_file


MAX_CONFIG = 65536
MAX_REPORT = 131072
MAX_RESPONSE = 1048576
CREDENTIALS = ("ATLASSIAN_BASE_URL", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN")
ID = re.compile(r"[1-9][0-9]{0,31}")
READ_CHARS = 12000
MAX_CURSOR = 8192


class FeedbackError(Exception):
    """Only fixed, nonsecret messages are returned to the caller."""


def _directory(home=None):
    return (Path.home() if home is None else Path(home)) / ".config" / "harness"


def _read_private(name, home=None):
    """Read bounded owner-private files through non-following directory FDs."""
    if os.name == "nt":
        try:
            return safe_read(_directory(home) / name, MAX_CONFIG, private=True)
        except ValueError:
            raise FeedbackError("Private settings require an owner-private Windows ACL and safely owned directories.") from None
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


def _private_mise(argv, **kwargs):
    kwargs.pop("stdin", None)
    kwargs.pop("text", None)
    result = run_bounded(argv, **kwargs)
    return subprocess.CompletedProcess(argv, result.returncode,
                                       result.stdout.decode("utf-8", errors="strict"), "")


def _credentials(directory, environment):
    home = directory.parent.parent
    before = _read_private("mise.local.toml", home)
    mise = shutil.which("mise")
    if not mise:
        raise FeedbackError("mise is required for private feedback authentication.")
    # Never let the consuming project's activated mise environment choose credentials.
    child_env = {k: v for k, v in environment.items()
                 if not k.upper().startswith(("ATLASSIAN_", "MISE_", "__MISE_"))}
    child_env["MISE_YES"] = "0"
    try:
        result = _private_mise([mise, "env", "--json-extended"], cwd=directory,
                               env=child_env, stdin=subprocess.DEVNULL, text=True)
        if result.returncode or len(result.stdout.encode("utf-8")) > MAX_RESPONSE:
            raise ValueError("private mise evaluation failed")
        data = json.loads(result.stdout)
        credentials = {}
        expected = str(directory / "mise.local.toml")
        for name in CREDENTIALS:
            entry = data[name]
            if (not same_file(entry["source"], expected) or not isinstance(entry["value"], str)
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


class _Response(dict):
    def __init__(self, value, links):
        super().__init__(value)
        self.links = links


def _request(site, credentials, method, path, body=None):
    token = base64.b64encode((credentials["ATLASSIAN_EMAIL"] + ":" + credentials["ATLASSIAN_API_TOKEN"]).encode()).decode("ascii")
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(site + path, data=raw, method=method,
                      headers={"Authorization": "Basic " + token, "Accept": "application/json", "Content-Type": "application/json"})
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=20) as response:
        if response.status != (201 if method == "POST" else 200):
            raise ValueError("unexpected response status")
        links = response.headers.get_all("Link", [])
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError("response too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid response")
    return _Response(value, links)


def _read_options(value):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {"limit", "cursor"}:
        raise FeedbackError("Read accepts only limit and the returned cursor.")
    limit = value.get("limit", 10)
    cursor = value.get("cursor")
    if type(limit) is not int or not 1 <= limit <= 25:
        raise FeedbackError("Read limit must be an integer from 1 to 25.")
    if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= MAX_CURSOR):
        raise FeedbackError("Use the unchanged cursor returned by this inbox.")
    return limit, cursor


def _cursor_key(selected, credentials):
    scope = json.dumps(["harness-feedback-read-v1", selected["site"], selected["page_id"],
                        credentials["ATLASSIAN_EMAIL"]], separators=(",", ":")).encode()
    return hmac.new(credentials["ATLASSIAN_API_TOKEN"].encode(), scope, hashlib.sha256).digest()


def _cursor(key, account, kind, **fields):
    raw = json.dumps(dict(v=1, account=account, kind=kind, **fields), separators=(",", ":"), sort_keys=True).encode()
    result = base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + hmac.new(key, raw, hashlib.sha256).hexdigest()
    if len(result) > MAX_CURSOR:
        raise FeedbackError("Native continuation exceeds the supported read budget.")
    return result


def _decode_cursor(value, key):
    if value is None:
        return None
    try:
        encoded, signature = value.split(".")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise ValueError()
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        if not hmac.compare_digest(signature, hmac.new(key, raw, hashlib.sha256).hexdigest()):
            raise ValueError()
        data = json.loads(raw)
        fields = {"page": {"offset", "version", "hash"}, "comment": {"id", "offset", "version", "hash"},
                  "comments": {"native"}, "replies": {"id", "native"}}
        if (not isinstance(data, dict) or data.get("v") != 1 or data.get("kind") not in fields
                or set(data) != {"v", "account", "kind"} | fields[data["kind"]]
                or not isinstance(data["account"], str) or not 1 <= len(data["account"]) <= 256):
            raise ValueError()
        if "id" in data and (not isinstance(data["id"], str) or not ID.fullmatch(data["id"])):
            raise ValueError()
        if "offset" in data and (type(data["offset"]) is not int or data["offset"] < 0
                or type(data["version"]) is not int or data["version"] <= 0
                or not isinstance(data["hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["hash"])):
            raise ValueError()
        if "native" in data and (data["native"] is not None and (not isinstance(data["native"], str)
                or not 1 <= len(data["native"]) <= 4096 or any(ord(c) < 32 for c in data["native"]))):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise FeedbackError("Cursor is invalid or no longer matches the private connection; read the inbox again without it.") from None


def _version(value):
    number = value.get("version", {}).get("number")
    if type(number) is not int or number <= 0:
        raise ValueError("native version unavailable")
    return number


def _body(value, state, key, account, kind, **identity):
    storage = value.get("body", {}).get("storage", {})
    text = storage.get("value")
    if not isinstance(text, str) or storage.get("representation", "storage") != "storage":
        raise ValueError("native storage unavailable")
    version = _version(value)
    digest = hashlib.sha256(text.encode()).hexdigest()
    offset = state["offset"] if state else 0
    if state and (state["version"] != version or state["hash"] != digest or offset > len(text)):
        raise FeedbackError("The selected content changed; read the inbox again without the old cursor.")
    end = min(len(text), offset + READ_CHARS)
    result = dict(format="storage", value=text[offset:end], offset=offset, offsetUnit="unicode_codepoint", truncated=end < len(text))
    if end < len(text):
        result["read"] = dict(cursor=_cursor(key, account, kind, offset=end, version=version, hash=digest, **identity))
    return result


def _next_cursor(response, site, path):
    links = []
    body = response.get("_links", {})
    if not isinstance(body, dict):
        raise ValueError("invalid native links")
    if body.get("next") is not None:
        links.append(body["next"])
    for header in getattr(response, "links", []):
        if not isinstance(header, str) or len(header) > MAX_CURSOR:
            raise ValueError("invalid native Link header")
        for segment in re.split(r",\s*(?=<)", header):
            match = re.fullmatch(r'\s*<([^<>]+)>((?:\s*;\s*[A-Za-z][A-Za-z0-9_-]*=(?:"[^"\r\n]*"|[^;,\s]+))*)\s*', segment)
            if not match:
                raise ValueError("invalid native Link header")
            relations = re.findall(r';\s*rel=("[^"\r\n]*"|[^;,\s]+)', match[2], flags=re.I)
            if len(relations) > 1:
                raise ValueError("ambiguous native Link header")
            if relations and "next" in relations[0].strip('"').lower().split():
                links.append(match[1])
    cursors = set()
    for link in links:
        if not isinstance(link, str) or not 1 <= len(link) <= MAX_CURSOR:
            raise ValueError("invalid native continuation")
        url = urlsplit(urljoin(site + path, link))
        if _origin(url.scheme + "://" + url.netloc) != site or url.path != path or url.fragment:
            raise ValueError("native continuation changed scope")
        query = parse_qs(url.query, keep_blank_values=True, strict_parsing=True)
        if set(query) - {"cursor", "limit", "body-format", "sort"} or any(len(v) != 1 for v in query.values()):
            raise ValueError("invalid native continuation query")
        if (query.get("body-format", ["storage"]) != ["storage"] or query.get("sort", ["-created-date"]) != ["-created-date"]
                or "limit" in query and (not query["limit"][0].isdigit() or not 1 <= int(query["limit"][0]) <= 25)):
            raise ValueError("native continuation changed read parameters")
        value = query.get("cursor", [None])[0]
        if not isinstance(value, str) or not 1 <= len(value) <= 4096 or any(ord(c) < 32 for c in value):
            raise ValueError("native continuation cursor unavailable")
        cursors.add(value)
    if len(cursors) > 1:
        raise ValueError("conflicting native continuations")
    return next(iter(cursors), None)


def _comment_location(site, credentials, comment, page_id):
    seen = set()
    while comment is not None:
        if comment in seen or len(seen) >= 16:
            raise ValueError("comment ancestry exceeds read budget")
        seen.add(comment)
        raw = _request(site, credentials, "GET", "/wiki/api/v2/footer-comments/" + comment)
        if (raw.get("id") != comment or raw.get("status") != "current"
                or any(raw.get(k) is not None for k in ("blogPostId", "attachmentId", "customContentId"))):
            raise ValueError("comment identity unavailable")
        if raw.get("pageId") is not None and raw["pageId"] != page_id:
            raise ValueError("comment belongs to another page")
        parent = raw.get("parentCommentId")
        if parent is not None and (not isinstance(parent, str) or not ID.fullmatch(parent)):
            raise ValueError("invalid native parent")
        if parent is None:
            if raw.get("pageId") != page_id:
                raise ValueError("comment page unavailable")
            return
        comment = parent


def _read_feedback(selected, credentials, account, key, state, page, limit):
    site, page_id = selected["site"], selected["page_id"]
    if not isinstance(page.get("title"), str) or not 1 <= len(page["title"]) <= 4096:
        raise ValueError("native page title unavailable")
    result = dict(status="read", page=dict(id=page_id, url=selected["page"], title=page["title"], version=_version(page)))
    kind = state["kind"] if state else "overview"
    result["kind"] = kind
    if kind in {"overview", "page"}:
        result["document"] = _body(page, state, key, account, "page")
        if kind == "page":
            return result
    if kind == "comment":
        _comment_location(site, credentials, state["id"], page_id)
        raw = _request(site, credentials, "GET", "/wiki/api/v2/footer-comments/" + state["id"] + "?body-format=storage")
        if raw.get("id") != state["id"] or raw.get("status") != "current" or raw.get("pageId", page_id) != page_id:
            raise ValueError("comment identity changed")
        result["comment"] = dict(id=state["id"], version=_version(raw), body=_body(raw, state, key, account, "comment", id=state["id"]))
        _comment_location(site, credentials, state["id"], page_id)
        return result
    parent = state["id"] if kind == "replies" else None
    if parent:
        _comment_location(site, credentials, parent, page_id)
    path = "/wiki/api/v2/footer-comments/" + parent + "/children" if parent else f"/wiki/api/v2/pages/{page_id}/footer-comments"
    query = f"?body-format=storage&limit={limit}&sort=-created-date"
    if state and state.get("native"):
        query += "&cursor=" + quote(state["native"], safe="")
    response = _request(site, credentials, "GET", path + query)
    rows = response.get("results")
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError("invalid native comment page")
    comments = []
    for raw in rows:
        if (not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not ID.fullmatch(raw["id"])
                or raw.get("status") != "current" or raw.get("pageId", page_id) != page_id
                or any(raw.get(k) is not None for k in ("blogPostId", "attachmentId", "customContentId"))
                or raw.get("parentCommentId", parent) != parent):
            raise ValueError("native comment scope changed")
        item = dict(id=raw["id"], version=_version(raw), body=_body(raw, None, key, account, "comment", id=raw["id"]))
        item["replies"] = dict(cursor=_cursor(key, account, "replies", id=raw["id"], native=None))
        comments.append(item)
    if len({c["id"] for c in comments}) != len(comments):
        raise ValueError("duplicate native comments")
    result["comments"] = comments
    native = _next_cursor(response, site, path)
    result["complete"] = native is None
    if native is not None:
        result["next"] = dict(cursor=_cursor(key, account, "replies" if parent else "comments", native=native, **({"id": parent} if parent else {})))
    return result


def perform(action, report=None, home=None, environment=None, request=None):
    selected = load_destination(home)
    if action == "status" or selected["status"] != "configured":
        return selected
    if action not in ("check", "send", "read"):
        return {"status": "unavailable", "message": "Unknown feedback action."}
    started = False
    comment_id = None
    try:
        limit, cursor = _read_options(request) if action == "read" else (None, None)
        if action == "send" and (not isinstance(report, str) or not report.strip()
                or len(report.encode("utf-8")) > MAX_REPORT or any(ord(c) < 32 and c not in "\n\t\r" for c in report)):
            raise FeedbackError("Send requires nonempty UTF-8 report text on stdin, at most 128 KiB.")
        credentials = _credentials(_directory(home), dict(os.environ if environment is None else environment))
        if _origin(credentials["ATLASSIAN_BASE_URL"]) != selected["site"]:
            raise FeedbackError("Private credential origin does not match the feedback page; no request was sent.")
        if ":" in credentials["ATLASSIAN_EMAIL"]:
            raise FeedbackError("Invalid private account email.")
        key = _cursor_key(selected, credentials) if action == "read" else None
        state = _decode_cursor(cursor, key) if action == "read" else None
        if action == "send" and credentials["ATLASSIAN_API_TOKEN"] in report:
            raise FeedbackError("Report contains the private token; redact it before sending.")
        site, page_id = selected["site"], selected["page_id"]
        user = _request(site, credentials, "GET", "/wiki/rest/api/user/current")
        account = user.get("accountId")
        if not isinstance(account, str) or not account.strip() or len(account) > 256:
            raise FeedbackError("Authenticated account identity is unavailable; no write was sent.")
        if state and state["account"] != account:
            raise FeedbackError("The authenticated private account changed; read the inbox again without the old cursor.")
        include_body = action == "read" and (state is None or state["kind"] == "page")
        page = _request(site, credentials, "GET", f"/wiki/api/v2/pages/{page_id}" + ("?body-format=storage" if include_body else ""))
        if page.get("id") != page_id or page.get("status") != "current":
            raise FeedbackError("Feedback page is not the selected current page; no write was sent.")
        if action == "check":
            return {"status": "checked", "page": selected["page"], "publication": "not_attempted"}
        if action == "read":
            result = _read_feedback(selected, credentials, account, key, state, page, limit)
            if credentials["ATLASSIAN_API_TOKEN"] in json.dumps(result, ensure_ascii=False):
                raise FeedbackError("Native content contains private authentication material; no content was returned.")
            return result
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
Requires native Windows or POSIX, PyYAML in this Python and mise for check/send.
On Windows use an owner-private ACL; mode bits do not establish privacy.
A compatible Confluence Cloud personal API token is required. Scoped gateway
tokens and Data Center are not supported. The private BASE_URL must match
the page origin. Project/global credentials never fill missing private values.

Page links: /wiki/spaces/KEY/pages/ID[/title] (including ~personal keys),
/spaces/KEY/pages/ID[/title], or /wiki/pages/viewpage.action?pageId=ID
(and /pages/viewpage.action?pageId=ID). Remove tracking queries/fragments;
short links need the full page URL. No destination environment override.

status: local settings only, no credentials/network. check: read-only access.
read: internal wiki MCP read bridge; optional JSON limit/cursor on stdin.
send: redacted UTF-8 report on stdin, <=128 KiB; appends one footer comment.
Configuration does not authorize sending. No automatic publication or retry.
Exit 0: success/status; 1: unavailable; 2: invalid CLI; 3: uncertain write.
If unknown, inspect the page/known comment ID before another submission.
""")
    parser.add_argument("action", nargs="?", default="status", choices=("status", "check", "send", "read"))
    args = parser.parse_args()
    report = None
    request = None
    if args.action == "read":
        try:
            raw = sys.stdin.buffer.read(16385)
            if len(raw) > 16384:
                raise ValueError()
            def unique(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError()
                    value[key] = item
                return value
            request = json.loads(raw.decode("utf-8"), object_pairs_hook=unique) if raw.strip() else {}
            _read_options(request)
        except (ValueError, OSError):
            print(json.dumps({"status": "unavailable", "message": "Read requires a bounded JSON object with only limit and the returned cursor."}))
            return 1
    if args.action == "send":
        try:
            raw = sys.stdin.buffer.read(MAX_REPORT + 1)
            if len(raw) > MAX_REPORT:
                raise ValueError()
            report = raw.decode("utf-8", errors="strict")
        except (ValueError, OSError):
            print(json.dumps({"status": "unavailable", "message": "Report must be UTF-8 and at most 128 KiB."}))
            return 1
    result = perform(args.action, report, request=request)
    # ASCII JSON escapes preserve native Unicode content through redirected
    # Windows streams using a legacy locale encoding.
    print(json.dumps(result, ensure_ascii=True))
    return 3 if result["status"] == "unknown" else 1 if result["status"] == "unavailable" or args.action != "status" and result["status"] == "unconfigured" else 0


if __name__ == "__main__":
    raise SystemExit(main())
