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

Start with a standalone `knowledge-confluence` package, a service-named
`confluence` MCP and global connection configuration. Home storage is partitioned
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
