# Architecture constraints
tags: [constraints, architecture, optional]
summary: Machine-enforceable architecture rules for this project.
updated: {{SETUP_DATE}}

# Purpose
This file defines architecture boundaries that can be verified by check-architecture.sh.
Only generate this when the repo shape benefits from machine constraints
(monorepo, layered app, strict boundary rules, etc.).

# Example rules — uncomment and adapt:

# Layer boundaries:
# - src/domain/ must NOT import from src/infra/ or src/api/
# - src/api/ may import from src/domain/ but NOT from src/infra/ directly
# - src/infra/ may import from src/domain/

# Module boundaries:
# - packages/auth/ must NOT import from packages/billing/
# - shared/ may be imported by any package

# File conventions:
# - Test files must be co-located with source files or in __tests__/
# - No circular imports between top-level directories
