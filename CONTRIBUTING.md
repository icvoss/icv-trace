# Contributing to icv-trace

## Prerequisites

- Python 3.11 or later
- `uv` or pip

The package is pure Python. Django, a database and ICV sibling packages are
not required for development or tests.

## Local setup and checks

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy src/icv_trace
pytest tests -v --tb=short
```

Use conventional commits and work on a branch. Public behaviour is the
package-root API and the `icv-trace.*.v1` contracts. Keep core changes aligned
with the umbrella trace specification and preserve caller ownership of domain
truth, settings, transport, audit and error reporting.

## Test modes

The normal suite supports an editable installation. CI additionally runs a
built-wheel job with `ICV_TRACE_REQUIRE_WHEEL=1`; it removes `src` from the
import path and fails unless `icv_trace` resolves from `site-packages`.

## Releasing

See [RELEASING.md](RELEASING.md). A `v<version>` tag publishes to the private
index, so it is pushed only after all release gates pass on that exact commit.
