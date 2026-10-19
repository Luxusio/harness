"""Explicit, one-time import of retired Goal records; never an execution path.

Only apply writes. Its durable manifest is the commit point: original bytes are
archived first, destinations follow, and source retirement happens last.
"""
from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

LIMIT = 1024 * 1024
MAX_RECORDS = 256
ARCHIVE = "doc/harness/archive/goal-migration"
MANIFEST = ARCHIVE + "/manifest.json"
GOALS = "doc/harness/goals"
IDENT = re.compile(r"(?:GOAL|TASK)__[A-Za-z0-9_.-]{1,180}")


def _require(ok, message):
    if not ok:
        raise ValueError("Goal migration: " + message)


def _hash(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    try:
        def constant(_):
            raise ValueError("non-finite JSON constant")
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Goal migration: invalid bounded JSON") from exc


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _path(repo, relative):
    _require(isinstance(relative, str) and relative and not relative.startswith("/")
             and all(p not in ("", ".", "..") for p in relative.split("/")), "invalid relative path")
    path = repo
    for part in relative.split("/"):
        path = path / part
        if os.path.lexists(path):
            info = path.lstat()
            _require(not stat.S_ISLNK(info.st_mode), "symlink path refused: " + relative)
            _require(stat.S_ISDIR(info.st_mode) or path == repo / relative,
                     "non-directory ancestor: " + relative)
    return path


def _read(repo, relative, limit=LIMIT):
    path = _path(repo, relative)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
                 "unsafe or oversized file: " + relative)
        data = b""
        while len(data) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(data)))
            if not block:
                break
            data += block
        after = os.fstat(fd)
        _require(len(data) <= limit and (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                 == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "file changed while reading")
        _require(path.lstat().st_ino == info.st_ino, "file replaced while reading")
        return data
    finally:
        os.close(fd)


def _write(repo, relative, data):
    from _lib import _atomic_text_write
    path = _path(repo, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    ancestor = path.parent
    while ancestor != repo:
        parent_fd = os.open(ancestor.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        ancestor = ancestor.parent
    _path(repo, relative)
    _atomic_text_write(str(path), data.decode("utf-8"))
    file_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(file_fd)
    finally:
        os.close(file_fd)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _legacy_spec(goal_id, task_id, value):
    import batch_state as batch
    _require(isinstance(value, dict) and set(value) == {"batch_id", "requests"}, "invalid legacy pack")
    requests = value["requests"]
    try:
        batch.requests_valid(requests)
    except batch.Refusal as exc:
        raise ValueError("Goal migration: invalid legacy intake: " + str(exc)) from exc
    _require(len(requests) >= 2 and all(set(item) <= {"slug", "request", "scopes", "depends_on", "submodules"}
             for item in requests), "invalid legacy intake")
    normalized = copy.deepcopy(batch.normalize_requests(requests))
    identity = json.dumps([goal_id, task_id], separators=(",", ":")).encode()
    expected = {"batch_id": "goal-" + hashlib.sha256(identity).hexdigest(), "requests": normalized}
    _require(value == expected and all("TASK__" + r["slug"] != task_id for r in normalized),
             "legacy pack identity or intake mismatch")
    return dict(task_id=task_id, **expected)


def _binding(repo, goal_id, task_id, spec, recoveries, *, require_pool):
    import work_pack
    _require(isinstance(goal_id, str) and IDENT.fullmatch(goal_id) and goal_id.startswith("GOAL__"),
             "invalid legacy Goal identity")
    result = _legacy_spec(goal_id, task_id, spec)
    route = work_pack.route(str(repo), task_id, result)
    recovery = dict(wbs_id="WBS__" + task_id[6:], **result,
                    next_action=route.get("next_action", "Resume the retained pool through batch_state."))
    if route.get("batch_state") == "missing" and require_pool:
        recovery["blocker"] = "Initialize this exact batch_id and requests through batch_state, then retry migration."
    recoveries.append(recovery)
    return result


def _collect(repo):
    from _lib import native_wbs_id, read_task_control
    sources, destinations, recoveries = {}, {}, []
    goals_path = _path(repo, GOALS)
    rows = {}
    if goals_path.exists():
        _require(goals_path.is_dir(), "invalid Goal directory")
        names = sorted(p.name for p in goals_path.iterdir())
        _require(len(names) <= MAX_RECORDS + 2, "too many Goal records")
        for name in names:
            _require(name == ".lock" or name == "current.json" or re.fullmatch(r"GOAL__[A-Za-z0-9_.-]{1,180}\.json", name),
                     "unknown Goal directory entry: " + name)
            relative = GOALS + "/" + name
            raw = _read(repo, relative)
            _require(raw is not None, "Goal record disappeared")
            if name == ".lock":
                _require(not raw, "unexpected Goal lock contents")
                sources[relative] = raw
                continue
            data = _json(raw)
            _require(isinstance(data, dict) and isinstance(data.get("goal_id"), str)
                     and IDENT.fullmatch(data["goal_id"]) and data["goal_id"].startswith("GOAL__"), "invalid Goal record")
            _require(isinstance(data.get("objective"), str) and data["objective"].strip()
                     and isinstance(data.get("tasks"), list), "invalid Goal objective/intake")
            _require(name == "current.json" or name == data["goal_id"] + ".json", "Goal filename identity mismatch")
            sources[relative] = raw
            if name != "current.json":
                rows[data["goal_id"]] = data
        current = sources.get(GOALS + "/current.json")
        if current is not None:
            value = _json(current)
            _require(sources.get(GOALS + "/" + value["goal_id"] + ".json") == current, "current/named Goal conflict")
    reserved = {}
    notes = {}
    def destination(relative, before, after):
        _require(len(after) <= LIMIT, "migration destination exceeds storage budget")
        _require(relative not in destinations or destinations[relative] == (before, after), "conflicting migration target")
        destinations[relative] = (before, after)
    for gid, row in sorted(rows.items()):
        wbs_id = native_wbs_id(row["objective"])
        target = "doc/harness/tasks/TASK__" + wbs_id[5:] + "/MIGRATION.md"
        notes.setdefault(target, []).append(row)
        children = set()
        for child in row["tasks"]:
            _require(isinstance(child, dict) and isinstance(child.get("task_id"), str)
                     and IDENT.fullmatch(child["task_id"]) and child["task_id"].startswith("TASK__"), "invalid child identity")
            tid = child["task_id"]
            _require(tid not in children, "duplicate child identity")
            children.add(tid)
            directory = "doc/harness/tasks/" + tid
            _require(not child.get("task_dir") or child["task_dir"] in (directory, str(repo / directory)), "noncanonical child directory")
            _path(repo, directory)
            if "batch" not in child:
                continue
            spec = _binding(repo, gid, tid, child["batch"], recoveries, require_pool=True)
            for identity in [tid] + ["TASK__" + item["slug"] for item in spec["requests"]]:
                _require(identity not in reserved, "duplicate legacy pack reservation")
                reserved[identity] = gid
            relative = directory + "/WBS.json"
            raw = _read(repo, relative)
            if raw is None:
                control = read_task_control(str(_path(repo, directory)))
                _require(_read(repo, directory + "/TASK.json") is None or bool(control), "invalid existing task control")
                value = dict(version=1, nodes=[], checks={}, run_id=(control or {}).get("run_id", ""),
                             started_at=0, last_progress_at=0, pack_only=True, batch=spec)
                destination(relative, None, _encode(value))
            else:
                value = _json(raw)
                _require(value.get("batch") == child["batch"] and value.get("batch_goal_id") == gid,
                         "existing WBS conflicts with legacy pack")
    for relative, records in sorted(notes.items()):
        before = _read(repo, relative)
        _require(before is None, "migration notes target already exists")
        text = ("# Imported historical Goal intake\n\n"
                "These records are unverified historical intent, not confirmed user requirements or completion evidence. "
                "They do not create executable work, a WBS baseline, or measured progress.\n\n```json\n"
                + json.dumps(records, ensure_ascii=False, sort_keys=True, indent=2) + "\n```\n")
        destination(relative, before, text.encode())
    tasks = _path(repo, "doc/harness/tasks")
    if tasks.exists():
        _require(tasks.is_dir(), "invalid tasks directory")
        entries = sorted(tasks.iterdir())
        _require(len(entries) <= 4096, "too many task directories")
        for directory in entries:
            if not directory.name.startswith("TASK__"):
                continue
            _require(IDENT.fullmatch(directory.name), "invalid task directory")
            if directory.is_symlink():
                continue  # Never traverse unrelated directories outside migration ownership.
            _path(repo, "doc/harness/tasks/" + directory.name)
            if not directory.is_dir():
                continue
            relative = "doc/harness/tasks/" + directory.name + "/WBS.json"
            raw = _read(repo, relative)
            if raw is None:
                continue
            value = _json(raw)
            _require(isinstance(value, dict), "invalid WBS state")
            from wbs import validate_state
            validate_state(value)
            changed = False
            _require(isinstance(value.get("batch_history", []), list), "invalid WBS batch history")
            containers = [value] + value.get("batch_history", [])
            _require(len(containers) <= 33,
                     "invalid WBS batch history")
            for container in containers:
                _require(isinstance(container, dict), "invalid WBS pack binding")
                if "batch_goal_id" in container:
                    container["batch"] = _binding(repo, container["batch_goal_id"], directory.name,
                                                 container.get("batch"), recoveries, require_pool=False)
                    del container["batch_goal_id"]
                    changed = True
            if changed:
                destination(relative, raw, _encode(value))
    _require(len(sources) + len(destinations) <= MAX_RECORDS * 4, "migration exceeds record budget")
    return sources, destinations, recoveries


def _report(sources, destinations, recoveries, status):
    return dict(status=status, sources=sorted(sources), destinations=sorted(destinations),
                recoveries=recoveries, blockers=[r for r in recoveries if "blocker" in r],
                next_action="Initialize blocked exact intakes, then retry migration." if any("blocker" in r for r in recoveries)
                else "Apply this explicit migration." if status == "preview" else "Use the WBS control plane.")


def _manifest(repo):
    raw = _read(repo, MANIFEST, 16 * LIMIT)
    if raw is None:
        return None
    data = _json(raw)
    _require(isinstance(data, dict) and data.get("version") == 1 and data.get("status") in {"prepared", "complete"}
             and isinstance(data.get("sources"), dict) and isinstance(data.get("destinations"), dict)
             and isinstance(data.get("recoveries"), list), "invalid migration manifest")
    _require(len(data["sources"]) + len(data["destinations"]) <= MAX_RECORDS * 4, "oversized migration manifest")
    for relative, record in data["sources"].items():
        _require(relative.startswith(GOALS + "/"), "invalid archived source")
        _require(isinstance(record, dict) and set(record) == {"sha256"}
                 and isinstance(record["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]), "invalid source manifest")
        original = _read(repo, ARCHIVE + "/original/" + relative)
        _require(original is not None and _hash(original) == record["sha256"], "archive changed or missing")
        current = _read(repo, relative)
        _require(current is None or _hash(current) == record["sha256"], "legacy source changed after preparation")
        if data["status"] == "complete":
            _require(current is None, "retired source recreated; explicit reconciliation required")
    for relative, record in data["destinations"].items():
        _require(re.fullmatch(r"doc/harness/tasks/TASK__[A-Za-z0-9_.-]{1,180}/(?:WBS.json|MIGRATION.md)", relative),
                 "invalid migration destination")
        _require(isinstance(record, dict) and set(record) == {"before", "after", "data"}
                 and isinstance(record["after"], str) and re.fullmatch(r"[0-9a-f]{64}", record["after"])
                 and (record["before"] is None or isinstance(record["before"], str)
                      and re.fullmatch(r"[0-9a-f]{64}", record["before"])), "invalid destination manifest")
        try:
            after = base64.b64decode(record["data"], validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("Goal migration: invalid archived destination") from exc
        _require(len(after) <= LIMIT and _hash(after) == record["after"], "destination archive changed")
        _require(_hash(_read(repo, relative)) in ({record["after"]} if data["status"] == "complete" else {record["before"], record["after"]}), "destination changed after preparation")
        original = _read(repo, ARCHIVE + "/original/" + relative)
        _require(_hash(original) == record["before"], "destination original archive changed")
    directory = _path(repo, GOALS)
    if directory.exists():
        _require({GOALS + "/" + p.name for p in directory.iterdir()} <= data["sources"].keys(),
                 "new legacy source after preparation")
    return data


def preview(repo_root):
    repo = Path(repo_root).resolve(strict=True)
    journal = _manifest(repo)
    if journal:
        return _report(journal["sources"], journal["destinations"], journal["recoveries"], journal["status"])
    return _report(*_collect(repo), "preview")


def apply(repo_root):
    from _lib import _trusted_control_writer
    _require(_trusted_control_writer(), "apply requires the WBS MCP owner")
    repo = Path(repo_root).resolve(strict=True)
    # Refuse all bad inputs before creating archive/lock state.
    initial = _manifest(repo)
    if initial is None:
        sources, destinations, recoveries = _collect(repo)
        report = _report(sources, destinations, recoveries, "preview")
        _require(not report["blockers"], "missing legacy pool; preview returns exact initialization intake")
        if not sources and not destinations:
            return dict(report, status="complete")
    directory = _path(repo, ARCHIVE)
    directory.mkdir(parents=True, exist_ok=True)
    lock = _path(repo, ARCHIVE + "/.lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    goal_fd = None
    try:
        _require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_nlink == 1, "unsafe migration lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        goals = _path(repo, GOALS)
        if goals.exists():
            goal_fd = os.open(goals, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(goal_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Goal migration: legacy writer active; retry after it exits") from exc
        journal = _manifest(repo)
        if journal is None:
            current = _collect(repo)
            _require(current == (sources, destinations, recoveries), "inputs changed before preparation")
            for relative, raw in sources.items():
                archive = ARCHIVE + "/original/" + relative
                existing = _read(repo, archive)
                _require(existing is None or existing == raw, "conflicting original archive")
                if existing is None:
                    _write(repo, archive, raw)
            for relative, (before, _) in destinations.items():
                archive = ARCHIVE + "/original/" + relative
                existing = _read(repo, archive)
                _require(existing is None or existing == before, "conflicting destination archive")
                if before is not None and existing is None:
                    _write(repo, archive, before)
            journal = dict(version=1, status="prepared", recoveries=recoveries,
                           sources={p: {"sha256": _hash(raw)} for p, raw in sources.items()},
                           destinations={p: dict(before=_hash(before), after=_hash(after),
                                                  data=base64.b64encode(after).decode())
                                         for p, (before, after) in destinations.items()})
            _require(len(_encode(journal)) <= 16 * LIMIT, "migration manifest exceeds budget")
            _write(repo, MANIFEST, _encode(journal))
        if journal["status"] == "complete":
            return _report(journal["sources"], journal["destinations"], journal["recoveries"], "complete")
        # Recheck the whole transaction before each stage; replay accepts only
        # recorded before/after hashes, never overwrites subsequent edits.
        _manifest(repo)
        for relative, record in journal["destinations"].items():
            _require(_hash(_read(repo, relative)) in {record["before"], record["after"]}, "destination changed")
            if _hash(_read(repo, relative)) != record["after"]:
                _write(repo, relative, base64.b64decode(record["data"], validate=True))
        _manifest(repo)
        for relative, record in journal["sources"].items():
            current = _read(repo, relative)
            if current is not None:
                _require(_hash(current) == record["sha256"], "source changed before retirement")
                _path(repo, relative).unlink()
        goals = _path(repo, GOALS)
        if goals.exists():
            goals.rmdir()
            fd_parent = os.open(goals.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd_parent)
            finally:
                os.close(fd_parent)
        journal["status"] = "complete"
        _write(repo, MANIFEST, _encode(journal))
        return _report(journal["sources"], journal["destinations"], journal["recoveries"], "complete")
    finally:
        if goal_fd is not None:
            os.close(goal_fd)
        os.close(fd)
