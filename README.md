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
python -m pip install "icv-trace==0.1.0rc4"
```

Public release candidates use PyPI and an explicit version as shown above.
`0.1.0rc3` remains available from the private index at `pypi.icvoss.com` for
existing consumers. Validate the explicit PyPI version before removing a
private extra index, and retain that index while any other private dependency
requires it. Install by distribution name, never by a sibling path.

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
`HumanRenderer`, `HumanTraceSink`, `TraceInputError`,
`TraceConfigurationError` and `TraceSinkError`. The complete v1 input, output,
failure and compatibility contracts are in [docs/contracts.md](docs/contracts.md).

## Readable projection of structured traces

`HumanRenderer` turns admitted JSONL records into compact, indented terminal
lines. It has no Django or worker dependency and only retains a bounded set of
open span identities for indentation. `HumanTraceSink` makes this useful with
the existing core: bind the core to its `structured` input, then it writes the
readable line and optionally forwards the original JSONL line unchanged.

```python
import sys
from secrets import token_hex

from icv_trace import (
    HumanTraceSink, TraceContext, TraceIncomplete, TracePolicy, TraceSinks,
    bind_trace, emit_incomplete, textio_sink, trace,
)

human_sink = HumanTraceSink(human=textio_sink(sys.stderr))
policy = TracePolicy(TRACE_ENABLED=True, TRACE_DESTINATIONS=("structured",))
run = bind_trace(lambda: policy, TraceSinks(structured=human_sink))

with run("example.command", entrypoint="management_command") as command:
    with trace("example.pipeline"):
        with trace("example.service") as service:
            service.note("loaded", count=2)
            service.result(count=2)
    command.result(completed=True)
    command_context = command.context

# This simulates a supervisor observation of a context it saved at dispatch.
# A missing END alone never authorises an incomplete record.
assert command_context is not None
remote_context = TraceContext(command_context.trace_id, token_hex(8), token_hex(16), command_context.span_id)
emit_incomplete(
    TraceIncomplete(remote_context, "example.worker.task", "worker_lost"),
    policy=policy,
    sinks=TraceSinks(structured=human_sink),
)
```

Labels can replace operation names for a particular host:

```python
from icv_trace import HumanRenderer

renderer = HumanRenderer(labels={"example.command": "import command"})
```

Retry and continuation records include their attempt kind and number in this
view. Correlation IDs remain in the original JSONL rather than the compact
line. `HumanTraceSink.output_health` is sticky and must be checked separately
from core trace health because the core sees the composite as one destination.
The host owns prompt flushing: `textio_sink()` does not flush or close the
stream. The human and optional JSONL callables must be different underlying
channels. The adapter rejects the same callable object, but cannot detect two
wrappers around one stream. Do not make either destination call the same
`HumanTraceSink` recursively. If the host's warning channel is also broken,
it cannot report that diagnostic failure elsewhere.

The runnable dependency-free example mirrors a command invoking a pipeline of
services. It writes compact output to stderr, can fan the original JSONL to a
new file, and labels the remote incomplete record as an explicit simulated
supervisor observation rather than an inference from a missing END. The
[human-renderer guide](docs/human-renderer.md) covers labels, bounded nesting,
fanout, health and host-owned task supervision.

```bash
python examples/human_trace.py
python examples/human_trace.py --detail --jsonl-file trace.jsonl
python examples/human_trace.py --fail
python examples/human_trace.py --simulate-worker-lost
```

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

## Documentation

- [Contracts](docs/contracts.md): v1 inputs, policy and limits, records,
  output health, task context and compatibility.
- [Human renderer](docs/human-renderer.md): readable services, commands,
  pipelines and task observations.
- [Example](examples/human_trace.py): runnable dependency-free program.
- [Contributing](CONTRIBUTING.md), [verification](VERIFICATION.md), and
  [releasing](RELEASING.md): development and publication practice.

## Provenance

This package extracts the site-owned incubation core from icvlocal commit
`087479b96929a56447c2ac8011011023b4df2dcd`. See
[PROVENANCE.md](PROVENANCE.md) for historical source, retained hashes and
extraction adaptations. The public package documentation above is sufficient
to install and use the distribution; the historical material is not a runtime
dependency.
