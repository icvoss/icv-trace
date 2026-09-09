# Verification

The package verifies its supported pure-Python API, including the core trace
contract and human renderer. The normal development commands are:

```bash
ruff check .
ruff format --check .
mypy src/icv_trace
pytest tests -v --tb=short
```

CI runs those checks across Python 3.11 through 3.14. It also builds a wheel,
installs it without the repository source on the import path, and runs the
suite with `ICV_TRACE_REQUIRE_WHEEL=1`. This proves that documented public
imports resolve from `site-packages`, including `HumanRenderer` and
`HumanTraceSink`.

Before any release, follow the exact-commit gates in [RELEASING.md](RELEASING.md):
CI parity, a clean reproduction of the publish workflow, artefact inspection,
and an installation by name from the target index. Consumer hosts separately
prove their policy resolution, safe projection, context propagation and
supervision integrations because those behaviours remain host-owned.

## Historical incubation evidence

The package originated in a site trial. Its archived checksums and site-only
verification are retained in [PROVENANCE.md](PROVENANCE.md). They explain the
extraction history but do not replace this package's wheel and release checks.
