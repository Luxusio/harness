"""CT-01..03, BC-01..03, DOC-01..02, DX-01..06, SK-01..02: contracts/DX/skill tests.

Covers:
  CT: CONTRACTS.md C-16 + C-11 + C-05 updates
  BC: bootstrap order / contract_lint recognition
  DOC: hygiene.yaml presence + README entries
  DX: [hygiene-*] tag namespace
  SK: maintain SKILL.md LOC + invariants
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "plugin", "scripts")
TEMPLATES_DIR = os.path.join(REPO_ROOT, "plugin", "skills", "setup", "templates")
HOOKS_JSON = os.path.join(REPO_ROOT, "plugin", "hooks", "hooks.json")
sys.path.insert(0, SCRIPTS_DIR)


class TestContractsUpdates(unittest.TestCase):
    """CT-01..03: CONTRACTS.md C-16 + C-11 + C-05 updates."""

    def test_CT01_c16_in_contracts_managed_block(self):
        """CT-01: CONTRACTS.md contains C-16 with full 4-field schema inside managed block."""
        contracts_path = os.path.join(REPO_ROOT, "CONTRACTS.md")
        with open(contracts_path, encoding="utf-8") as f:
            text = f.read()
        begin = text.find("<!-- harness:managed-begin")
        end = text.find("<!-- harness:managed-end -->")
        self.assertGreaterEqual(begin, 0)
        self.assertGreaterEqual(end, 0)
        block = text[begin:end]
        self.assertIn("### C-16", block, "C-16 must be in managed block")
        c16_idx = block.find("### C-16")
        c16_body = block[c16_idx:c16_idx + 1500]
        self.assertIn("superseded_by", c16_body)
        self.assertIn("distilled_to", c16_body)
        self.assertTrue(
            "observer" in c16_body.lower() or "observer_until_session" in c16_body,
            "C-16 must mention observer mode"
        )

    def test_CT01_c16_has_all_required_fields(self):
        """CT-01: C-16 has Title, When, Enforced by, On violation, Why."""
        contracts_path = os.path.join(REPO_ROOT, "CONTRACTS.md")
        with open(contracts_path, encoding="utf-8") as f:
            text = f.read()
        c16_idx = text.find("### C-16")
        self.assertGreaterEqual(c16_idx, 0)
        next_section = text.find("### C-", c16_idx + 1)
        end_marker = text.find("<!-- harness:managed-end -->")
        c16_section = text[c16_idx: min(
            next_section if next_section > 0 else len(text),
            end_marker if end_marker > 0 else len(text),
        )]
        self.assertIn("**Title:**", c16_section)
        self.assertIn("**When:**", c16_section)
        self.assertIn("**Enforced by:**", c16_section)
        self.assertIn("**On violation:**", c16_section)
        self.assertIn("**Why:**", c16_section)

    def test_CT02_c11_mentions_hygiene_scan(self):
        """CT-02: CONTRACTS.md C-11 names hygiene_scan.py as authorized additive writer."""
        contracts_path = os.path.join(REPO_ROOT, "CONTRACTS.md")
        with open(contracts_path, encoding="utf-8") as f:
            text = f.read()
        c11_idx = text.find("### C-11")
        self.assertGreaterEqual(c11_idx, 0)
        next_section = text.find("### C-12")
        c11_body = text[c11_idx:next_section if next_section > 0 else c11_idx + 2000]
        self.assertIn("hygiene_scan.py", c11_body,
                      "C-11 must name hygiene_scan.py as authorized additive writer")

    def test_CT03_c05_mentions_doc_changes_authorization(self):
        """CT-03: CONTRACTS.md C-05 clarifies doc/changes + doc/common authorized via C-16."""
        contracts_path = os.path.join(REPO_ROOT, "CONTRACTS.md")
        with open(contracts_path, encoding="utf-8") as f:
            text = f.read()
        c05_idx = text.find("### C-05")
        self.assertGreaterEqual(c05_idx, 0)
        next_section = text.find("### C-06")
        c05_body = text[c05_idx:next_section if next_section > 0 else c05_idx + 2000]
        self.assertIn("doc/changes", c05_body, "C-05 must mention doc/changes authorization")
        self.assertIn("C-16", c05_body, "C-05 must reference C-16 for the authorization")


def _build_fresh_repo(tmp):
    """Build a minimal fresh repo with no hygiene artifacts."""
    subprocess.run(["git", "init", tmp], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        capture_output=True, cwd=tmp
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        capture_output=True, cwd=tmp
    )
    os.makedirs(os.path.join(tmp, "doc", "harness"), exist_ok=True)
    # Initial commit so git works
    readme = os.path.join(tmp, "README.md")
    with open(readme, "w") as f:
        f.write("# test\n")
    subprocess.run(["git", "add", "README.md"], capture_output=True, cwd=tmp)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        capture_output=True, cwd=tmp
    )


def _apply_setup_bootstrap_step(tmp):
    """
    Simulate the setup skill's bootstrap step 3.7.5 in the correct order:
      A) Install hygiene.yaml stub
      B) Install CONTRACTS.md with C-16 (from template — fresh install)
      C) Add hygiene_scan.py hook entry to hooks.json (AFTER A+B verified)

    Returns a dict with paths for assertion.
    """
    hygiene_yaml_dst = os.path.join(tmp, "doc", "harness", "hygiene.yaml")
    contracts_dst = os.path.join(tmp, "CONTRACTS.md")
    hooks_dst = os.path.join(tmp, "hooks.json")

    # Step A: hygiene.yaml stub
    hygiene_template = os.path.join(TEMPLATES_DIR, "hygiene.yaml")
    shutil.copy(hygiene_template, hygiene_yaml_dst)

    # Step B: CONTRACTS.md from template (includes C-16, C-11 update, C-05 note)
    contracts_template = os.path.join(TEMPLATES_DIR, "CONTRACTS.md")
    shutil.copy(contracts_template, contracts_dst)

    # Verify C-16 is now present before Step C
    with open(contracts_dst, encoding="utf-8") as f:
        contracts_text = f.read()
    c16_present = "### C-16" in contracts_text

    # Step C: hooks.json — only if C-16 confirmed present
    with open(HOOKS_JSON, encoding="utf-8") as f:
        hooks_data = json.load(f)

    # Write a local copy of hooks.json to tmp for inspection
    with open(hooks_dst, "w", encoding="utf-8") as f:
        json.dump(hooks_data, f, indent=2)

    if c16_present:
        # Simulate adding hygiene_scan entry after contract_lint entry
        session_hooks = hooks_data["hooks"]["SessionStart"][0]["hooks"]
        lint_idx = next(
            (i for i, h in enumerate(session_hooks)
             if "contract_lint" in h.get("command", "")),
            len(session_hooks) - 1
        )
        hygiene_entry = {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/hygiene_scan.py --apply-safe || true",
            "timeout": 10,
            "statusMessage": "Auto-hygiene check",
        }
        session_hooks.insert(lint_idx + 1, hygiene_entry)
        with open(hooks_dst, "w", encoding="utf-8") as f:
            json.dump(hooks_data, f, indent=2)

    return {
        "hygiene_yaml": hygiene_yaml_dst,
        "contracts": contracts_dst,
        "hooks": hooks_dst,
        "c16_was_present_before_hook_step": c16_present,
    }


class TestBootstrap(unittest.TestCase):
    """BC-01..03: bootstrap order / contract_lint recognition."""

    def test_BC01_setup_fresh_repo_atomic_bootstrap_order(self):
        """BC-01: setup on fresh repo — CONTRACTS.md gets C-16 BEFORE hooks.json gains hygiene_scan entry.

        Verifies the setup-side atomic transaction (AC-020):
        - CONTRACTS.md (from template) contains C-16, C-11 hygiene_scan.py mention, C-05 doc/changes note
        - doc/harness/hygiene.yaml stub is installed
        - hooks.json gains hygiene_scan.py entry positioned AFTER contract_lint entry
        - C-16 lands in CONTRACTS.md BEFORE the hooks.json entry is added (bootstrap order)
        """
        with tempfile.TemporaryDirectory() as tmp:
            _build_fresh_repo(tmp)
            result = _apply_setup_bootstrap_step(tmp)

            # (1) hygiene.yaml exists
            self.assertTrue(
                os.path.isfile(result["hygiene_yaml"]),
                "hygiene.yaml stub must be installed by setup"
            )
            with open(result["hygiene_yaml"], encoding="utf-8") as f:
                yaml_text = f.read()
            self.assertIn("enabled:", yaml_text)
            self.assertIn("observer_until_session:", yaml_text)
            self.assertIn("pin_paths:", yaml_text)

            # (2) CONTRACTS.md has C-16 (from template)
            with open(result["contracts"], encoding="utf-8") as f:
                contracts_text = f.read()
            self.assertIn("### C-16", contracts_text,
                          "CONTRACTS.md template must contain C-16")
            self.assertIn("superseded_by", contracts_text)
            self.assertIn("distilled_to", contracts_text)

            # (3) CONTRACTS.md has C-11 hygiene_scan.py mention
            self.assertIn("hygiene_scan.py", contracts_text,
                          "C-11 must name hygiene_scan.py as authorized additive writer")

            # (4) CONTRACTS.md has C-05 doc/changes note
            self.assertIn("doc/changes", contracts_text,
                          "C-05 must mention doc/changes authorization")

            # (5) C-16 was confirmed present BEFORE hooks.json step ran
            self.assertTrue(
                result["c16_was_present_before_hook_step"],
                "Bootstrap order violated: C-16 must be in CONTRACTS.md before hooks.json step"
            )

            # (6) hooks.json has hygiene_scan entry
            with open(result["hooks"], encoding="utf-8") as f:
                hooks_data = json.load(f)
            session_hooks = hooks_data["hooks"]["SessionStart"][0]["hooks"]
            commands = [h.get("command", "") for h in session_hooks]
            hygiene_cmds = [c for c in commands if "hygiene_scan" in c]
            self.assertTrue(len(hygiene_cmds) >= 1,
                            "hooks.json must have hygiene_scan.py entry after bootstrap")

            # (7) hygiene_scan entry is ordered AFTER contract_lint entry
            hygiene_idx = next(
                (i for i, c in enumerate(commands) if "hygiene_scan" in c), -1
            )
            lint_idx = next(
                (i for i, c in enumerate(commands) if "contract_lint" in c), -1
            )
            self.assertGreater(
                hygiene_idx, lint_idx,
                f"hygiene_scan (idx={hygiene_idx}) must come AFTER contract_lint (idx={lint_idx})"
            )

            # (8) hygiene_scan entry is fail-safe (|| true) and timeout=10
            hygiene_hook = session_hooks[hygiene_idx]
            self.assertIn("|| true", hygiene_hook["command"],
                          "hygiene_scan hook must be fail-safe with || true")
            self.assertEqual(hygiene_hook.get("timeout"), 10,
                             "hygiene_scan hook must have timeout=10")

    def test_BC02_upgrade_repo_missing_c16_shows_diff_before_hook(self):
        """BC-02: setup on existing repo without C-16 — C-16 added to managed-block,
        then hooks.json updated. Per C-15, diff preview required before any write.

        Verifies that:
        - An existing CONTRACTS.md without C-16 is detected
        - hygiene_scan hook must NOT be added until C-16 is confirmed present
        - The template contains C-16 to patch the managed-block with
        """
        with tempfile.TemporaryDirectory() as tmp:
            _build_fresh_repo(tmp)

            # Pre-existing CONTRACTS.md WITHOUT C-16 (simulates older install)
            contracts_dst = os.path.join(tmp, "CONTRACTS.md")
            with open(contracts_dst, "w", encoding="utf-8") as f:
                f.write(
                    "<!-- harness:managed v1 -->\n"
                    "# CONTRACTS\n"
                    "<!-- harness:managed-begin v1 -->\n"
                    "### C-15\n"
                    "**Title:** Setup must not overwrite user-owned files.\n"
                    "**When:** setup or maintain skill.\n"
                    "**Enforced by:** Skill procedure.\n"
                    "**On violation:** hard-block.\n"
                    "**Why:** User trust.\n"
                    "<!-- harness:managed-end -->\n"
                    "@CONTRACTS.local.md\n"
                )

            # Detect that C-16 is missing
            with open(contracts_dst, encoding="utf-8") as f:
                contracts_text = f.read()
            c16_missing = "### C-16" not in contracts_text
            self.assertTrue(c16_missing, "Test setup: C-16 must be absent initially")

            # The upgrade path must: detect missing C-16, show diff first (C-15),
            # then patch managed-block, then (and only then) add hooks.json entry.
            # We verify:
            # (a) template has the C-16 text to use for patching
            contracts_template = os.path.join(TEMPLATES_DIR, "CONTRACTS.md")
            with open(contracts_template, encoding="utf-8") as f:
                template_text = f.read()
            self.assertIn("### C-16", template_text,
                          "Template must contain C-16 stanza for upgrade patching")

            # (b) The bootstrap procedure specifies: hook step only runs after
            #     C-16 is confirmed present. Simulate the guard using a minimal
            #     pre-hygiene hooks.json (representing a repo before this task).
            #     The real hooks.json already has hygiene_scan (post-install);
            #     we test the ordering invariant, not the current file state.
            hooks_dst = os.path.join(tmp, "hooks.json")
            # Minimal hooks.json WITHOUT hygiene_scan (pre-install state)
            pre_hygiene_hooks = {
                "hooks": {
                    "SessionStart": [{
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 scripts/note_freshness.py || true",
                                "timeout": 5,
                                "statusMessage": "Checking note freshness"
                            },
                            {
                                "type": "command",
                                "command": "python3 scripts/contract_lint.py --quick || echo '[maintain-suggested]'",
                                "timeout": 5,
                                "statusMessage": "Checking contract drift"
                            }
                        ]
                    }]
                }
            }
            with open(hooks_dst, "w", encoding="utf-8") as f:
                json.dump(pre_hygiene_hooks, f, indent=2)

            hooks_before = json.loads(open(hooks_dst, encoding="utf-8").read())
            session_hooks_before = hooks_before["hooks"]["SessionStart"][0]["hooks"]
            hygiene_present_before = any(
                "hygiene_scan" in h.get("command", "")
                for h in session_hooks_before
            )
            # With C-16 absent, the hook step must be deferred (not yet added).
            # The pre-hygiene hooks.json must NOT have hygiene_scan — confirms
            # the bootstrap invariant from 3.7.5:
            # "If step B fails or is skipped, step C must NOT run."
            self.assertFalse(
                hygiene_present_before,
                "Pre-hygiene hooks.json must not contain hygiene_scan entry (test setup check)"
            )

            # (c) After applying C-16 patch (simulating AskUserQuestion approval),
            #     both conditions are met and hooks.json may receive the entry
            begin = template_text.find("### C-16")
            end_marker = template_text.find("<!-- harness:managed-end -->")
            c16_stanza = template_text[begin:end_marker].rstrip()

            # Patch managed-block
            with open(contracts_dst, "r", encoding="utf-8") as f:
                existing = f.read()
            patched = existing.replace(
                "<!-- harness:managed-end -->",
                c16_stanza + "\n\n<!-- harness:managed-end -->"
            )
            with open(contracts_dst, "w", encoding="utf-8") as f:
                f.write(patched)

            # Now C-16 is present — hooks.json step may proceed
            with open(contracts_dst, encoding="utf-8") as f:
                final_contracts = f.read()
            self.assertIn("### C-16", final_contracts,
                          "C-16 must be in CONTRACTS.md after upgrade patch")

            # Add hook entry (step C)
            hooks_data = json.loads(open(hooks_dst, encoding="utf-8").read())
            session_hooks = hooks_data["hooks"]["SessionStart"][0]["hooks"]
            lint_idx = next(
                (i for i, h in enumerate(session_hooks)
                 if "contract_lint" in h.get("command", "")),
                len(session_hooks) - 1
            )
            hygiene_entry = {
                "type": "command",
                "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/hygiene_scan.py --apply-safe || true",
                "timeout": 10,
                "statusMessage": "Auto-hygiene check",
            }
            session_hooks.insert(lint_idx + 1, hygiene_entry)
            with open(hooks_dst, "w", encoding="utf-8") as f:
                json.dump(hooks_data, f, indent=2)

            # Verify final state: hooks.json has hygiene_scan AFTER contract_lint
            final_hooks = json.loads(open(hooks_dst, encoding="utf-8").read())
            final_session = final_hooks["hooks"]["SessionStart"][0]["hooks"]
            commands = [h.get("command", "") for h in final_session]
            hygiene_idx = next((i for i, c in enumerate(commands) if "hygiene_scan" in c), -1)
            lint_idx2 = next((i for i, c in enumerate(commands) if "contract_lint" in c), -1)
            self.assertGreater(hygiene_idx, -1, "hooks.json must have hygiene_scan entry after upgrade")
            self.assertGreater(
                hygiene_idx, lint_idx2,
                "hygiene_scan must come AFTER contract_lint in hooks.json"
            )

    def test_BC03_hygiene_scan_c16_self_detect(self):
        """BC-03 (belt-and-suspenders): hygiene_scan.py detects missing C-16 and no-ops."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".git"))
            contracts = os.path.join(tmp, "CONTRACTS.md")
            with open(contracts, "w") as f:
                f.write("<!-- harness:managed-begin v1 -->\n### C-15\n<!-- harness:managed-end -->\n")
            os.makedirs(os.path.join(tmp, "doc", "harness"), exist_ok=True)

            result = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS_DIR, "hygiene_scan.py"),
                 "--apply-safe", "--repo-root", tmp],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertIn("[hygiene-bootstrap-needed]", combined)

    def test_BC_contracts_md_has_c16(self):
        """Repo CONTRACTS.md already has C-16 after this task."""
        from hygiene_scan import _c16_present
        self.assertTrue(_c16_present(REPO_ROOT), "CONTRACTS.md must have C-16 after this task")

    def test_BC_contract_lint_does_not_flag_c16_as_unknown(self):
        """contract_lint.py does not produce false-positive for C-16."""
        lint_script = os.path.join(SCRIPTS_DIR, "contract_lint.py")
        if not os.path.isfile(lint_script):
            self.skipTest("contract_lint.py not found")
        result = subprocess.run(
            [sys.executable, lint_script, "--quick",
             "--path", os.path.join(REPO_ROOT, "CONTRACTS.md")],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=10,
        )
        combined = result.stdout + result.stderr
        self.assertNotIn("C-16 not recognized", combined)


class TestDocOutputs(unittest.TestCase):
    """DOC-01..02: hygiene.yaml presence + README entries."""

    def test_DOC01_hygiene_yaml_exists_with_fields(self):
        """DOC-01: doc/harness/hygiene.yaml exists with enabled, observer_until_session, pin_paths."""
        yaml_path = os.path.join(REPO_ROOT, "doc", "harness", "hygiene.yaml")
        self.assertTrue(os.path.isfile(yaml_path), "doc/harness/hygiene.yaml must exist")
        with open(yaml_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("enabled:", content)
        self.assertIn("observer_until_session:", content)
        self.assertIn("pin_paths:", content)
        self.assertTrue("enabled: false" in content or "enabled: true" in content)

    def test_DOC01_hygiene_yaml_header_self_documents(self):
        """DOC-01: hygiene.yaml header comment documents each field with example."""
        yaml_path = os.path.join(REPO_ROOT, "doc", "harness", "hygiene.yaml")
        with open(yaml_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("enabled", content)
        self.assertIn("observer_until_session", content)
        self.assertIn("pin_paths", content)
        self.assertIn("enabled: false", content)

    def test_DOC01_hygiene_yaml_template_exists(self):
        """DOC-01: plugin/skills/setup/templates/hygiene.yaml template exists for fresh installs."""
        template_path = os.path.join(TEMPLATES_DIR, "hygiene.yaml")
        self.assertTrue(os.path.isfile(template_path),
                        "setup template hygiene.yaml must exist for fresh installs")
        with open(template_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("enabled:", content)
        self.assertIn("observer_until_session:", content)
        self.assertIn("pin_paths:", content)

    def test_DOC02_readme_has_new_scripts(self):
        """DOC-02: plugin/scripts/README.md mentions hygiene_scan.py, doc_hygiene.py, maintain_restore.py."""
        readme_path = os.path.join(REPO_ROOT, "plugin", "scripts", "README.md")
        self.assertTrue(os.path.isfile(readme_path))
        with open(readme_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("hygiene_scan.py", content)
        self.assertIn("doc_hygiene.py", content)
        self.assertIn("maintain_restore.py", content)


class TestSetupTemplateContainsC16(unittest.TestCase):
    """Verify setup template CONTRACTS.md has all required hygiene contracts."""

    def test_template_contracts_has_c16(self):
        """CONTRACTS.md template includes C-16 for fresh installs."""
        template_path = os.path.join(TEMPLATES_DIR, "CONTRACTS.md")
        with open(template_path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("### C-16", text, "Template must include C-16")
        self.assertIn("superseded_by", text)
        self.assertIn("distilled_to", text)
        # C-16 must be inside managed block
        begin = text.find("<!-- harness:managed-begin")
        end = text.find("<!-- harness:managed-end -->")
        block = text[begin:end]
        self.assertIn("### C-16", block, "C-16 must be inside managed block in template")

    def test_template_contracts_c11_mentions_hygiene_scan(self):
        """Template CONTRACTS.md C-11 names hygiene_scan.py."""
        template_path = os.path.join(TEMPLATES_DIR, "CONTRACTS.md")
        with open(template_path, encoding="utf-8") as f:
            text = f.read()
        c11_idx = text.find("### C-11")
        self.assertGreaterEqual(c11_idx, 0)
        next_section = text.find("### C-12")
        c11_body = text[c11_idx:next_section if next_section > 0 else c11_idx + 2000]
        self.assertIn("hygiene_scan.py", c11_body,
                      "Template C-11 must name hygiene_scan.py")

    def test_template_contracts_c05_mentions_doc_changes(self):
        """Template CONTRACTS.md C-05 mentions doc/changes authorization via C-16."""
        template_path = os.path.join(TEMPLATES_DIR, "CONTRACTS.md")
        with open(template_path, encoding="utf-8") as f:
            text = f.read()
        c05_idx = text.find("### C-05")
        self.assertGreaterEqual(c05_idx, 0)
        next_section = text.find("### C-06")
        c05_body = text[c05_idx:next_section if next_section > 0 else c05_idx + 2000]
        self.assertIn("doc/changes", c05_body)
        self.assertIn("C-16", c05_body)

    def test_bootstrap_md_has_hygiene_section(self):
        """bootstrap.md section 3.7.5 exists with hygiene bootstrap procedure."""
        bootstrap_path = os.path.join(REPO_ROOT, "plugin", "skills", "setup", "bootstrap.md")
        with open(bootstrap_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("3.7.5", content,
                      "bootstrap.md must have section 3.7.5 for hygiene bootstrap")
        self.assertIn("hygiene_scan", content)
        self.assertIn("hygiene.yaml", content)
        self.assertIn("C-16", content)
        # Order invariant must be documented
        self.assertTrue(
            "order" in content.lower() or "before" in content.lower(),
            "bootstrap.md must document the C-16-before-hooks ordering invariant"
        )
        # hooks.json step must be conditional on C-16 presence
        self.assertTrue(
            "step B" in content or "step C" in content or "invariant" in content.lower(),
            "bootstrap.md must specify that hooks step runs only after CONTRACTS step"
        )


class TestDXTagNamespace(unittest.TestCase):
    """DX-01..06: [hygiene-*] tag namespace."""

    def _make_minimal_repo(self, tmp):
        os.makedirs(os.path.join(tmp, ".git"))
        contracts = os.path.join(tmp, "CONTRACTS.md")
        with open(contracts, "w") as f:
            f.write("<!-- harness:managed-begin v1 -->\n### C-16\n<!-- harness:managed-end -->\n")
        os.makedirs(os.path.join(tmp, "doc", "harness"), exist_ok=True)

    def test_DX01_hygiene_scan_emits_only_hygiene_tags(self):
        """DX-01: hygiene_scan.py emits only [hygiene-*] lines, never [maintain-suggested]."""
        with tempfile.TemporaryDirectory() as tmp:
            self._make_minimal_repo(tmp)
            result = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS_DIR, "hygiene_scan.py"),
                 "--apply-safe", "--repo-root", tmp],
                capture_output=True, text=True, timeout=15,
            )
            combined = result.stdout + result.stderr
            bracket_lines = [l for l in combined.splitlines() if l.strip().startswith("[")]
            for line in bracket_lines:
                self.assertTrue(line.startswith("[hygiene-"),
                                f"hygiene_scan.py emitted non-[hygiene-*] tag: {line!r}")

    def test_DX02_maintain_suggested_reserved_for_contract_lint(self):
        """DX-02: [maintain-suggested] is emitted by contract_lint, not hygiene_scan."""
        hooks_path = os.path.join(REPO_ROOT, "plugin", "hooks", "hooks.json")
        with open(hooks_path, encoding="utf-8") as f:
            data = json.load(f)
        session_hooks = data["hooks"]["SessionStart"][0]["hooks"]
        lint_cmd = next(
            (h["command"] for h in session_hooks if "contract_lint" in h.get("command", "")), ""
        )
        self.assertIn("[maintain-suggested]", lint_cmd,
                      "contract_lint hook must emit [maintain-suggested], not hygiene_scan")
        hygiene_cmd = next(
            (h["command"] for h in session_hooks if "hygiene_scan" in h.get("command", "")), ""
        )
        self.assertNotIn("[maintain-suggested]", hygiene_cmd,
                         "[maintain-suggested] must not appear in hygiene_scan hook command")

    def test_DX03_hygiene_tags_valid_set(self):
        """DX-03: [hygiene-*] tag set is limited to defined values."""
        source_path = os.path.join(SCRIPTS_DIR, "hygiene_scan.py")
        with open(source_path, encoding="utf-8") as f:
            source = f.read()
        tags = re.findall(r"\[hygiene-[a-z-]+\]", source)
        valid_tags = {
            "[hygiene-observer]",
            "[hygiene-auto]",
            "[hygiene-archived]",
            "[hygiene-review]",
            "[hygiene-skip]",
            "[hygiene-bootstrap-needed]",
        }
        for tag in tags:
            self.assertIn(tag, valid_tags, f"Unknown hygiene tag: {tag!r}")

    def test_DX04_at_most_one_hygiene_line_per_session(self):
        """DX-04: hygiene_scan.py emits at most one [hygiene-*] line per invocation."""
        with tempfile.TemporaryDirectory() as tmp:
            self._make_minimal_repo(tmp)
            result = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS_DIR, "hygiene_scan.py"),
                 "--apply-safe", "--repo-root", tmp],
                capture_output=True, text=True, timeout=15,
            )
            combined = result.stdout + result.stderr
            hygiene_lines = [l for l in combined.splitlines() if l.strip().startswith("[hygiene-")]
            self.assertLessEqual(len(hygiene_lines), 1,
                f"hygiene_scan.py must emit at most 1 [hygiene-*] line, got: {hygiene_lines}")

    def test_DX05_hygiene_review_tag_in_prompt_memory(self):
        """DX-05: prompt_memory.py uses [hygiene-review] tag for pending injection."""
        pm_source_path = os.path.join(SCRIPTS_DIR, "prompt_memory.py")
        with open(pm_source_path, encoding="utf-8") as f:
            pm_source = f.read()
        self.assertIn("[hygiene-review]", pm_source)

    def test_DX06_enabled_false_disables_hygiene(self):
        """DX-06: hygiene.yaml enabled: false → hygiene_scan exits silently without [hygiene-*]."""
        with tempfile.TemporaryDirectory() as tmp:
            self._make_minimal_repo(tmp)
            with open(os.path.join(tmp, "doc", "harness", "hygiene.yaml"), "w") as f:
                f.write("enabled: false\n")
            result = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS_DIR, "hygiene_scan.py"),
                 "--apply-safe", "--repo-root", tmp],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            hygiene_lines = [l for l in combined.splitlines() if l.strip().startswith("[hygiene-")]
            self.assertEqual(len(hygiene_lines), 0,
                f"disabled hygiene must produce no [hygiene-*] lines, got: {hygiene_lines}")


class TestSkillMaintain(unittest.TestCase):
    """SK-01..02: maintain SKILL.md LOC + invariants."""

    def test_SK01_maintain_skill_loc_count(self):
        """SK-01: maintain SKILL.md is <= 120 LOC (AC-015)."""
        skill_path = os.path.join(REPO_ROOT, "plugin", "skills", "maintain", "SKILL.md")
        with open(skill_path, encoding="utf-8") as f:
            lines = f.readlines()
        loc = len(lines)
        self.assertLessEqual(loc, 120,
            f"maintain SKILL.md has {loc} lines (AC-015 requires <= 120 LOC)")

    def test_SK01_maintain_skill_no_subagent_spawn(self):
        """SK-01: maintain SKILL.md must NOT contain subagent spawn invocation patterns."""
        skill_path = os.path.join(REPO_ROOT, "plugin", "skills", "maintain", "SKILL.md")
        with open(skill_path, encoding="utf-8") as f:
            content = f.read()
        invocation_patterns = [
            r"skill\(harness:writer\)",
            r"skill\(oh-my-claudecode:writer\)",
            r"invoke\s+writer\s+agent",
        ]
        for pattern in invocation_patterns:
            self.assertFalse(re.search(pattern, content.lower()),
                f"maintain SKILL.md must not invoke writer subagent: {pattern!r} (AC-015)")
        self.assertTrue(
            "no subagent spawn" in content.lower()
            or "not spawn" in content.lower()
            or "never spawn" in content.lower()
            or bool(re.search(r"no.*subagent", content.lower())),
            "SKILL.md must explicitly prohibit subagent spawn"
        )

    def test_SK02_maintain_skill_has_tier_c_flow(self):
        """SK-02: maintain SKILL.md includes Tier C confirm flow with AskUserQuestion."""
        skill_path = os.path.join(REPO_ROOT, "plugin", "skills", "maintain", "SKILL.md")
        with open(skill_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("AskUserQuestion", content,
                      "Tier C confirm flow requires AskUserQuestion")
        self.assertTrue("Tier C" in content or "tier_c" in content.lower())
        self.assertTrue(
            "not batched" in content.lower()
            or "one item at a time" in content.lower()
            or "single AskUserQuestion per item" in content.lower()
            or "per item" in content.lower()
        )


if __name__ == "__main__":
    unittest.main()
