---
tags: [harness, install, guards, permissions, diagnostics]
summary: 설치된 런타임 트리의 권한 모드는 설치기의 책임이다. 가드가 group/other-writable 모듈을 거부하는 것은 정상이며, 완화 대상이 아니라 설치기가 그런 트리를 만들지 않아야 한다. 그리고 거부 메시지는 실제로 거부한 조건을 말해야 한다.
updated: 2026-09-17
freshness: current
invalidated_by_paths:
  - install.py
  - plugin/scripts/install_smoke.py
  - plugin/scripts/_lib.py
  - tests/test_install_writable_payload.py
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
  the `--if-stale` skip path.
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
  normalization makes the same copy pass; and both runtimes' `--if-stale` entry
  points are driven so that deleting a call site fails a test rather than only
  contradicting a comment.

## Known limitation — comparison still refuses a writable *source*

`_tree_inventory` refuses any file outside `{0600, 0644, 0755}` and any
group/other-writable directory or path component, on the source side as well as
the installed side. On a world-writable checkout, `install.py --if-stale`
therefore reports `payload comparison failed: unsafe payload path component`
before it reaches any of the above. That is a separate defect with its own
security weight — the refusal exists so an attacker-writable tree is never
compared or trusted — and relaxing it for source checkouts is a decision this
note does not make. `--force` is unaffected and is the working path today.
