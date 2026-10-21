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

A failed discovery with an existing control endpoint, malformed daemon metadata,
or failed reload makes installation report failure after payload publication.
Discovery diagnostics name `codex app-server daemon version`, its exit code,
and stderr (stdout when stderr is empty, or an explicit no-output indication).
The installer distinguishes an absent endpoint, a dangling control symlink,
a non-socket entry, and a socket entry whose discovery failed; a socket entry
does not prove the daemon is reachable. Only `FileNotFoundError` when inspecting
the control path itself means absence. Permission and other inspection errors
remain failures and retain the discovery diagnostics. A dangling link remains
a failed refresh, rather than being treated as a daemon-free installation.
When no local endpoint exists, hooks load in the next session and discovery
diagnostics remain visible. Installing to a
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

Successful native task-start/context completion binds the exact hook session to
the returned open task generation. The validation, conflict fencing and
future-only watcher rules remain defined in
[the receipt protocol](patterns/ADR__single-direct-codex-receipt-protocol.md).
Outer JavaScript, printed tool results and compatibility markers are not
substitutes for native identity. Existing unregistered work is never replayed
into receipts.

Derived reliability constraint from the existing receipt protocol: native
session authentication reads bounded metadata using the same 256MiB parent
history limit as coordinator ancestry authentication. A valid long-lived
coordinator between64MiB and256MiB must not lose task binding merely because
the metadata reader used the separate child-transcript limit. Parent histories
above256MiB still refuse, and child completion reads retain their64MiB limit.
Ownership, link, descriptor/path identity, native cwd and ancestry checks remain
required. This does not authorize replay of already completed unregistered work.

Verification must cover installed routing and a real native task-result event,
then fresh required review and QA started/completed receipts. Missing event
delivery must remain distinguishable from rejected task binding; configuration
or unit-test success alone cannot establish working receipt acquisition.
After the source task closes, installation validation must also exercise the
next task in the same running session: its native task result replaces the previous binding
without a daemon restart, and its fresh lens must record lifecycle receipts.
