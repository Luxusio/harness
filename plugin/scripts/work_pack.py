"""Task-owned work-pack proof for the existing isolated batch pool.

The spec is intent, not close evidence. Batch state and preserved task evidence
must still agree before the owning WBS execution may run or finish.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re

import batch_state as batch


def _spec(task_id: str, requests: list, *, batch_id=None) -> dict:
    if not isinstance(task_id, str) or not re.fullmatch(r"TASK__[A-Za-z0-9_.-]{1,180}", task_id):
        raise ValueError("owning WBS execution identity required")
    batch.requests_valid(requests)
    batch.require(len(requests) >= 2, "work-pack batch requires at least two requests")
    batch.require(all(set(item) <= {"slug", "request", "scopes", "depends_on", "submodules"}
                      for item in requests), "unknown work-pack batch request field")
    normalized = copy.deepcopy(batch.normalize_requests(requests))
    batch.require(len(json.dumps(normalized).encode()) <= batch.LIMIT, "work-pack batch intake exceeds 1 MiB")
    if any("TASK__" + item["slug"] == task_id for item in normalized):
        raise ValueError("integration child cannot also be a batch lead")
    identity = json.dumps([task_id, normalized], sort_keys=True, separators=(",", ":")).encode()
    if batch_id is None:
        batch_id = "wbs-" + hashlib.sha256(identity).hexdigest()
    batch.require(isinstance(batch_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", batch_id),
                  "invalid work-pack pool identity")
    return {"task_id": task_id, "batch_id": batch_id, "requests": normalized}


def make_spec(repo_root: str, task_id: str, requests: list) -> dict:
    """Validate new intake without reserving, opening tasks, or writing state."""
    try:
        spec = _spec(task_id, requests)
        repo = os.path.realpath(repo_root)
        directory = os.path.join(repo, "doc/harness/runtime/batches")
        records = {}
        ancestor = directory
        while not os.path.lexists(ancestor):
            ancestor = os.path.dirname(ancestor)
        batch.parents(ancestor)
        if os.path.lexists(directory):
            records = batch.load_all(directory, repo)
        existing = records.get(spec["batch_id"])
        if existing is not None:
            batch.require(existing.get("owner_task_id") == task_id, "work-pack pool owner mismatch")
            batch.require(batch.normalize_requests(existing["requests"]) == spec["requests"],
                          "work-pack batch intake differs from existing pool")
        else:
            batch.require(len(records) < batch.MAX_BATCHES, "batch history limit reached")
            batch.require_unused_task_ids(repo, spec["requests"], records)
        effective_requests = existing['requests'] if existing is not None else spec['requests']
        admission = batch.ordinary_admission_scopes(repo, effective_requests, managed=True)
        for item in effective_requests:
            # Preserve immutable intake; validate current effective ownership.
            batch.preflight(repo, copy.deepcopy(item), dirty_scopes=admission)
        return spec
    except (batch.Refusal, OSError) as exc:
        raise ValueError("work-pack batch intake refused: " + str(exc)) from exc


def _integrated_proof(repo: str, data: dict, *, measure=True) -> None:
    ref = data["destination_ref"]
    current_ref = batch.finish_helper._git(repo, "symbolic-ref", "-q", "HEAD").stdout.strip()
    batch.require(current_ref == ref, "work-pack batch destination branch changed")
    commits = [data["initial_head"]]
    for item in data["requests"]:
        batch.disposal_complete(repo, item)
        checkpoint = item.get("checkpoint", {})
        tip = checkpoint.get("integrated_tip")
        archive = os.path.join(repo, "doc/harness/archive/batch", item["task_id"])
        batch.require(checkpoint.get("stage") == "harvested"
                      and isinstance(tip, str) and batch.finish_helper.COMMIT_RE.fullmatch(tip)
                      and checkpoint.get("branch_tip") == tip,
                      "work-pack batch integration checkpoint missing")
        harvest = checkpoint.get("harvest")
        batch.require(isinstance(harvest, dict) and harvest.get("archived") == archive,
                      "work-pack batch archive identity mismatch")
        batch.parents(archive)
        batch.require(isinstance(checkpoint.get("archive_fingerprint"), str)
                      and re.fullmatch(r"sha256:[0-9a-f]{64}", checkpoint["archive_fingerprint"]),
                      "work-pack batch archive fingerprint missing")
        if measure:
            batch.require(batch.fingerprint(archive) == checkpoint["archive_fingerprint"],
                          "work-pack batch archive fingerprint changed")
        batch.control(item, base=archive, closed=True)
        if item.get("submodule_manifest"):
            batch.batch_submodules.preserved_proof(repo, item["submodule_manifest"])
        commits.append(tip)
    for commit in commits:
        batch.require(batch.finish_helper._git(
            repo, "merge-base", "--is-ancestor", commit, ref,
        ).returncode == 0, "work-pack batch commit is missing from destination ancestry")


def route(repo_root: str, task_id: str, spec: dict, *, measure=True) -> dict:
    """Return batch dispatch/resume or integration routing; refuse corrupt proof.

With measure=False this is stored/advisory routing: archive contents are not
fingerprinted and the result cannot authorize completion. Ownership, archive
presence, closed control, disposal and destination ancestry are still checked.
No state is created here. A missing batch is initialized through batch_state;
an existing pool keeps its reservation, recovery, and cleanup ownership.
"""
    try:
        batch.require(isinstance(spec, dict), "invalid work-pack batch spec")
        expected = _spec(task_id, spec.get("requests"), batch_id=spec.get("batch_id"))
        batch.require(spec == expected, "work-pack batch spec identity mismatch")
        repo = os.path.realpath(repo_root)
        directory = os.path.join(repo, "doc/harness/runtime/batches")
        path = os.path.join(directory, expected["batch_id"] + ".json")
        result = {**copy.deepcopy(expected), "route": "batch", "batch_state": "missing",
                  "next_action": "Initialize this exact batch intake with --owner-task-id " + task_id + ", then dispatch ready reservations through batch_state."}
        if not os.path.lexists(directory):
            # Validate existing ancestors even when this is the first pool.
            existing = directory
            while not os.path.lexists(existing):
                existing = os.path.dirname(existing)
            batch.parents(existing)
            return result
        batch.parents(directory)
        if not os.path.lexists(path):
            return result
        data = batch.validate(batch.read_json(path), repo, expected["batch_id"])
        return inspect_pool(repo, task_id, expected, data, measure=measure)
    except (batch.Refusal, OSError, KeyError, TypeError) as exc:
        raise ValueError("work-pack batch proof refused: " + str(exc)) from exc


def inspect_pool(repo, task_id, expected, data, *, measure=True):
    """Validate a neutral owner-bound pool, including staged importer metadata."""
    data = batch.validate(data, repo, expected["batch_id"])
    batch.require(data.get("owner_task_id") == task_id, "work-pack pool owner mismatch")
    result = {**copy.deepcopy(expected), "route": "batch", "proof_fresh": bool(measure)}
    batch.require(batch.normalize_requests(data["requests"]) == expected["requests"],
                  "work-pack batch intake differs from existing pool")
    result["batch_state"] = data["status"]
    unfinished = {item["slug"]: item["status"] for item in data["requests"]
                  if item["status"] != "integrated"}
    if data["halted"] or unfinished:
        result["unfinished"] = unfinished
        if data["halted"] or any(status in {"integrating", "recovery-required", "kept"}
                                 for status in unfinished.values()):
            result["next_action"] = "Recover the retained batch integration through batch_state before dispatch or integration review."
        elif any(status in {"abandoned", "blocked", "failed"} for status in unfinished.values()):
            result["next_action"] = "Resolve unfinished batch work through its existing recovery or disposition workflow; this pack cannot complete the WBS."
        else:
            result["next_action"] = "Resume this exact batch, dispatch ready reservations, and integrate returned leads through batch_state."
        return result
    _integrated_proof(repo, data, measure=measure)
    if data["status"] != "closed":
        result["next_action"] = "Close the fully integrated batch through batch_state, then start the owning WBS execution."
        return result
    result.update(route="integration", next_action="Start or resume the owning WBS execution; independently review and verify the integrated result before closing it.")
    return result


def require_integrated(repo_root: str, task_id: str, spec: dict) -> None:
    """Guard WBS completion in addition to its canonical child close checks."""
    result = route(repo_root, task_id, spec)
    if result["route"] != "integration":
        raise ValueError("work-pack batch is unfinished: " + result["next_action"])
