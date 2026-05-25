#!/usr/bin/env python3
"""Detect when observable behavior needs a durable REQ document.

Stdlib only. Used by plan/develop/close gates and by req_scaffold.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Iterable


UI_KEYWORDS = {
    "screen", "page", "modal", "layout", "navigation", "navigate", "back",
    "back-stack", "gesture", "reader", "settings", "detail", "search",
    "filter", "sort", "empty", "loading", "error", "validation", "button",
    "click", "tap", "swipe", "mobile", "android", "ios", "native", "apk",
    "emulator", "browser", "viewport", "responsive",
}
API_KEYWORDS = {
    "endpoint", "route", "controller", "schema", "response", "request",
    "status code", "auth", "validation", "pagination", "webhook",
}
DESKTOP_KEYWORDS = {
    "window", "menu", "dialog", "shortcut", "focus", "resize", "toolbar",
}

UI_PATH_FRAGMENTS = (
    "/components/", "/pages/", "/views/", "/routes/", "/screens/",
    "/navigation/", "/navigator/", "/mobile/", "/android/", "/ios/",
    "/app/",
)
UI_EXTENSIONS = (".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss")
API_PATH_FRAGMENTS = (
    "/api/", "/apis/", "/controllers/", "/controller/", "/routes/",
    "/handlers/", "/handler/", "/endpoints/", "/endpoint/",
)
DESKTOP_PATH_FRAGMENTS = (
    "/desktop/", "/gui/", "/native/", "/electron/", "/tauri/", "/qt/",
    "/gtk/", "/windows/", "/window/", "/menus/", "/dialogs/",
)

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "need",
    "needs", "should", "must", "when", "then", "user", "users", "behavior",
    "observable", "change", "changes", "fix", "fixes",
}


def _norm_text(texts: Iterable[str] | None) -> str:
    return "\n".join(t for t in (texts or []) if isinstance(t, str)).lower()


def _norm_path(path: str) -> str:
    return "/" + path.replace("\\", "/").lstrip("./").lower()


def _slugify(parts: Iterable[str], fallback: str = "observable-behavior") -> str:
    words: list[str] = []
    for part in parts:
        for word in re.findall(r"[a-z0-9]+", part.lower()):
            if len(word) < 3 or word in STOPWORDS:
                continue
            words.append(word)
            if len(words) >= 5:
                break
        if len(words) >= 5:
            break
    return "-".join(words[:5]) or fallback


def _keyword_in_text(keyword: str, body: str) -> bool:
    escaped = re.escape(keyword)
    if " " in keyword or "-" in keyword:
        return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", body) is not None
    return re.search(rf"\b{escaped}\b", body) is not None


def detect_req_need(texts: Iterable[str] | None = None,
                    paths: Iterable[str] | None = None) -> dict:
    """Return a JSON-serializable REQ need classification."""
    body = _norm_text(texts)
    surfaces: set[str] = set()
    reasons: list[str] = []
    slug_hints: list[str] = []

    for keyword in sorted(UI_KEYWORDS, key=len, reverse=True):
        if _keyword_in_text(keyword, body):
            surface = "mobile-native" if keyword in {"mobile", "android", "ios", "native", "apk", "emulator", "back-stack", "gesture"} else "ui"
            surfaces.add(surface)
            reasons.append(f"text:{keyword}")
            slug_hints.append(keyword)
    for keyword in sorted(API_KEYWORDS, key=len, reverse=True):
        if _keyword_in_text(keyword, body):
            surfaces.add("api")
            reasons.append(f"text:{keyword}")
            slug_hints.append(keyword)
    for keyword in sorted(DESKTOP_KEYWORDS, key=len, reverse=True):
        if _keyword_in_text(keyword, body):
            surfaces.add("desktop")
            reasons.append(f"text:{keyword}")
            slug_hints.append(keyword)

    for raw in paths or []:
        if not isinstance(raw, str):
            continue
        path = _norm_path(raw)
        if path.endswith(UI_EXTENSIONS) or any(fragment in path for fragment in UI_PATH_FRAGMENTS):
            surfaces.add("ui")
            reasons.append(f"path:{raw}")
            slug_hints.extend(os.path.splitext(os.path.basename(path))[0].split("-"))
        if any(fragment in path for fragment in API_PATH_FRAGMENTS):
            surfaces.add("api")
            reasons.append(f"path:{raw}")
            slug_hints.extend(os.path.splitext(os.path.basename(path))[0].split("-"))
        if any(fragment in path for fragment in DESKTOP_PATH_FRAGMENTS):
            surfaces.add("desktop")
            reasons.append(f"path:{raw}")
            slug_hints.extend(os.path.splitext(os.path.basename(path))[0].split("-"))

    requires = bool(surfaces)
    confidence = "high" if any(r.startswith("path:") for r in reasons) or len(reasons) >= 2 else ("medium" if requires else "low")
    if "api" in surfaces:
        area = "api"
    elif "desktop" in surfaces:
        area = "desktop"
    else:
        area = "ui"
    return {
        "requires_req": requires,
        "confidence": confidence,
        "surfaces": sorted(surfaces),
        "reasons": reasons[:20],
        "suggested_area": area if requires else "",
        "suggested_slug": _slugify(slug_hints if slug_hints else [body[:120]]),
    }


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect whether a durable REQ is required.")
    parser.add_argument("--text", action="append", default=[])
    parser.add_argument("--text-file", action="append", default=[])
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    texts = list(args.text)
    texts.extend(_read_file(path) for path in args.text_file)
    result = detect_req_need(texts=texts, paths=args.path)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("requires_req:", str(result["requires_req"]).lower())
        print("confidence:", result["confidence"])
        print("surfaces:", ", ".join(result["surfaces"]))
        print("suggested:", f"doc/{result['suggested_area']}/REQ__{result['suggested_slug']}.md" if result["requires_req"] else "n/a")
        for reason in result["reasons"]:
            print("reason:", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
