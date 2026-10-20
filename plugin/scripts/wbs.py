"""Measured WBS projections; PLAN owns intent, explicit checks own evidence.

No read executes commands and no caller supplies percentage or completion.
The MCP owns WBS.json under the existing task transaction. This is not a
replacement for independent review, QA, task close or batch integration proof.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
from functools import cache

STATE = "WBS.json"
STALL_SECONDS = 7200
MAX_NODES = 256
MAX_EVIDENCE_ENTRIES = 4096
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
MAX_LISTING_BYTES = 1024 * 1024
MAX_MODULE_DEPTH = 8


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError("WBS: " + message)


def validate(nodes):
    require(isinstance(nodes, list) and 0 < len(nodes) <= MAX_NODES, "declare 1..256 nodes")
    index = {}
    for raw in nodes:
        require(isinstance(raw, dict), "node must be an object")
        allowed = {"id", "title", "weight", "parent", "depends_on", "check"}
        require(not set(raw) - allowed, "unknown node fields")
        item = dict(raw, parent=raw.get("parent"), depends_on=raw.get("depends_on", []))
        key = item.get("id")
        require(isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", key), "invalid node id")
        require(key not in index, "duplicate node id")
        require(isinstance(item.get("title"), str) and 0 < len(item["title"].strip()) <= 2000, "observable title required")
        require(type(item.get("weight")) in (int, float) and 0 < item["weight"] <= 1000000
                and math.isfinite(item["weight"]), "positive finite weight required")
        require(item["parent"] is None or isinstance(item["parent"], str), "invalid parent")
        deps = item["depends_on"]
        require(isinstance(deps, list) and all(isinstance(d, str) for d in deps)
                and len(set(deps)) == len(deps), "unique dependencies required")
        index[key] = item
    children = {key: [] for key in index}
    for key, item in index.items():
        parent = item["parent"]
        require(parent is None or parent in index and parent != key, "unknown or self parent")
        if parent is not None:
            children[parent].append(key)
        require(set(item["depends_on"]) <= index.keys() and key not in item["depends_on"], "unknown or self dependency")
    # A parent is complete only after its children; combining edges catches
    # dependencies on one's own ancestor as well as ordinary dependency cycles.
    unresolved = {key: set(item["depends_on"]) | set(children[key]) for key, item in index.items()}
    while unresolved:
        ready = {key for key, deps in unresolved.items() if not deps}
        require(ready, "cyclic parent/dependency graph")
        unresolved = {key: deps - ready for key, deps in unresolved.items() if key not in ready}
    inherited = {}
    for key, item in index.items():
        deps, ancestor = set(item["depends_on"]), item["parent"]
        while ancestor:
            deps.update(index[ancestor]["depends_on"])
            ancestor = index[ancestor]["parent"]
        inherited[key] = deps | set(children[key])
    while inherited:
        ready = {key for key, deps in inherited.items() if not deps}
        require(ready, "cyclic inherited dependencies")
        inherited = {key: deps - ready for key, deps in inherited.items() if key not in ready}
    for key, item in index.items():
        if children[key]:
            require(weight_sum(index[c] for c in children[key]) == Decimal(str(item["weight"])), "split must conserve parent weight")
            require(not any(field in item for field in ("check", "task_id", "leaf_id")), "group cannot claim leaf verification")
        else:
            check = item.get("check")
            require(check is None or isinstance(check, list) and 0 < len(check) <= 128
                    and all(isinstance(s, str) and s and "\0" not in s for s in check), "check must be assertion argv when supplied")
    return list(index.values())


def weight_sum(nodes):
    """Conserve decimal weights exactly, including splits of a unit outcome."""
    with localcontext() as context:
        context.prec = 400  # Includes the smallest positive float and all 256 nodes.
        return sum((Decimal(str(n["weight"])) for n in nodes), Decimal(0))


def leaves(nodes):
    parents = {n["parent"] for n in nodes}
    return [n for n in nodes if n["id"] not in parents]


def from_plan(text):
    blocks = re.findall(r"^```wbs\s*\n(.*?)^```\s*$", text, re.M | re.S)
    require(len(blocks) <= 1, "use one WBS fence")
    return validate(json.loads(blocks[0])) if blocks else None


def transition(old, new, completed=()):
    """Only refinement: stable nodes cannot disappear or silently change credit."""
    previous, current = {n["id"]: n for n in old}, {n["id"]: n for n in new}
    require(previous.keys() <= current.keys(), "cannot remove baseline nodes")
    old_leaves = {n["id"] for n in leaves(old)}
    new_leaves = {n["id"] for n in leaves(new)}
    for key, item in previous.items():
        candidate = current[key]
        if key in old_leaves - new_leaves:
            require(key not in completed, "cannot split a verified outcome")
        require(all(item.get(field) == candidate.get(field) for field in ("parent", "weight")),
                "preserve baseline weight and parent; implementation and pending checks may adapt")
    for key in current.keys() - previous.keys():
        ancestor = current[key]["parent"]
        while ancestor is not None and ancestor not in previous:
            ancestor = current[ancestor]["parent"]
        require(ancestor is None or ancestor in old_leaves - new_leaves,
                "new work must be a new outcome or refine unfinished work")


def read_state(task_dir):
    from _lib import _strict_regular_text_snapshot
    snapshot = _strict_regular_text_snapshot(os.path.join(task_dir, STATE), max_size=1024 * 1024)
    if not snapshot["exists"]:
        return None
    state = json.loads(snapshot["text"])
    state = validate_state(state)
    require("batch_goal_id" not in state and all("batch_goal_id" not in row for row in state.get("batch_history", [])),
            'retired WBS binding; use wbs(action="migrate")')
    return state


def validate_state(state):
    """Validate evidence independently from storage-format migration."""
    require(isinstance(state, dict) and state.get("version") == 1, "invalid evidence state")
    if state.get("pack_only") is True:
        require(state.get("nodes") == [] and state.get("checks") == {} and bool(state.get("batch")),
                "invalid unbaselined pack binding")
    else:
        require("pack_only" not in state, "unknown baseline mode")
        validate(state.get("nodes"))
    require(isinstance(state.get("run_id"), str) and isinstance(state.get("checks"), dict), "invalid check evidence")
    for proof in state["checks"].values():
        require(isinstance(proof, dict) and type(proof.get("passed")) is bool, "invalid assertion evidence")
        if "last_attempt" in proof:
            attempt = proof["last_attempt"]
            require(isinstance(attempt, dict) and set(attempt) == {"result", "checked_at", "measurement_error"}
                    and isinstance(attempt["result"], dict)
                    and set(attempt["result"]) == {"returncode", "error", "output"}
                    and (attempt["result"]["returncode"] is None or type(attempt["result"]["returncode"]) is int)
                    and (attempt["result"]["error"] is None or isinstance(attempt["result"]["error"], str))
                    and isinstance(attempt["result"]["output"], str)
                    and len(attempt["result"]["output"].encode()) <= 16000
                    and type(attempt["checked_at"]) in (int, float) and math.isfinite(attempt["checked_at"])
                    and isinstance(attempt["measurement_error"], str) and 0 < len(attempt["measurement_error"]) <= 4000,
                    "invalid inconclusive assertion evidence")
        if proof["passed"]:
            require(all(isinstance(proof.get(k), str) and re.fullmatch(r"[0-9a-f]{64}", proof[k])
                        for k in ("definition", "source")), "invalid assertion binding")
            require(isinstance(proof.get("result"), dict) and proof["result"].get("returncode") == 0
                    and not proof["result"].get("error"), "invalid assertion result")
            require(type(proof.get("checked_at")) in (int, float) and math.isfinite(proof["checked_at"]), "invalid assertion time")
    require(all(type(state.get(k)) in (int, float) and math.isfinite(state[k]) and state[k] >= 0
                for k in ("started_at", "last_progress_at")), "invalid progress clock")
    return state


def encode_state(state):
    """Every writer must fit the same bound as the next owner's read."""
    encoded = json.dumps(state, sort_keys=True) + "\n"
    require(len(encoded.encode()) <= 1024 * 1024, "evidence exceeds storage budget")
    return encoded


def prepare(task_dir, plan, run_id, *, now=None):
    nodes, old = from_plan(plan), read_state(task_dir)
    if nodes is None:
        require(old is None, "cannot remove an existing WBS")
        return None
    stamp = time.time() if now is None else now
    if old:
        from _lib import find_repo_root
        first_baseline = old.get("pack_only") is True
        if old.get("batch") and nodes != old["nodes"]:
            require(batch_route(task_dir, find_repo_root(task_dir), old)["route"] == "integration",
                    "finish the active work pack before refinement")
            old = dict(old, batch_history=old.get("batch_history", []) + [
                dict(batch=old["batch"])])
            require(len(old["batch_history"]) <= 32, "work pack history limit reached")
            old.pop("batch")
            old.pop("pack_only", None)
        projection = task_progress(task_dir, find_repo_root(task_dir), run_id, now=stamp)
        transition(old["nodes"], nodes, [row["id"] for row in projection.get("outcomes", []) if row["verified"]])
        if first_baseline:
            old = dict(old, started_at=stamp, last_progress_at=stamp, scope_revision=1)
        state = dict(old, nodes=nodes, run_id=run_id,
                    scope_revision=old.get("scope_revision", 1) + (not first_baseline and weight_sum(n for n in nodes if n["parent"] is None) != weight_sum(n for n in old["nodes"] if n["parent"] is None)),
                    checks=old["checks"] if old["run_id"] == run_id else {})
    else:
        state = dict(version=1, nodes=nodes, run_id=run_id, checks={}, started_at=stamp, last_progress_at=stamp)
    encode_state(state)
    return state


def fingerprint(repo):
    """Observe checkout contents internally, without prescribing edited files."""
    from _lib import _trusted_git_env
    root, result = Path(repo).resolve(), []
    total = 0
    listing_bytes = 0
    deadline = time.monotonic() + 30

    def remaining():
        seconds = deadline - time.monotonic()
        require(seconds > 0, "checkout evidence exceeds measurement budget (time)")
        return min(10, seconds)

    def git(directory, *args):
        nonlocal listing_bytes
        answer = _execute(["git", *args], directory, remaining(), env=_trusted_git_env(),
                          output_limit=MAX_LISTING_BYTES - listing_bytes, retain=MAX_LISTING_BYTES, binary=True)
        require(answer["returncode"] == 0 and not answer["error"],
                "checkout Git evidence unavailable: " + (answer["error"] or "Git command failed"))
        listing_bytes += len(answer["output"])
        return answer["output"]

    def add(path, value):
        require(len(result) < MAX_EVIDENCE_ENTRIES, "checkout evidence exceeds measurement budget (entries)")
        result.append([path.relative_to(root).as_posix(), value])

    def file(path):
        nonlocal total
        try:
            before = path.lstat()
        except FileNotFoundError:
            add(path, None)
            return
        require(stat.S_ISREG(before.st_mode), "nonregular or symlink scope cannot attest source")
        require(before.st_size <= MAX_EVIDENCE_BYTES - total,
                "checkout evidence exceeds measurement budget (bytes)")
        # No follow and nonblocking also refuse a replacement link/FIFO before read.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            opened = os.fstat(handle.fileno())
            require(identity(before) == identity(opened), "source changed during measurement")
            hashed = hashlib.sha256()
            while chunk := handle.read(min(65536, MAX_EVIDENCE_BYTES - total + 1)):
                remaining()
                total += len(chunk)
                require(total <= MAX_EVIDENCE_BYTES, "checkout evidence exceeds measurement budget (bytes)")
                hashed.update(chunk)
            require(identity(opened) == identity(os.fstat(handle.fileno())) == identity(path.lstat()),
                    "source changed during measurement")
        add(path, digest([before.st_mode, hashed.hexdigest()]))

    def checkout(directory, depth=0):
        require(depth <= MAX_MODULE_DEPTH, "checkout evidence exceeds measurement budget (module depth)")
        top = os.fsdecode(git(directory, "rev-parse", "--show-toplevel")).removesuffix("\n")
        require(Path(top).resolve() == directory, "checkout Git evidence belongs to another repository")
        entries = {}
        for row in git(directory, "ls-files", "--stage", "-z").split(b"\0"):
            if not row:
                continue
            header, name = row.split(b"\t", 1)
            mode, oid, stage = header.split()
            require(stage == b"0", "unmerged source cannot attest checkout")
            entries[os.fsdecode(name)] = (mode, oid)
        for name in git(directory, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
            if name:
                entries.setdefault(os.fsdecode(name), (None, None))
        require(len(entries) <= MAX_EVIDENCE_ENTRIES - len(result),
                "checkout evidence exceeds measurement budget (entries)")
        for scope, (mode, oid) in sorted(entries.items()):
            remaining()
            path = directory / scope
            require(path.resolve().is_relative_to(directory), "scope escapes checkout")
            # Check every ancestor rather than resolving through an in-root alias.
            ancestor = path
            while ancestor != directory:
                require(not ancestor.is_symlink(), "symlink scope cannot attest source")
                ancestor = ancestor.parent
            if mode == b"160000":
                require(not path.exists() or path.is_dir(), "gitlink checkout is not a directory")
                if not path.exists() or next(path.iterdir(), None) is None:
                    add(path, digest(["gitlink", os.fsdecode(oid), None]))
                else:
                    head = git(path, "rev-parse", "--verify", "HEAD").strip().decode("ascii")
                    require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head), "invalid module HEAD")
                    add(path, digest(["gitlink", os.fsdecode(oid), head]))
                    checkout(path, depth + 1)
            else:
                file(path)
    checkout(root)
    require(any(value is not None for _, value in result), "no existing source/test evidence")
    return digest(sorted(result))


def task_progress(task_dir, repo, run_id, *, now=None, _source=None):
    stamp = time.time() if now is None else now
    try:
        state = read_state(task_dir)
        if state is None:
            return dict(status="unbaselined", percentage=None, verified_weight=0, total_weight=None)
        if state.get("pack_only"):
            validate_history(task_dir, repo, state)
            return dict(status="unbaselined", percentage=None, verified_weight=0, total_weight=None,
                        integration=batch_route(task_dir, repo, state))
        plan_nodes = from_plan(Path(task_dir, "PLAN.md").read_text())
        require(plan_nodes == state["nodes"], "plan/evidence definition mismatch")
        rows = []
        source = fingerprint(repo) if _source is None else _source
        for node in leaves(state["nodes"]):
            proof = state["checks"].get(node["id"], {})
            valid = (state["run_id"] == run_id and proof.get("passed") is True
                     and "last_attempt" not in proof and proof.get("definition") == digest(node))
            valid = valid and proof.get("source") == source
            rows.append(dict(id=node["id"], title=node["title"], weight=node["weight"],
                             verified=bool(valid), checked_at=proof.get("checked_at") if valid else None))
        # Downstream outcomes lose current credit when prerequisite evidence
        # goes stale, even if their own files have not changed.
        by_id = {row["id"]: row for row in rows}
        index = {n["id"]: n for n in state["nodes"]}
        @cache
        def current(key):
            item = index[key]
            children = [n["id"] for n in state["nodes"] if n["parent"] == key]
            own = all(current(child) for child in children) if children else by_id[key]["verified"]
            deps, ancestor = set(item["depends_on"]), item["parent"]
            while ancestor:
                deps.update(index[ancestor]["depends_on"])
                ancestor = index[ancestor]["parent"]
            return own and all(current(dep) for dep in deps)
        valid = {row["id"]: current(row["id"]) for row in rows}
        for row in rows:
            row["verified"] = valid[row["id"]]
            if not row["verified"]:
                row["checked_at"] = None
        total = float(weight_sum(rows))
        earned = float(weight_sum(n for n in rows if n["verified"]))
        complete = all(n["verified"] for n in rows)
        validate_history(task_dir, repo, state)
        integration = batch_route(task_dir, repo, state) if state.get("batch") else None
        return dict(status="measured", percentage=100 if complete else min(99.99, round(100 * earned / total, 2)),
                    scope_revision=state.get("scope_revision", 1),
                    verified_weight=earned, total_weight=total, outcomes=rows,
                    last_progress_at=state["last_progress_at"],
                    refinement_due=not complete and stamp - state["last_progress_at"] >= STALL_SECONDS,
                    integration=integration,
                    next_action="Refine only unclear unfinished outcomes; preserve weights. Report real external blockers instead of recursive splitting.")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        return _unavailable(exc)


def _unavailable(reason):
    return dict(status="invalid", percentage=None, verified_weight=None, total_weight=None, reason=str(reason))


def _execute(argv, repo, timeout, *, env=None, output_limit=1024 * 1024, retain=4000, binary=False):
    """Bound time and captured output, terminating the entire assertion group."""
    proc = subprocess.Popen(argv, cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    output, failure = bytearray(), None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    def stop():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                failure = "timeout"
                break
            for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(chunk)
                    if len(output) > output_limit:
                        failure = "output limit exceeded"
                        break
            if failure:
                break
        if failure:
            stop()
        try:
            code = proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            stop()
            code, failure = proc.wait(), "timeout"
        captured = bytes(output[-retain:])
        return dict(returncode=code, error=failure, output=captured if binary else captured.decode(errors="replace"))
    finally:
        selector.close()
        proc.stdout.close()
        # A successful leader can leave redirected children alive. Its whole
        # group loses write ownership before source is fingerprinted again.
        stop()
        proc.wait()


def verify(task_dir, repo, run_id, leaf_id, *, timeout=60, now=None):
    from _lib import _atomic_text_write
    state = read_state(task_dir)
    require(state is not None and state["run_id"] == run_id, "baseline current task run first through wbs(action='plan')")
    if state.get("batch"):
        require(batch_route(task_dir, repo, state)["route"] == "integration", "work pack is unfinished; preserve parked parent")
    require(from_plan(Path(task_dir, "PLAN.md").read_text()) == state["nodes"], "plan/evidence mismatch")
    node = next((n for n in leaves(state["nodes"]) if n["id"] == leaf_id), None)
    require(node is not None, "unknown leaf")
    require(node.get("check"), "choose the outcome assertion through wbs(action='plan') when ready; no implementation layout required")
    observation_errors = (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError)

    def unavailable(reason, phase, result=None):
        return dict(leaf_id=leaf_id, passed=None,
                    execution="not_run" if result is None else "start_failed" if result["returncode"] is None else "completed",
                    **(result or dict(returncode=None, error=None, output="")),
                    measurement_status="unavailable", measurement_phase=phase, measurement_error=str(reason),
                    progress=_unavailable(reason))
    try:
        before = fingerprint(repo)
    except observation_errors as exc:
        return unavailable(exc, "before")
    before_progress = task_progress(task_dir, repo, run_id, now=now, _source=before)
    if before_progress["status"] != "measured":
        return unavailable(before_progress.get("reason", "current measurement unavailable"), "before")
    verified = {row["id"] for row in before_progress["outcomes"] if row["verified"]}
    def complete(key):
        children = [n["id"] for n in state["nodes"] if n["parent"] == key]
        return all(complete(child) for child in children) if children else key in verified
    deps, ancestor = set(node["depends_on"]), node["parent"]
    index = {n["id"]: n for n in state["nodes"]}
    while ancestor:
        deps.update(index[ancestor]["depends_on"])
        ancestor = index[ancestor]["parent"]
    require(all(complete(dep) for dep in deps), "dependencies are not verified")
    try:
        result = _execute(node["check"], repo, timeout)
    except OSError as exc:
        result = dict(returncode=None, error=str(exc), output="")
    stamp = time.time() if now is None else now
    try:
        after_source = fingerprint(repo)
        after_progress = task_progress(task_dir, repo, run_id, now=now, _source=after_source)
        require(after_progress["status"] == "measured", after_progress.get("reason", "current measurement unavailable"))
    except observation_errors as exc:
        # Preserve the prior proof's fields, but prevent it from resurrecting
        # credit after a newer inconclusive attempt, even if measurement heals.
        proof = dict(state["checks"].get(leaf_id, dict(passed=False)))
        proof["last_attempt"] = dict(result=result, checked_at=stamp, measurement_error=str(exc)[:4000])
        state["checks"][leaf_id] = proof
        _atomic_text_write(os.path.join(task_dir, STATE), encode_state(state))
        return unavailable(exc, "after", result)
    success = result["returncode"] == 0 and not result["error"]
    if success:
        success = after_source == before
        if not success:
            result["error"] = "source changed during verification; rerun against stable inputs"
    previous = state["checks"].pop(leaf_id, None)
    state["checks"][leaf_id] = dict(passed=False, result=result, checked_at=stamp)
    if success:
        state["checks"][leaf_id] = dict(previous) if leaf_id in verified else dict(
            passed=True, definition=digest(node), source=before, checked_at=stamp, result=result)
    after = weight_sum(n for n in leaves(state["nodes"]) if n["id"] in (verified - {leaf_id}) | ({leaf_id} if success else set()))
    if after > weight_sum(n for n in before_progress["outcomes"] if n["verified"]):
        state["last_progress_at"] = stamp
    _atomic_text_write(os.path.join(task_dir, STATE), encode_state(state))
    return dict(leaf_id=leaf_id, passed=success, **result,
                execution="start_failed" if result["returncode"] is None else "completed",
                measurement_status="measured", measurement_error=None,
                progress=task_progress(task_dir, repo, run_id, now=now, _source=after_source))


def batch_route(task_dir, repo, state):
    import work_pack
    return work_pack.route(repo, Path(task_dir).name, state["batch"])


def validate_history(task_dir, repo, state):
    for prior in state.get("batch_history", []):
        require(batch_route(task_dir, repo, prior)["route"] == "integration", "previous work pack proof is no longer integrated")


def require_integrated_for_task(repo, task_id):
    from _lib import canonical_task_dir
    td = canonical_task_dir(task_id=task_id, repo_root=repo)
    state = read_state(td) if Path(td).is_dir() else None
    if state:
        validate_history(td, repo, state)
    if state and state.get("batch"):
        require(batch_route(td, repo, state)["route"] == "integration", "work pack unfinished; integrate and close the pool before resuming parent")


def work_pack(task_dir, repo, run_id, *, bind=False, ownership=None):
    """Deterministic request graph for the existing pool, never a spawn/merge."""
    state = read_state(task_dir)
    require(state is not None, "baseline WBS before dispatch")
    if state.get("batch"):
        return batch_route(task_dir, repo, state)
    progress = task_progress(task_dir, repo, run_id)
    require(progress["status"] == "measured", "valid current WBS required")
    done = {row["id"] for row in progress["outcomes"] if row["verified"]}
    pending = [n for n in leaves(state["nodes"]) if n["id"] not in done]
    require(len(pending) >= 2, "use direct development for a single unfinished outcome")
    require(isinstance(ownership, dict) and set(ownership) == {n["id"] for n in pending},
            "declare current execution ownership for the actual worktree handoff, separately from progress WBS")
    prefix = "wbs-" + digest([Path(task_dir).name, state["nodes"]])[:12]
    slug = {n["id"]: prefix + "-" + n["id"] for n in pending}
    def descendants(key):
        children = [n["id"] for n in state["nodes"] if n["parent"] == key]
        return [leaf for child in children for leaf in descendants(child)] if children else [key]
    requests = []
    index = {n["id"]: n for n in state["nodes"]}
    for node in pending:
        deps, ancestor = set(node["depends_on"]), node["parent"]
        while ancestor:
            deps.update(index[ancestor]["depends_on"])
            ancestor = index[ancestor]["parent"]
        requests.append(dict(slug=slug[node["id"]], request=node["title"] + ("\nVerify argv: " + json.dumps(node["check"]) if node.get("check") else "\nChoose outcome assertions during development."),
                             scopes=ownership[node["id"]], depends_on=sorted({slug[d] for dep in deps for d in descendants(dep) if d in slug})))
    # Existing validator owns path overlap/dependency admission and task IDs.
    import batch_state
    batch_state.requests_valid(requests)
    result = dict(batch_id=prefix, requests=requests,
                next_action="Before dispatch park this open parent through wbs(action='block') for the worktree handoff. Run the existing batch workflow with these exact requests and --owner-task-id " + Path(task_dir).name + ". No parent source writes while leads run. Resume parent only after the pool is integrated; recheck outcomes and run combined review/QA.")
    if bind:
        import work_pack
        from _lib import _atomic_text_write
        batch_state.main_boundary(repo)
        spec = work_pack.make_spec(repo, Path(task_dir).name, requests)
        state.update(batch=spec)
        _atomic_text_write(os.path.join(task_dir, STATE), encode_state(state))
        result.update(task_id=spec["task_id"], batch_id=spec["batch_id"])
    return result


def ready_outcomes(state, progress):
    """Ready IDs from the same outcome graph, never a second task queue."""
    if progress.get("status") != "measured":
        return []
    if progress.get("integration") and progress["integration"]["route"] != "integration":
        return []
    done = {row["id"] for row in progress.get("outcomes", []) if row["verified"]}
    nodes = state["nodes"]
    index = {node["id"]: node for node in nodes}
    @cache
    def complete(key):
        children = [n["id"] for n in nodes if n["parent"] == key]
        return all(complete(child) for child in children) if children else key in done
    ready = []
    for node in leaves(nodes):
        if node["id"] in done:
            continue
        deps, ancestor = set(node["depends_on"]), node["parent"]
        while ancestor:
            deps.update(index[ancestor]["depends_on"])
            ancestor = index[ancestor]["parent"]
        if all(complete(dep) for dep in deps):
            ready.append(node["id"])
    return ready


