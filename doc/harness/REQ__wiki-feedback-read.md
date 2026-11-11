# Personal feedback reads through wiki MCP

Status: accepted user outcome; derived implementation choices are labelled separately.

## User intent and provenance

The user explicitly requested reading Harness improvement opinions through our wiki MCP and rejected use of Rovo: “그 개선안도 우리 wiki mcp써서 읽어야지 rovo를 왜 썼어?” and “wiki mcp써서 읽어야지”. This authorizes the read route, not publication, new credentials, another service, or project connection changes.

## Required behavior

- The existing `atlassian` MCP can read the personally configured Harness feedback page and its comments even when the current project has no wiki connection.
- The private destination and its credentials remain in the existing home-private configuration. Project credentials, Rovo and arbitrary caller-supplied remote destinations are not fallbacks.
- An absent inbox keeps normal unconfigured project behavior inert. Unsafe or unavailable private settings/authentication produce a truthful unavailable result.
- Reading never posts, edits, acknowledges or deletes feedback, issues tokens, or changes configuration. Sending remains separately and explicitly authorized.
- Results identify the page/version and preserve native source content, with bounded reads and explicit continuation rather than pretending truncated results are complete.

## Derived implementation boundaries

These are technical choices supporting the user outcome, not additional user quotations: one `read_personal_feedback(limit, cursor)` tool; reuse of the guarded canonical private helper; server-issued connection-bound cursors; native pagination and reply ancestry checks; bounded isolated helper subprocess; no background fetching; packaged helper dependencies and interpreter. Cursor validity is not a claim that remote content never changes.

## Verification

Exercise the packaged MCP stdio boundary with project wiki absent, a separate private account, page/comment/reply continuations, rejected forged/cross-scope cursors, transport/subprocess failures and zero writes. Preserve existing project wiki reads and explicit send behavior. Synthetic TLS fixtures prove transport boundaries; actual configured-inbox MCP read proves the real read route only, not every host's automatic tool loading.
