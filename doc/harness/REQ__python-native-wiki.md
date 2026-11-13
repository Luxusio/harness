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

## Installation and synchronization feedback follow-up

The user explicitly requested “개선의견 다시 확인해서 개선 ㄱ”. The configured
Harness feedback inbox, version 55 dated 2026-11-12, reports two regressions
against the Python migration: native Windows atomic publication fails with
Win32 error 87, and a several-hundred-page whole-space synchronization exhausts
its aggregate budget before publishing a readable snapshot. These are reported
observations; the particular rejected Windows argument is not established.
Unrelated product feedback is not an additional Harness requirement.

The accepted observable repairs are safe native publication of new and replaced
files, including Unicode paths, and successful cold and warm synchronization
of an authorized whole space of representative size. Recognized installer-owned
0.1 package selections have a checked one-way upgrade; unknown or modified
selections remain protected. Existing payloads and unrelated settings survive
failed publication or registration.

Derived from the existing security and bounded-execution requirements: use
authorized bulk results only for whole-space connections, validate the complete
current selected graph before atomic admission, and never regrant a missing
page from older cache data. Observed revocation or unresolvable admission drift
must keep unsafe cached content fenced even if candidate publication fails.
ROOT connections retain their narrower body-read scope. Preserve response,
aggregate byte, request and elapsed-time guards; reducing duplicate work must
not replace them with an unlimited operation.

Synchronization status distinguishes its bounded stage, elapsed time, request
count and terminal reason without source bodies or authentication data.
Aggregate deadline exhaustion, network timeout and size/request limits remain
distinguishable. Document publication and successful comment observation are
separate results. Native Windows and real-account acceptance must still be
reported from actual execution, independently of synthetic or Linux tests.

## Native Windows follow-up from feedback revision 56

The same user request applies to the next feedback revision. Its author reports
that the preceding installation and whole-space synchronization worked on
Windows 11/NTFS at `887c645b`. That report is distinct from this workflow's
execution evidence. Remaining reported failures concern replacement with an
open delete-sharing reader, CPython-created private directories, and Windows
test collection or DNS-fixture assumptions.

Derived repairs preserve the existing publication and access-control contract:

- A successful replacement keeps an existing delete-sharing handle on the old
  bytes while a new open sees the new bytes. A reader that refuses delete
  sharing must still prevent replacement. Preserve checked ancestry, ACLs,
  reparse/hardlink checks, private temporaries and the previous target on failure.
- Use the required extended rename capability without retrying a weaker
  operation. Preserve the original native error and expose only a fixed
  operation/capability label and bounded numeric code through installer errors.
  Error 87 alone does not establish an unsupported filesystem or its cause.
  Unpublished temporary cleanup targets only the owned handle; a failed cleanup
  attempt must not replace the primary error or imply guaranteed removal.
- Interpret only OWNER_RIGHTS as the actual owner obtained from the retained
  handle, after that owner passes the existing policy. Ordinary CPython `0700`
  directories can then work without rewriting ACLs. Foreign owners or grants,
  unsupported ACEs and other symbolic owner identities remain rejected under
  the existing policy.
- Selected native tests collect without `fcntl`. Only the POSIX marker fixture
  may skip before its unavailable primitives or filesystem mutation. Preserve
  all other test isolation and installation/ledger protections.
- Synthetic stale-DNS answers are deterministic. Separate real-TLS tests cover
  address fallback and the unchanged aggregate deadline; a host's IPv6 ordering
  is not evidence that production DNS behavior is defective.

The extended rename requires a supporting Windows API and filesystem/driver;
an OS or filesystem name alone does not prove support. New native regressions,
installed MCP and real-account acceptance require actual execution. Linux,
modeled descriptors or supplied reports do not turn an unrun case into PASS.

## Mise-managed installed interpreters

User-explicit correction, 2026-11-13: “플러그인 설치같은거 그 경로로 하면
안된다” and “무조건 mise 경로 써야한다”, referring to a registered
`/project/harness-…/.venv/bin/python`. Installed product execution must use
mise-managed Python rather than remain coupled to the source checkout or its
virtual environment.

Derived implementation contract: both installers select and validate an installed
mise base interpreter, Python 3.12 or later, and record its resolved executable
in canonical MCP, hook and smoke commands. Wiki dependency checks apply to that
interpreter, including PyYAML, rather than the installer caller. Selection uses
an isolated home-based mise context; consumer activation, configuration and
version overrides cannot silently redirect it. Legitimate mise storage settings
remain usable. Missing or invalid mise, Python or required dependencies fail
with bounded, nonsecret diagnostics before installation writes; no system or
project-Python fallback or implicit dependency installation is permitted.

Portable marketplace entry points may bootstrap through mise but must validate
the managed base interpreter before any product entry point runs. Missing
`python@3.12` must not execute product code through a PATH fallback. The raw
templates require an installed 3.12 release; canonical installation accepts
validated 3.12 or later. Preserve entrypoint arguments, hook fail-safety,
timeouts, bytecode prevention, prior payloads and unrelated settings. Effective
payload identities include interpreter changes, and equal reruns remain stable.

Verify generated commands independently, actual fresh/update/rerun installation,
and execution from a different consumer directory without the source checkout
or its virtual environment. Modeled Windows path checks are distinct from
actual Windows execution; the standing native/account acceptance remains.

## Native repair completion

User-explicit correction, 2026-11-13: testing and reporting Windows failures
alone does not complete the requested work. Repair confirmed defects and
continue through actual native verification of the resulting implementation.
The measured startup, installer, hook and filesystem failures remain preserved
as failed evidence until the affected behavior is reverified.

Derived acceptance covers real protected WBS operations, canonical source
authorization, current native CLI package layouts, clean installation and
updates/reruns, and selected MCP/hook execution through mise Python. Preserve
JSON input, denial output, Unicode paths, bounded execution, prior payloads and
unrelated settings. Native SID/DACL, reparse, hardlink, locking and atomic
publication checks must remain effective. An unsuitable test-machine ACL is
an environment precondition to fix in an isolated test fixture, not permission
to disable guards, substitute UID values or skip native coverage. Record actual
Windows and Linux results independently; unexecuted account or platform checks
remain explicit.
