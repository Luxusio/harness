# Confluence knowledge plugin

Standalone business knowledge and document management extracted from Talos.
Harness remains the development controller. This plugin registers a service-named
`confluence` MCP; no workflow, database, test-publication or Jira controller is
registered. Ordinary document operations do not require project registration,
WBS, Jira or repository-local document copies.

## Connection

Configure `~/.config/knowledge-confluence/config.json` (or the absolute file in
`KNOWLEDGE_CONFLUENCE_CONFIG`). Do not put credential values in this file:

```json
{
  "schemaVersion": 1,
  "enabled": true,
  "site": "https://YOUR-SITE.atlassian.net",
  "space": "YOURSPACE",
  "spaceId": "123",
  "rootPageId": "456",
  "emailEnv": "JIRA_EMAIL",
  "tokenEnv": "JIRA_API_TOKEN",
  "pollIntervalSeconds": 300
}
```

IDs must identify real accessible native content in your selected scope. Set the
named environment variables through your runtime's credential configuration.
Optional `memoryPageId` and `feedbackPageId` select existing destinations; the
plugin must not guess or create company destinations. An absent/disabled
connection does not authorize network operations. An invalid enabled connection
is an error, not a request to write fallback repository documents.

## Shared corpus and operations

The read projection lives under `~/.local/share/knowledge-confluence/` (or
`$XDG_DATA_HOME/knowledge-confluence/`). Different projects share the same
configured connection; site, credential identity and registered ROOT partition
the corpus. Do not edit generated Markdown/storage/manifests. Remote documents
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

## Build and install

Java 21 and Gradle are build prerequisites. Build/test in this directory:

```sh
gradle --no-daemon test shadowJar
python3 install.py --skip-build --runtime codex
# Or: python3 install.py --skip-build --runtime claude
```

The installer prepares a versioned standalone payload and registers its local
marketplace through the selected host CLI. It preserves existing plugin trees;
`--prepare-only` prepares the payload without registration. Restart/reconnect
the host to load a changed MCP or skill. Installation does not configure a
company connection, import documents, replace Talos or publish remote content.

The stdio launcher uses `KNOWLEDGE_CONFLUENCE_JAVA` when set to an absolute Java
executable, otherwise `JAVA_HOME/bin/java`, otherwise Java from PATH. Source
launches require `build/libs/knowledge-confluence.jar`; packaged launches use
`lib/knowledge-confluence.jar`.

See [source provenance](PROVENANCE.md). Historical Talos QA results are not
validation of this extraction. Live company integration needs an authorized
connection and is reported separately from fixture/native transport tests.
