# Personal feedback reads through wiki MCP

Status: accepted user outcome; derived implementation choices are labelled separately.

## User intent and provenance

The user explicitly requested reading Harness improvement opinions through our wiki MCP and rejected use of Rovo: “그 개선안도 우리 wiki mcp써서 읽어야지 rovo를 왜 썼어?” and “wiki mcp써서 읽어야지”. This authorizes the read route, not publication, new credentials, another service, or project connection changes.

The user subsequently rejected a dedicated feedback tool: “아니 그냥 atlassian mcp 공용으로 만들어두고 돌려쓰면 되잖아”. On 2026-11-14 they explicitly approved its integration and actual connected-account verification. The shared document/comment operations must select the personal connection explicitly; a separate feedback operation is not the public API. That verification permits a clearly identified test comment on the configured inbox and versioned update/readback, not arbitrary remote edits or changing existing reports.

## Required behavior

- The existing `atlassian` MCP can read the personally configured Harness feedback page and its comments even when the current project has no wiki connection.
- The private destination and its credentials remain in the existing home-private configuration. Project credentials, Rovo and arbitrary caller-supplied remote destinations are not fallbacks.
- An absent inbox keeps normal unconfigured project behavior inert. Unsafe or unavailable private settings/authentication produce a truthful unavailable result.
- Reading never posts, edits, acknowledges or deletes feedback, issues tokens, or changes configuration. Sending remains separately and explicitly authorized.
- Results identify the page/version and preserve native source content, with bounded reads and explicit continuation rather than pretending truncated results are complete.
- The same general document, comment and history operations support the configured personal page with `connection: personal`. Project operations retain their own connection and credentials; neither connection is an implicit fallback for the other.
- Personal operations stay within the configured page and its native comments. They do not admit descendants, another space, page creation or moving the configured page. Existing reports are preserved.
- Explicitly authorized publication uses the same general comment operation and requires verified native readback. Unknown publication remains unknown until read back; it must not trigger automatic retransmission.

## Derived implementation boundaries

These are technical choices supporting the user outcome, not additional user quotations: an explicit connection selector on existing Atlassian operations; reuse of guarded private configuration and credential resolution; the existing native document service with exact page-only admission; connection-bound cursors and replica identity; native pagination and reply ancestry checks; bounded isolated execution; packaged helper dependencies and interpreter. Cursor validity is not a claim that remote content never changes. No dedicated feedback MCP tool or compatibility alias remains.

## Verification

Exercise the packaged MCP stdio boundary with project wiki absent, separate project/private accounts, explicit connection selection, page/comment/reply continuations, rejected forged/cross-scope cursors, transport/subprocess failures and zero writes for reads. Test write readback/conflicts/unknown results without retry. Synthetic TLS fixtures prove transport boundaries; actual configured-inbox MCP acceptance separately proves the connected account, cold/warm reuse and explicitly authorized test-comment readback. Neither substitutes for another platform's actual execution.
