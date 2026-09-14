"""Task-local review detail storage stays bounded, selective, and non-authoritative."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugin" / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "review_detail_store_lib", SCRIPTS / "_lib.py",
)
assert SPEC and SPEC.loader
lib = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lib
SPEC.loader.exec_module(lib)


def _task(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    harness = root / "doc/harness"
    harness.mkdir(parents=True)
    (harness / "manifest.yaml").write_text("version: 5\ntype: test\n", encoding="utf-8")
    task = harness / "tasks/TASK__review-detail"
    task.mkdir(parents=True)
    (task / "TASK.json").write_text(json.dumps({
        "run_id": lib.new_uuid7(),
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "close_receipt_fingerprint": None,
    }) + "\n", encoding="utf-8")
    return root, task


def _run(script: str, root: Path, *args: str, input_bytes: bytes = b""):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=root,
        input=input_bytes,
        capture_output=True,
        check=False,
    )


def _integrity_failure(call):
    try:
        call()
    except RuntimeError as exc:
        assert "integrity" in str(exc)
    else:
        raise AssertionError("unsafe review detail store must fail closed")


def _formal_final(verdict: str, fix_now: int, investigate: int, detail: object) -> str:
    return (
        f"VERDICT: {verdict}\n"
        f"FINDING_COUNTS: FIX_NOW={fix_now} INVESTIGATE={investigate} OPTIONAL=0\n"
        "REVIEW_DETAIL: "
        + json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
    )


def test_structured_code_review_detail_is_cross_checked_against_envelope():
    finding = {
        "anchor": "plugin/example.py:7",
        "issue": "the empty branch returns the wrong value",
        "evidence": "calling parse([]) reaches the success return",
        "fix": "return the documented empty value before the success branch",
    }
    valid = (
        ("PASS", 0, 0, {"blocker": None, "findings": []}),
        ("FAIL", 1, 0, {"blocker": None, "findings": [finding]}),
        ("BLOCKED_ENV", 1, 1, {"blocker": "dependency unavailable", "findings": [finding]}),
    )
    for expected, fix_now, investigate, detail in valid:
        verdict, compact = lib.normalize_receipt_completion(
            "review-code", _formal_final(expected, fix_now, investigate, detail),
        )
        assert verdict == expected
        assert compact.splitlines()[0] == f"VERDICT: {expected}"

    contradictions = (
        _formal_final("PASS", 0, 0, {"blocker": None, "findings": [finding]}),
        _formal_final("FAIL", 2, 0, {"blocker": None, "findings": [finding]}),
        _formal_final("BLOCKED_ENV", 0, 2, {"blocker": "blocked", "findings": []}),
    )
    for final in contradictions:
        verdict, compact = lib.normalize_receipt_completion("review-code", final)
        assert verdict == "PENDING"
        assert compact.splitlines()[0] == "VERDICT: PENDING"


def test_structured_code_review_detail_is_strict_but_legacy_reviews_remain_valid():
    legacy = "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nclean"
    assert lib.normalize_receipt_completion("review-code", legacy)[0] == "PASS"

    malformed = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n"
        "REVIEW_DETAIL: "
    )
    extra = _formal_final(
        "PASS", 0, 0, {"blocker": None, "findings": [], "status": "approved"},
    )
    empty_field = _formal_final(
        "FAIL", 1, 0, {
            "blocker": None,
            "findings": [{"anchor": "", "issue": "x", "evidence": "y", "fix": "z"}],
        },
    )
    duplicate = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n"
        'REVIEW_DETAIL: {"blocker":null,"blocker":null,"findings":[]}'
    )
    indented = _formal_final("PASS", 0, 0, {"blocker": None, "findings": []}).replace(
        "\nREVIEW_DETAIL:", "\n REVIEW_DETAIL:", 1,
    )
    spaced_colon = _formal_final(
        "PASS", 0, 0, {"blocker": None, "findings": []},
    ).replace("\nREVIEW_DETAIL:", "\nREVIEW_DETAIL :", 1)
    wrong_case = _formal_final(
        "PASS", 0, 0, {"blocker": None, "findings": []},
    ).replace("\nREVIEW_DETAIL:", "\nreview_detail:", 1)
    delayed = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n"
        "narrative\n"
        'REVIEW_DETAIL: {"blocker":null,"findings":['
        '{"anchor":"a","issue":"i","evidence":"e","fix":"f"}]}'
    )
    repeated = _formal_final(
        "PASS", 0, 0, {"blocker": None, "findings": []},
    ) + '\nREVIEW_DETAIL: {"blocker":null,"findings":[]}'
    for final in (
        malformed, extra, empty_field, duplicate, indented, spaced_colon, wrong_case,
        delayed, repeated,
    ):
        assert lib.normalize_receipt_completion("review-code", final)[0] == "PENDING"

    # The extension is code-review specific; legacy security reports keep their
    # existing parser contract even if their narrative begins with this label.
    assert lib.normalize_receipt_completion("review-security", extra)[0] == "PASS"


def test_deeply_nested_structured_detail_normalizes_to_pending():
    nested = "[" * 10000 + "]" * 10000
    final = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n"
        f'REVIEW_DETAIL: {{"blocker":null,"findings":{nested}}}'
    )
    assert lib.normalize_receipt_completion("review-code", final)[0] == "PENDING"


def test_deeply_nested_store_row_is_an_integrity_error(tmp_path):
    _, task = _task(tmp_path)
    nested = "[" * 10000 + "]" * 10000
    store = task / lib.REVIEW_DETAILS_NAME
    store.write_text(
        '{"detail_sha256":"' + "0" * 64 + '","detail":' + nested + "}\n",
        encoding="utf-8",
    )
    store.chmod(0o600)
    _integrity_failure(lambda: lib.read_review_detail(task, "0" * 64))


def test_overlong_numeric_finding_count_normalizes_to_pending():
    digits = "9" * 5000
    final = (
        "VERDICT: FAIL\n"
        f"FINDING_COUNTS: FIX_NOW={digits} INVESTIGATE=0 OPTIONAL=0\n"
        'REVIEW_DETAIL: {"blocker":null,"findings":[]}'
    )
    verdict, compact = lib.normalize_receipt_completion("review-code", final)
    assert verdict == "PENDING"
    assert "FINDING_COUNTS: UNREADABLE FAIL" in compact


def test_defect_hunter_task_names_are_lifecycle_invisible():
    for name in (
        "defect_hunter_correctness_01a09ef9",
        "defect_hunter_contract_tests_01a09ef9",
        "harness:defect-hunter",
    ):
        assert lib._infer_receipt_lens(name) == ""


def test_content_addressed_round_trip_is_exact_idempotent_and_owner_only(tmp_path):
    _root, task = _task(tmp_path)
    detail = "VERDICT: PASS\n한글 evidence without a trailing newline"
    expected = hashlib.sha256(detail.encode("utf-8")).hexdigest()

    assert lib.append_review_detail(task, detail) == expected
    first_bytes = (task / lib.REVIEW_DETAILS_NAME).read_bytes()
    assert lib.append_review_detail(task, detail) == expected
    assert (task / lib.REVIEW_DETAILS_NAME).read_bytes() == first_bytes
    assert lib.read_review_detail(task, expected) == detail

    rows = (task / lib.REVIEW_DETAILS_NAME).read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert set(row) == {"detail_sha256", "detail"}
    assert row == {"detail_sha256": expected, "detail": detail}
    assert stat.S_IMODE((task / lib.REVIEW_DETAILS_NAME).stat().st_mode) == 0o600


def test_missing_detail_is_legacy_compatible_not_found(tmp_path):
    _root, task = _task(tmp_path)
    digest = "0" * 64
    try:
        lib.read_review_detail(task, digest)
    except FileNotFoundError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("missing legacy detail must report not found")
    assert not (task / lib.REVIEW_DETAILS_NAME).exists()


def test_standalone_writer_refuses_a_terminal_task(tmp_path):
    _root, task = _task(tmp_path)
    (task / "BLOCKED.md").write_text("blocked\n", encoding="utf-8")
    try:
        lib.append_review_detail(task, "late review detail")
    except RuntimeError as exc:
        assert "terminal" in str(exc)
    else:
        raise AssertionError("terminal task accepted new review detail")
    assert not (task / lib.REVIEW_DETAILS_NAME).exists()


def test_explicit_digest_read_still_works_after_task_is_terminal(tmp_path):
    root, task = _task(tmp_path)
    detail = "completed review detail"
    digest = lib.append_review_detail(task, detail)
    (task / "BLOCKED.md").write_text("blocked\n", encoding="utf-8")

    result = _run("review-read", root, "--task-dir", str(task), digest)
    assert result.returncode == 0
    assert result.stdout == detail.encode("utf-8")
    assert result.stderr == b""


def test_rejects_empty_oversized_and_store_limit_without_partial_append(tmp_path):
    _root, task = _task(tmp_path)
    for detail in ("", "x" * (lib._REVIEW_DETAIL_MAX_BYTES + 1)):
        try:
            lib.append_review_detail(task, detail)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid review detail size was accepted")
    assert not (task / lib.REVIEW_DETAILS_NAME).exists()

    lib.append_review_detail(task, "first")
    before = (task / lib.REVIEW_DETAILS_NAME).read_bytes()
    _digest, next_payload = lib._review_detail_payload("second")
    with mock.patch.object(
        lib, "_REVIEW_DETAIL_STREAM_MAX_BYTES", len(before) + len(next_payload) - 1,
    ):
        try:
            lib.append_review_detail(task, "second")
        except RuntimeError as exc:
            assert "16 MiB" in str(exc)
        else:
            raise AssertionError("full review detail store accepted another row")
    assert (task / lib.REVIEW_DETAILS_NAME).read_bytes() == before


def test_partial_write_is_rolled_back(tmp_path):
    _root, task = _task(tmp_path)
    real_write = lib.os.write
    calls = 0

    def fail_after_prefix(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, data[:5])
        raise OSError("simulated detail append failure")

    with mock.patch.object(lib.os, "write", side_effect=fail_after_prefix):
        _integrity_failure(lambda: lib.append_review_detail(task, "review final"))
    assert not (task / lib.REVIEW_DETAILS_NAME).exists()


def test_failed_first_creation_removes_an_unsafe_empty_store(tmp_path):
    _root, task = _task(tmp_path)
    previous_umask = os.umask(0o777)
    try:
        _integrity_failure(lambda: lib.append_review_detail(task, "review final"))
    finally:
        os.umask(previous_umask)

    assert not (task / lib.REVIEW_DETAILS_NAME).exists()
    digest = lib.append_review_detail(task, "review final")
    assert lib.read_review_detail(task, digest) == "review final"


def test_partial_second_write_preserves_existing_store_byte_for_byte(tmp_path):
    _root, task = _task(tmp_path)
    lib.append_review_detail(task, "first review")
    store = task / lib.REVIEW_DETAILS_NAME
    before = store.read_bytes()
    real_write = lib.os.write
    calls = 0

    def fail_after_prefix(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, data[:7])
        raise OSError("simulated second detail append failure")

    with mock.patch.object(lib.os, "write", side_effect=fail_after_prefix):
        _integrity_failure(lambda: lib.append_review_detail(task, "second review"))
    assert store.read_bytes() == before
    assert lib.read_review_detail(
        task, hashlib.sha256(b"first review").hexdigest(),
    ) == "first review"


def test_malformed_or_unsafe_store_never_exposes_detail(tmp_path):
    _root, task = _task(tmp_path)
    store = task / lib.REVIEW_DETAILS_NAME
    digest = hashlib.sha256(b"secret finding").hexdigest()
    malformed = (
        b'{"detail":"secret finding","detail_sha256":"' + digest.encode() + b'","extra":"x"}\n'
    )
    store.write_bytes(malformed)
    store.chmod(0o600)
    _integrity_failure(lambda: lib.read_review_detail(task, digest))
    _integrity_failure(lambda: lib.append_review_detail(task, "another"))

    store.write_text("{}\n", encoding="utf-8")
    store.chmod(0o644)
    _integrity_failure(lambda: lib.read_review_detail(task, digest))


def test_reader_validates_rows_after_the_selected_digest_before_returning(tmp_path):
    _root, task = _task(tmp_path)
    detail = "selected finding"
    digest = lib.append_review_detail(task, detail)
    store = task / lib.REVIEW_DETAILS_NAME
    with store.open("ab") as handle:
        handle.write(b"{}\n")
    _integrity_failure(lambda: lib.read_review_detail(task, digest))


def test_same_digest_with_different_body_is_an_integrity_error(tmp_path):
    _root, task = _task(tmp_path)

    class FixedDigest:
        def __init__(self, _raw=b""):
            pass

        def hexdigest(self):
            return "a" * 64

    with mock.patch.object(lib.hashlib, "sha256", side_effect=FixedDigest):
        assert lib.append_review_detail(task, "first body") == "a" * 64
        before = (task / lib.REVIEW_DETAILS_NAME).read_bytes()
        _integrity_failure(lambda: lib.append_review_detail(task, "different body"))
    assert (task / lib.REVIEW_DETAILS_NAME).read_bytes() == before


def test_symlink_and_hardlink_stores_fail_closed(tmp_path):
    _root, task = _task(tmp_path)
    store = task / lib.REVIEW_DETAILS_NAME
    external = tmp_path / "external.jsonl"
    external.write_text("{}\n", encoding="utf-8")
    external.chmod(0o600)

    store.symlink_to(external)
    _integrity_failure(lambda: lib.append_review_detail(task, "secret"))
    store.unlink()

    os.link(external, store)
    _integrity_failure(lambda: lib.append_review_detail(task, "secret"))


def test_fifo_store_fails_closed_without_waiting_for_a_peer(tmp_path):
    _root, task = _task(tmp_path)
    os.mkfifo(task / lib.REVIEW_DETAILS_NAME, mode=0o600)
    _integrity_failure(lambda: lib.append_review_detail(task, "secret"))


def test_cli_writes_from_stdin_and_reads_only_one_exact_digest(tmp_path):
    root, task = _task(tmp_path)
    first = b"VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nfirst"
    second = b"VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0\nsecond"

    logged = _run("review-log", root, "--task-dir", str(task), input_bytes=first)
    assert logged.returncode == 0, logged.stderr.decode()
    first_digest = hashlib.sha256(first).hexdigest()
    assert logged.stdout == f"DETAIL_SHA256:{first_digest}\n".encode()
    assert logged.stderr == b""
    other_digest = lib.append_review_detail(task, second.decode())

    read = _run("review-read", root, "--task-dir", str(task), first_digest)
    assert read.returncode == 0
    assert read.stdout == first
    assert read.stderr == b""
    assert other_digest.encode() not in read.stdout
    assert second not in read.stdout


def test_cli_round_trips_the_exact_two_mib_detail_boundary(tmp_path):
    root, task = _task(tmp_path)
    detail = b"x" * lib._REVIEW_DETAIL_MAX_BYTES
    digest = hashlib.sha256(detail).hexdigest()

    logged = _run("review-log", root, "--task-dir", str(task), input_bytes=detail)
    assert logged.returncode == 0, logged.stderr.decode()
    assert logged.stdout == f"DETAIL_SHA256:{digest}\n".encode()

    read = _run("review-read", root, "--task-dir", str(task), digest)
    assert read.returncode == 0
    assert read.stdout == detail
    assert read.stderr == b""


def test_cli_active_task_and_error_contract(tmp_path):
    root, task = _task(tmp_path)
    (task.parent / ".active").write_text(str(task) + "\n", encoding="utf-8")
    detail = b"formal review"
    digest = hashlib.sha256(detail).hexdigest()

    logged = _run("review-log", root, input_bytes=detail)
    assert logged.returncode == 0
    assert logged.stdout == f"DETAIL_SHA256:{digest}\n".encode()
    assert _run("review-read", root, digest).stdout == detail

    empty = _run("review-log", root)
    assert empty.returncode == 2
    assert empty.stdout == b""
    assert b"must not be empty" in empty.stderr

    invalid_utf8 = _run("review-log", root, input_bytes=b"\xff")
    assert invalid_utf8.returncode == 2
    assert invalid_utf8.stdout == b""
    assert b"valid UTF-8" in invalid_utf8.stderr

    invalid = _run("review-read", root, "DETAIL_SHA256:" + digest)
    assert invalid.returncode == 2
    assert invalid.stdout == b""
    assert b"64 lowercase" in invalid.stderr

    missing = _run("review-read", root, "0" * 64)
    assert missing.returncode == 3
    assert missing.stdout == b""
    assert b"may predate stored detail" in missing.stderr

    bulk = _run("review-read", root, "--all", digest)
    assert bulk.returncode == 2
    assert bulk.stdout == b""


def test_cli_integrity_error_does_not_echo_stored_detail(tmp_path):
    root, task = _task(tmp_path)
    secret = "do not echo this finding"
    digest = lib.append_review_detail(task, secret)
    (task / lib.REVIEW_DETAILS_NAME).chmod(0o644)

    result = _run("review-read", root, "--task-dir", str(task), digest)
    assert result.returncode == 4
    assert result.stdout == b""
    assert secret.encode() not in result.stderr
    assert b"integrity unavailable" in result.stderr
