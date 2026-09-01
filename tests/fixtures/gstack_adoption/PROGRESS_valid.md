phase: implementation
current_ac: AC-001
partial_ac: null
completed_acs: []

allowed_paths:
  - src/feature.py
  - src/utils.py
  - plugin/CLAUDE.md

test_paths:
  - tests/test_feature.py
  - tests/fixtures/gstack_adoption/

forbidden_paths:
  - src/billing.py
  - db/migrations/
