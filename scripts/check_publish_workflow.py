"""Guard the public publish workflow's release and artefact contracts."""

from pathlib import Path

workflow = Path(".github/workflows/publish.yml").read_text()
resolve = workflow.split("  verify:", maxsplit=1)[0]
setup = resolve.index("uses: actions/setup-python@v6")
metadata = resolve.index("Require matching release metadata and a dated changelog entry")
assert setup < metadata, "resolve must set up Python before checking release metadata"

assert "runs-on: [self-hosted" not in workflow, "public workflow must not use private runners"
assert "pypi.icvoss.com" not in workflow, "public workflow must not use the private index"

publish = workflow.split("  publish:", maxsplit=1)[1].split("  release:", maxsplit=1)[0]
assert "id-token: write" in publish, "PyPI publishing needs OIDC permission"
assert "environment: pypi" in publish, "PyPI publishing must use the pypi environment"
assert "pypa/gh-action-pypi-publish@release/v1" in publish

release = workflow.split("  release:", maxsplit=1)[1]
assert "contents: write" in release, "release creation needs contents write permission"

verify = workflow.split("  verify:", maxsplit=1)[1].split("  build:", maxsplit=1)[0]
assert "ICV_TRACE_REQUIRE_WHEEL" in verify
assert "site-packages" in verify, "installed-wheel origin guard is required"

for path in (
    "icv_trace/adapters.py",
    "icv_trace/core.py",
    "icv_trace/human.py",
    "icv_trace/py.typed",
):
    assert path in workflow, f"wheel contents guard must require {path}"
