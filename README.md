# icv-trace

`icv-trace` is a pure-Python diagnostic boundary for meaningful operations.
It creates correlation identity, attempts immediate baseline lifecycle records,
admits bounded detail when enabled, and exposes local output health. It has no
Django, database, ICV-package, logger or exporter dependency.

The package consumes caller-selected safe scalar facts, a `TracePolicy`,
`TraceSinks`, and optional parent or retry context. It produces human and
JSONL records plus local sink health. The caller and host retain ownership of
domain truth, persistence, policy resolution, destinations, propagation,
error reporting, metrics and audit.

## Install

```bash
pip install icv-trace
```

Private releases are published to `pypi.icvoss.com`. A consuming environment
uses its configured private-index credentials and installs by distribution
name, never by a sibling path.

## Use

The default `trace()` binding writes baseline records to stderr. It reads the
documented `TRACE_*` environment values and has detail disabled by default.

```python
from icv_trace import trace

with trace("catalogue.import", source="supplier") as operation:
    operation.note("rows_loaded", count=12)
    operation.result(imported=12, rejected=0)
```

For explicit policy and separate human and JSONL destinations, a host binds a
callable. Sinks receive one complete rendered line and own writing, buffering
and flushing it.

```python
import sys

from icv_trace import TracePolicy, TraceSinks, bind_trace, textio_sink

policy = TracePolicy(
    TRACE_ENABLED=True,
    TRACE_DESTINATIONS=("human", "structured"),
    TRACE_MAX_DETAIL_EVENTS=100,
    TRACE_MAX_BYTES=65536,
)
with open("trace.jsonl", "x", encoding="utf-8") as jsonl_file:
    trace_scan = bind_trace(
        lambda: policy,
        TraceSinks(human=textio_sink(sys.stderr), structured=textio_sink(jsonl_file)),
    )
    with trace_scan("scan") as operation:
        operation.note("classification", matched=4)
        operation.result(completed=True)
```

`TRACE_ENABLED` controls additional detail only. Baseline START and END are
attempted independently of `TRACE_ENABLED`, `DEBUG` and logging levels.
`TextIOSink` rejects short writes and never flushes or closes the caller's
stream. Logging, Eliot and OpenTelemetry trial adapters are retained as
internal comparison code in `icv_trace.adapters`; they are not supported
public delivery APIs.

## Public API and contract

The supported package-root exports are `trace`, `bind_trace`,
`current_trace_context`, `emit_incomplete`, `capture_callback`, `TracePolicy`,
`TraceSinks`, `TraceContext`, `TraceAttempt`, `TraceIncomplete`,
`TraceOutputHealth`, `TraceHandle`, `TextIOSink`, `textio_sink`,
`TraceInputError`, `TraceConfigurationError` and `TraceSinkError`.

The authoritative consumes, does and produces contract is maintained in the
[ICV Trace specification](https://github.com/icvoss/icv-oss-umbrella/tree/main/docs/specs/icv-trace).
The same-thread `capture_callback()` bridge is bounded to its live owner and
creator thread. It does not register callbacks or transfer work across
threads or processes.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy src/icv_trace
pytest tests -v --tb=short
```

The normal development suite permits the editable install. CI and release
verification also build a wheel, install it without the source tree on the
import path, and assert that `icv_trace` resolves from `site-packages`.

## Provenance

This package extracts the site-owned incubation core from icvlocal commit
`087479b96929a56447c2ac8011011023b4df2dcd`. See
[PROVENANCE.md](PROVENANCE.md) for archived trial hashes and the extraction
adaptations. Extraction is governed by
[ADR-103](https://github.com/icvoss/icv-oss-umbrella/blob/main/docs/adrs/ADR-103-extract-icv-trace.md).
