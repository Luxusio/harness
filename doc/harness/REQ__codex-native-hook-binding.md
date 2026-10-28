# Native Codex hook binding

After publishing the Codex payload, configuration, hook trust and marketplace
registration, Harness must refresh the installed hooks in running local Codex
sessions. An enabled, trusted hook listing establishes configuration, not actual
event delivery: a loaded session can still hold an earlier hook snapshot.

The installer discovers the daemon with `codex app-server daemon version` and
uses its native `config/batchWrite` API with empty edits and
`reloadUserConfig: true`. It does not restart the daemon or change user settings.
The local socket and connected peer must belong to the current user. The native
symlink from the private control directory to the private runtime socket is
supported; connection time, message size and notification count are bounded.

A failed discovery with an existing socket or non-socket entry, malformed daemon metadata,
or failed reload makes installation report failure after payload publication.
Discovery diagnostics name `codex app-server daemon version`, its exit code,
and stderr (stdout when stderr is empty, or an explicit no-output indication).
The installer distinguishes an absent endpoint, a dangling control symlink,
a non-socket entry, and a socket entry whose discovery failed; a socket entry
does not prove the daemon is reachable. Only `FileNotFoundError` when inspecting
the control path itself, or when following an already identified symlink whose
target is missing, permits deferred activation. Permission and other inspection
errors remain failures and retain the discovery diagnostics. A dangling link
is preserved without deleting it or starting a daemon. This is a point-in-time
observation, not proof that every Codex session is stopped.
When the endpoint or its symlink target is absent, installation succeeds with
an English next-session notice; it does not claim loaded hooks were refreshed.
Discovery diagnostics remain available in verbose output. This derived repair
addresses the reported installer failure without weakening live reload checks.
Installing to a
config path different from the runtime's `CODEX_HOME/config.toml` (default
`~/.codex/config.toml`) reports that a new session must use that config and never
refreshes an unrelated daemon. An explicit path matching the runtime home does
reload that daemon.
Publication is retained after a reload failure; rollback is not automatic.
On a discovery or reload failure, the recovery instructions first tell the user
to repair the Codex daemon installation and, when applicable, container mounts
in the same shell/runtime. They then require a successful
`codex app-server daemon version` before retrying
`python3 install.py --codex-only --force`. A missing executable reported by
Codex is a runtime installation problem; reinstalling Harness alone does not
repair it. The installer does not automatically start, restart, install or
delete daemon resources or claim a particular container mount caused the error.
Retain the original `CODEX_HOME` and any explicit `--config-path`; the installer
includes the shell-quoted configuration path in its retry command. This complete
recovery instruction replaces the generic force-retry footer for these failures.
The ordinary synchronized-payload fast path does not repeat installation or
repair configuration and must not be used as evidence that a failed reload
has recovered.

Successful native WBS open/context completion binds the exact hook session to
the returned open task generation. Validate native identity, task/run and
registered workspace under the existing conflict fences. Outer JavaScript,
printed results and compatibility markers do not substitute for native identity.
There is no watcher registration or lifecycle collection requirement. See
[receipt-free execution](REQ__receipt-free-execution-and-completion.md).

Native session authentication remains bounded and validates ownership, link,
descriptor/path identity, native cwd and ancestry. These checks authorize source
ownership, not machine-attested review or QA.

Verification covers installed routing and a real native task-result event,
distinguishing missing event delivery from rejected binding. The next task in a
running session replaces the previous closed binding without a daemon restart.
Actual independent review PASS before actual QA and measured WBS checks remain
separately required.
