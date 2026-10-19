# Device Lab native QA

## User requirement and provenance

Source: parent coordinator's conversation summary for TASK__install-and-device-lab.
The user_explicit request is “qa-desktop도 device-lab mcp기준으로 싹 바꿔줘”,
clarified as “x11-mcp 포함해서 macos windows android ios다 device-lab으로 들어감”.
Accepted outcome: native QA uses Device Lab for X11, macOS, Windows, Android
and iOS. These quotes are attributed to that handoff; the methods below are
derived implementation rules, not additional quoted user requirements.

## Derived execution and evidence contract

Basis: the accepted Device Lab outcome, actual session tool metadata, and the
existing QA evidence/ownership contracts. A backend advertises support; an
owner-scoped device and its current capabilities determine executable actions.

- Discover `devices` with `view: backends`, `view: owned`, and `view: available`;
  select an actual returned owner-scoped `deviceId` and inspect `status` before
  actions. Available candidates are not automatically owned or running. Missing
  unrelated platforms do not block the project's selected supported target.
- Preserve `qa-desktop`, `ux-desktop`, `desktop_qa_supported`,
  `app_launch_command`, and optional `display_command`; no new manifest schema.
  Do not reject remote devices using local OS or `$DISPLAY` conditions.
- Desktop window inspection/focus applies only where supported. Mobile uses
  app identity, supported UI inspection, screenshot and touch/input. X11 has no
  remote `exec` or `ui`; launch locally only when the documented project
  command targets the same current display. Remote execution requires selected
  backend support. `wait_for_app` and `wait_for_text` are mobile-only; `back` is
  Android-only. There is no universal resize, evaluate, or wait API.
- Each required GUI AC records deviceId, backend, platform, window handle/title
  or appId, expected state, real action, inspected screenshot or privacy
  redaction note, and observed result. Coordinates use full returned screenshot
  dimensions; crops do not change input coordinates. UI hierarchy is optional
  when unsupported. A screenshot without the required action is insufficient.
- Lower tiers can pass non-GUI checks only. Required unexercised GUI ACs remain
  `BLOCKED_ENV`; observed product defects are `FAIL`. Bounded readiness probes
  do not establish GUI acceptance, and mobile waits returning `matched:false`
  are unmet conditions.
- Keep first-line QA verdict parsing, screenshot privacy, owner-scoped actions
  and lifecycle cleanup. Do not reset, clear data, delete existing devices or
  create paid resources without actual authority. Clean up only resources
  created for this task; preserve pre-existing devices and attachments.
- Setup discovers session Device Lab tools and reports exact missing backend,
  target, capability or app prerequisites. It does not bootstrap Xvfb/sudo,
  repair project `.mcp.json`, or prescribe a standalone x11 allowlist.

## Verification

Both runtime role/setup contract regressions cover discovery, desktop/mobile
branches, evidence tiers and removal of obsolete bootstrap. Existing desktop
gate and setup ownership tests preserve compatibility. Live inventory/status
probes establish environment readiness separately from actual GUI interaction.
All five platforms need not run for every project; report only targets exercised.
