# Releasing icv-trace

`icv-trace` publishes to the private index at `pypi.icvoss.com`. Pushing a
`v<semver>` tag triggers the publish workflow and is irreversible. The tag
must point to the merged `main` commit.

This is the canonical repository release guide adapted for a pure-Python
private package: there are no Django, database, migration or consumer-smoke
legs. The required static, built-wheel and clean-environment checks remain.

## Before tagging

Record all six gates for the exact commit:

1. `pyproject.toml` and `src/icv_trace/__init__.py` contain the same version,
   and `CHANGELOG.md` has a dated matching heading with nothing to ship under
   Unreleased.
2. Every lint, mypy and Python 3.11 through 3.14 test leg in `ci.yml` is green.
3. In a clean virtual environment, reproduce the `publish.yml` install list
   and pass its installed-wheel test command.
4. Build wheel and sdist, run `twine check`, inspect the wheel for
   `icv_trace`, `core.py`, `adapters.py` and `py.typed`, then install the wheel
   cleanly and import the package. After publication repeat the installation
   by name from the private index.
5. Review the tagged commit's own `publish.yml`.
6. State all existing-consumer behaviour in the release notes. For `0.1.0rc2`,
   the public core and `TextIOSink` API are unchanged from incubation; the
   package home changes to index installation. Logging, Eliot and OpenTelemetry
   helpers remain experimental and unsupported.

## Tag

```bash
git checkout main
git pull
git tag v0.1.0rc2
git push origin v0.1.0rc2
```

Confirm the workflow publishes to the private index, creates the prerelease,
and the package installs by name. Do not move a tag. If any gate or publish
step fails, fix it on a new commit and publish a new release-candidate version.
