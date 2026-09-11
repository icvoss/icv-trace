# Releasing icv-trace

The public repository publishes releases to PyPI through the GitHub Actions
trusted publisher. The private-index transition is complete: known consumers
use public `0.1.0rc4`, and the former private rc2/rc3 distributions are archived
outside the served index for operator recovery.
Pushing a `v<semver>` tag is irreversible and must point to the merged `main`
commit.

This is a pure-Python package. There are no Django, database or migration
legs, but static, built-wheel and clean-environment checks are required.

## Before tagging

Record all six gates for the exact commit:

1. `pyproject.toml` and `src/icv_trace/__init__.py` contain the same version,
   and `CHANGELOG.md` has a dated matching heading with nothing to ship under
   Unreleased.
2. Every lint, mypy and Python 3.11 through 3.14 test leg in `ci.yml` is green.
3. In a clean virtual environment, reproduce the `publish.yml` install list
   and pass its installed-wheel test command.
4. Build wheel and sdist, run `twine check`, inspect the wheel for
   `icv_trace`, `core.py`, `adapters.py`, `human.py` and `py.typed`, then
   install the wheel cleanly and import the documented package-root API. After
   publication repeat the installation by name from the target index.
5. Review the tagged commit's own `publish.yml`.
6. State all existing-consumer behaviour in the release notes. For `0.1.0rc5`,
   acceptance coverage and published install guidance change; core record
   semantics remain unchanged from `0.1.0rc4`. Logging, Eliot and OpenTelemetry
   helpers remain experimental and unsupported.

## Tag

```bash
git checkout main
git pull
git tag v<version>
git push origin v<version>
```

Confirm the workflow publishes to PyPI, creates the appropriate release
record, and that the package installs by name. Do not move a tag. If any gate
or publish step fails, fix it on a new commit and publish a new release-
candidate version.

## Public PyPI release and private-index transition

The repository is public and its trusted publisher is restricted to
`icvoss/icv-trace`, workflow `publish.yml`, environment `pypi`. Run every gate
above against the exact commit whose workflow targets PyPI, including a clean
PyPI installation after publication.

The first public version is a deliberate compatibility point. Release notes
must state the upgrade path for private-index consumers: pin the version while
validating the PyPI artefact, remove the private extra index only after all
private dependencies are absent, and retain the private index where other
private packages still require it. Do not replace an existing tag or silently
move consumers across indexes. Record the target index, installed artefact
version and consumer-visible behaviour in the release evidence.
