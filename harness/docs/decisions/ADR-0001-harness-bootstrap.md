# ADR-0001: harness bootstrap

**Status:** accepted
**Date:** 2026-03-20

## Context

This repository needs a consistent operating layer for AI-assisted development. Without it, each session starts from zero — no memory of past decisions, no awareness of risk zones, no validation discipline.

## Decision

Bootstrap harness as the repo-local operating system:
- `harness/` for routing, policies, and working state
- `harness/docs/` for durable knowledge (constraints, decisions, domains, runbooks)
- `harness/scripts/` for validation and architecture checks

## Consequences

- All future AI work in this repo follows the harness runtime loop
- Durable knowledge is captured in repo-local files, not session memory
- Risk zones are checked before dangerous changes
- Every change goes through a validation loop
- The structure will evolve as the project grows
