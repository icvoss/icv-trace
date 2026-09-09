# Human renderer

This document is the public copy of the human-renderer contract, derived from
`docs/specs/icv-trace/HUMAN-RENDERER.md` in the ICV OSS umbrella at revision
`67bcccd51d4b564b472e62f1d668e802f39c1942` (2026-09-09). Keep it synchronised
with that source until graduation changes normative ownership.

`HumanRenderer` turns one admitted `icv-trace.record.v1` JSONL record into a
compact, LF-terminated line. `HumanTraceSink` renders the structured line and
optionally forwards the original JSONL unchanged. They are useful for services,
management commands, tasks and pipelines because those callers retain the
same core instrumentation and policy.

## Basic binding

```python
import sys

from icv_trace import HumanTraceSink, TracePolicy, TraceSinks, bind_trace, textio_sink

sink = HumanTraceSink(human=textio_sink(sys.stderr))
run = bind_trace(
    lambda: TracePolicy(TRACE_ENABLED=True, TRACE_DESTINATIONS=("structured",)),
    TraceSinks(structured=sink),
)

with run("inventory.command") as command:
    with run("inventory.pipeline"):
        with run("inventory.service.load") as service:
            service.note("loaded", row_count=2)
            service.result(row_count=2)
    command.result(domain_outcome="completed")
```

The renderer immediately projects each record. It retains only bounded open
span identity and indentation metadata, caps depth at 32, and treats missing
parents as depth zero. It is concurrent-safe but has no global state. Indent
is a readability aid, never evidence of complete ancestry.

## Labels and presentation

Pass `labels` to replace selected operation names for one host:

```python
from icv_trace import HumanRenderer

renderer = HumanRenderer(labels={"inventory.service.load": "load inventory"})
```

Keys use the operation-name grammar. Labels are non-empty strings up to 96
characters and cannot contain control, format, surrogate or line-separator
characters. Invalid labels, renderer options or destinations raise
`TraceConfigurationError` before writing. Unknown operations keep their
original names.

START shows operation and admitted entry fields; NOTE shows event and fields;
END, FAIL and CANCELLED show execution, reported/unreported state, fields,
omissions and elapsed time. INCOMPLETE names the supervisor reason. DETAIL
SUPPRESSED names its reason and both counters. False, zero, null, empty
strings and an explicitly empty report stay distinguishable. Retry and
continuation attempts show their kind and number. Correlation IDs are omitted
from the compact line: retain the structured JSONL when you need them.

The line wording and whitespace are presentation, not a parsing schema. The
renderer prints only admitted safe facts. It never captures arguments, return
values, exception messages, tracebacks or ambient data.

## Fanout, health and stream ownership

`HumanTraceSink(human=..., structured=..., renderer=...)` renders first, then
attempts the optional structured destination even if rendering or human output
fails. Both channels must be callable and distinct. Each receives a complete
line and acknowledges with `None`. Original structured fanout remains
byte-for-byte unchanged.

`sink.output_health` is sticky for the instance lifetime and preserves the
first safe exception class. Inspect it separately from root trace health: core
sees the composite adapter as one selected sink. Adapter errors never replace
the business result. The adapter opens, flushes and closes no stream. If an
operator needs immediate output, the host sink must flush after a complete
write. A broken warning channel cannot report its own failure elsewhere.

Do not use the same callable for both destinations or route either destination
back into the same `HumanTraceSink`. The adapter rejects identical callable
objects but cannot detect wrappers around one physical stream. Separate
instances sharing a channel need host coordination.

## Tasks and supervision

For dispatched work, the host saves `TraceContext` at dispatch and starts the
worker under an explicit binding. A supervisor may emit `TraceIncomplete` only
from framework evidence, never from a missing END. The renderer makes that
record visibly `INCOMPLETE`; it does not fabricate a worker END, duration or
success. Durable job state, retries, correlation headers, deduplication and
late-worker reconciliation remain host responsibilities.

Run `python examples/human_trace.py --simulate-worker-lost` for a complete,
dependency-free command, pipeline, service and supervisor-observation example.
