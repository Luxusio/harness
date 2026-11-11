# Atlassian wiki plugin

Standalone business knowledge and document management extracted from Talos.
Harness remains the development controller. This plugin registers a service-named
`atlassian` MCP; no workflow, database, test-publication or Jira controller is
registered. Ordinary document operations do not require project registration,
WBS, Jira or repository-local document copies.

## Connection through injected environment

The plugin reads only its process environment. It does not open JSON or TOML
connection files and does not invoke mise to load connection settings. Set variables in your own ignored
`mise.local.toml`, for example:

```toml
[env]
WIKI_ATLASSIAN_ENABLED = "true"
WIKI_ATLASSIAN_SITE = "https://YOUR-SITE.atlassian.net"
WIKI_ATLASSIAN_SPACE = "YOURSPACE"
WIKI_ATLASSIAN_SPACE_ID = "123"
WIKI_ATLASSIAN_ROOT_PAGE_ID = "456"
ATLASSIAN_EMAIL = "YOUR_EMAIL"
ATLASSIAN_API_TOKEN = "YOUR_TOKEN"
# WIKI_ATLASSIAN_MEMORY_PAGE_ID = "789"
# WIKI_ATLASSIAN_FEEDBACK_PAGE_ID = "790"
# WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS = "300"
# WIKI_ATLASSIAN_WATCH_COMMENT_PAGES = "456,789"
```

Keep real credentials out of Git and documents. A secret manager or any other
environment injector works too. Start the host with the intended environment,
for example `mise exec -- codex` or `mise exec -- claude`. Merely creating the
file does not change an already running host or MCP; reconnect/restart after
changing the environment. The MCP receives a fixed connection for its lifetime.
Different projects receiving the same connection share the home corpus without
project registration. Projects using different sites, accounts, tokens, spaces
or roots receive separate hashed corpus directories. Run each host/MCP with
that project's environment; changing cwd in an existing MCP does not switch
its account. A host that shares one MCP process across projects needs separate
MCP execution contexts for different connections.

All required IDs must identify accessible native content in your selected scope.
Optional memory/feedback IDs select existing destinations; the plugin never
guesses or creates company destinations. Polling defaults to 300 seconds and
accepts 30..86400. `WIKI_ATLASSIAN_ENABLED` accepts exactly `true` or `false`.
No connection variables means local mode; shared `ATLASSIAN_*` credentials alone
do not activate the wiki. Exact `false` explicitly disables it. Partial or
invalid connection settings, including fields supplied without activation,
are errors and never authorize fallback repository document writes.

The current backend implements Confluence documents, native comments and
history; the `atlassian` name does not imply Jira issue operations.

## Shared corpus and operations

The read projection lives under `~/.local/share/wiki-atlassian/` (or
`$XDG_DATA_HOME/wiki-atlassian/`). Different projects share the same
injected connection; site, credential identity and selected ROOT partition
the corpus. A SHA-256 connection key includes origin, account, credential and
space/root IDs; raw credentials never appear in directory names. Token rotation
selects a new partition rather than trusting a prior permission snapshot. Do not
edit generated Markdown/storage/manifests. Remote documents
remain authoritative. Cached reads describe snapshot freshness; offline access
does not prove that remote access permissions are still current.

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

Use the `knowledge` skill for source attribution, targeted retrieval and native
editing. The `migrate-docs` skill and independent `document-reviewer` cover
authorized knowledge consolidation and migration. They do not replace actual
product code review/QA or authorize deleting repository originals.

## Change context in Harness

Install the current Harness plugin in the same runtime and enable its hooks.
Use the Harness installer to update the hooks and reload the host after
installation. Run the wiki MCP with the same environment as the Harness
session. Its first completed mirror establishes the document baseline. Later committed changes
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
up to 64 events and 128 session cursors. The hook delivers up to eight events
per call. Visibility follows the latest committed snapshot. A confirmed
access denial for the configured root pauses notices until the next successful
authorized synchronization. Notice text consists of fixed labels, native IDs
and observed versions. The agent checks relevance
and reads the evidence through MCP before acting.

Set `WIKI_ATLASSIAN_WATCH_COMMENT_PAGES` to up to ten distinct page IDs from the
configured root, separated by commas. Comment monitoring starts with a
baseline for each selected page. A later observed change directs the agent to
`list_comments`. Provider access failures and incomplete inventories keep the
last complete observation. The watcher uses the wiki poll interval and a shared
lock. It checks the least recently attempted pages first, with a shared budget
of 512 requests, 32 MiB and 30 seconds per scan, and up to eight seconds per
page. A page interrupted by the shared scan budget keeps its priority for the
next scan. The connection retains up to 64 page observations. Use
`connection_status` to check coverage and whether root access has paused it.

## Search and knowledge maintenance

Local search ranks exact native IDs and textual matches and returns short
source excerpts. For a result that includes a `read` argument object, use it
with `get_document` to read the same projection at its evidence location.
Increase `length` to read more context, or use `nextOffset` with the same
document, version, format and snapshot. Repeat the search if its snapshot
has changed. Native outline and section operations remain available for selecting and editing a precise section.
Limit the domain root when searching a large corpus.

The knowledge skill covers attributed source records, topic structure,
explicit document relationships, correction, and handling notices. Document
publication still uses the selected Confluence scope and native permissions.

## Build and install

Java 21 and Gradle are build prerequisites. Build/test in this directory:

```sh
gradle --no-daemon test shadowJar
python3 install.py --skip-build --runtime codex
# Or: python3 install.py --skip-build --runtime claude
```

The installer prepares a versioned standalone payload and registers its local
marketplace through the selected host CLI. It preserves existing plugin trees;
the installed MCP configuration resolves the Python interpreter and private
launcher path explicitly, without relying on host plugin-variable expansion.
The registered marketplace path stays stable across upgrades. A private,
installer-locked selection points to an immutable payload; existing processes
retain their original runtime path while the next installation selects a new
version. Unknown marketplace metadata or selections are preserved and refused.
`--prepare-only` prepares the payload without registration. Restart/reconnect
the host to load a changed MCP or skill. Installation does not inject a
company connection, import documents, replace Talos or publish remote content.

The stdio launcher uses `WIKI_ATLASSIAN_JAVA` when set to an absolute Java
executable, otherwise `JAVA_HOME/bin/java`, otherwise Java from PATH. Source
launches require `build/libs/wiki-atlassian.jar`; packaged launches use
`lib/wiki-atlassian.jar`.

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
