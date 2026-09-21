"""A stale bytecode cache must not be able to disable the receipt subsystem.

Mechanism under test, verified against the live tree on 2026-09-16: CPython's
default timestamp invalidation compares only a `.pyc` header's mtime and size
against its `.py`. A cache whose header matches but whose bytecode does not is
therefore accepted indefinitely, the receipt-adapter guard rejects the resulting
module, `background_hook` dies during import, and every hook afterwards exits 0
having written nothing.

See `doc/harness/REQ__bytecode-cache-cannot-disable-receipts.md`.
"""
from __future__ import annotations

import importlib.util
import json
import marshal
import os
import re
import stat
import struct
import subprocess
import sys
import tomllib
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"
HOOKS_JSON = REPO / "plugin" / "hooks" / "hooks.json"

sys.path.insert(0, str(SCRIPTS))


def _load_install():
    """Import `install.py` by path; it is not an importable package member."""
    name = "harness_install_for_bytecode_test"
    spec = importlib.util.spec_from_file_location(name, REPO / "install.py")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `install.py` defines dataclasses, and
    # `dataclasses` resolves annotations through `sys.modules[cls.__module__]`.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _poison_cache(py_path: Path) -> Path:
    """Write a `.pyc` whose header matches `py_path` but whose code does not.

    This is the exact on-disk shape observed in the live tree: same mtime, same
    size, different bytecode. Built by compiling different source and then
    stamping the real file's mtime and size into the header, which is all
    timestamp invalidation ever checks.
    """
    cache_dir = py_path.parent / "__pycache__"
    cache_dir.mkdir(exist_ok=True)
    tag = sys.implementation.cache_tag
    pyc = cache_dir / f"{py_path.stem}.{tag}.pyc"

    divergent = compile(
        py_path.read_text(encoding="utf-8") + "\n_DIVERGENT_MARKER = True\n",
        str(py_path),
        "exec",
    )
    stat = py_path.stat()
    pyc.write_bytes(
        importlib.util.MAGIC_NUMBER
        + struct.pack("<I", 0)  # timestamp-based invalidation
        + struct.pack("<II", int(stat.st_mtime) & 0xFFFFFFFF, stat.st_size & 0xFFFFFFFF)
        + marshal.dumps(divergent)
    )
    return pyc


def test_poisoned_cache_header_passes_timestamp_invalidation(tmp_path):
    """The premise: Python accepts this cache, so nothing self-corrects.

    Poisons a copy, never `plugin/scripts` itself. Poisoning the real tree would
    disable this session's own receipt subsystem for as long as the cache sat
    there, and an interrupted run would leave it behind — reproducing the outage
    under test instead of testing it.
    """
    module = tmp_path / "subagent_lifecycle.py"
    module.write_bytes((SCRIPTS / "subagent_lifecycle.py").read_bytes())

    pyc = _poison_cache(module)
    data = pyc.read_bytes()
    header_mtime, header_size = struct.unpack("<II", data[8:16])
    stat = module.stat()

    assert header_mtime == int(stat.st_mtime) & 0xFFFFFFFF
    assert header_size == stat.st_size & 0xFFFFFFFF
    # Header valid, bytecode divergent — the combination timestamp
    # invalidation cannot detect, and the reason this whole file exists.
    assert marshal.loads(data[16:]) != compile(
        module.read_bytes(), str(module), "exec"
    )


def test_guard_names_the_stale_cache_instead_of_accusing_tamper():
    """AC2: the benign shape raises the named error, not a bare refusal."""
    import _lib

    assert issubclass(_lib.StaleBytecodeCacheError, PermissionError)

    message = _lib.stale_bytecode_message("/tree/scripts/subagent_lifecycle.py")
    assert "stale bytecode cache" in message
    # A reader must get the remedy, not just the diagnosis. The month-long
    # recurrence was diagnosed three different ways precisely because the
    # message named neither.
    assert "/tree/scripts/__pycache__" in message
    assert "safe" in message


def test_guard_still_refuses_when_a_filesystem_predicate_fails(tmp_path):
    """AC2 negative case: the split must not widen what is allowed to bind.

    A world-writable module is a tamper shape, not a cache shape, and must keep
    raising the original refusal rather than the recoverable one.

    An earlier version of this test passed a locally defined function, which the
    identity check rejects long before `structural`/`code_matches` are reached,
    so it would have passed even if `structural` were deleted entirely. This one
    drives the real registered adapter in a copied tree and breaks exactly one
    filesystem predicate, so it fails if the tamper half stops being enforced.
    """
    tree = tmp_path / "scripts"
    tree.mkdir()
    for name in ("_lib.py", "subagent_lifecycle.py"):
        (tree / name).write_bytes((SCRIPTS / name).read_bytes())

    # `mode & 0o022` — a group/other-writable module is a tamper shape, and the
    # guard must keep refusing it with the original message.
    (tree / "subagent_lifecycle.py").chmod(0o666)

    probe = tmp_path / "probe.py"
    probe.write_text(
        textwrap.dedent(
            """
            import sys
            sys.path.insert(0, sys.argv[1])
            try:
                import subagent_lifecycle  # noqa: F401
            except BaseException as exc:
                print(type(exc).__name__)
                print(str(exc))
            else:
                print("BOUND")
                print("")
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-B", str(probe), str(tree)],
        capture_output=True, text=True, timeout=60,
    )
    kind, _, message = result.stdout.partition("\n")
    kind = kind.strip()

    assert kind != "BOUND", "a world-writable module must never bind"
    assert kind != "StaleBytecodeCacheError", (
        "a filesystem-predicate failure is a tamper shape and must not be "
        f"reported as a recoverable cache problem: {message!r}"
    )
    assert "canonical module import" in message, message


def test_tamper_wins_over_stale_when_both_are_true(tmp_path):
    """A module that is BOTH world-writable AND backed by a poisoned cache must
    raise the tamper refusal, not the recoverable one.

    `if structural and not code_matches` is what enforces that precedence.
    Mutating it to `if not code_matches` survived the rest of the suite, because
    the other negative case uses a world-writable module whose code still
    matches a fresh compile and so never reaches the branch. Under that mutation
    nothing binds either — but the hook would classify a tamper shape as
    recoverable and prune, which is the wrong response to a modified module.
    """
    tree = tmp_path / "scripts"
    tree.mkdir()
    for name in ("_lib.py", "subagent_lifecycle.py"):
        (tree / name).write_bytes((SCRIPTS / name).read_bytes())

    _poison_cache(tree / "subagent_lifecycle.py")
    (tree / "subagent_lifecycle.py").chmod(0o666)

    probe = tmp_path / "probe.py"
    probe.write_text(
        textwrap.dedent(
            """
            import sys
            sys.path.insert(0, sys.argv[1])
            try:
                import subagent_lifecycle  # noqa: F401
            except BaseException as exc:
                print(type(exc).__name__)
            else:
                print("BOUND")
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(probe), str(tree)],
        capture_output=True, text=True, timeout=60,
    )
    kind = result.stdout.strip().splitlines()[0].strip()

    assert kind != "BOUND", result.stdout
    assert kind != "StaleBytecodeCacheError", (
        "a world-writable module is a tamper shape even when its cache is also "
        f"stale; got {kind}"
    )


def test_hook_detects_the_stale_shape_by_name_and_message():
    """AC3: detection cannot import `_lib`, because `_lib` may be the casualty."""
    import background_hook

    class StaleBytecodeCacheError(PermissionError):
        """Same name, unrelated class — matching is by name, not identity."""

    assert background_hook._is_stale_bytecode_failure(StaleBytecodeCacheError("x"))
    assert background_hook._is_stale_bytecode_failure(
        RuntimeError("stale bytecode cache for /x/y.py")
    )
    # A stale `_lib` can fail to define the class it now references.
    assert background_hook._is_stale_bytecode_failure(
        NameError("name 'StaleBytecodeCacheError' is not defined")
    )
    assert not background_hook._is_stale_bytecode_failure(
        PermissionError("receipt adapter binding requires its canonical module import")
    )


def test_prune_refuses_a_symlinked_cache(tmp_path, monkeypatch):
    """AC3 bound: nothing outside the script's own tree may be reached."""
    import background_hook

    tree = tmp_path / "scripts"
    tree.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    (tree / "__pycache__").symlink_to(outside, target_is_directory=True)

    fake = tree / "background_hook.py"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(background_hook, "__file__", str(fake))

    result = background_hook._prune_sibling_bytecode_cache()

    assert "no bytecode cache to prune" in result
    assert (outside / "keep.txt").exists(), "a symlinked cache must not be followed"


def test_hooks_never_write_bytecode_into_an_installed_tree():
    """AC1: prevention, so no cache can be created to go stale later."""
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    events = config.get("hooks", config)

    commands = [
        hook.get("command", "")
        for matchers in events.values()
        if isinstance(matchers, list)
        for matcher in matchers
        if isinstance(matcher, dict)
        for hook in matcher.get("hooks", [])
    ]
    python_commands = [c for c in commands if "python3" in c]
    assert python_commands, "no python hook commands found — the check would be vacuous"

    for command in python_commands:
        assert command.startswith("PYTHONDONTWRITEBYTECODE=1 "), command
        # C-12 stays intact: this change must not cost the harness fail-safety.
        assert command.rstrip().endswith("|| true"), command


def test_every_process_that_imports_the_installed_tree_disables_bytecode():
    """`plugin/hooks/hooks.json` is not the whole surface, and checking only it
    was the first version's real defect.

    Importer sets overlap, so no single launcher explains the six caches found
    poisoned on 2026-09-16: `_lib` is imported by hook commands, by the MCP
    server, and by most `scripts/*.py` entrypoints. What `hooks.json` alone
    cannot cover is the MCP server and the Codex hook commands, which
    `install.py` generates rather than reading from that file.
    """
    install = _load_install()

    config = install._codex_hooks_config(Path("/installed/plugin"))
    # Every generated Codex hook command, not merely one of them.
    commands = [
        hook["command"]
        for matchers in config["hooks"].values()
        for matcher in matchers
        for hook in matcher["hooks"]
    ]
    assert len(commands) == 4, commands
    for command in commands:
        assert command.startswith("PYTHONDONTWRITEBYTECODE=1 "), command

    codex_mcp = install._codex_mcp_config(Path("/installed/plugin"))
    env = codex_mcp["mcpServers"]["harness"]["env"]
    assert env.get("PYTHONDONTWRITEBYTECODE") == "1", env

    # The Claude MCP registration builds its env as `claude mcp add -e ...`
    # arguments rather than a dict, so assert against the source of that list.
    source = (REPO / "install.py").read_text(encoding="utf-8")
    env_args_block = source.split("env_args = [", 1)[1].split("]", 1)[0]
    assert "PYTHONDONTWRITEBYTECODE=1" in env_args_block, env_args_block

    # The TOML block is the registration Codex actually launches — missing it
    # while the JSON sibling had it is exactly how the first attempt at this
    # test passed while the live Codex MCP process kept writing bytecode.
    block = tomllib.loads(install._mcp_block("/installed/plugin"))
    assert block["mcp_servers"]["harness"]["env"]["PYTHONDONTWRITEBYTECODE"] == "1"

    # The plugin's own MCP manifest. This is the launcher for the
    # marketplace-cached Claude plugin, i.e. the tree under
    # ~/.claude/plugins/cache/harness/, and it is registered by the plugin
    # payload rather than by install.py — so every install.py-scoped assertion
    # above is blind to it. Its `__pycache__` was populated in the field.
    manifest = json.loads((REPO / "plugin" / ".mcp.json").read_text(encoding="utf-8"))
    assert manifest["mcpServers"]["harness"]["env"]["PYTHONDONTWRITEBYTECODE"] == "1"

    # And the documented mirror, so the example cannot drift from the writer.
    example = (REPO / "plugin-codex" / "config.toml.example").read_text(
        encoding="utf-8"
    )
    assert tomllib.loads(example)["mcp_servers"]["harness"]["env"][
        "PYTHONDONTWRITEBYTECODE"
    ] == "1"


def test_documented_script_invocations_also_disable_bytecode():
    """Skills and agents run `scripts/*.py` from the installed tree by Bash.

    Those invocations import `_lib` — the single most widely imported module in
    the tree, and one of the six found poisoned. Covering only hook commands and
    MCP registrations left this writer class able to recreate the exact cache
    the REQ is about.
    """
    roots = [REPO / "plugin", REPO / "plugin-codex",
             REPO / "doc" / "harness" / "patterns"]
    extra = [REPO / "README.md", REPO / "README.codex.md"]
    # Any plugin-root variable spelling, braced or not, quoted or not, and any
    # script name including the extensionless `review-read`/`review-log`. The
    # first version of this test matched only `${CLAUDE|HARNESS_PLUGIN_ROOT}`
    # with a `.py` suffix, so it could not see nine real invocations that used
    # `${_PLUGIN_ROOT}` or an unbraced `$HARNESS_PLUGIN_ROOT` — the class it
    # exists to prevent walked straight past it.
    pattern = re.compile(
        r"(?P<prefix>\S*\s*)python3 \"?\$\{?[A-Za-z_]*PLUGIN_ROOT\}?/scripts/"
    )

    unprefixed = []
    seen = 0
    for root in roots:
        for path in list(root.rglob("*.md")) + (extra if root == roots[0] else []):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                for match in pattern.finditer(line):
                    seen += 1
                    if "PYTHONDONTWRITEBYTECODE=1 " not in match.group("prefix"):
                        unprefixed.append(f"{path.relative_to(REPO)}: {line.strip()}")

    assert seen, "no documented script invocations found — the check would be vacuous"
    assert not unprefixed, unprefixed


def test_any_import_failure_prunes_not_only_the_named_shape(tmp_path):
    """A poisoned `_lib` raises `ImportError: cannot import name ...`, which
    never reaches the guard that raises `StaleBytecodeCacheError`. Gating the
    prune on that name left the commonest module in the tree uncovered.
    """
    import background_hook

    tree = tmp_path / "scripts"
    tree.mkdir()
    cache = tree / "__pycache__"
    cache.mkdir()
    (cache / "junk.pyc").write_bytes(b"x")

    fake = tree / "background_hook.py"
    fake.write_text("", encoding="utf-8")

    repo = tmp_path / "repo"
    (repo / "doc" / "harness").mkdir(parents=True)

    original = background_hook.__file__
    background_hook.__file__ = str(fake)
    try:
        background_hook._report_import_failure(
            ImportError("cannot import name 'find_harness_root' from '_lib'")
        )
    finally:
        background_hook.__file__ = original

    assert not cache.exists(), "an unnamed import failure must still prune"


def test_the_marker_is_ignored_by_setup_not_only_by_this_repo():
    """A tracked marker would reappear on every checkout and pin the signal
    false repo-wide, so setup must ignore it wherever harness is installed.
    """
    sys.path.insert(0, str(SCRIPTS))
    import setup_finalize

    assert "doc/harness/.receipt-capability-broken" in setup_finalize.OPERATIONAL_IGNORES


def test_marker_write_does_not_hang_on_a_planted_fifo(tmp_path):
    """C-12: a hook must fail safe, and `O_NOFOLLOW` does not reject a FIFO.

    The marker name is fixed, gitignored, and predictable, so a local actor can
    plant a FIFO there. Without `O_NONBLOCK` the open blocks until a reader
    appears and the hook stalls until its 3s timeout kills it — losing the
    breadcrumb and the marker. `_lib.read_json_diagnostics` already carries this
    guard on the read side; this is its write-side twin.
    """
    import background_hook

    repo = tmp_path / "repo"
    (repo / "doc" / "harness").mkdir(parents=True)
    os.mkfifo(repo / "doc" / "harness" / ".receipt-capability-broken")

    # The assertion is that this returns at all. A regression hangs here, so the
    # test is run in a subprocess under a hard timeout rather than inline.
    probe = tmp_path / "probe.py"
    probe.write_text(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(SCRIPTS)!r})
            import background_hook
            background_hook._write_capability_marker(
                {str(repo)!r}, PermissionError("stale bytecode cache for /x.py"), "pruned"
            )
            print("RETURNED")
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-B", str(probe)],
        capture_output=True, text=True, timeout=20,
    )

    assert "RETURNED" in result.stdout, (result.stdout, result.stderr)


def test_marker_write_refuses_a_fifo_that_has_a_reader(tmp_path):
    """The reader-present FIFO is the case that exercises the `S_ISREG` gate.

    With no reader, `O_NONBLOCK` makes the open fail at `ENXIO` and the `fstat`
    is never reached — so the sibling test above pins only half the protection,
    and deleting the gate leaves it green. With a reader holding the read end
    the open succeeds, and only `S_ISREG` stops the marker JSON from being
    written into the attacker's pipe.
    """
    import background_hook

    repo = tmp_path / "repo"
    (repo / "doc" / "harness").mkdir(parents=True)
    fifo = repo / "doc" / "harness" / ".receipt-capability-broken"
    os.mkfifo(fifo)

    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        background_hook._write_capability_marker(
            str(repo), PermissionError("stale bytecode cache for /x.py"), "pruned"
        )
        try:
            leaked = os.read(reader, 65536)
        except BlockingIOError:
            leaked = b""
    finally:
        os.close(reader)

    assert leaked == b"", f"marker content reached the pipe: {leaked[:120]!r}"
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode), "the FIFO must be left intact"


def test_marker_read_does_not_hang_on_a_planted_fifo(tmp_path, monkeypatch):
    """The MCP server has no timeout around this read, so a FIFO at the marker
    name would hang the control plane rather than one hook.
    """
    sys.path.insert(0, str(REPO / "plugin" / "mcp"))
    import harness_server

    repo = tmp_path / "repo"
    task_dir = repo / "doc" / "harness" / "tasks" / "TASK__x"
    task_dir.mkdir(parents=True)
    (repo / "doc" / "harness" / "manifest.yaml").write_text("p: x\n", encoding="utf-8")
    os.mkfifo(repo / "doc" / "harness" / ".receipt-capability-broken")

    monkeypatch.setattr(harness_server, "_server_runtime", lambda: "claude")

    # A FIFO is not a regular file, so the lstat/S_ISREG gate should reject it
    # before any read is attempted.
    assert harness_server._hook_capability_marker_file(str(task_dir)) == ""
    # And the reason path must still return promptly with its fallback.
    reason = harness_server._hook_capability_marker_reason(str(task_dir))
    assert reason and "import" in reason.lower()


def test_marker_write_refuses_a_symlinked_parent_directory(tmp_path):
    """`O_NOFOLLOW` guards only the final component. A symlinked `doc/harness`
    — which survives checkout, since Git tracks symlinks — would otherwise
    redirect the write outside the repo.
    """
    import background_hook

    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    # `doc` is the symlink, not `doc/harness`. That distinction is the whole
    # test: `os.makedirs(.../doc/harness, exist_ok=True)` where `doc/harness`
    # itself is a symlink always raises FileExistsError — `exist_ok` checks
    # `isdir`, which is False for a dangling link — so it can never leak. With
    # `doc` linked, makedirs happily creates `outside/harness`. Two earlier
    # versions of this test used the non-leaking shape and stayed green under
    # the mutation that moves makedirs back above the confinement check.
    (repo / "doc").symlink_to(outside, target_is_directory=True)

    background_hook._write_capability_marker(
        str(repo), PermissionError("stale bytecode cache for /x.py"), "pruned"
    )

    assert not (outside / "harness").exists(), (
        "nothing may be created at the link target — the confinement check must "
        "run before os.makedirs"
    )


def test_marker_write_refuses_to_follow_a_symlink(tmp_path):
    """The marker writer runs on every hook import failure — a condition the
    live tree hit 23 times in ten days. Plain `open(..., "w")` would follow a
    symlink planted at that fixed name and truncate the target, so an actor who
    cannot touch RECEIPTS.jsonl could destroy it through this path.
    """
    import background_hook

    repo = tmp_path / "repo"
    (repo / "doc" / "harness").mkdir(parents=True)
    victim = tmp_path / "victim.jsonl"
    victim.write_text("precious receipt data\n", encoding="utf-8")

    marker = repo / "doc" / "harness" / ".receipt-capability-broken"
    marker.symlink_to(victim)

    background_hook._write_capability_marker(
        str(repo), PermissionError("stale bytecode cache for /x/y.py"), "pruned"
    )

    assert victim.read_text(encoding="utf-8") == "precious receipt data\n"


def test_a_symlinked_marker_cannot_pin_the_capability_signal(tmp_path, monkeypatch):
    """`isfile` follows symlinks; a link to any existing file would otherwise
    masquerade as a marker and hold `receipts_recordable` false forever.
    """
    sys.path.insert(0, str(REPO / "plugin" / "mcp"))
    import harness_server

    repo = tmp_path / "repo"
    (repo / "doc" / "harness").mkdir(parents=True)
    (tmp_path / "unrelated.json").write_text("{}", encoding="utf-8")
    (repo / "doc" / "harness" / ".receipt-capability-broken").symlink_to(
        tmp_path / "unrelated.json"
    )

    monkeypatch.setattr(harness_server, "_server_runtime", lambda: "claude")
    monkeypatch.setattr(harness_server, "find_harness_root", lambda _="": str(repo))
    monkeypatch.setattr(harness_server, "find_repo_root", lambda _="": str(repo))

    assert harness_server._hook_capability_marker_file() == ""
    assert harness_server._hook_capability_marker() is False


def _watcher_status_with_marker(tmp_path, monkeypatch, *, present: bool, receipts: bool):
    """Drive the real `_watcher_status` against a marker in an isolated repo."""
    sys.path.insert(0, str(REPO / "plugin" / "mcp"))
    import harness_server

    repo = tmp_path / "repo"
    task_dir = repo / "doc" / "harness" / "tasks" / "TASK__x"
    task_dir.mkdir(parents=True)
    (repo / "doc" / "harness" / "manifest.yaml").write_text("p: x\n", encoding="utf-8")
    if present:
        (repo / "doc" / "harness" / ".receipt-capability-broken").write_text(
            json.dumps({"error": "StaleBytecodeCacheError: stale bytecode cache for /x.py",
                        "remedy": "pruned bytecode cache"}),
            encoding="utf-8",
        )

    monkeypatch.setattr(harness_server, "_server_runtime", lambda: "claude")
    monkeypatch.setattr(harness_server, "_diagnostics_for_this_session", lambda: {})
    monkeypatch.setattr(harness_server, "receipt_capability_warning", lambda: "")
    monkeypatch.setattr(harness_server, "_run_has_receipts", lambda *a, **k: receipts)
    monkeypatch.setattr(harness_server, "_SERVER", None)

    return harness_server._watcher_status(task_dir=str(task_dir), run_id="run-1")


def test_marker_makes_receipts_recordable_false(tmp_path, monkeypatch):
    """AC4: with the marker present the signal is a definite False, with a reason."""
    status = _watcher_status_with_marker(
        tmp_path, monkeypatch, present=True, receipts=False
    )

    assert status["receipts_recordable"] is False
    assert status["receipts_unrecordable_reason"]
    assert "import" in status["receipts_unrecordable_reason"].lower()


def test_no_marker_leaves_receipts_recordable_true(tmp_path, monkeypatch):
    """AC4: the signal must not be stuck false once the marker is retired."""
    status = _watcher_status_with_marker(
        tmp_path, monkeypatch, present=False, receipts=False
    )

    assert status["receipts_recordable"] is True
    assert status["receipts_unrecordable_reason"] == ""


def test_earlier_receipts_do_not_mask_a_later_marker(tmp_path, monkeypatch):
    """AC4 ordering: the marker is evaluated above the `_run_has_receipts` override.

    A receipt written earlier in the run cannot disprove a break that happened
    after it, which is the shape a mid-run cache poisoning takes.
    """
    status = _watcher_status_with_marker(
        tmp_path, monkeypatch, present=True, receipts=True
    )

    assert status["receipts_recordable"] is False


def test_marker_is_inert_on_codex(tmp_path, monkeypatch):
    """Only the Claude hook tree writes and clears this marker.

    On Codex the recording path is the watcher, and no Codex-side writer could
    ever retire a marker left by an earlier Claude failure — it would pin
    `receipts_recordable: false` permanently. Every other marker test patches
    `_server_runtime` to `"claude"`, so without this one the early return is
    unpinned and a regression is a durable self-DoS of the Codex signal.
    """
    sys.path.insert(0, str(REPO / "plugin" / "mcp"))
    import harness_server

    repo = tmp_path / "repo"
    task_dir = repo / "doc" / "harness" / "tasks" / "TASK__x"
    task_dir.mkdir(parents=True)
    (repo / "doc" / "harness" / "manifest.yaml").write_text("p: x\n", encoding="utf-8")
    (repo / "doc" / "harness" / ".receipt-capability-broken").write_text(
        json.dumps({"error": "x", "remedy": "y"}), encoding="utf-8"
    )

    monkeypatch.setattr(harness_server, "find_harness_root", lambda _="": str(repo))
    monkeypatch.setattr(harness_server, "find_repo_root", lambda _="": str(repo))

    monkeypatch.setattr(harness_server, "_server_runtime", lambda: "claude")
    assert harness_server._hook_capability_marker_file(str(task_dir)) != "", (
        "the fixture must be a marker the Claude path would see, or this test "
        "would pass for the wrong reason"
    )

    monkeypatch.setattr(harness_server, "_server_runtime", lambda: "codex")
    assert harness_server._hook_capability_marker_file(str(task_dir)) == ""
    assert harness_server._hook_capability_marker(str(task_dir)) is False


def test_marker_is_scoped_to_the_task_dir_not_the_server_cwd(tmp_path, monkeypatch):
    """A marker in an unrelated repo must not be visible to this task.

    Resolving from the server's cwd made any local marker global, which turned
    three unrelated `_watcher_status` tests red on a developer machine.
    """
    sys.path.insert(0, str(REPO / "plugin" / "mcp"))
    import harness_server

    other = tmp_path / "other"
    (other / "doc" / "harness").mkdir(parents=True)
    (other / "doc" / "harness" / "manifest.yaml").write_text("p: y\n", encoding="utf-8")
    (other / "doc" / "harness" / ".receipt-capability-broken").write_text(
        "{}", encoding="utf-8"
    )

    monkeypatch.setattr(harness_server, "_server_runtime", lambda: "claude")
    monkeypatch.chdir(other)

    mine = tmp_path / "mine"
    (mine / "doc" / "harness" / "tasks" / "TASK__y").mkdir(parents=True)
    (mine / "doc" / "harness" / "manifest.yaml").write_text("p: z\n", encoding="utf-8")

    assert harness_server._hook_capability_marker(
        str(mine / "doc" / "harness" / "tasks" / "TASK__y")
    ) is False


def test_hook_writes_then_clears_the_capability_marker(tmp_path):
    """AC4: `receipts_recordable` is driven by observation, not inference.

    Runs the real hook as a subprocess against a poisoned tree, which is the
    only way to exercise the import-time failure path the live outage took.
    """
    tree = tmp_path / "plugin" / "scripts"
    tree.mkdir(parents=True)
    repo = tmp_path
    (repo / "doc" / "harness").mkdir(parents=True)
    # `is_harness_enabled_repo` gates the healthy path on setup having run;
    # without the manifest `main()` returns before it can clear the marker.
    (repo / "doc" / "harness" / "manifest.yaml").write_text(
        "project_name: marker-test\n", encoding="utf-8"
    )

    for name in ("background_hook.py", "_lib.py", "subagent_lifecycle.py"):
        (tree / name).write_bytes((SCRIPTS / name).read_bytes())

    marker = repo / "doc" / "harness" / ".receipt-capability-broken"
    payload = json.dumps({"cwd": str(repo), "agent_type": "harness:code-reviewer"})

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(tree / "background_hook.py"), "--event", "stop"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(repo),
        )

    pyc = _poison_cache(tree / "subagent_lifecycle.py")
    assert pyc.exists()

    first = run()
    assert first.returncode == 0, first.stderr
    assert marker.exists(), "a failing hook must record that receipts are impossible"
    recorded = json.loads(marker.read_text(encoding="utf-8"))
    assert recorded["error"].startswith("StaleBytecodeCacheError"), recorded
    assert "pruned bytecode cache" in recorded["remedy"]
    # Mode 0600. Its `error`/`remedy` text reaches the orchestrator (bounded by
    # `_safe_reason`), so a second local account must not be able to choose it.
    assert marker.stat().st_mode & 0o077 == 0, oct(marker.stat().st_mode)
    assert not pyc.exists(), "the poisoned cache must be gone after the first run"

    second = run()
    assert second.returncode == 0, second.stderr
    assert not marker.exists(), "a healthy import must clear the marker"
