# Python wiki MCP on native Windows and POSIX

## User requirements

The user explicitly requested that the existing wiki MCP be implemented in
Python rather than Java, that native Windows always be supported, and that
POSIX compatibility not be a baseline prerequisite. The user also authorized
all applicable Harness improvement-inbox proposals.

The source inbox comment 62980159 version 1 requests reuse of an existing
compatible token for the same site, account, authentication transport and
required permissions. If none is valid, setup should issue one token and
securely populate the separately owned private settings. A newly required token
uses the maximum expiration actually permitted by the provider UI/account
policy. Reusing an existing token does not require extending its expiration.
Actual issuance count and expiration are reported without credential values.

## Required behavior

- The standalone `atlassian` MCP and installer run with Python on native
  Windows and POSIX, without Java, Gradle, WSL, Bash or privileged symlink
  creation. Python dependencies are installed and verified for the interpreter
  actually registered with each host.
- Preserve the existing public document, comment, history, search, relation,
  connection-status and private-inbox operations, their bounded schemas and
  truthful error/freshness behavior. Runtime migration does not authorize
  additional public Jira operations.
- Keep the common project YAML selections and independently private project
  and personal-inbox authentication. Public site changes never authorize
  forwarding an existing secret to another origin. Matching credentials may
  be deliberately reused during authorized setup; runtime never falls back
  between their private sources.
- Preserve exact rich-storage edits, native version/ancestry checks,
  reference multiplicities, inline anchors, one-shot mutations and readback.
  Uncertain remote writes are never automatically repeated.
- Keep the shared read projection under the user's home, partitioned by site,
  account, selected space and optional ROOT, independently of token rotation.
  Replacement credentials require fresh authorization. Existing cache data
  stays recoverable; partial transactions never become readable snapshots.
- Automatic synchronization and existing hook change notices continue across
  both hosts. Healthy operation stays quiet, and partial/unavailable scopes
  remain explicit.
- Implement real platform-specific private-file, reparse/symlink, ownership,
  locking, atomic replacement and process-tree cleanup protections. Windows
  privacy is not inferred from POSIX mode bits or a missing UID API.
- An installation failure preserves prior usable payloads and unrelated host
  configuration. Register a valid native interpreter and source path for the
  actual host; one OS's registration is not evidence for another OS.

## Derived implementation decisions and validation

Use one direct Python implementation with bounded standard-library transport
and the existing safe YAML parser. Small shared platform primitives support
the plugin and its touched Harness document helpers. Keep the token-independent
compact UTF-8 JSON identity and interoperable corpus/hook lock protocol.
Do not introduce another state ledger, gateway or runtime JVM fallback.

Validate actual stdio, synthetic TLS/native document behavior, concurrency,
interruption, authentication isolation and installed payloads. Native Windows
execution is mandatory acceptance evidence; Linux or mocked platform checks
cannot establish it. An unavailable Windows device remains unfinished with its
real unblock condition. Completion evidence belongs to the owning WBS rather
than a copied claim in this requirement.
