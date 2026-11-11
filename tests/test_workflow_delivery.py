"""Real committed/dirty payload impact without touching installed runtimes."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin/scripts'))
import install_verified
import wbs
from test_wbs import node
from test_wbs_input_policy import public, policy_plan


def committed_fixture(tmp_path):
    repo, td, call, _ = public(tmp_path)
    payload = repo / 'plugin/example.py'
    payload.parent.mkdir(); payload.write_text('original\n')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    git('config', 'user.name', 'Harness Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    git('add', '.')
    git('commit', '-qm', 'fixture base')
    assert not call('open', fresh_run=True).get('isError')
    assert not call('plan', plan=policy_plan([node()], {'source': ['src.txt']})).get('isError')
    assert wbs.read_state(str(td))['source_base'] == git('rev-parse', 'HEAD')
    return repo, td, call, payload, git


def test_committed_payload_change_is_detected_even_with_clean_status(tmp_path):
    repo, td, _, payload, git = committed_fixture(tmp_path)
    assert install_verified.automatic_delivery_required(td)[0] is False
    payload.write_text('changed\n')
    git('add', 'plugin/example.py'); git('commit', '-qm', 'implementation')
    assert git('status', '--porcelain') == ''
    assert install_verified.automatic_delivery_required(td)[0] is True


def test_dirty_and_untracked_payload_changes_are_detected(tmp_path):
    repo, td, _, payload, _ = committed_fixture(tmp_path)
    payload.write_text('dirty\n')
    assert install_verified.automatic_delivery_required(td)[0] is True
    payload.write_text('original\n')
    (payload.parent / 'new.py').write_text('new\n')
    assert install_verified.automatic_delivery_required(td)[0] is True


def test_docs_only_work_does_not_repair_unrelated_installed_drift(tmp_path, monkeypatch):
    repo, td, _, _, git = committed_fixture(tmp_path)
    (repo / 'README.md').write_text('documentation\n')
    git('add', 'README.md'); git('commit', '-qm', 'docs')
    def no_install(*args, **kwargs):
        raise AssertionError('selection must not inspect or repair an installed runtime')
    monkeypatch.setattr(install_verified, 'install_verified', no_install)
    monkeypatch.setattr(sys, 'argv', ['install_verified.py', '--if-changed', '--wbs-dir', str(td)])
    assert install_verified.main() == 0


def test_unknown_baseline_is_conservative_and_manual_never_claims_deployment(tmp_path):
    repo, td, _, _, _ = committed_fixture(tmp_path)
    state = wbs.read_state(str(td)); state['source_base'] = None
    (td / 'WBS.json').write_text(wbs.encode_state(state))
    assert install_verified.automatic_delivery_required(td)[0] is True
    (repo / 'doc/harness/manifest.yaml').write_text('version: 7\nworkflow:\n  install: manual\n')
    required, reason = install_verified.automatic_delivery_required(td)
    assert required is False and 'no deployment claimed' in reason


def test_document_and_plan_selection_skip_product_installation(tmp_path):
    repo, td, call, _, _ = committed_fixture(tmp_path)
    for kind in ('document', 'plan'):
        text = policy_plan([node()], {'source': ['src.txt']}) + '\n```wbs-work\n' + json.dumps({'kind': kind}) + '\n```\n'
        assert not call('plan', plan=text).get('isError')
        required, reason = install_verified.automatic_delivery_required(td)
        assert required is False and 'no deployment claimed' in reason


def test_nonancestor_baseline_does_not_skip_delivery(tmp_path):
    _, td, _, _, _ = committed_fixture(tmp_path)
    state = wbs.read_state(str(td)); state['source_base'] = 'f' * 40
    (td / 'WBS.json').write_text(wbs.encode_state(state))
    assert install_verified.automatic_delivery_required(td)[0] is True
