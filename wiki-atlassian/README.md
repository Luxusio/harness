# Atlassian wiki plugin

Standalone business knowledge and document management extracted from Talos.
Harness remains the development controller. This plugin registers a service-named
`atlassian` MCP; no workflow, database, test-publication or Jira controller is
registered. Ordinary document operations do not require WBS, Jira or repository-local
document copies. Connection selection uses the existing Harness project settings.

## Project connection and private authentication

Add `wiki` to the consuming project's existing `doc/harness/manifest.yaml`,
preserving all other Harness settings and comments:

```yaml
# Existing Harness fields stay here.
wiki:
  backend: atlassian
  enabled: true
  site: "https://YOUR-SITE.atlassian.net"
  # pollIntervalSeconds: 300
  spaces:
    - key: "TEAM"               # All accessible content in this space
    - key: "DEV"
      rootPageId: "456"          # Only this document and its descendants
      # memoryPageId: "789"
      # feedbackPageId: "790"
      # watchCommentPages: "456,789"
```

One connection has exactly one site and up to 32 distinct space keys. Native
space IDs are resolved automatically; do not put `spaceId` in the settings.
Omit `rootPageId` for all accessible documents in that space, including separate
top-level trees outside the homepage. A restricted homepage does not narrow a
whole-space selection. Setting ROOT selects that document and its descendants.
The space count is a bounded runtime resource limit.

Keep authentication in the project's Git-ignored, owner-private `mise.local.toml`:

```toml
[env]
ATLASSIAN_BASE_URL = "https://YOUR-SITE.atlassian.net"
ATLASSIAN_EMAIL = "YOUR_EMAIL"
ATLASSIAN_API_TOKEN = "YOUR_TOKEN"
```

`ATLASSIAN_BASE_URL` binds credentials to their intended HTTPS origin. It must
match YAML `wiki.site` (a terminal slash is ignored); it cannot override that site.
Missing or mismatched authentication blocks connection before requests or cache
activation. Keep the pin associated with the credential's actual issuer; do not
silently repin existing tokens when committed YAML changes. YAML rejects
credentials and unknown fields. Secrets never belong in the shared file.

Start the host from the consuming project, for example `mise exec -- codex` or
`mise exec -- claude`. The plugin does not read TOML or invoke mise: it receives
injected authentication. From subdirectories it finds the nearest Git boundary,
including linked worktrees, and reads only that root's common manifest. Nested repositories
do not inherit an outer repository's connection. Outside Git, only the startup
directory's `doc/harness/manifest.yaml` is used. No home, plugin-directory, JSON, old connection env or
configuration-path override supplies a fallback connection.

The MCP snapshots its startup project and settings. Reconnect/restart after
changing YAML or authentication; changing cwd cannot retarget a running MCP.
Different projects with the same site/account/space/ROOT share the home corpus
without replica registration. Different connections remain partitioned. A host
sharing one MCP process across projects needs separate execution contexts for
different connections.

Use quoted positive string page IDs, actual YAML booleans and integer polling values.
Selected IDs must identify accessible content in the corresponding space/scope.
Optional memory/feedback IDs select existing destinations; the plugin does not
guess or create them. Polling defaults to 300 seconds and accepts 30..86400.
The file is a bounded mapping; duplicates, aliases, explicit tags and unknown
keys are rejected. Missing common settings, an absent `wiki` section or valid `wiki.enabled: false`
means local mode without authentication. A present invalid/unreadable wiki
configuration or missing enabled credentials stays unavailable and never
authorizes fallback repository writes. Other manifest sections are not wiki
settings. Backend setup reuses the existing common manifest; it never creates a
wiki-only stub that would implicitly initialize Harness. Use explicit Harness
setup when the common project settings have not been initialized.

Harness's Python policy/hooks use PyYAML when the optional wiki section needs
interpretation. A conservative plain-key absence check keeps ordinary common
settings without `wiki` usable without that dependency; ambiguous YAML requires
the safe parser rather than guessing no backend. Backend setup verifies PyYAML
with the exact interpreter registered for installed Harness hooks and installs
that prerequisite through the selected package manager when needed. A configured or ambiguous
wiki selection without its parser remains unavailable; hooks never install packages or
start Java to parse settings.

The current backend implements Confluence documents, native comments and
history; the `atlassian` name does not imply Jira issue operations.

## Guided setup

Ask the agent to install and configure the backend from its guide using your
logged-in Chrome, or invoke `wiki-atlassian:setup`. Before installation, the
agent can read [the setup skill](skills/setup/SKILL.md) from this source checkout.
The skill handles installation, compatible token issuance, protected local
settings and read-only connection verification. It asks which spaces on the
selected site to connect and, for each, whether to synchronize the whole space
or an existing ROOT document and its descendants. A nested page is a valid ROOT. ROOT selection
does not narrow the token's native account permissions.

Browser attachment, login/MFA and account policy can require user action.
The agent establishes a protected token transfer before issuance and reports
actual host reload and synchronization results. A guide for another product,
including Talos, retains that product's installer and settings.

## Shared corpus and operations

The read projection lives under `~/.local/share/wiki-atlassian/`. The corpus
and plugin installation use the standard user-home location and ignore
`XDG_DATA_HOME`. Different projects share the same account/site/space/ROOT
corpus. Its SHA-256 directory key includes origin, exact account email and
space key and ROOT ID (or the whole-space marker). A separate credential fingerprint binds a completed inventory
to the current token. After token rotation, the MCP freshly authorizes the
selected documents before reusing intact same-version bodies. Cached reads
and hook notices wait for that authorization. No token text appears in paths.
Overlapping tokens can each publish a fresh inventory; a process waits for
its own credential binding before reading the shared replica. Do not
edit generated Markdown/storage/manifests. Remote documents
remain authoritative. Cached reads describe snapshot freshness; offline access
does not prove that remote access permissions are still current.

Confirmed revocation immediately blocks cached reads in the observing process.
Publishing the shared revocation marker has a three-second aggregate lease
budget. If publication remains blocked, error details report
`scopeFence: pending` and `scopeFenceCode: replica_denial_pending`, preserving
the original provider error or unknown-write outcome. Existing synchronization
retries; other processes observe revocation after the shared marker is
published. Immediate cross-process revocation is not claimed while pending.

The earlier single-space settings (`space`, `spaceId` and top-level ROOT or
destination fields) are rejected. Move the selected key and optional page IDs
into `spaces` and remove the old fields. The new key-based partitions can need
a fresh synchronization. Existing cache directories remain untouched; their
authorization is not imported into a new selection.

The MCP owns automatic startup/periodic synchronization and publication of
verified writes. Search/read queries use the shared corpus; history, comments
and native edits retain backend-specific semantics. Normal writes update the
existing replica without another refresh call. A remote success with local
failure is distinct from a failed or uncertain remote write; never retry an
uncertain mutation merely to refresh local files.

Current document reads/search use the local replica. Exact XML outline/section
and historical-version reads retain native validation and can contact the
service. Background synchronization also makes network requests: inventory
polls check versions/access, and one coordinated authoritative body sweep per
configured interval catches changes that do not increment native page versions.
Multiple processes share a sweep lease and the existing checkpoint's derived
body-freshness timestamp. A completed inventory alone never claims a completed
body sweep. Known local publication failures trigger exact-page repair; after
restart, a lost in-memory notification is recovered by the next due sweep,
subject to service availability. Neither path repeats a remote mutation.

Whole-space synchronization uses paginated native batches for authorized
document bodies and complete bulk metadata verification before publication.
It does not require several metadata requests per document. ROOT connections
retain their narrower body-read scope. A complete current inventory cannot
regrant a missing document from an older snapshot; observed admission drift
fences unsafe cached access until a complete authorized repair succeeds.

Synchronization remains bounded by elapsed time, request count, response size
and aggregate bytes. `connection_status` exposes the sweep stage, elapsed time,
request count and terminal failure reason without document bodies or credentials.
Aggregate deadline exhaustion is distinct from a network timeout. A published
document snapshot does not imply successful comment observation; failed comment
polling preserves the previous fingerprint and reports its remaining work.

Use the `knowledge` skill for source attribution, targeted retrieval and native
editing. The `migrate-docs` skill and independent `document-reviewer` cover
authorized knowledge consolidation and migration. They do not replace actual
product code review/QA or authorize deleting repository originals.

Knowledge guidance keeps source statements, observations, hypotheses, proposals
and authorized decisions distinguishable within topic documents. Source
attribution, evidence strength and decision authority are separate: user
hypotheses remain hypotheses, and an approved decision does not prove deployment.
Search answers, reused summaries, corrections and migrations retain those
distinctions and link important conclusions to their applicable evidence.
The skill and independent document reviewer assess meaning; native versions,
CAS and readback establish revision/write integrity, not factual truth. These
are authoring/review conventions, not a runtime semantic classifier or a required
per-sentence record schema. See [knowledge guidance](skills/knowledge/SKILL.md#distinguish-evidence-and-authority).

## Change context in Harness

Install the current Harness plugin in the same runtime and enable its hooks.
Use the Harness installer to update the hooks and reload the host after
installation. Start the wiki MCP and Harness session for the same project YAML
and injected authentication. Its first completed mirror establishes the document baseline. Later committed changes
produce a bounded local notice stream. Verified edits through the MCP use the
same publication path as changes observed during remote synchronization.
The stream tracks page additions, changes, and changes to visible scope.
It includes same-version storage changes that a refresh can observe.

In a Harness-enabled checkout, the next prompt or supported post-tool event
supplies relevant notice metadata to the model. An idle session receives
context when it next runs a hook. Detection of external edits follows the
MCP's polling interval. A session can catch up from retained notices and sees
a gap message if older metadata is unavailable. The hook keeps a bounded
cursor for each session in the connection partition. Each partition keeps
up to 64 events and 128 session cursors. The hook delivers up to 32 events
per call across all configured spaces, with space labels and an allowance for
each space. A busy or unavailable partition does not suppress another space's
notices; cursors remain independent. Visibility follows the latest committed snapshot. A confirmed
access denial for the configured root pauses notices until the next successful
authorized synchronization. Notice text consists of fixed labels, native IDs
and observed versions. The agent checks relevance
and reads the evidence through MCP before acting.

Set each `wiki.spaces` entry's `watchCommentPages` to up to ten distinct page
IDs in that selection, separated by commas. Comment monitoring starts with a
baseline for each selected page. A later observed change directs the agent to
`list_comments`. Provider access failures and incomplete inventories keep the
last complete observation. The watcher uses the wiki poll interval and a shared
lock. It checks the least recently attempted pages first, with a shared budget
of 512 requests, 32 MiB and 30 seconds per scan, and up to eight seconds per
page. When earlier pages consume the shared budget, the interrupted page keeps
its priority for the next scan. A page that exhausts the shared request or
response budget on its own yields to its peers. The connection retains up to 64
page observations. Use `connection_status` to check comment coverage. Confirmed
root denial reports coverage as unavailable.

## Search and knowledge maintenance

Local search ranks exact native IDs and textual matches and returns short
source excerpts. For a result that includes a `read` argument object, use it
with `get_document` to read the same projection at its evidence location.
Increase `length` to read more context, or use `nextOffset` with the same
document, version, format and snapshot. Repeat the search if its snapshot
has changed. Native outline and section operations remain available for selecting and editing a precise section.
Limit the domain root when searching a large corpus.

One MCP serves all configured spaces. Search combines the spaces by default;
use an explicit space key to filter. Results identify their space, and
`connection_status` reports each space separately. Missing access or a failed
synchronization in one space leaves other usable spaces available, with partial
coverage reported. Search continuations are tied to their selected spaces and
snapshots; restart a search when that evidence changes.

Existing document and comment identities determine their owning space. An
explicit destination key must agree with the document or parent. For creation,
an explicit parent determines the space. Without a parent, choose the space
explicitly: a whole-space selection supports native top-level creation, while
a rooted selection creates under its configured ROOT. Logical memory/feedback
records use a unique configured destination or require an explicit space when
several qualify. Ambiguity makes no remote write. Cross-space edits are separate
explicit operations; selecting several spaces does not broadcast a mutation.
Version conditions in `basedOn` and native link-retargeting still enforce the
destination service's selected scope; cross-space source/link-edit references
are rejected. Existing authored hyperlinks remain intact. If an existing key
resolves to a different native space ID, synchronization refuses to reuse its
previous replica and reports `space_identity_changed`; preserve that data for
inspection.

The knowledge skill covers attributed source records, topic structure,
explicit document relationships, correction, and handling notices. Document
publication still uses the selected Confluence scope and native permissions.

## Install the Python runtime

Use Python 3.12 or later with PyYAML in that same interpreter. From this
directory, run with the detected native Python (`py` on Windows, or `python3`
on POSIX):

```text
python -m pip install "PyYAML>=6.0"
python install.py --runtime codex
python install.py --runtime claude
```

The installer prepares a versioned standalone payload and registers its local
marketplace through the selected host CLI. It preserves existing plugin trees;
the installed MCP configuration resolves the Python interpreter and private
launcher path explicitly, without relying on host plugin-variable expansion.
It refreshes Claude's cache and verifies the host's enabled selection and
actual cached payload bytes before reporting success.
The registered marketplace path stays stable across upgrades. A private,
installer-locked selection points to an immutable payload; existing processes
retain their original runtime path while the next installation selects a new
version. A recognized, owner-verified `packages/0.1.0+h…/plugin` selection can
migrate once to the Python payload. The old immutable package stays intact;
registration failure preserves the owned previous selection for recovery.
Unknown, altered or linked marketplace selections are preserved and refused.
Host-owned caches follow each CLI's lifecycle: Codex can remove its previous
code cache during update, while installer-owned immutable launchers remain
available. Preserving payloads does not claim that every host cache is retained.
`--prepare-only` prepares the payload without registration. Restart/reconnect
the host to load a changed MCP or skill. Installation does not inject a
company connection, import documents, replace Talos or publish remote content.

The MCP runs directly in the installer-selected Python interpreter. Java,
Gradle, WSL, Bash and privileged symlink creation are not prerequisites.
Versioned payloads live inside the stable marketplace directory, and its
manifest selects an ordinary relative directory. Windows uses native file
handles, ACLs, locks and process jobs; POSIX uses its descriptor and record-lock
implementations. Neither platform is an emulation prerequisite for the other.

The plugin's portable tests run from this directory with
`python -m pytest --confcutdir=. tests`. This keeps unrelated repository test
infrastructure outside the standalone plugin's requirements. Native Windows
filesystem, ACL, process and installation acceptance is reported separately
from Linux tests and platform-specific skips.

## Personal Harness improvement inbox

When `~/.config/harness/feedback.yaml` exists, the same `atlassian` MCP also
offers `read_personal_feedback(limit, cursor)`. It works without enabling a
project wiki and reads only the page selected by that private setting, using
the separate `~/.config/harness/mise.local.toml` authentication. Project
credentials and Rovo are never fallbacks. No new token or project connection is
needed when that existing private connection already works.

The first read returns native storage content and a comment page. Follow returned
`read`, `next` or `replies` cursors to expand bounded content. Cursors bind the
private connection and observed scope; changed content or credentials may require
starting without the old cursor. HTTP and body continuations are checked, not
followed to arbitrary destinations. No background reading, acknowledgement,
publication or local report storage occurs.

The installed launcher uses its own Python invocation and the packaged canonical
private helper directly. That Python needs PyYAML for configured private YAML; missing
dependencies return unavailable and are not installed automatically. Start via
the launcher, then reconnect the host after installation to expose the new tool.

## Replacing the early knowledge-confluence installation

The new name has no legacy config reader or duplicate service alias. If the
`knowledge-documents` marketplace still points to the old installation, use the
host's marketplace removal command for that source before registering this one
(Codex: `codex plugin marketplace remove knowledge-documents`). Install and
verify `wiki-atlassian@knowledge-documents`, then remove the old plugin
registration (`codex plugin remove knowledge-confluence@knowledge-documents`).
Keep old corpus data; this rename does not automatically migrate or delete it.
These steps change only local plugin registration, never remote documents.

See [source provenance](PROVENANCE.md). Historical Talos QA results are not
validation of this extraction. Live company integration needs an authorized
connection and is reported separately from fixture/native transport tests.
