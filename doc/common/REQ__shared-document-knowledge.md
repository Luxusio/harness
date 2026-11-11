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
revised configuration on 2026-11-11: “연결정보는 프로젝트에 커밋되는 파일” and
“인증정보는 커밋 안되는 mise.local.toml”, specifying YAML and commonly used
token/email names. These user_explicit instructions supersede earlier env-only
connection selection. The user further requested the Harness common configuration instead of a
provider-specific YAML. `doc/harness/manifest.yaml` under `wiki` owns nonsecret
connection choices; private authentication is injected. The plugin does not read TOML or
invoke mise. No JSON or old connection-env fallback remains. The initial
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

## Personal Harness feedback

User explicit correction on 2026-11-11: the personal improvement inbox must
not be committed with the project. The user approved implementing that private
setting and identified the need for a separate account from project wiki auth.
Project knowledge selections remain in the shared manifest; the personal
Harness feedback destination and its authentication remain outside repositories.

Derived implementation: `~/.config/harness/feedback.yaml` selects one existing
Confluence Cloud page with `backend: atlassian` and `page: <HTTPS page URL>`.
`~/.config/harness/mise.local.toml` supplies its own `ATLASSIAN_BASE_URL`,
`ATLASSIAN_EMAIL` and `ATLASSIAN_API_TOKEN`. Evaluate this file independently;
inherited project or global credentials cannot supply missing private values.
The credential origin must match the selected page before any network request.
No project setting, environment destination override or additional registered
MCP selects the personal inbox.

Reporting remains printable and read-only by default. Only an explicit request
to publish invokes the short-lived personal sender. It appends a native footer
comment without overwriting the page, accepts report text on stdin and does not
save it to Git or echo credentials/report content in results. A successful
result requires readback of the exact comment, page, body and authenticated
author. An uncertain response remains unknown and must not trigger automatic
resubmission. Missing/invalid configuration or unavailable authentication leaves
publication pending without suppressing the copyable report or falling back to
the project's wiki. Local fixtures must demonstrate separate project/private
accounts, zero requests on pin mismatch and one POST on uncertain outcomes.

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
installation. Preserve injected authentication; public connection settings come from project YAML.

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

## Project connection and private authentication

The project Git root owns common `doc/harness/manifest.yaml`; its optional
`wiki` mapping selects `backend: atlassian` with activation, one site and
`spaces: [{key: TEAM}, {key: DEV, rootPageId: "456"}]`. The user explicitly
approved multiple spaces on that one site and optional ROOT on 2026-11-11.
The runtime resolves native space IDs from keys; `spaceId` is not user input.
Omitting ROOT selects all accessible content in that space, including separate
top-level trees outside the homepage ancestry. A selected ROOT confines access
to that document and its descendants. Each space may select existing memory
and feedback destinations and comment watches; polling is shared configuration.
Duplicate space keys, mixed old single-space fields and unknown fields are
invalid. Preserve unrelated Harness fields and comments. No separate provider
settings file or fallback remains. Subdirectories use the nearest Git root,
including gitfile worktrees. Nested repositories cannot borrow outer settings;
without Git use only the startup directory. Missing common settings, absent wiki or valid disabled wiki is local/inert. Present malformed, unreadable or unauthorized configuration stays
unavailable. Authentication fields never belong in YAML.

Use private injected `ATLASSIAN_EMAIL` and `ATLASSIAN_API_TOKEN`, with
`ATLASSIAN_BASE_URL` as a credential-origin binding. This last field is a derived
authentication safeguard: committed `site` otherwise controls the first Basic
auth request. The private origin must match the YAML site before clients/cache
activation; it never overrides routing. Setup preserves a credential's original
issuer and does not silently repin it from changed repository content.

Runtime reads no mise file, does not execute mise and accepts no nonsecret env
overrides or fallback connection source. MCP settings are immutable startup
snapshots; reconnect after connection/auth changes. Preserve shared HOME cache
identity independently of project path or Claude/Codex. Python policy/hooks conservatively detect plain-key manifests without wiki
before loading PyYAML; ambiguous roots or a configured wiki need the real safe
parser. A missing parser for those cases stays unavailable. Empty/null or
malformed wiki is invalid; missing wiki is distinct. Backend setup never creates
a wiki-only installation-marker stub. Setup
checks the actual installed hook interpreter and handles that optional
prerequisite; runtime hooks never install dependencies. Parsing, scope selection
and errors must agree across native runtime and Harness, without exposing input
values or secrets. Existing permission, root, native version and uncertain-write
boundaries remain intact.

One MCP searches the selected spaces together or filters an explicit key.
Results and readiness/failures identify the space; one unavailable space does
not suppress usable spaces. Continuations remain bound to query, selected
spaces and their snapshots. An existing document or comment is routed by its
native identity; creating under an explicit parent uses that parent's space.
A parentless creation requires explicit space selection and the supported
native creation path. Conflicting key/document/parent selections are rejected
before writes. Multiple possible logical memory/feedback destinations require
explicit selection, with no arbitrary first-space default or implicit broadcast.
All operations still enforce current permissions, configured scope and native
version/readback/uncertain-write rules.

Derived implementation: existing per-space corpus and native services remain
behind one typed dispatcher. Cache partitioning uses normalized site, exact
account email, ROOT or the whole-space marker, and space key, independently of
project path, host runtime and token value. This keeps hooks network-free and
avoids a separate resolution registry. Native manifests verify the resolved
space ID, selection kind and current credential admission. Old partitions are
preserved; changing the public schema may require a fresh synchronization and
never imports old authorization. Change notices label spaces and retain
independent cursors and local locking/error boundaries. Derived resource bounds
limit a connection to 32 distinct spaces and one hook delivery to 32 events
across those spaces, with an allowance for each; this is an implementation
bound rather than a user-requested number.
