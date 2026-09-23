---
tags: [harness, setup, migration, gitignore]
summary: Harness 관리 파일 변경은 manifest의 정수 harness_version과 검증된 순차 마이그레이션으로 갱신한다.
updated: 2026-09-22
freshness: current
invalidated_by_paths:
  - plugin/scripts/setup_finalize.py
  - plugin/scripts/project_format_check.py
  - plugin/scripts/hook_session_start.py
  - plugin/hooks/hooks.json
freshness_updated: 2026-09-23T00:42:07Z
---

# REQ — Harness 버전 마이그레이션

`doc/harness/manifest.yaml`의 최상위 `harness_version` 필드는 Harness가
프로젝트 안에 관리하는 파일 구성의 정수 버전이다. Harness 릴리스인
`doc/harness/.version`과 같은 manifest의 YAML 스키마 `version`은 다른 값이다. 관리 파일의
필드 추가·폐기, 운영 파일 ignore 규칙 변경처럼 기존 프로젝트에 적용해야 할
변경이 생길 때마다 `harness_version`을 1씩 올리고 해당 버전의 마이그레이션을
정의한다. 버전을 올리는 일만으로는 기존 프로젝트를 갱신한 것으로 보지 않는다.
스키마 v5 이전 manifest에 있던 옛 `harness_version` 값은 이 정수 버전과
의미가 다르므로, 스키마 마이그레이션에서 제거한 뒤 새 값을 기록한다.

필드가 없는 프로젝트는 Harness 버전 0으로 읽는다. 버전 1은 Harness 운영 파일의
표준 `.gitignore` 목록을 포함한다. 여기에는
`doc/harness/.watcher-diagnostics.json`이 포함된다. 설치 및
`--migrate-harness-version` 명령은 기존 사용자 규칙을 보존하면서 표준 목록을
적용하고, 실제 ignore 동작을 검증한 뒤에만 manifest에 버전 1을 기록한다. 이미 추적
중인 운영 파일은 ignore 규칙만으로 숨길 수 없으므로 마이그레이션을 실패로
보고한다. 에이전트는 사용자의 추적 상태를 임의로 변경하지 않는다.
명령은 정확한 Git 루트만 받는다. 하위 디렉터리에서 시작한 세션의 안내도
실제 루트의 절대 경로를 사용한다.
이전 배포판이 만든 `doc/harness/.format-version` 파일은 성공한 마이그레이션에서
제거한다. 새 코드는 manifest 필드만 버전의 근거로 사용한다.

Codex와 Claude의 SessionStart 검사는 읽기 전용이다. 오래된 Harness 버전이나
표준 ignore 목록의 누락을 발견하면 에이전트에게 마이그레이션 명령을
안내한다. 현재 버전과 규칙이 모두 맞으면 메시지를 내지 않는다. 지원 범위를
넘는 미래 버전이나 잘못된 필드 값은 자동으로 덮어쓰지 않고 진단한다.
마이그레이션은 반복 실행해도 같은 결과여야 한다.

향후 버전에서는 각 번호의 변경 내용, 검증 조건, 실패 시 복구 경로를
마이그레이션 코드와 테스트에 함께 추가한다. 전체 setup의 성공 버전 기록과
부분적인 `--gitignore-only` 갱신은 구별한다.
