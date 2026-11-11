"""Private inbox reads, scoped continuations and native membership boundaries."""
import copy
import json
from urllib.parse import parse_qs, urlsplit

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

from test_personal_feedback import CREDS, SITE, feedback, forbidden, private_auth, private_settings


def native_comment(identifier, body, **extra):
    return dict(id=identifier, status="current", version={"number": 1},
                body={"storage": {"representation": "storage", "value": body}}, **extra)


class ReadTransport:
    def __init__(self):
        self.calls = []
        self.page = dict(id="790", status="current", title="Feedback", version={"number": 4},
                         body={"storage": {"representation": "storage", "value": "<p>원문 🧪</p>" * 1500}})
        self.roots = [native_comment("900", "<p>First</p>", pageId="790"),
                      native_comment("902", "<p>Second</p>", pageId="790")]
        self.reply = native_comment("901", "답글 " * 6000, parentCommentId="900")
        self.next_kind = "header"
        self.bad_link = None
        self.account = "private-account"

    def __call__(self, site, credentials, method, path, body=None):
        self.calls.append((method, path))
        assert site == SITE and credentials == CREDS and method == "GET" and body is None
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        if parsed.path == "/wiki/rest/api/user/current":
            return {"accountId": self.account}
        if parsed.path == "/wiki/api/v2/pages/790":
            return copy.deepcopy(self.page)
        if parsed.path == "/wiki/api/v2/pages/790/footer-comments":
            limit = int(query["limit"][0])
            rows = self.roots[1:] if "cursor" in query else self.roots[:limit]
            result = {"results": copy.deepcopy(rows)}
            next_link = self.bad_link or SITE + parsed.path + "?cursor=native%2B%2Fcursor&limit=1&body-format=storage"
            if "cursor" not in query and limit == 1:
                if self.next_kind in {"body", "both"}:
                    result["_links"] = {"next": next_link}
                if self.next_kind in {"header", "both"}:
                    header = '<' + next_link + '>; rel="next"'
                    return feedback._Response(result, [header])
            return result
        if parsed.path == "/wiki/api/v2/footer-comments/900/children":
            return {"results": [copy.deepcopy(self.reply)]}
        if parsed.path in {"/wiki/api/v2/footer-comments/900", "/wiki/api/v2/footer-comments/901"}:
            return copy.deepcopy(self.roots[0] if parsed.path.endswith("900") else self.reply)
        raise AssertionError("Unexpected native path: " + path)


def reader(home, monkeypatch):
    private_auth(home)
    server = ReadTransport()
    monkeypatch.setattr(feedback, "_credentials", lambda *a, **k: CREDS.copy())
    monkeypatch.setattr(feedback, "_request", server)
    return server


def read(home, **request):
    return feedback.perform("read", home=home, request=request)


@pytest.mark.parametrize("next_kind", ["header", "body", "both"])
def test_initial_read_and_native_continuation_preserve_scope_and_bounded_ranges(tmp_path, monkeypatch, next_kind):
    server = reader(tmp_path, monkeypatch); server.next_kind = next_kind
    first = read(tmp_path, limit=1)
    assert first["status"] == "read" and first["page"]["id"] == "790"
    assert first["document"]["value"] == server.page["body"]["storage"]["value"][:12000]
    assert first["document"]["offsetUnit"] == "unicode_codepoint" and first["document"]["truncated"]
    assert [c["id"] for c in first["comments"]] == ["900"] and not first["complete"]
    rest = read(tmp_path, limit=1, **first["next"])
    assert [c["id"] for c in rest["comments"]] == ["902"] and rest["complete"]
    assert any("cursor=native%2B%2Fcursor" in path for _, path in server.calls)
    body = read(tmp_path, **first["document"]["read"])
    assert body["kind"] == "page" and body["document"]["offset"] == 12000
    assert body["document"]["value"] == server.page["body"]["storage"]["value"][12000:24000]
    assert all(method == "GET" for method, _ in server.calls)
    assert CREDS["ATLASSIAN_API_TOKEN"] not in json.dumps(first)


def test_reply_and_large_comment_follow_observed_native_ancestry(tmp_path, monkeypatch):
    server = reader(tmp_path, monkeypatch)
    first = read(tmp_path)
    replies = read(tmp_path, **first["comments"][0]["replies"])
    assert replies["status"] == "read" and replies["kind"] == "replies"
    reply = replies["comments"][0]
    assert reply["id"] == "901" and reply["body"]["truncated"]
    detail = read(tmp_path, **reply["body"]["read"])
    assert detail["kind"] == "comment" and detail["comment"]["id"] == "901"
    assert detail["comment"]["body"]["value"] == server.reply["body"]["storage"]["value"][12000:]
    assert any(path == "/wiki/api/v2/footer-comments/900" for _, path in server.calls)


@pytest.mark.parametrize("options", [{"limit": True}, {"limit": 0}, {"limit": 26}, {"page": "791"},
                                      {"cursor": ""}, {"cursor": "x" * 8193}],
                         ids=["boolean", "zero", "oversize", "arbitrary-page", "empty-cursor", "large-cursor"])
def test_invalid_read_shape_never_evaluates_authentication_or_transport(tmp_path, monkeypatch, options):
    private_settings(tmp_path)
    monkeypatch.setattr(feedback, "_credentials", forbidden)
    monkeypatch.setattr(feedback, "_request", forbidden)
    assert feedback.perform("read", home=tmp_path, request=options)["status"] == "unavailable"


def test_forged_cursor_and_changed_private_connection_fail_before_http(tmp_path, monkeypatch):
    server = reader(tmp_path, monkeypatch)
    cursor = read(tmp_path, limit=1)["next"]["cursor"]
    server.calls.clear()
    assert read(tmp_path, cursor=cursor[:-1] + ("1" if cursor[-1] != "1" else "2"))["status"] == "unavailable"
    assert server.calls == []
    monkeypatch.setattr(feedback, "_credentials", lambda *a, **k: dict(CREDS, ATLASSIAN_API_TOKEN="rotated-fixture"))
    assert read(tmp_path, cursor=cursor)["status"] == "unavailable"
    assert server.calls == []


def test_native_account_and_same_version_body_changes_reject_old_cursors(tmp_path, monkeypatch):
    server = reader(tmp_path, monkeypatch)
    first = read(tmp_path)
    server.account = "different-private-account"
    assert read(tmp_path, **first["document"]["read"])["status"] == "unavailable"
    server.account = "private-account"
    server.page["body"]["storage"]["value"] += "changed without a version increment"
    assert read(tmp_path, **first["document"]["read"])["status"] == "unavailable"


@pytest.mark.parametrize("link", ["https://other.invalid/wiki/api/v2/pages/790/footer-comments?cursor=x",
                                    SITE + "/wiki/api/v2/pages/791/footer-comments?cursor=x",
                                    SITE + "/wiki/api/v2/pages/790/footer-comments?cursor=x&cursor=y",
                                    SITE + "/wiki/api/v2/pages/790/footer-comments?cursor=x&token=secret"])
def test_native_continuation_cannot_change_origin_page_or_query_authority(tmp_path, monkeypatch, link):
    server = reader(tmp_path, monkeypatch); server.bad_link = link
    result = read(tmp_path, limit=1)
    assert result["status"] == "unavailable" and "secret" not in json.dumps(result)
    assert all("other.invalid" not in path and "/pages/791" not in path for _, path in server.calls)


def test_reply_revalidation_rejects_foreign_parent_without_returning_content(tmp_path, monkeypatch):
    server = reader(tmp_path, monkeypatch)
    request = read(tmp_path)["comments"][0]["replies"]
    server.roots[0]["pageId"] = "791"
    result = read(tmp_path, **request)
    assert result["status"] == "unavailable" and "First" not in json.dumps(result)
    assert not any(path.endswith("/children") for _, path in server.calls)


def test_conflicting_continuation_and_token_reflection_do_not_return_partial_content(tmp_path, monkeypatch):
    server = reader(tmp_path, monkeypatch)
    original = server.__call__
    def conflict(*args, **kwargs):
        result = original(*args, **kwargs)
        if isinstance(result, feedback._Response):
            result["_links"] = {"next": SITE + "/wiki/api/v2/pages/790/footer-comments?cursor=different"}
        return result
    monkeypatch.setattr(feedback, "_request", conflict)
    assert read(tmp_path, limit=1)["status"] == "unavailable"
    monkeypatch.setattr(feedback, "_request", server)
    server.roots[0]["body"]["storage"]["value"] = CREDS["ATLASSIAN_API_TOKEN"]
    result = read(tmp_path)
    assert result["status"] == "unavailable" and CREDS["ATLASSIAN_API_TOKEN"] not in json.dumps(result)
