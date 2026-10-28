"""Measured WBS projections; PLAN owns intent, explicit checks own evidence.

No read executes commands and no caller supplies percentage or completion.
The MCP owns WBS.json under the existing task transaction. This is not a
replacement for independent review, QA, task close or batch integration proof.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from contextlib import contextmanager, ExitStack
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
MAX_SOURCE_BYTE_LIMIT = 64 * 1024 * 1024
MAX_LISTING_BYTES = 1024 * 1024
MAX_MODULE_DEPTH = 8
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


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
        allowed = {"id", "title", "weight", "parent", "depends_on", "check", "inputs"}
        require(not set(raw) - allowed, "unknown node fields")
        item = dict(raw, parent=raw.get("parent"), depends_on=raw.get("depends_on", []))
        if "inputs" in item:
            require(item["inputs"] is not None, "leaf inputs must be an explicit policy")
            item["inputs"] = input_policy(item["inputs"])
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
            require(not any(field in item for field in ("check", "task_id", "leaf_id", "inputs")), "group cannot claim leaf verification")
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


def input_policy(value):
    if value is None:
        return None
    require(isinstance(value, dict) and not set(value) - {"source", "artifacts", "exclude", "deny", "source_byte_limit"}, "invalid input policy")
    result = {key: value.get(key, []) for key in ("artifacts", "exclude", "deny")}
    if "source" in value:
        result["source"] = value["source"]
    for paths in result.values():
        require(isinstance(paths, list) and len(paths) <= 128, "input policy needs bounded literal paths")
        for path in paths:
            require(isinstance(path, str) and 0 < len(path) <= 2048 and "\0" not in path
                    and "\\" not in path and not path.startswith("/") and not re.match(r"^[A-Za-z]:", path)
                    and (path == "." or all(p not in {"", ".", ".."} for p in path.split("/"))),
                    "input policy paths must be canonical checkout-relative literals")
        require(len(paths) == len(set(paths)), "duplicate input policy paths")
    require(result.get("source") != [] or result["artifacts"], "input policy selects no evidence")
    if "source_byte_limit" in value:
        limit = value["source_byte_limit"]
        require(type(limit) is int and 0 < limit <= MAX_SOURCE_BYTE_LIMIT,
                "source_byte_limit must be a positive integer <= 67108864")
        result["source_byte_limit"] = limit
    return result


def inputs_from_plan(text):
    blocks = re.findall(r"^```wbs-inputs\s*\n(.*?)^```\s*$", text, re.M | re.S)
    require(len(blocks) <= 1, "use one wbs-inputs fence")
    return input_policy(json.loads(blocks[0])) if blocks else None


def covers(root, path):
    return root == "." or path == root or path.startswith(root + "/")


def required_inputs(policy):
    """Check mandatory roots without touching their potentially denied bytes."""
    for required in policy.get('source', []) + policy['artifacts']:
        require(not any(covers(p, required) for p in policy['exclude'] + policy['deny']),
                'required input is excluded or denied: ' + required)


def transition(old, new):
    """Only refinement: stable nodes cannot disappear or silently change credit."""
    previous, current = {n["id"]: n for n in old}, {n["id"]: n for n in new}
    require(previous.keys() <= current.keys(), "cannot remove baseline nodes")
    old_leaves = {n["id"] for n in leaves(old)}
    new_leaves = {n["id"] for n in leaves(new)}
    for key, item in previous.items():
        candidate = current[key]
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
    input_policy(state.get("inputs"))
    blocks = state.get("blocks", {})
    require(isinstance(blocks, dict) and set(blocks) <= {n["id"] for n in state["nodes"]}, "invalid outcome blocks")
    for block in blocks.values():
        require(isinstance(block, dict) and set(block) == {"reason", "unblock_condition"}
                and all(isinstance(v, str) and 0 < len(v.strip()) <= 4000 for v in block.values()), "invalid outcome blocker")
    for proof in state["checks"].values():
        require(isinstance(proof, dict) and type(proof.get("passed")) is bool, "invalid assertion evidence")
        require("invalidated" not in proof or type(proof["invalidated"]) is bool, "invalid assertion invalidation")
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
            require(batch_route(task_dir, find_repo_root(task_dir), old, measure=False)["route"] == "integration",
                    "finish the active work pack before refinement")
            old = dict(old, batch_history=old.get("batch_history", []) + [
                dict(batch=old["batch"])])
            require(len(old["batch_history"]) <= 32, "work pack history limit reached")
            old.pop("batch")
            old.pop("pack_only", None)
        # Planning has no authority to assert current completion from history.
        # New children never inherit their former leaf's proof.
        transition(old["nodes"], nodes)
        if first_baseline:
            old = dict(old, started_at=stamp, last_progress_at=stamp, scope_revision=1)
        state = dict(old, nodes=nodes, run_id=run_id,
                    scope_revision=old.get("scope_revision", 1) + (not first_baseline and weight_sum(n for n in nodes if n["parent"] is None) != weight_sum(n for n in old["nodes"] if n["parent"] is None)),
                    checks=old["checks"] if old["run_id"] == run_id else {})
    else:
        state = dict(version=1, nodes=nodes, run_id=run_id, checks={}, started_at=stamp, last_progress_at=stamp)
    policy = inputs_from_plan(plan)
    if policy is None:
        state.pop("inputs", None)
    else:
        state["inputs"] = policy
    encode_state(state)
    return state


def fingerprint(repo, policy=None, *, _deadline=None):
    """Observe checkout contents internally, without prescribing edited files."""
    from _lib import _trusted_git_env
    root, result = Path(repo).resolve(), []
    policy = input_policy(policy)
    selected = policy.get("source") if policy else None
    excludes = policy["exclude"] if policy else []
    denied = policy["deny"] if policy else []
    artifacts = policy["artifacts"] if policy else []
    seen = set()
    if policy is not None:
        required_inputs(policy)
    # Git can read ignore/config includes outside its enumerated candidate set.
    # Only explicit inputs can enforce a no-read boundary without invoking Git.
    require(not denied or selected is not None,
            "deny requires explicit source inputs; Git metadata reads cannot be confined")
    total = {"source": 0, "artifact": 0}
    listing_bytes = 0
    deadline = time.monotonic() + 30 if _deadline is None else _deadline
    root_info = root.stat()

    def remaining():
        seconds = deadline - time.monotonic()
        require(seconds > 0, "checkout evidence exceeds measurement budget (time; limit 30 seconds)")
        return min(10, seconds)

    @contextmanager
    def directory_handle(path):
        """Open each component relative to its parent; verify the chain on exit."""
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        with ExitStack() as stack:
            fd = os.open(root, flags)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            require((info.st_dev, info.st_ino) == (root_info.st_dev, root_info.st_ino),
                    "checkout directory identity changed")
            chain, current = [(root, info)], root
            for part in path.relative_to(root).parts:
                remaining()
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
                current = current / part
                chain.append((current, os.fstat(fd)))
            yield fd
            for location, opened in chain:
                observed = location.lstat()
                require(stat.S_ISDIR(observed.st_mode)
                        and (observed.st_dev, observed.st_ino) == (opened.st_dev, opened.st_ino),
                        "checkout directory identity changed")

    def git(directory, *args, directory_fd):
        nonlocal listing_bytes
        # Preserve the validated cwd across a concurrent pathname replacement.
        fd_root = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
        answer = _execute(["git", *args], f"{fd_root}/{directory_fd}", remaining(), env=_trusted_git_env(),
                          pass_fds=(directory_fd,),
                          output_limit=MAX_LISTING_BYTES - listing_bytes, retain=MAX_LISTING_BYTES, binary=True)
        require(answer["returncode"] == 0 and not answer["error"],
                "checkout Git evidence unavailable: " + (answer["error"] or "Git command failed")
                + f" (input {directory.relative_to(root)}; listing used {listing_bytes}; limit {MAX_LISTING_BYTES} bytes)")
        listing_bytes += len(answer["output"])
        return answer["output"]

    def add(path, value):
        require(len(result) < MAX_EVIDENCE_ENTRIES, "checkout evidence exceeds measurement budget (entries)")
        result.append([path.relative_to(root).as_posix(), value])

    def file(path, category="source"):
        require(len(result) < MAX_EVIDENCE_ENTRIES, "checkout evidence exceeds measurement budget (entries)")
        observed = False
        try:
            with directory_handle(path.parent) as parent_fd:
                observed = True
                observe_file(path, parent_fd, category)
        except FileNotFoundError:
            if observed:
                raise  # A bound ancestor disappeared during observation.
            add(path, None)  # A tracked directory was already deleted.

    def observe_file(path, parent_fd, category):
        nonlocal total
        limit = (policy or {}).get("source_byte_limit", MAX_EVIDENCE_BYTES) if category == "source" else MAX_ARTIFACT_BYTES
        budget_error = f"checkout evidence exceeds measurement budget ({category} bytes; input {path.relative_to(root)}; limit {limit})"
        try:
            before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            add(path, None)
            return
        require(stat.S_ISREG(before.st_mode), "nonregular or symlink scope cannot attest source")
        require(before.st_size <= limit - total[category], budget_error + f"; size {before.st_size}")
        # No follow and nonblocking also refuse a replacement link/FIFO before read.
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as handle:
            identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            opened = os.fstat(handle.fileno())
            require(identity(before) == identity(opened), "source changed during measurement")
            hashed = hashlib.sha256()
            while chunk := handle.read(min(65536, limit - total[category] + 1)):
                remaining()
                total[category] += len(chunk)
                require(total[category] <= limit, budget_error)
                hashed.update(chunk)
            require(identity(opened) == identity(os.fstat(handle.fileno()))
                    == identity(os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)),
                    "source changed during measurement")
        add(path, digest([before.st_mode, hashed.hexdigest()]))

    def checkout(directory, depth=0):
        require(depth <= MAX_MODULE_DEPTH, "checkout evidence exceeds measurement budget (module depth)")
        with directory_handle(directory) as directory_fd:
            scan(directory, depth, directory_fd)

    def scan(directory, depth, directory_fd):
        top = os.fsdecode(git(directory, "rev-parse", "--show-toplevel", directory_fd=directory_fd)).removesuffix("\n")
        require(Path(top).resolve() == directory, "checkout Git evidence belongs to another repository")
        entries = {}
        for row in git(directory, "ls-files", "--stage", "-z", directory_fd=directory_fd).split(b"\0"):
            if not row:
                continue
            header, name = row.split(b"\t", 1)
            mode, oid, stage = header.split()
            require(stage == b"0", "unmerged source cannot attest checkout")
            entries[os.fsdecode(name)] = (mode, oid)
        for name in git(directory, "ls-files", "--others", "--exclude-standard", "-z", directory_fd=directory_fd).split(b"\0"):
            if name:
                entries.setdefault(os.fsdecode(name), (None, None))
        for scope, (mode, oid) in sorted(entries.items()):
            remaining()
            path = directory / scope
            relative = path.relative_to(root).as_posix()
            if relative in artifacts:
                continue  # Artifacts have a separate explicit read and budget.
            if any(covers(p, relative) for p in excludes):
                continue
            require(not any(covers(p, relative) for p in denied), "required input is denied: " + relative)
            require(len(result) < MAX_EVIDENCE_ENTRIES,
                    f"checkout evidence exceeds measurement budget (entries; input {relative}; limit {MAX_EVIDENCE_ENTRIES})")
            require(path.resolve().is_relative_to(directory), "scope escapes checkout")
            # Check every ancestor rather than resolving through an in-root alias.
            ancestor = path
            while ancestor != directory:
                require(not ancestor.is_symlink(), "symlink scope cannot attest source")
                ancestor = ancestor.parent
            if mode == b"160000":
                require(not path.exists() or path.is_dir(), "gitlink checkout is not a directory")
                if not path.exists():
                    add(path, digest(["gitlink", os.fsdecode(oid), None]))
                else:
                    with directory_handle(path) as module_fd:
                        with os.scandir(module_fd) as children:
                            empty = next(children, None) is None
                        if empty:
                            add(path, digest(["gitlink", os.fsdecode(oid), None]))
                        else:
                            head = git(path, "rev-parse", "--verify", "HEAD", directory_fd=module_fd).strip().decode("ascii")
                            require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head), "invalid module HEAD")
                            add(path, digest(["gitlink", os.fsdecode(oid), head]))
                            checkout(path, depth + 1)
            else:
                file(path)
    if selected is None:
        checkout(root)
    else:
        # Required roots describe verification inputs, independently of Git's
        # index/ignore rules. Never ask Git to discover these paths.
        pending, visited = sorted(root / p for p in selected), set()
        # Keep only the current parent chain, not one descriptor per visited
        # directory. Siblings share its validated handle for stat and read.
        # Closing/replacing this stack revalidates every ancestor as before.
        with ExitStack() as parent_stack:
            parent, parent_fd = None, None
            while pending:
                remaining()
                path = pending.pop()
                relative = path.relative_to(root).as_posix()
                if relative in visited or relative in artifacts or any(covers(p, relative) for p in excludes):
                    continue
                require(not any(covers(p, relative) for p in denied), "required input is denied: " + relative)
                require(".git" not in path.relative_to(root).parts, "Git administrative input cannot attest source")
                visited.add(relative)
                if path == root:
                    is_directory = True
                else:
                    if path.parent != parent:
                        parent_stack.close()
                        parent_fd = parent_stack.enter_context(directory_handle(path.parent))
                        parent = path.parent
                    info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    is_directory = stat.S_ISDIR(info.st_mode)
                if is_directory:
                    with directory_handle(path) as directory_fd:
                        with os.scandir(directory_fd) as children:
                            candidates = []
                            for child in children:
                                remaining()
                                listing_bytes += len(os.fsencode(child.name)) + 1
                                require(listing_bytes <= MAX_LISTING_BYTES,
                                        "checkout evidence exceeds measurement budget (input listing bytes)")
                                if child.name != ".git":
                                    candidates.append(path / child.name)
                            pending.extend(sorted(candidates))
                else:
                    require(len(result) < MAX_EVIDENCE_ENTRIES,
                            "checkout evidence exceeds measurement budget (entries)")
                    observe_file(path, parent_fd, "source")
        seen.update(p for p in selected if any(value is not None and covers(p, name) for name, value in result))
    require(selected is None or all(p in seen for p in selected), "required source input missing from evidence")
    for artifact in artifacts:
        path = root / artifact
        require(not any(covers(p, artifact) for p in denied + excludes), "required artifact is excluded or denied: " + artifact)
        count = len(result)
        file(path, "artifact")
        require(len(result) > count and result[-1][1] is not None, "required artifact missing: " + artifact)
    require(any(value is not None for _, value in result), "no existing source/test evidence")
    remaining()
    return digest([policy, sorted(result)]) if policy is not None else digest(sorted(result))


def verification_inputs(state, node):
    policy = node.get("inputs", state.get("inputs"))
    # A leaf can specialize its dependency scope, never remove a task-wide
    # no-read boundary. Root excludes remain defaults, not security denials.
    denied = (state.get("inputs") or {}).get("deny", [])
    if "inputs" in node and denied:
        policy = dict(policy, deny=list(dict.fromkeys(denied + policy.get("deny", []))))
    return policy


def measurement_sources(repo, state, *, only=None, observed=None):
    """One observation per distinct actual scope, preserving the root default."""
    if not any("inputs" in node for node in leaves(state["nodes"])):
        return fingerprint(repo, state.get("inputs")) if observed is None else observed
    policies = {digest(verification_inputs(state, node)): verification_inputs(state, node)
                for node in leaves(state["nodes"]) if only is None or node["id"] in only}
    deadline = time.monotonic() + 30
    return {key: observed[key] if isinstance(observed, dict) and key in observed else fingerprint(repo, policy, _deadline=deadline)
            for key, policy in policies.items()}


def check_outcomes(state, leaf_id):
    """Selected leaf plus transitive prerequisites, including group dependencies."""
    index = {node["id"]: node for node in state["nodes"]}
    required = set()
    def include(key):
        if key in required:
            return
        required.add(key)
        for child in state["nodes"]:
            if child["parent"] == key:
                include(child["id"])
        ancestor = key
        while ancestor is not None:
            for dep in index[ancestor]["depends_on"]:
                include(dep)
            ancestor = index[ancestor]["parent"]
    include(leaf_id)
    return required


def scoped_source(sources, policy):
    return sources.get(digest(policy)) if isinstance(sources, dict) else sources


def task_progress(task_dir, repo, run_id, *, now=None, _source=None, measure=True, _state=None,
                  _measure_custody=True):
    stamp = time.time() if now is None else now
    try:
        state = read_state(task_dir) if _state is None else _state
        if state is None:
            return dict(status="unbaselined", percentage=None, verified_weight=0, total_weight=None)
        if state.get("pack_only"):
            validate_history(task_dir, repo, state, measure=measure and _measure_custody)
            return dict(status="unbaselined", percentage=None, verified_weight=0, total_weight=None,
                        integration=batch_route(task_dir, repo, state, measure=measure and _measure_custody))
        plan_text = Path(task_dir, "PLAN.md").read_text()
        plan_nodes = from_plan(plan_text)
        require(plan_nodes == state["nodes"], "plan/evidence definition mismatch")
        require(inputs_from_plan(plan_text) == state.get("inputs"), "plan/evidence input policy mismatch")
        rows = []
        if measure:
            sources = measurement_sources(repo, state) if _source is None else _source
        else:
            sources = {}
            for proof in sorted(state["checks"].values(), key=lambda p: p.get("checked_at", 0)):
                if proof.get("source"):
                    sources[proof.get("inputs", digest(None))] = proof["source"]
        for node in leaves(state["nodes"]):
            policy = verification_inputs(state, node)
            source = scoped_source(sources, policy)
            proof = state["checks"].get(node["id"], {})
            valid = (state["run_id"] == run_id and proof.get("passed") is True
                     and "last_attempt" not in proof and not proof.get("invalidated")
                     and proof.get("definition") == digest(node))
            valid = valid and source is not None and proof.get("source") == source
            valid = valid and proof.get("inputs") == (digest(policy) if policy is not None else None)
            rows.append(dict(id=node["id"], title=node["title"], weight=node["weight"],
                             verified=bool(valid), checked_at=proof.get("checked_at") if valid else None))
        # Downstream outcomes lose current credit when prerequisite evidence
        # goes stale, even if their own files have not changed.
        by_id = {row["id"]: row for row in rows}
        index = {n["id"]: n for n in state["nodes"]}
        @cache
        def current(key):
            item = index[key]
            if key in state.get("blocks", {}):
                return False
            children = [n["id"] for n in state["nodes"] if n["parent"] == key]
            own = all(current(child) for child in children) if children else by_id[key]["verified"]
            deps, ancestor = set(item["depends_on"]), item["parent"]
            while ancestor:
                if ancestor in state.get("blocks", {}):
                    return False
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
        validate_history(task_dir, repo, state, measure=measure and _measure_custody)
        integration = batch_route(task_dir, repo, state, measure=measure and _measure_custody) if state.get("batch") else None
        if not measure:
            for row in rows:
                row["historical_verified"], row["verified"] = row["verified"], False
            return dict(status="unmeasured", percentage=None, verified_weight=None, total_weight=total,
                        historical_percentage=100 if complete else min(99.99, round(100 * earned / total, 2)),
                        historical_verified_weight=earned, outcomes=rows, integration=integration,
                        blocks=state.get("blocks", {}), scope_revision=state.get("scope_revision", 1),
                        last_progress_at=state["last_progress_at"],
                        refinement_due=not complete and stamp - state["last_progress_at"] >= STALL_SECONDS,
                        next_action="Stored evidence only; explicit check or final verify/close must measure current inputs.")
        return dict(status="measured", percentage=100 if complete else min(99.99, round(100 * earned / total, 2)),
                    scope_revision=state.get("scope_revision", 1),
                    verified_weight=earned, total_weight=total, outcomes=rows,
                    last_progress_at=state["last_progress_at"],
                    refinement_due=not complete and stamp - state["last_progress_at"] >= STALL_SECONDS,
                    integration=integration,
                    custody_measurement="fresh" if _measure_custody else "stored",
                    next_action="Refine only unclear unfinished outcomes; preserve weights. Report real external blockers instead of recursive splitting."
                    if _measure_custody else "Source evidence measured; batch archive custody remains stored-only until final verify/close.")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        return _unavailable(exc)


def _unavailable(reason):
    return dict(status="invalid", percentage=None, verified_weight=None, total_weight=None, reason=_error_text(reason))


def summary(context):
    """Pure advisory projection of owner-assembled facts; never close authority.

    Current WBS assertions establish measured software checks. Independent role
    results and real deployment/device tests are not tracked here.
    """
    progress = context.get("progress", {})
    integration = progress.get("integration") or {}
    verdicts = (context.get("runtime_verdict"),)
    if "FAIL" in verdicts:
        software = "failed"
    elif "BLOCKED_ENV" in verdicts:
        software = "blocked"
    elif verdicts[0] == "PASS":
        software = "current" if progress.get("status") == "measured" else "stored"
    else:
        software = "pending"
    return dict(advisory=True, task_id=context.get("task_id"), task_status=context.get("status"),
                evidence=dict(implemented="unknown",
                              integrated=("current" if integration.get("proof_fresh") else "stored")
                              if integration.get("route") == "integration" else "unknown",
                              software_verified=software, actual_environment="unknown",
                              complete="stored" if context.get("status") == "closed" else "pending"),
                measurement=progress.get("status", "unknown"),
                outcomes=[dict(id=row["id"], title=row["title"],
                               evidence="current" if row.get("verified") else
                               "stored" if row.get("historical_verified") else "pending")
                          for row in progress.get("outcomes", [])])


def _error_text(reason):
    return (str(reason) or type(reason).__name__).encode(errors="replace")[:4000].decode(errors="replace")


def _execute(argv, repo, timeout, *, env=None, output_limit=1024 * 1024, retain=4000, binary=False, pass_fds=()):
    """Bound time and captured output, terminating the entire assertion group."""
    proc = subprocess.Popen(argv, cwd=repo, env=env, pass_fds=pass_fds, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
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
        require(batch_route(task_dir, repo, state, measure=False)["route"] == "integration", "work pack is unfinished; preserve parked parent")
    plan_text = Path(task_dir, "PLAN.md").read_text()
    require(from_plan(plan_text) == state["nodes"], "plan/evidence mismatch")
    require(inputs_from_plan(plan_text) == state.get("inputs"), "plan/evidence input policy mismatch")
    node = next((n for n in leaves(state["nodes"]) if n["id"] == leaf_id), None)
    require(node is not None, "unknown leaf")
    require(leaf_id not in blocked_outcomes(state), "selected outcome or prerequisite is blocked")
    require(node.get("check"), "choose the outcome assertion through wbs(action='plan') when ready; no implementation layout required")
    observation_errors = (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError)
    needed = check_outcomes(state, leaf_id)

    def aggregate(sources, current_state):
        try:
            complete_sources = measurement_sources(repo, current_state, observed=sources)
            return task_progress(task_dir, repo, run_id, now=now, _source=complete_sources,
                                 _state=current_state, _measure_custody=False)
        except observation_errors as exc:
            return _unavailable(exc)

    def unavailable(reason, phase, result=None):
        return dict(leaf_id=leaf_id, passed=None,
                    execution="not_run" if result is None else "start_failed" if result["returncode"] is None else "completed",
                    **(result or dict(returncode=None, error=None, output="")),
                    measurement_status="unavailable", measurement_phase=phase, measurement_error=_error_text(reason),
                    progress=_unavailable(reason))
    # Reserve the largest bounded publication before any assertion can run.
    # Preserve the previous proof even when the next result is inconclusive.
    reserve = dict(state, checks=dict(state["checks"]))
    reserved_proof = dict(state["checks"].get(leaf_id, dict(passed=False)))
    reserved_proof["last_attempt"] = dict(
        result=dict(returncode=-999999999999999999999, error="\0" * 4000, output="\0" * 4000),
        checked_at=1.7976931348623157e308, measurement_error="\0" * 4000)
    reserve["checks"][leaf_id] = reserved_proof
    try:
        encode_state(reserve)
    except ValueError as exc:
        return unavailable(exc, "before")
    try:
        before_sources = measurement_sources(repo, state, only=needed)
        before = scoped_source(before_sources, verification_inputs(state, node))
    except observation_errors as exc:
        return unavailable(exc, "before")
    before_progress = task_progress(task_dir, repo, run_id, now=now, _source=before_sources,
                                    _measure_custody=False)
    if before_progress["status"] != "measured":
        return unavailable(before_progress.get("reason", "current measurement unavailable"), "before")
    verified = {row["id"] for row in before_progress["outcomes"] if row["verified"]}
    def complete(key):
        children = [n["id"] for n in state["nodes"] if n["parent"] == key]
        return all(complete(child) for child in children) if children else key in verified
    deps, ancestor = set(node["depends_on"]), node["parent"]
    index = {n["id"]: n for n in state["nodes"]}
    require(leaf_id not in state.get("blocks", {}), "selected outcome is blocked")
    while ancestor:
        require(ancestor not in state.get("blocks", {}), "selected outcome group is blocked")
        deps.update(index[ancestor]["depends_on"])
        ancestor = index[ancestor]["parent"]
    require(all(complete(dep) for dep in deps), "dependencies are not verified")
    # Independent assertions must not wait for unrelated scopes to exist.
    before_aggregate = (aggregate(before_sources, state)
                        if any('inputs' in n for n in leaves(state['nodes'])) else before_progress)
    try:
        result = _execute(node["check"], repo, timeout)
    except OSError as exc:
        result = dict(returncode=None, error=_error_text(exc), output="")
    stamp = time.time() if now is None else now
    try:
        after_sources = measurement_sources(repo, state, only=needed)
        after_source = scoped_source(after_sources, verification_inputs(state, node))
        after_progress = task_progress(task_dir, repo, run_id, now=now, _source=after_sources,
                                       _measure_custody=False)
        require(after_progress["status"] == "measured", after_progress.get("reason", "current measurement unavailable"))
    except observation_errors as exc:
        # Preserve the prior proof's fields, but prevent it from resurrecting
        # credit after a newer inconclusive attempt, even if measurement heals.
        proof = dict(state["checks"].get(leaf_id, dict(passed=False)))
        proof["last_attempt"] = dict(result=result, checked_at=stamp, measurement_error=_error_text(exc))
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
        policy = verification_inputs(state, node)
        if policy is not None:
            state["checks"][leaf_id]["inputs"] = digest(policy)
    final_progress = aggregate(after_sources, state)
    if (final_progress["status"] == before_aggregate["status"] == "measured"
            and weight_sum(n for n in final_progress["outcomes"] if n["verified"])
            > weight_sum(n for n in before_aggregate["outcomes"] if n["verified"])):
        state["last_progress_at"] = stamp
        final_progress["last_progress_at"] = stamp
    _atomic_text_write(os.path.join(task_dir, STATE), encode_state(state))
    return dict(leaf_id=leaf_id, passed=success, **result,
                execution="start_failed" if result["returncode"] is None else "completed",
                measurement_status="measured", measurement_error=None,
                progress=final_progress)


def batch_route(task_dir, repo, state, *, measure=True):
    import work_pack
    return work_pack.route(repo, Path(task_dir).name, state["batch"], measure=measure)


def validate_history(task_dir, repo, state, *, measure=True):
    for prior in state.get("batch_history", []):
        require(batch_route(task_dir, repo, prior, measure=measure)["route"] == "integration", "previous work pack proof is no longer integrated")


def require_integrated_for_task(repo, task_id, *, measure=True):
    from _lib import canonical_task_dir
    td = canonical_task_dir(task_id=task_id, repo_root=repo)
    state = read_state(td) if Path(td).is_dir() else None
    if state:
        validate_history(td, repo, state, measure=measure)
    if state and state.get("batch"):
        require(batch_route(td, repo, state, measure=measure)["route"] == "integration", "work pack unfinished; integrate and close the pool before resuming parent")


def admission_definition(state, ownership, selected):
    # Assignment freshness concerns control/intent, not changing source bytes.
    return digest([state['run_id'], state['nodes'], state['checks'], state.get('blocks', {}), ownership, selected])


def require_source_assignment(repo, task_id, path=None):
    """Use a bound pool lead or a current owner-published direct assignment."""
    from _lib import canonical_task_dir, read_task_control, task_control_status, resolve_registered_worktree
    import batch_state
    td = canonical_task_dir(task_id=task_id, repo_root=repo)
    control = read_task_control(td)
    require(control and task_control_status(td, control) == 'open', 'source assignment requires the same open task')
    # Existing managed ownership is the authority for a lead; never create a
    # nested pool merely because its own WBS has not measured its next check.
    if Path(repo, '.git').is_file():
        common = batch_state.finish_helper._git_out(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()
        root = str(Path(common).parent)
        require(resolve_registered_worktree(root, str(Path(repo).resolve())), 'linked worktree binding required')
        batches = Path(root, 'doc/harness/runtime/batches')
        try:
            batches.lstat()
        except FileNotFoundError:
            # No batch metadata is consumed; direct admission is worktree-local.
            pass
        else:
            with batch_state.locked_records(root, readonly=True) as (_, records):
                for data in records.values():
                    for item in data['requests']:
                        owned = item.get('worktree') or item.get('creation_intent', {}).get('worktree')
                        if owned == repo:
                            require(item['task_id'] == task_id, 'source belongs to another managed task')
                            require(not data['halted'] and item['status'] == 'running' and item.get('worker_id'),
                                    'managed lead is not currently bound for development')
                            batch_state.bound(root, item)
                            batch_state.control(item)
                            scopes = batch_state.effective_scopes(item)
                            require(path is None or any(covers(scope, path) for scope in scopes), 'source path outside managed ownership')
                            return dict(mode='managed-lead', scopes=scopes)
    state = read_state(td)
    require(state and state['run_id'] == control['run_id'], 'baseline WBS and dispatch prepare before source edits')
    assignment = state.get('execution_admission')
    require(isinstance(assignment, dict) and assignment.get('run_id') == control['run_id']
            and assignment.get('definition') == admission_definition(state, assignment.get('ownership'), assignment.get('selected')),
            'execution assignment missing or stale; refresh dispatch prepare with actual host inventory and ownership')
    require(assignment.get('mode') == 'direct', 'source belongs to managed worktrees or verification only; use dispatch prepare')
    scopes = [scope for key in assignment['selected'] for scope in assignment['ownership'][key]]
    require(path is None or any(covers(scope, path) for scope in scopes), 'source path outside direct execution ownership')
    return assignment


def work_pack(task_dir, repo, run_id, *, bind=False, ownership=None, host=None):
    """Derive direct/managed admission within the existing owner's transaction."""
    from parallel_dispatch import host_slots, independent_order, _paths
    import batch_state
    from _lib import _atomic_text_write
    state = read_state(task_dir)
    require(state is not None and state['run_id'] == run_id, 'baseline WBS before dispatch')
    available = host_slots(host)
    assignment = dict(run_id=run_id, host=host,
                      ownership=ownership, selected=[], available_slots=available)
    if state.get('batch'):
        result = batch_route(task_dir, repo, state, measure=False)
        assignment.update(mode='verification' if result['route'] == 'integration' else 'managed',
                          ownership={r['slug']: r['scopes'] for r in state['batch']['requests']})
        if not host['spawn_available'] or not host['continuation_available']:
            assignment['serial_facts'] = [dict(kind='capability',
                spawn_available=host['spawn_available'], continuation_available=host['continuation_available'])]
    else:
        progress = task_progress(task_dir, repo, run_id, measure=False)
        require(progress['status'] == 'unmeasured', 'valid stored WBS required')
        done = {row['id'] for row in progress['outcomes'] if row.get('historical_verified')}
        blocked = blocked_outcomes(state)
        pending = [n for n in leaves(state['nodes']) if n['id'] not in done and n['id'] not in blocked]
        if not pending:
            assignment.update(mode='blocked' if blocked else 'verification', ownership={})
            result = dict(route=assignment['mode'], next_action='Resolve recorded outcome blockers or measure existing proofs; unmeasured does not request new development.')
        else:
            require(isinstance(ownership, dict) and set(ownership) == {n['id'] for n in pending},
                    'declare execution ownership for every pending outcome separately from progress WBS')
            ownership = {key: _paths(paths, key, Path(repo).resolve()) for key, paths in ownership.items()}
            require(all(ownership.values()), 'each pending outcome requires execution ownership')
            assignment['ownership'] = ownership
            prefix = 'wbs-' + digest([Path(task_dir).name, state['nodes']])[:12]
            slug = {n['id']: prefix + '-' + n['id'] for n in pending}
            index = {n['id']: n for n in state['nodes']}
            def descendants(key):
                children = [n['id'] for n in state['nodes'] if n['parent'] == key]
                return [leaf for child in children for leaf in descendants(child)] if children else [key]
            requests = []
            for node in pending:
                deps, ancestor = set(node['depends_on']), node['parent']
                while ancestor:
                    deps.update(index[ancestor]['depends_on'])
                    ancestor = index[ancestor]['parent']
                requests.append(dict(slug=slug[node['id']], request=node['title'] +
                    ('\nVerify argv: ' + json.dumps(node['check']) if node.get('check') else '\nChoose outcome assertions during development.'),
                    scopes=ownership[node['id']], depends_on=sorted({slug[d] for dep in deps for d in descendants(dep) if d in slug})))
            batch_state.requests_valid(requests)
            ready = [r for r in requests if not r['depends_on']]
            ordered = independent_order(ready, lambda r: r['scopes'])
            independent = len(ordered) >= 2 and not batch_state.overlap(ordered[0]['scopes'], ordered[1]['scopes'])
            managed = independent and available >= 2 and host['spawn_available'] and host['continuation_available']
            facts = []
            def outcome(request):
                return request['slug'][len(prefix)+1:]
            if len(pending) == 1:
                facts.append(dict(kind='single_outcome', outcome=outcome(requests[0])))
            for request in requests:
                if request['depends_on']:
                    facts.append(dict(kind='dependency', outcome=outcome(request),
                                      predecessors=[value[len(prefix)+1:] for value in request['depends_on']]))
            if not independent and len(ready) > 1:
                first = ready[0]
                for other in ready[1:]:
                    collision = next(([a, b] for a in first['scopes'] for b in other['scopes']
                                      if batch_state.overlap([a], [b])), None)
                    if collision:
                        facts.append(dict(kind='file_collision', outcomes=[outcome(first), outcome(other)], scopes=collision))
            if available < 2:
                facts.append(dict(kind='host_capacity', capacity=host['capacity'],
                                  live=len(host['live_agents']), reserved=len(host['reserved_slots']),
                                  verification_reserve=host['verification_reserve'], available=available))
            if not host['spawn_available'] or not host['continuation_available']:
                facts.append(dict(kind='capability', spawn_available=host['spawn_available'],
                                  continuation_available=host['continuation_available']))
            assignment.update(mode='managed' if managed else 'direct' if ready else 'blocked',
                              selected=[] if managed else [outcome(n) for n in ordered[:1]],
                              serial_facts=facts)
            if managed:
                import work_pack as packs
                if bind:
                    batch_state.main_boundary(repo, scopes=batch_state.ordinary_admission_scopes(repo, requests, managed=True))
                spec = (packs.make_spec(repo, Path(task_dir).name, requests) if bind
                        else packs._spec(Path(task_dir).name, requests))
                state['batch'] = spec
                result = dict(route='batch', **spec)
            else:
                result = dict(route=assignment['mode'], next_action='Develop only the assigned direct scope; refresh prepare after control/ownership or host changes.')
    assignment['definition'] = admission_definition(state, assignment['ownership'], assignment['selected'])
    state['execution_admission'] = assignment
    if bind:
        _atomic_text_write(os.path.join(task_dir, STATE), encode_state(state))
    return dict(result, execution_admission=assignment, available_slots=available)


def publish_handoffs(task_dir, run_id, expected, slugs):
    """Owner-transaction publication after the pool returns real reservations."""
    from _lib import _atomic_text_write
    state = read_state(task_dir)
    require(state and state['run_id'] == run_id and state.get('execution_admission') == expected,
            'assignment changed during preparation; preserve pool and inspect status')
    assignment = dict(expected)
    ownership = {item['slug']: item['scopes'] for item in state['batch']['requests']}
    require(isinstance(slugs, list) and len(set(slugs)) == len(slugs) and set(slugs) <= ownership.keys(),
            'handoffs must identify exact managed intake')
    assignment.update(ownership=ownership, selected=slugs)
    assignment['definition'] = admission_definition(state, ownership, slugs)
    state['execution_admission'] = assignment
    _atomic_text_write(os.path.join(task_dir, STATE), encode_state(state))
    return assignment


def blocked_outcomes(state):
    index = {n["id"]: n for n in state["nodes"]}
    @cache
    def blocked(key):
        node = index[key]
        if key in state.get("blocks", {}):
            return True
        ancestor = node["parent"]
        deps = set(node["depends_on"])
        while ancestor:
            if ancestor in state.get("blocks", {}):
                return True
            deps.update(index[ancestor]["depends_on"])
            ancestor = index[ancestor]["parent"]
        children = {n["id"] for n in state["nodes"] if n["parent"] == key}
        return any(blocked(dep) for dep in deps | children)
    return {key for key in index if blocked(key)}


def ready_outcomes(state, progress):
    """Ready IDs from the same outcome graph, never a second task queue."""
    if progress.get("status") not in {"measured", "unmeasured"}:
        return []
    if progress.get("integration") and progress["integration"]["route"] != "integration":
        return []
    done = {row["id"] for row in progress.get("outcomes", [])
            if row["verified"] or progress["status"] == "unmeasured" and row.get("historical_verified")}
    nodes = state["nodes"]
    index = {node["id"]: node for node in nodes}
    @cache
    def complete(key):
        if key in state.get("blocks", {}):
            return False
        children = [n["id"] for n in nodes if n["parent"] == key]
        return all(complete(child) for child in children) if children else key in done
    ready = []
    blocked = blocked_outcomes(state)
    for node in leaves(nodes):
        if node["id"] in done or node["id"] in blocked:
            continue
        deps, ancestor = set(node["depends_on"]), node["parent"]
        while ancestor:
            if ancestor in state.get("blocks", {}):
                deps.add(ancestor)
            deps.update(index[ancestor]["depends_on"])
            ancestor = index[ancestor]["parent"]
        if all(complete(dep) for dep in deps):
            ready.append(node["id"])
    return ready


def block_outcome(task_dir, node_id, *, run_id, reason=None, unblock_condition=None, clear=False):
    """Local WBS parking; the task owner holds the existing task transaction."""
    from _lib import _atomic_text_write
    state = read_state(task_dir)
    require(state is not None and node_id in {n["id"] for n in state["nodes"]}, "unknown outcome")
    require(state["run_id"] == run_id, "baseline the current task run before changing outcome blockers")
    state = dict(state, blocks=dict(state.get("blocks", {})), checks=dict(state["checks"]))
    if clear:
        state["blocks"].pop(node_id, None)
    else:
        require(all(isinstance(v, str) and 0 < len(v.strip()) <= 4000 for v in (reason, unblock_condition)),
                "bounded blocker reason and unblock condition required")
        state["blocks"][node_id] = dict(reason=reason, unblock_condition=unblock_condition)
        affected = blocked_outcomes(state)
        for key in affected & state["checks"].keys():
            state["checks"][key] = dict(state["checks"][key], invalidated=True)
    _atomic_text_write(os.path.join(task_dir, STATE), encode_state(state))
    return dict(node_id=node_id, blocked=not clear, blocks=state["blocks"])


