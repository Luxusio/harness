# ADR-0001: repo-os bootstrap

**Status:** accepted
**Date:** {{SETUP_DATE}}

## Context

This repository needs a consistent operating layer for AI-assisted development. Without it, each session starts from zero — no memory of past decisions, no awareness of risk zones, no validation discipline.

## Decision

Bootstrap repo-os as the repo-local operating system:
- `.claude-harness/` for routing, policies, and working state
- `docs/` for durable knowledge (constraints, decisions, domains, runbooks)
- `scripts/agent/` for validation and architecture checks

## Consequences

- All future AI work in this repo follows the repo-os runtime loop
- Durable knowledge is captured in repo-local files, not session memory
- Risk zones are checked before dangerous changes
- Every change goes through a validation loop
- The structure will evolve as the project grows
