# Incubation verification retained as provenance

On 2026-09-08, this site worktree was installed into a fresh Python 3.12
environment from `requirements.txt` by package name using the private index.
The CI-equivalent ruff, Django system check, migration-drift check, fresh
migration and pytest gates passed: 471 passed, 1 expected failure.

The workflow's mypy leg remains advisory. It reported the existing legacy
backlog and did not affect the green CI result. This differs from Cookie
Scan's installed-wheel consumer mypy gate, which is a required package CI
check and passed for the candidate wheel.

This records site-incubation evidence only. It predates this extracted package
and does not prove the package wheel, private-index release or consumer
cutover. Those checks are recorded by the six release gates in `RELEASING.md`.
