"""Copied verification inputs and applicability.

Native roles consume the issued source view. The association authenticates the
invocation's declared target, not arbitrary tool reads or hostile same-UID code.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time

import wbs

FIELD = "verification_targets"


def _require(ok, message):
    if not ok:
        raise ValueError("verification target: " + message)


def _state(task_dir):
    from _lib import read_task_control
    state = wbs.read_state(task_dir)
    control = read_task_control(task_dir)
    _require(state is not None and control and state["run_id"] == control["run_id"], "open planned WBS required")
    return state, control


def _publish(task_dir, state):
    from _lib import _atomic_text_write
    _atomic_text_write(str(Path(task_dir) / wbs.STATE), wbs.encode_state(state))


def _spec(value):
    from _lib import SUPPORTED_LENSES
    _require(isinstance(value, dict) and not set(value) - {"inputs", "commands", "lenses", "purpose", "environment", "toolchains", "output_dirs"}, "unknown specification fields")
    policy = wbs.input_policy(value.get("inputs"))
    _require(policy is not None and policy.get("source"), "explicit source input roots required")
    commands = value.get("commands", [])
    _require(isinstance(commands, list) and len(commands) <= 32 and all(isinstance(c, list) and 0 < len(c) <= 128 and all(isinstance(a, str) and a and "\0" not in a for a in c) for c in commands), "commands must be argv arrays")
    purpose = value.get("purpose", "acceptance")
    lenses = value.get("lenses", [])
    _require(isinstance(lenses, list) and (lenses or purpose == "diagnostic")
             and all(isinstance(x, str) and x in SUPPORTED_LENSES for x in lenses)
             and len(set(lenses)) == len(lenses), "explicit unique supported lenses required")
    purpose = value.get("purpose", "acceptance")
    _require(purpose in {"acceptance", "diagnostic"}, "purpose must be acceptance or diagnostic")
    result = dict(inputs=policy, commands=commands, lenses=lenses, purpose=purpose)
    for key in ("environment", "toolchains"):
        names = value.get(key, [])
        _require(isinstance(names, list) and len(names) <= 32 and all(isinstance(n, str) and (re.fullmatch(r"[A-Za-z0-9_.+-]+", n) or key == "toolchains" and os.path.isabs(n) and "\0" not in n) for n in names), "invalid " + key)
        result[key] = sorted(set(names))
    return result


def _context(spec, repo):
    tools = {}
    for name in spec["toolchains"]:
        location = shutil.which(name)
        _require(location is not None, "toolchain unavailable: " + name)
        digest = hashlib.sha256()
        fd = os.open(str(Path(location).resolve()), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "toolchain is not a regular executable: " + name)
            total = 0
            while data := stream.read(1024 * 1024):
                total += len(data)
                _require(total <= 256 * 1024 * 1024, "toolchain exceeds 256 MiB identity budget: " + name)
                digest.update(data)
        # Proxies such as rustup can retain identical wrapper bytes while the
        # actual selected compiler changes. Resolve version in the source cwd.
        version = wbs._execute([location, "--version"], str(repo), 3,
                               output_limit=16000, retain=16000)
        _require(version["returncode"] == 0 and not version["error"], "toolchain version unavailable: " + name)
        tools[name] = dict(binary=digest.hexdigest(), version=version["output"].strip())
    # Absolute installation paths are provenance, not content identity.
    return dict(platform=[sys.platform, platform.machine()], toolchains=tools,
                environment={key: wbs.digest(os.environ.get(key)) for key in spec["environment"]})


def _paths(repo, policy, *, _deadline=None):
    """Enumerate explicit roots only; wbs owns policy/budgets and stable hashing."""
    wbs.required_inputs(policy)
    pending = [Path(repo) / p for p in policy["source"] + policy["artifacts"]]
    found, listing_bytes = set(), 0
    while pending:
        _require(_deadline is None or time.monotonic() < _deadline, "input measurement time budget exceeded")
        path = pending.pop()
        relative = path.relative_to(repo).as_posix()
        if any(wbs.covers(p, relative) for p in policy["exclude"]):
            continue
        _require(not any(wbs.covers(p, relative) for p in policy["deny"]),
                 "required input is denied: " + relative)
        if path.name == ".git":
            continue
        info = path.lstat()
        _require(not stat.S_ISLNK(info.st_mode), "symlink input unsupported: " + relative)
        _require(relative not in policy['artifacts'] or stat.S_ISREG(info.st_mode),
                 'required artifact must be a regular file: ' + relative)
        if stat.S_ISDIR(info.st_mode):
            _require(path == Path(repo) or not (path / ".git").exists(), "nested checkout unsupported; select its concrete inputs: " + relative)
            for child in path.iterdir():
                _require(_deadline is None or time.monotonic() < _deadline, "input measurement time budget exceeded")
                listing_bytes += len(os.fsencode(child.name)) + 1
                _require(listing_bytes <= wbs.MAX_LISTING_BYTES, 'input listing byte budget exceeded')
                pending.append(child)
                _require(len(found) + len(pending) <= wbs.MAX_EVIDENCE_ENTRIES, "input entry budget exceeded")
        else:
            _require(stat.S_ISREG(info.st_mode), "nonregular input unsupported: " + relative)
            found.add(relative)
        _require(len(found) + len(pending) <= wbs.MAX_EVIDENCE_ENTRIES, "input entry budget exceeded")
    _require(all(any(wbs.covers(required, name) for name in found)
                 for required in policy['source'] + policy['artifacts']),
             'required input missing from evidence')
    return sorted(found)


def _manifest(repo, paths, *, destination=None, readonly=False, policy=None, _deadline=None):
    # One existing descriptor-relative reader, with streaming consumers shared
    # with verified installation; no second security walker.
    from install_verified import _read_payload_file
    rows, total = [], {'source': 0, 'artifact': 0}
    for relative in paths:
        category = 'artifact' if policy and relative in policy['artifacts'] else 'source'
        limit = ((policy or {}).get('source_byte_limit', wbs.MAX_EVIDENCE_BYTES)
                 if category == 'source' else wbs.MAX_ARTIFACT_BYTES)
        def budget():
            _require(_deadline is None or time.monotonic() < _deadline, 'input measurement time budget exceeded')
            _require(policy is None or total[category] <= limit,
                     f'input measurement budget exceeded ({category} bytes; input {relative}; used {total[category]}; limit {limit})')
        budget()
        if policy is not None:
            _require((Path(repo) / relative).lstat().st_size <= limit - total[category],
                     f'input measurement budget exceeded ({category} bytes; input {relative}; limit {limit})')
        digest = hashlib.sha256()
        output = None
        if destination is not None:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            output = target.open("xb")
        def consume(chunk):
            total[category] += len(chunk)
            budget()
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
        try:
            record = _read_payload_file(Path(repo), relative, consume=consume, readonly=readonly, source_input=True)
        finally:
            if output is not None:
                output.close()
        _require(record is not None, "input changed or unsafe ownership/mode: " + relative)
        info = record[1]
        mode = 0o555 if info.st_mode & 0o111 else 0o444
        if destination is not None:
            os.chmod(destination / relative, mode)
        rows.append([relative, bool(info.st_mode & 0o111), info.st_size, digest.hexdigest()])
    return rows


def _root(task_dir, token):
    _require(re.fullmatch(r"[0-9a-f]{16}", token or ""), "invalid token")
    return Path(task_dir) / "work" / "verification-targets" / token / "source"


def _record_path(task_dir, token):
    return _root(task_dir, token).parent / "target.json"


def _write_private(path, data):
    from _lib import _atomic_text_write
    text = json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"
    _require(len(text.encode()) <= 1024 * 1024, "target metadata exceeds 1 MiB; refine verification scope")
    _atomic_text_write(str(path), text)
    os.chmod(path, 0o444)
    return hashlib.sha256(text.encode()).hexdigest()


def _read_private(path, task_dir):
    from install_verified import _read_payload_file
    chunks, size = [], 0
    def consume(chunk):
        nonlocal size
        size += len(chunk)
        _require(size <= 1024 * 1024, "target metadata exceeds 1 MiB")
        chunks.append(chunk)
    record = _read_payload_file(Path(task_dir), path.relative_to(task_dir).as_posix(), consume=consume, readonly=True)
    _require(record is not None, "target metadata missing or unsafe")
    data = b"".join(chunks)
    return json.loads(data), hashlib.sha256(data).hexdigest()


def _output_paths(output_dirs, manifest, policy):
    from install_verified import _normalize_payload_path
    _require(isinstance(output_dirs, (list, tuple)) and len(output_dirs) <= 8, "declare at most eight runtime output directories")
    result = []
    for raw in output_dirs:
        path = _normalize_payload_path(raw)
        _require(isinstance(raw, str) and path == raw and "\0" not in raw and path and ".git" not in Path(path).parts, "runtime output must be a canonical relative non-Git directory")
        _require(not any(wbs.covers(path, row[0]) or wbs.covers(row[0], path) for row in manifest), "runtime output overlaps selected input: " + path)
        _require(not any(wbs.covers(source, path) for source in policy["source"]) or any(wbs.covers(excluded, path) for excluded in policy["exclude"]), "runtime output must be outside declared source scope: " + path)
        result.append(path)
    return sorted(set(result))


def _seal_view(root, output_dirs=()):
    for output in output_dirs:
        cursor = root
        for part in Path(output).parts:
            cursor = cursor / part
            cursor.mkdir(mode=0o700, exist_ok=True)
            info = cursor.lstat()
            _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid(), "unsafe runtime output directory")
    for directory, _, _ in os.walk(root, topdown=False):
        relative = Path(directory).relative_to(root).as_posix()
        os.chmod(directory, 0o700 if any(wbs.covers(p, relative) for p in output_dirs) else 0o500)


def issue_target(task_dir, repo, spec, *, output_dirs=()):
    """Owner transaction required. Copy inputs before issuing a native token."""
    state, control = _state(task_dir)
    output_dirs = spec.get("output_dirs", output_dirs) if isinstance(spec, dict) else output_dirs
    spec = _spec(spec)
    index = state.get(FIELD, {"required": {}, "run_id": control["run_id"]})
    if index.get("run_id", control["run_id"]) != control["run_id"]:
        index = {"required": {}, "run_id": control["run_id"]}
    index["run_id"] = control["run_id"]
    wbs.fingerprint(repo, spec["inputs"])
    paths = _paths(Path(repo), spec["inputs"])
    for relative in paths:
        _require(not wbs.covers("doc/harness/tasks", relative), "task/runtime state cannot be recursively captured")
    context, expected = _context(spec, repo), _manifest(repo, paths)
    output_dirs = _output_paths(output_dirs, expected, spec["inputs"])
    for token in sorted({ref["token"] for ref in index["required"].values()}):
        try:
            existing = load_target(task_dir, token)
        except (OSError, ValueError):
            continue  # Unavailable history cannot be reused or prevent recovery.
        if existing["spec"] == spec and existing["context"] == context and existing["manifest"] == expected:
            prepared = ensure_snapshot(task_dir, token, repo, output_dirs=output_dirs)
            if spec["purpose"] == "acceptance":
                reference = next(ref for ref in index["required"].values() if ref["token"] == token)
                for lens in spec["lenses"]:
                    index["required"][lens] = dict(reference)
                state[FIELD] = index
                _publish(task_dir, state)
            return prepared
    token = secrets.token_hex(8)
    root = _root(task_dir, token)
    current = Path(task_dir)
    for part in ("work", "verification-targets", token, "source"):
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            _require(part != token, "snapshot nonce already exists; preserve existing data")
            info = current.lstat()
            _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022, "unsafe snapshot storage: " + str(current))
    try:
        manifest = _manifest(repo, paths, destination=root)
        _seal_view(root, output_dirs)
        record = dict(token=token, run_id=control["run_id"], spec=spec, context=context,
                      manifest=manifest, input_fingerprint=wbs.digest([spec, manifest]),
                      issued_at=time.time(), source_origin=str(Path(repo).resolve()))
        observed = _manifest(repo, _paths(Path(repo), spec["inputs"]))
        before, after = {row[0]: row for row in manifest}, {row[0]: row for row in observed}
        changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
        _require(not changed, "source changed during target capture: " + ", ".join(changed[:8]))
        digest = _write_private(_record_path(task_dir, token), record)
    except BaseException:
        # This operation owns only this freshly random unissued directory.
        # Never delete previous views/metadata or any active target pointer.
        for directory, _, _ in os.walk(root.parent, topdown=False):
            os.chmod(directory, 0o700)
        shutil.rmtree(root.parent)
        raise
    if spec["purpose"] == "acceptance":
        for lens in spec["lenses"]:
            index["required"][lens] = dict(token=token, digest=digest)
    state[FIELD] = index
    _publish(task_dir, state)
    return load_target(task_dir, token)


def load_target(task_dir, token):
    state, control = _state(task_dir)
    record, digest = _read_private(_record_path(task_dir, token), Path(task_dir))
    _require(record["token"] == token and record["run_id"] == control["run_id"], "target missing or belongs to another run")
    references = state.get(FIELD, {}).get("required", {})
    _require(all(ref["digest"] == digest for ref in references.values() if ref["token"] == token), "target metadata changed")
    return dict(record, snapshot_root=str(_root(task_dir, token)))


def _outputs_ready(root, output_dirs):
    for output in output_dirs:
        cursor = root
        for part in Path(output).parts:
            cursor = cursor / part
            try:
                if not stat.S_ISDIR(cursor.lstat().st_mode):
                    return False
            except FileNotFoundError:
                return False
        if not os.access(cursor, os.W_OK):
            return False
    return True


def ensure_snapshot(task_dir, token, repo=None, *, output_dirs=()):
    """Prepare an actual immutable view for a new job, reusing durable identity.

    Completed results do not depend on this regenerable copy's continued
    existence. Reconstruction creates a new view, never edits an issued view.
    """
    record = load_target(task_dir, token)
    root = Path(record["snapshot_root"])
    paths = [row[0] for row in record["manifest"]]
    output_dirs = _output_paths(output_dirs, record["manifest"], record["spec"]["inputs"])
    ready_outputs = _outputs_ready(root, output_dirs)
    if all((root / name).exists() for name in paths) and ready_outputs:
        _require(_manifest(root, paths, readonly=True) == record["manifest"], "frozen snapshot changed; preserve evidence and investigate")
        return record
    _require(repo is not None, "snapshot missing; restore identical inputs then issue this target again")
    _require(_manifest(repo, _paths(Path(repo), record["spec"]["inputs"])) == record["manifest"] and _context(record["spec"], repo) == record["context"], "snapshot cannot be restored from changed inputs")
    root = root.parent / ("source-restored-" + secrets.token_hex(8))
    root.mkdir(mode=0o700)
    _require(_manifest(repo, paths, destination=root) == record["manifest"], "inputs changed during snapshot restoration")
    _seal_view(root, output_dirs)
    return dict(record, snapshot_root=str(root))


def measure_inputs(task_dir, repo):
    """Measure source outside task publication; retain exact owner authority."""
    from _lib import read_task_control
    state, control = wbs.read_state(task_dir), read_task_control(task_dir)
    index = state.get(FIELD) if state else None
    rows = _input_rows(task_dir, repo, index, measure=True) if index and index.get("required") else []
    return dict(task_dir=os.path.realpath(task_dir), repo=os.path.realpath(repo),
                state=state, control=control, rows=rows)


def _input_rows(task_dir, repo, index, *, measure):
    tokens = sorted({ref["token"] for ref in index["required"].values()})
    rows = []
    for token in tokens:
        row = dict(token=token, status="unmeasured")
        if measure:
            try:
                record = load_target(task_dir, token)
                row.update(purpose=record["spec"]["purpose"], lenses=record["spec"]["lenses"])
                root = _root(task_dir, token)
                paths = [r[0] for r in record["manifest"]]
                # Copies are regenerable. Validate every present byte, while
                # current declared inputs/context determine applicability.
                present = [path for path in paths if os.path.lexists(root / path)]
                row["snapshot_available"] = root.is_dir() and len(present) == len(paths)
                expected_present = [entry for entry in record["manifest"] if entry[0] in present]
                _require(_manifest(root, present, readonly=True) == expected_present,
                         "frozen snapshot changed; preserve the view and investigate")
                wbs.fingerprint(repo, record["spec"]["inputs"])
                current = _manifest(repo, _paths(Path(repo), record["spec"]["inputs"]))
                row["status"] = "applicable" if current == record["manifest"] and _context(record["spec"], repo) == record["context"] else "stale"
            except (OSError, ValueError, KeyError) as error:
                row.update(status="unavailable", reason=str(error)[:400])
        rows.append(row)
    return rows


def applicability(task_dir, repo, *, measure=False, measured_inputs=None):
    """Measure declared snapshot inputs without claiming any role ran."""
    from _lib import read_task_control
    if measure and measured_inputs is None:
        measured_inputs = measure_inputs(task_dir, repo)
    state, control = wbs.read_state(task_dir), read_task_control(task_dir)
    if measured_inputs is not None:
        _require(measured_inputs["task_dir"] == os.path.realpath(task_dir)
                 and measured_inputs["repo"] == os.path.realpath(repo)
                 and measured_inputs["state"] == state and measured_inputs["control"] == control,
                 "authority or target index changed during measurement")
        measure = True
    index = state.get(FIELD) if state else None
    if not index or not index.get("required"):
        return dict(status="unbound", enforced=False, source_binding="wbs_inputs", targets=[])
    rows = measured_inputs["rows"] if measured_inputs is not None else _input_rows(task_dir, repo, index, measure=False)
    by_token = {row["token"]: row for row in rows}
    missing = []
    if measure:
        for lens, reference in index["required"].items():
            row = by_token.get(reference["token"], {})
            if index.get("run_id") != control.get("run_id") or row.get("purpose") != "acceptance" or row.get("status") != "applicable":
                missing.append(lens)
    return dict(status=("stale" if missing else "applicable") if measure else "unmeasured", enforced=True,
                source_binding="issued_snapshot_inputs", missing_lenses=missing, targets=rows)
