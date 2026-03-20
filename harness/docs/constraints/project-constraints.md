# Project constraints

<!-- Confirmed rules that all work must respect. -->
<!-- Only add entries here after user confirmation or explicit repo evidence. -->

## Format
<!-- - [YYYY-MM-DD] <rule>. Scope: <where it applies>. Reason: <why>. -->

- [2026-03-20] Plugin directory structure (`plugin/`) must be preserved as-is. Scope: repo structure. Reason: user requirement — do not flatten into root.
- [2026-03-20] Only one public command: `/harness:setup`. All other workflows are hidden and auto-routed. Scope: plugin UX. Reason: users should not memorize commands.
- [2026-03-20] Never store unverified guesses as confirmed facts. Memory promotion: hypothesis → observed_fact → confirmed → enforced. Scope: all memory operations. Reason: core product principle.
- [2026-03-20] Prefer executable enforcement over documentation: test > lint rule > config assertion > docs. Scope: decision capture and rule encoding. Reason: docs alone don't prevent violations.
- [2026-03-20] Every code change must close a validation loop with evidence. Scope: all workflows. Reason: "code was written" is not proof — "change was verified" is.
- [2026-03-20] Brownfield: always inventory → protect → encode → constrain → operate. Never jump to editing. Scope: brownfield-adoption. Reason: prevents dangerous modifications in unfamiliar code.
