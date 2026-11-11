# Shared document knowledge

## User-requested outcomes

Extract document and knowledge management into a separate backend plugin. Keep
Harness focused on planning, development, independent review, QA and execution
state. The document plugin owns native document operations, comments/history,
knowledge guidance, document review and migration methodology.

Keep a shared read replica under the user's home and reuse it across projects;
do not require registering each project or downloading identical copies into
every repository. Synchronize automatically and query documents through MCP.
Provide concise guidance naming the document MCP. Preserve backend-specific
features rather than flattening them into Markdown upload/download.

Local repository operation remains the default without an external document
backend. Avoid conflicting local/remote writing instructions or telling the
model to ignore an already-injected competing instruction. Multiple backends
and both reading/writing were discussed; the first implemented backend is
Confluence, without imposing its interface on future providers.

## Implementation decisions

Use the user-requested `wiki-atlassian` package and `atlassian` MCP. The user
clarified that connection settings come only from injected environment variables;
`mise.local.toml` is an environment setup example, never a file the plugin reads.
No JSON connection file or automatic mise invocation remains. The initial
implemented capabilities are Confluence documents, comments and history. Home storage is partitioned
by remote origin, account identity and registered root. Read
replicas and manifests are derived; Confluence remains the document authority.
Native storage and safe mutation logic are extracted from the supplied Talos
snapshot. No credentials, company pages or bundled old application JAR enter Git.

Document destination configuration selects the Harness guidance in code. Keep
runtime instructions, product assets and WBS control/evidence local. Connection
failure is distinct from no backend, and does not authorize local fallback
writes. Selecting a plugin is not remote publication authorization.

## Acceptance and safety

Two project working directories can search/read the same corpus without local
document replication or per-project installation. Automatic synchronization and
verified remote writes update the corpus; freshness and failures are visible.
Native version checks, unambiguous partial edits, rich references, comments,
history and uncertain-write outcomes remain intact. A successful remote write
with local publication failure must recover without repeating the mutation.

Different accounts/roots cannot read each other's cached content. Retain
permission/registered-root checks for mutations. Local reads describe snapshot
freshness and do not claim instantaneous permission revocation while offline.
No silent incomplete-content success or fake review/QA/installation results.

## Scope limits

No new vector/graph DB, central server, retry queue, generic invocation gateway,
company document changes or automatic migration/deletion. Preserve the provided
archive and unrelated workspace changes. Existing Talos test results establish
only its historical baseline and do not approve this extraction.

## Knowledge retrieval and change context

The user requested the remaining knowledge features from the Talos handoff,
excluding vector DB, and asked for hook notifications when documents change
so that the AI can decide how to respond. Deliver detected changes during
normal agent work. The agent consults the current document, its history or
comments, and relevant authored relationships before acting.

The wiki publishes bounded notification metadata from committed snapshots.
An enabled Harness session checks for notices on prompts and after tools.
The first mirror establishes a baseline. Subsequent page changes and
configured Confluence comment observations use the same connection partition
as the read replica. Notices report available evidence locations and gaps
without including authored content. The hook uses local metadata and keeps
its own per-session delivery position. A session receives context at its
next hook event. Change detection depends on a running wiki process and its
poll interval.

Search returns ranked excerpts and locations tied to the read snapshot.
Native IDs, short Korean terms, and scope filters are supported by the
local search. Tests use representative local questions and exact evidence.
The knowledge skill defines source capture, synthesis, correction, and
impact review using existing native tools. Live company retrieval quality
and operation against a real Atlassian account require separate evaluation.

## Guided installation and connection

The user requests backend-owned setup that can follow a supplied installation
guide in logged-in Chrome, install the selected product/plugin, issue compatible
credentials, configure synchronization and verify the result. Ask the user
which Confluence space and which existing page to use as ROOT when unspecified.
ROOT can be a nested page; replicate that page and its descendants. Account
permissions and token scope remain distinct from replica ROOT selection.

The wiki-atlassian setup skill owns its provider-specific onboarding. Reuse
working installations and credentials, preserve unrelated local settings, and
store secrets through a protected handoff in ignored private configuration.
Retain the named guide's product and settings contract. Report actual login,
account-policy, secret-transfer and host-reload blockers without claiming a
saved configuration proves a working connection. Verify the chosen ROOT with
read-only native/MCP operations. Authoring this skill does not authorize live
company installation or token issuance from an example request.

## Account cache reuse

The user objects to downloading a new corpus after API token replacement and
requests removal of environment-based cache location overrides. Use the
standard user-home location and ignore XDG_DATA_HOME for wiki data and plugin
installation. Preserve the injected environment contract for connection data.

Isolate content by site, exact account email, space ID and ROOT ID. Token
replacement requires fresh authorization before cached content and notices
are accessible, while intact unchanged bodies can be reused. Only a complete
authorized inventory switches the current credential binding. Partial writes
must not mix another credential's admitted contents into that snapshot.
Retired credential failures must not invalidate the replacement's admission.
Preserve normal body-refresh detection and existing cache bytes during any
one-time migration. Unknown opaque legacy directories cannot establish
account ownership by proximity or matching space alone.

## Attribution, evidence and decision authority

Status: user_confirmed. Source: conversation on 2026-11-11, user asked how to
apply “사실이랑 추측 뭐 그런 내용 분리하라고”, then replied “ㅇㅇ ㄱㄱ”
to the proposal to distinguish source statements,
observed facts, interpretations/hypotheses, proposals and authorized decisions
in the existing authoring, retrieval and correction workflow. This approval
covers that proposal, including preserving the distinction in migration/reuse;
it does not authorize company publication or turn unrelated research ideas
into requirements.

Preserve who said a claim, its actual evidence and the authority for a decision
as separate concerns. A user's hypothesis and an AI proposal retain their
status when summarized, searched, answered, migrated or reingested. A source
assertion establishes what its author said, not independent verification.
Acceptance of a decision does not prove implementation, QA or deployment.
Changes in evidence or authorization may justify a scoped change in status;
retain applicable conditions, sources and unresolved uncertainty. Missing
provenance stays unknown, and changed source versions require reassessment
rather than automatically proving prior claims false.

Implementation decisions (derived from that approved outcome): use visible
prose/sections in existing topic documents and keep one behavior reference in
the knowledge skill. The document reviewer checks source fidelity and status
changes; migration guidance preserves them. Existing native versions, CAS,
basedOn and readback establish revision/write integrity, not semantic truth or
approval. No mandatory page template, per-sentence schema, new ledger or runtime
truth classifier is introduced. Independent source-to-draft scenarios evaluate
the guidance; successful packaging alone cannot establish semantic correctness.
