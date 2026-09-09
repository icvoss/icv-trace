"""Guard the publish resolve job's explicit Python setup."""

from pathlib import Path

workflow = Path(".github/workflows/publish.yml").read_text()
resolve = workflow.split("  verify:", maxsplit=1)[0]
setup = resolve.index("uses: actions/setup-python@v6")
metadata = resolve.index("Require matching release metadata and a dated changelog entry")
assert setup < metadata, "resolve must set up Python before checking release metadata"

publish = workflow.split("  publish:", maxsplit=1)[1].split("  release:", maxsplit=1)[0]
setup = publish.index("uses: actions/setup-python@v6")
python = publish.index('python3 -m venv "$RUNNER_TEMP/twine-venv"')
assert setup < python, "publish must set up Python before creating its twine environment"
