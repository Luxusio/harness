from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = REPO_ROOT / "plugin" / "scripts" / "verify_runner.py"

spec = importlib.util.spec_from_file_location("verify_runner", RUNNER_PATH)
assert spec and spec.loader
verify_runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_runner)


def test_parallel_runner_preserves_manifest_order_and_passes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".git").mkdir()
        payload = verify_runner.run(
            [
                "python3 -c 'print(\"first\")'",
                "python3 -c 'print(\"second\")'",
            ],
            root,
            parallel=True,
            max_workers=2,
            timeout=5,
        )
    assert payload["returncode"] == 0
    assert payload["status"] == "PASS"
    assert [r["index"] for r in payload["commands"]] == [0, 1]
    assert "first" in payload["commands"][0]["stdout"]
    assert "second" in payload["commands"][1]["stdout"]


def test_parallel_runner_aggregates_failure():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".git").mkdir()
        payload = verify_runner.run(
            [
                "python3 -c 'print(\"ok\")'",
                "python3 -c 'raise SystemExit(7)'",
            ],
            root,
            parallel=True,
            max_workers=2,
            timeout=5,
        )
    assert payload["returncode"] == 1
    assert payload["status"] == "FAIL"
    assert payload["commands"][0]["returncode"] == 0
    assert payload["commands"][1]["returncode"] == 7
