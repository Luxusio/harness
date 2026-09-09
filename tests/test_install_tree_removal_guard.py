"""The install-tree removal guard must detect removals and stay silent otherwise.

`tests/conftest.py::install_trees_lose_no_files` runs on every invocation of
this suite. Two properties matter, and the second is the load-bearing one:

  * a run that deletes a file from an install tree fails, and
  * a run that only *adds* files stays green.

Live session hooks legitimately write into `~/.claude/harness-dev` while the
suite runs, so a content- or mtime-based comparison reports a change on a tree
nobody damaged. A guard that fires on normal activity gets switched off, and a
switched-off guard is worse than no guard at all.

The end-to-end cases run a nested pytest whose conftest re-exports the real
fixture object with `_INSTALL_TREE_ROOTS` repointed at a stand-in tree. Testing
the helpers alone would not show that a session-scoped teardown assertion
actually turns the run red — under xdist it fires inside a worker, which is
exactly the arrangement the real guard runs in.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import conftest

REPO_ROOT = Path(conftest.REPO_ROOT)
CONFTEST = REPO_ROOT / "tests" / "conftest.py"
VERIFICATION_GATE = REPO_ROOT / "plugin" / "skills" / "develop" / "verification-gate.md"


def _stand_in_tree(root: Path) -> Path:
    """An install-shaped tree: real files, plus bytecode the guard ignores."""
    scripts = root / "plugin" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "background_hook.py").write_text("x = 1\n", encoding="utf-8")
    (scripts / "_lib.py").write_text("y = 2\n", encoding="utf-8")
    (scripts / "__pycache__").mkdir()
    (scripts / "__pycache__" / "_lib.cpython-312.pyc").write_bytes(b"\x00\x01")
    return scripts


def test_inventory_reports_files_and_skips_bytecode(tmp_path):
    scripts = _stand_in_tree(tmp_path / "tree")
    inventory = conftest._install_tree_inventory(str(tmp_path / "tree"))
    assert set(inventory) == {
        str(scripts / "background_hook.py"),
        str(scripts / "_lib.py"),
    }
    assert inventory[str(scripts / "_lib.py")] == 6


def test_an_absent_tree_inventories_empty(tmp_path):
    """Fresh machine / CI: degrade cleanly, never raise."""
    assert conftest._install_tree_inventory(str(tmp_path / "nope")) == {}
    assert conftest._install_tree_removals({str(tmp_path / "nope"): {}}) == []


def test_removals_are_reported_and_additions_are_not(tmp_path):
    root = tmp_path / "tree"
    scripts = _stand_in_tree(root)
    before = {str(root): conftest._install_tree_inventory(str(root))}

    # Everything a live run legitimately does: new files, rewritten bytecode.
    (scripts / "__pycache__" / "background_hook.cpython-312.pyc").write_bytes(b"\x02")
    (scripts / "new_script.py").write_text("z = 3\n", encoding="utf-8")
    (scripts / "_lib.py").write_text("y = 2  # touched\n", encoding="utf-8")
    assert conftest._install_tree_removals(before) == []

    (scripts / "_lib.py").unlink()
    removals = conftest._install_tree_removals(before)
    assert removals == [f"{scripts / '_lib.py'} (6 bytes)"], removals


def _nested_run(tmp_path: Path, body: str, *, xdist: bool) -> subprocess.CompletedProcess:
    """Run the real fixture in a nested pytest session over a stand-in tree."""
    tree = tmp_path / "tree"
    scripts = _stand_in_tree(tree)
    project = tmp_path / "project"
    project.mkdir()
    (project / "conftest.py").write_text(
        textwrap.dedent(
            f"""
            import importlib.util, sys

            spec = importlib.util.spec_from_file_location(
                "harness_real_conftest", {str(CONFTEST)!r},
            )
            real = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = real
            spec.loader.exec_module(real)
            real._INSTALL_TREE_ROOTS = [{str(tree)!r}]

            # The fixture object under test, adopted by this nested session.
            install_trees_lose_no_files = real.install_trees_lose_no_files
            """
        ),
        encoding="utf-8",
    )
    (project / "test_body.py").write_text(
        "from pathlib import Path\n\n"
        f"SCRIPTS = Path({str(scripts)!r})\n\n"
        "def test_body():\n"
        + textwrap.indent(textwrap.dedent(body).strip() + "\n", "    "),
        encoding="utf-8",
    )
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(project)]
    if xdist:
        args += ["-n", "2", "--dist", "worksteal"]
    return subprocess.run(args, capture_output=True, text=True, cwd=project, timeout=180)


def test_a_run_that_deletes_an_install_file_goes_red(tmp_path):
    """Detection, through the real fixture, in the arrangement it runs in."""
    result = _nested_run(tmp_path, "(SCRIPTS / '_lib.py').unlink()", xdist=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "removed 1 file(s)" in result.stdout + result.stderr
    assert "_lib.py (6 bytes)" in result.stdout + result.stderr


def test_a_run_that_only_adds_files_stays_green(tmp_path):
    """No false alarm — the property that keeps this guard switched on.

    Serial: nothing here asserts in teardown, so the xdist worker plumbing the
    deletion case exercises adds only start-up cost.
    """
    result = _nested_run(
        tmp_path,
        "(SCRIPTS / '__pycache__' / 'x.pyc').write_bytes(b'\\x09')\n"
        "(SCRIPTS / 'added.py').write_text('a = 1\\n')\n"
        "(SCRIPTS / '_lib.py').write_text('y = 22222\\n')\n",
        xdist=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_codex_plugin_cache_entry_is_watched():
    """Codex loads the cache entry, so the guard has to see it.

    `install.py` registers Codex hooks with absolute commands into
    `~/.codex/plugins/cache/harness/harness/<version>` and prunes bytecode
    inside it. A `rmtree(cache.parent)` mutation there destroys the running
    runtime while `~/.codex/harness` and `~/.claude/harness-dev` stay intact —
    invisible to a guard that watches only those two.
    """
    import pwd

    home = pwd.getpwuid(os.getuid()).pw_dir
    roots = conftest._default_install_tree_roots()
    assert os.path.join(home, ".codex", "plugins", "cache", "harness", "harness") in roots
    # Sibling marketplaces are other tools' trees, not the harness runtime.
    assert os.path.join(home, ".codex", "plugins", "cache") not in roots


def _step_0_5_snippet() -> str:
    """The bash block review and QA are told to run outside pytest."""
    section = VERIFICATION_GATE.read_text(encoding="utf-8")
    section = section.split("## Step 0.5:", 1)[1].split("\n## Step 1:", 1)[0]
    return section.split("```bash", 1)[1].split("```", 1)[0]


def test_the_documented_snapshot_matches_the_fixture(tmp_path):
    """AC-4's manual procedure must agree with the fixture it mirrors.

    The manual snapshot is the only coverage for runs the fixture cannot see —
    a `SIGKILL`ed session, a mutation experiment outside pytest — so it is the
    one place a false alarm cannot be caught by anything else. Its first draft
    emitted `%p %s` and included `__pycache__`, which reported "removals" after
    every rewritten file and after the `python3 install.py --force` repair the
    same paragraph prescribes.

    Executed, not pattern-matched: the block is lifted from the doc and run.
    """
    home = tmp_path / "home"
    documented = set(re.findall(r'"\$HOME/([^"]+)"', _step_0_5_snippet()))
    real_home = __import__("pwd").getpwuid(os.getuid()).pw_dir
    assert documented == {
        os.path.relpath(root, real_home)
        for root in conftest._default_install_tree_roots()
    }

    tree = home / next(iter(sorted(documented)))
    scripts = tree / "plugin" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "_lib.py").write_text("y = 2\n", encoding="utf-8")
    (scripts / "__pycache__").mkdir()
    (scripts / "__pycache__" / "_lib.cpython-312.pyc").write_bytes(b"\x00\x01")

    def run(mutation: str) -> str:
        script = _step_0_5_snippet().replace("<verification commands>", mutation)
        script = script.replace("/tmp/install-", f"{tmp_path}/install-")
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True,
            env={**os.environ, "HOME": str(home)}, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    # Everything a normal run does: rewrite to a different size, add a file,
    # and prune the bytecode caches exactly as `install.py --force` does.
    assert run(
        f"printf 'y = 22222\\n' > {scripts / '_lib.py'}\n"
        f"printf 'a = 1\\n' > {scripts / 'added.py'}\n"
        f"rm -rf {scripts / '__pycache__'}\n"
    ) == ""

    assert str(scripts / "_lib.py") in run(f"rm {scripts / '_lib.py'}")

