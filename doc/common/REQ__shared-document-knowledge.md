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
by remote origin, authenticated credential identity and registered root. Read
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
