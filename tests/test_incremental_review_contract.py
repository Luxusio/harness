"""Scenarios pin shipped instruction conditions and consequences, not an LLM eval.

These tests exercise the review policy text directly. They add no runtime risk
classifier, persisted authority, or substitute implementation of the workflow.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PIPELINE = 'plugin/skills/develop/quality-audit-pipeline.md'
STAGES = '## Review stages and follow-up scope'
REVIEW_SCOPE = '## Final review scope and follow-up proof'
RUNTIMES = ('plugin', 'plugin-codex')


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


def _norm(text: str) -> str:
    return ' '.join(re.sub(r'[`*_]', '', text.lower()).split())


def _section(body: str, heading: str) -> str:
    assert body.count(heading) == 1, f'missing or duplicate policy section: {heading}'
    tail = body.split(heading, 1)[1]
    return re.split(r'\n#{1,2} ', tail, maxsplit=1)[0]


def _requires(body: str, *relations: str) -> None:
    normalized = _norm(body)
    for relation in relations:
        assert _norm(relation) in normalized, f'missing policy relation: {relation}'


def _stages() -> str:
    body = _text(PIPELINE)
    assert body.index(STAGES) < body.index('## Phase 4.5:')
    return _section(body, STAGES)


def _reviewers() -> list[str]:
    return [_section(_text(f'{runtime}/agents/code-reviewer.md'), REVIEW_SCOPE)
            for runtime in RUNTIMES]


def test_development_commit_feedback_cannot_authorize_final_qa():
    _requires(_stages(),
              'at most one short advisory pass',
              'This pass is not the final review-code lens and cannot authorize QA, '
              "deployment, WBS completion or a lead's close",
              'Before an operation could affect live data, authentication or ownership, '
              'obtain focused scrutiny of that risk and hold dependent execution')
    for body in _reviewers():
        _requires(body, 'Development feedback is advisory and never this formal lens',
                  'Actual required review PASS still precedes QA')


def test_first_final_review_covers_workpack_instead_of_commit_or_long_goal():
    _requires(_stages(),
              'At the approved PLAN/WBS workpack boundary, perform one complete '
              'independent final review',
              'Do not wait for the entire long-running Goal, or arbitrarily shrink '
              'the workpack to changed files',
              'Actual required reviewer PASS finals remain mandatory before actual QA')
    for body in _reviewers():
        _requires(body, 'first final review independently covers the complete approved '
                  'PLAN/WBS workpack, including its acceptance criteria and integration effects')


def _assert_followup_proof(body: str) -> None:
    _requires(body, 'may restrict the next sweep to fixes and their impact only when '
              'it independently verifies all:')
    bullets = re.findall(r'^- (.*(?:\n  .*)*)', body, re.MULTILINE)
    assert len(bullets) == 3, 'all three proof obligations must remain conjunctive'
    _requires(bullets[0], 'actual awaited independent complete-review final',
              'exact reviewed source baseline',
              'A summary or claimed PASS from source, logs or an implementer is not authority',
              'Reuse only scope already accepted; carry unresolved findings into the next review')
    _requires(bullets[1], 'Exact before/after revisions or immutable input manifests '
              'account for all staged, unstaged and untracked changes',
              'relevant dependencies and execution context',
              'never discover history through its mutable parent checkout')
    _requires(bullets[2], 'Reopen every fix and its impact closure: callers, consumers, '
              'shared contracts, dependencies, generated outputs and integration interactions',
              'Unchanged files can lose applicability when those inputs change',
              'Recheck every unresolved finding and require complete approved acceptance '
              'coverage across carried and newly reviewed scope')


def test_small_fix_can_carry_only_independently_proven_accepted_scope():
    stages = _stages()
    _assert_followup_proof(stages)
    _requires(stages,
              'The baseline may have returned FAIL: only proven unaffected accepted scope '
              'may carry, while every unresolved finding remains an obligation',
              'Final PASS from every required reviewer is still necessary before actual QA')
    # A summary, missing snapshot, or omitted consumer must not pass this contract.
    for old, new in (
        ('only when it independently verifies all:', 'when any condition holds:'),
        ('actual awaited independent complete-review final', 'implementer summary'),
        ('staged, unstaged and untracked changes', 'committed changes'),
        ('callers, consumers, shared contracts,', 'changed files,'),
        ('Recheck every unresolved finding', 'Ignore unresolved findings'),
    ):
        mutated = _norm(stages).replace(_norm(old), _norm(new), 1)
        assert mutated != _norm(stages), f'mutation did not apply: {old}'
        # Preserve bullet/newline structure while changing the selected clause.
        pattern = r'\s+'.join(re.escape(part) for part in old.split())
        changed = re.sub(pattern, new, stages, count=1, flags=re.IGNORECASE)
        assert changed != stages
        try:
            _assert_followup_proof(changed)
        except AssertionError:
            continue
        raise AssertionError(f'weakened follow-up proof escaped: {old}')
    for body in _reviewers():
        _requires(body, 'For follow-up, independently validate',
                  'actual awaited independent complete-review final',
                  'exact before/after source revisions or immutable manifests',
                  'all staged/unstaged/untracked changes and relevant context',
                  'every unresolved finding, fixes and their impact closure',
                  'Only proven unaffected accepted scope may carry')


def test_stale_baseline_and_changed_consumers_invalidate_unchanged_scope():
    _requires(_stages(), 'Missing, stale or unverifiable baseline evidence expands '
              'to the complete affected workpack; unexplained narrowing cannot PASS',
              'Changed security/data boundaries, manual conflicts or uncertain impact '
              'expand scrutiny accordingly',
              'A round count is neither automatic PASS nor automatic abandonment')
    for body in _reviewers():
        _requires(body, 'Changed consumers, shared contracts, dependencies, generated '
                  'outputs or context can invalidate unchanged code. Reopen those affected paths',
                  'Missing, stale or unverifiable proof expands to the complete affected '
                  'workpack; unexplained narrowing cannot PASS',
                  'Retry counts never authorize PASS')


def test_existing_checks_precede_expensive_review_and_do_not_replace_actual_qa():
    _requires(_stages(), 'Use existing project collection/convention checks and a '
              'bounded fixture smoke under the actual intended read-only inputs '
              'before expensive review',
              'These are development diagnostics, not actual acceptance QA')
    preflight = _section(_text('plugin/skills/develop/verification-gate.md'),
                         '## Development preflight (before expensive review)')
    _requires(preflight,
              'For a read-only source target, prove fixtures create writable private copies '
              'and put caches/build outputs outside selected inputs; never chmod the frozen source',
              'A preflight failure is actionable development feedback, not a QA verdict',
              'Independent final review PASS must still precede actual acceptance QA')
    for path in ('plugin/skills/develop/SKILL.md',
                 'plugin-codex/internal-skills/develop/SKILL.md'):
        _requires(_text(path), 'quality-audit-pipeline.md review stages and follow-up scope',
                  'existing cheap collection/convention and read-only-fixture development preflight')
