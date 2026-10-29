---
tags: [harness, install, guards, permissions, diagnostics]
summary: 설치된 런타임 트리의 권한 모드는 설치기의 책임이다. 가드가 group/other-writable 모듈을 거부하는 것은 정상이며, 완화 대상이 아니라 설치기가 그런 트리를 만들지 않아야 한다. 그리고 거부 메시지는 실제로 거부한 조건을 말해야 한다.
updated: 2026-10-20
freshness: current
invalidated_by_paths:
  - install.py
  - plugin/scripts/install_smoke.py
  - tests/test_install_writable_source_comparison.py
  - plugin/scripts/_lib.py
  - tests/test_install_writable_payload.py
freshness_updated: 2026-10-20T07:01:13Z
---

# REQ — installed-tree modes are the installer's responsibility

## Context

Field report, 2026-09-17. `python install.py --force` reported both runtimes as
ERROR on every attempt:

```
FAIL import: an installed hook module does not import …
        background_hook: SystemExit: 0
FAIL receipt: the installed MCP server could not open a task
          File "…/scripts/_lib.py", line 1513, in bind
        PermissionError: task-control writer requires its canonical module import
```

The reported cause — "a stale `__pycache__` is the known cause; `python3
install.py --force` clears it" — was wrong, and `--force` was the loop the user
was already in. The real cause: the checkout was bind-mounted from a Windows
host, so every source file read as `0o777`. `shutil.copytree(..., copy2)`
preserves modes, so the installed runtime tree was world-writable, and both
canonical-import guards in `_lib` refuse exactly that:

| guard | refusal term |
|---|---|
| `bind()` (task-control writer) | `before.st_mode & 0o022` in the refusal conjunction |
| receipt-adapter bind | `not (info.st_mode & 0o022)` inside `structural` |

The second one is why the symptom is the *generic* `PermissionError` and never
`StaleBytecodeCacheError`: mode is a `structural` term, and the stale-bytecode
branch only fires when `structural` holds and `code_matches` does not.

Reproduced against a clean `git archive HEAD` copy: 0644 passes the runtime
smoke, `chmod -R 777` on the same copy fails it identically to the field report,
`chmod -R go-w` restores it.

## Requirement

### Codex cache updates and provenance (2026-10-29)

User-explicit: “그것도 버그 해결해” requests repair of the reported Codex
installation failure while replacing the existing cache on a Windows-backed
shared path. The following cache mechanics are derived implementation
constraints, not additional user requests.

- Give each effective Codex payload a deterministic content-derived version in
  the copied manifest. Preserve the source release manifest. Include shared
  runtime code, projected skills and generated hook/MCP semantics, normalizing
  installation roots so staging paths do not change identity.
- The mirror, marketplace declaration, selected cache and hook trust use that
  same version. Changed content publishes a fresh cache directory and retains
  earlier directories used by running sessions.
- Reuse an independently compared identical cache, including forced installs.
  The installer retains its existing repair of installer-owned writable modes
  before comparison. A remaining path, ownership or content mismatch is an
  error: preserve the cache and identify
  the path; repair it only after stopping its users and moving that exact cache
  out of the selected location. Never overwrite loaded files to obtain PASS.
- Preserve staged publication, ownership checks, installed smoke and explicit
  runtime reload/restart classification. A hash identifies content, not trust.

Verification: `tests/test_install_codex_content_cache.py` covers cache identity,
old-cache preservation, refusal, repeated publication and concurrency;
`tests/test_install_runtime_locations.py` exercises forced reinstall and trust.

1. **The installer owns the modes of every tree it writes.** After any payload
   is synced or a plugin cache entry is installed, group/other-write bits are
   cleared across that tree. A world-writable source checkout must not be able
   to produce an installed runtime the guards refuse.
2. **The guards are not relaxed to accommodate it.** A world-writable module is
   one any local account can swap between the guard's read and the
   interpreter's; the mode check is the property, not the obstacle. Nothing in
   `_lib`'s refusal conjunctions is widened for this.
3. **A refusal names the condition it actually rejected.** The runtime smoke
   reports a cause it has checked. Writable modes are reported when observed,
   with the offending paths and a `chmod -R go-w` remedy; the stale-`__pycache__`
   wording remains the fallback for the case where nothing is writable, stated
   as a known cause rather than an observed one. A diagnosis asserted without
   checking is worse than none: it sends the reader somewhere specific and
   wrong.

## Enforcement

- `install._normalize_payload_modes(root)` — clears `0o022` across an
  installer-owned tree; idempotent, skips symlinks, and a no-op on a clean tree
  so it cannot flip a SYNCHRONIZED payload pair to STALE. Called on the Claude
  install root and the Codex payload/cache roots, on both the post-sync path and
  the default run's SYNCHRONIZED skip path.
  A tree that *was* writable is a different case and normalizing it may well
  report STALE next: `0o777 & ~0o022` is `0o755`, which need not equal the
  source's `0o644`, and `_tree_inventory` records file mode. That is the right
  outcome — such a tree was never SYNCHRONIZED to begin with (the inventory
  refuses any mode outside `{0600, 0644, 0755}`), and the re-sync it triggers
  replaces the payload wholesale. Normalization runs before the staleness
  decision precisely so that either branch ends with a tree the guards accept.
- `install_smoke._world_writable_sources` / `_refusal_cause` — select the failure
  wording from what the tree is.
- `tests/test_install_writable_payload.py` — normalization clears the bits,
  preserves read/execute bits, skips symlinks, no-ops on a clean tree; end to
  end, a `0o777` payload copy makes the smoke name the writable-mode cause and
  normalization makes the same copy pass; and both runtimes' default-run entry
  points are driven so that deleting a call site fails a test rather than only
  contradicting a comment.

## Comparison from a world-writable checkout — resolved without relaxing anything

Originally recorded here as a known limitation: `_tree_inventory` refuses any
file outside `{0600, 0644, 0755}` and any group/other-writable directory or path
component, so on a world-writable checkout the conditional install died with
`expected payload unavailable`, taking out `install_verified.py` — the harness's
own delivery path — while `--force` kept working.

The refusal was never the problem. `_compare_payload_trees` builds the
*expected* side with `copytree(..., copy2)` into a `TemporaryDirectory`, so that
staging tree inherits the source's `0o777` and the installer ends up refusing
its own scratch copy, made seconds earlier, from bytes it is about to install.
Running `_normalize_payload_modes` on that staging tree fixes it and relaxes
nothing: the expected tree is the *canonical projection* of what an install
produces, and since this REQ an install produces cleared `0o022` bits — the
projection was simply missing the installer's own last step. No source mode is
trusted, compared, or admitted to a verdict.

Mode still participates in the verdict, deliberately. `0o777 & ~0o022` is
`0o755`, which is not `0o644`, so a checkout whose modes change between installs
reports STALE and gets re-synced. Both sides derive from the same checkout in
the real flow, so they converge.

## Installer-created ancestors are installer-owned too

`_open_inventory_root` refuses a writable *ancestor* of a payload root as well.
Normalizing only the payload roots left a state that nothing could clear: the
qa-cli lens found that a writable `~/.codex/harness`, `~/.codex/harness/plugins`,
or the marketplace directory made the conditional install fail permanently, the message
named the payload root rather than the ancestor, and the `--force` the output
printed exited 0 without clearing ancestors — so the next conditional install
failed identically.

Normalization therefore starts at the ancestors the harness **exclusively
owns**: `CODEX_INSTALL_ROOT`, and the cache's marketplace directory —
`<codex_home>/plugins/cache/harness/`, which holds harness cache entries and
nothing else. It deliberately stops there.

**The criterion is exclusive ownership, not authorship.** The weaker rule —
"don't chmod what you didn't create" — is false here and following it would
cause the harm this boundary exists to prevent: `install_codex_plugin_cache`
mkdirs the cache entry's parents, so under a permissive umask this installer
may well have created `plugins/` and `cache/` itself. Those directories hold
every other Codex plugin's tree. A maintainer who hits the stuck-loop on a
writable `~/.codex/plugins/cache` and applies the authorship test would conclude
the walk may be widened there, and would chmod trees belonging to plugins the
harness has nothing to do with. Creating a missing parent does not confer
ownership.

Everything outside that boundary — `plugins/` and `cache/` under the Codex home,
and everything above `~/.claude/harness-dev` — gets a named diagnosis instead of
a mutation. The refusal now reports the component it actually rejected with its
mode and uid, not the path that was asked for, on both rejection sites: the
`_open_inventory_root` walk and the absent-target ancestor walk in
`_tree_inventory` that a first install hits.

## A named diagnosis carries its remedy (2026-09-26)

A diagnosis alone still looped. On a host whose `~/.claude` and `~/.codex` were
root:root 0777, every conditional run — and so every `install_verified.py`
delivery — was refused, and the output then printed `--force` as the repair,
which never touches a directory above the payload. The refusal now ends with
`; fix: <command>`, `InstallResult.repair` carries it, and verbose `main()` prints
`repair: <command>, then re-run: python3 install.py --<runtime>-only`
in place of the `--force` line. The nearest-existing-ancestor refusal for a
missing target names its component, mode, uid and remedy the same way.

The remedy mirrors the trust rule and never loosens it. It also never alters a
directory outside your home that is not plainly yours, and it judges location
before ownership, so running as root does not make `/opt` "yours":

| Refused component | Remedy |
|---|---|
| inside your home, owned by you | `chmod go-w <c>` |
| inside your home, root-owned | `sudo chown "$(id -u):$(id -g)" <c> && chmod go-w <c>` |
| inside your home, owned by another uid | no command: its owner must fix it, or choose another install root |
| outside your home, owned by you (not root) | `chmod go-w <c>` |
| outside your home, anything else (`/opt`, `/tmp`, another user's dir) | no command: install under your home (Claude: `HARNESS_DEST`; Codex: `--config-path <dir under your home>/config.toml`) — or, for a directory above your home, have an administrator remove its group/other write |

When the target is **missing**, the refused directory is its nearest existing
ancestor, and the rule only needs a trusted directory to exist below it. Outside
your home that case creates the next directory as you and never touches the
ancestor:

| Nearest existing ancestor of a missing target | Remedy |
|---|---|
| inside your home | same as the table above, for the ancestor |
| outside home, root-owned and sticky (e.g. `/tmp`) | `mkdir -m 0755 <next missing directory>` |
| outside home, root-owned (e.g. `/opt`) | `sudo install -d -o "$(id -u)" -g "$(id -g)" -m 0755 <next missing directory>` |
| outside home, owned by you (not root) | `chmod go-w <ancestor>` |
| outside home, anything else (including another user's sticky dir) | no command: choose another install root |

The reviews of this change caught two unsafe alternatives: judging a missing
target's ancestor like a final component printed
`sudo chown … /tmp && chmod go-w /tmp`, and checking ownership before location
printed `chmod go-w /opt` when the installer ran as root. A third review found two more: a
home of `/` (a container uid with no passwd entry) made every directory "inside
your home" and printed `chown /`, so `/` never counts as a home; and the Codex
advice named `CODEX_HOME`, which install.py does not read, so it now names
`--config-path`.

`+t` is never offered, on purpose (the trust rule still accepts an existing
root-owned sticky ancestor such as `/tmp`). The sticky bit only
stops users renaming or deleting each other's entries; any local user could
still create new names there, and `~/.claude` / `~/.codex` are read at startup
(`CLAUDE.md`, `AGENTS.md`, agent and command directories). Passing the
installer's check while leaving that open would hide the attack the check
exists for. Symlinked or non-directory ancestors still fail with the bare
`OSError` text (`O_NOFOLLOW|O_DIRECTORY` refuses before any `fstat`); that
surface is out of scope here.

Verification: `tests/test_install_unsafe_ancestor_remedy.py`.

## Private projection permissions and concise results (2026-10-20)

User intent: “설치도 또 안되네...”, “그리고 설치 결과도 너무 복잡해 좀 단순화 해
줘”, and “그냥 클러드 성공 실패 코덱스 성공 실패 이렇게 주고 에러메세지 있으면
띄우고 그렇게 하면 안되나?”. Private staging and phase-aware errors are derived
implementation requirements, not permission changes requested by the user.

Copying a read-only source directory can prevent adding generated projection
content; a copied read-only configuration can prevent rewriting it. Prepare only
the copied private staging tree for owner writes before those mutations, keeping
executable intent and removing group/other write bits. Source modes and bytes,
shared parents, symlink targets and foreign ownership remain untouched.

Unexpected errors at wrapped Codex version, payload comparison/sync, cache,
config merge, hook trust and hook reload boundaries preserve phase and earlier
progress. Other unexpected installation errors retain their exception chain and
failing path when supplied by the OS, without a phase/progress guarantee.
Previously `repr(PermissionError)` hid its filename. Activation errors distinguish
failed activation, successful rollback, failed rollback
with a retained backup, and cleanup failure after a new tree is already active.
The original error must survive a subsequent rollback/cleanup error. These cases
are independently reproducible; the user's original inaccessible path was not
present in the supplied output and is not established by those reproductions.

Default CLI output contains one concise success/failure result per selected
runtime, plus an error message for failures. Internal steps, banners and summary
footers are omitted. A dry run says preview rather than success. Optional verbose
output exposes diagnostic steps and recovery details; exit codes still reflect
independent runtime results.

User correction: “영어로 해라”. Installer-owned result labels are English:
`SUCCESS`, `FAILED`, `DRY RUN`, and the `Error:` prefix. Runtime names and the
concise line structure stay the same. Existing OS/runtime diagnostic details
are preserved.
