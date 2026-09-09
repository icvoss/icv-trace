# icv-trace contracts

This document is the public copy of the v1 package contract. It is derived
from `docs/specs/icv-trace/CONTRACTS.md` in the ICV OSS umbrella at revision
`67bcccd51d4b564b472e62f1d668e802f39c1942` (2026-09-09). Until the
graduation change moves normative ownership, keep the two documents in sync;
the umbrella document resolves a conflict.

`icv-trace` consumes named meaningful operations, caller-selected safe scalar
facts, a resolved `TracePolicy`, `TraceSinks`, and optional parent, retry or
supervision context. It creates in-process correlation identity, attempts
bounded lifecycle and detail records, and reports local output health. It
produces diagnostics only. The caller owns the work, domain truth and safe
disclosure. The host owns policy resolution, destinations, propagation,
durable state, retries, supervision, alerting, audit, metrics and error
reporting.

## Operation input: `icv-trace.operation.v1`

`trace(name, /, **fields)` returns a single-entry context manager.
`handle.note(event, /, **fields)` requests detail, and `handle.result(**fields)`
replaces the terminal caller report, including an explicit empty mapping.

| Input | Contract |
| --- | --- |
| Name or event | 1 to 96 ASCII characters matching `[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*` |
| Field key | ASCII `[a-z][a-z0-9_]{0,63}` |
| Field value | `str`, `bool`, signed 64-bit `int`, finite `float` or `None` only |
| Field string | At most `TRACE_MAX_STRING_CHARS` Unicode code points; Cc, Cf, Cs, Zl and Zp characters are rejected |
| Field mapping | At most `TRACE_MAX_FIELDS` keys and 4,096 bytes in canonical JSON; copied at the call boundary |

Invalid input raises `TraceInputError` before that input is retained or
written, even if detail is disabled. Valid shape does not prove safety: never
supply credentials, tokens, signed URLs, cookies, headers, raw bodies, user
content, exception messages or tracebacks. The package captures none of those
values automatically.

Entry fields are optional detail. Terminal result fields are baseline evidence
at every detail level. `domain_outcome` and `domain_completeness` are useful
caller-owned conventions, not package enums. A return, no exception, or a
trace END never proves domain success or completeness.

## Policy and limits: `icv-trace.policy.v1`

`TracePolicy(**settings)` is immutable. Unknown settings and invalid values
raise `TraceConfigurationError`; booleans never satisfy integer bounds.

| Setting | Default and bound | Meaning |
| --- | --- | --- |
| `TRACE_ENABLED` | `False` | Enables additional detail only. It is independent of `DEBUG` and log levels. |
| `TRACE_LEVEL` | `"detail"`; `"summary"` or `"detail"` | Summary admits root entry fields. Detail also admits child entry fields and notes. Results remain baseline. |
| `TRACE_SCOPE` | `("*",)`; 1 to 32 prefixes | A root matches its prefix or `prefix.` descendant; selected descendants inherit the root decision. |
| `TRACE_DESTINATIONS` | `("human",)` | A non-empty tuple of distinct `"human"` and/or `"structured"`; both need separate channels. |
| `TRACE_MAX_DETAIL_EVENTS` | 100; 0 to 1,000 | Detail additions and notes per local root. |
| `TRACE_MAX_BYTES` | 16,384; 1,024 to 65,536 | Canonical detail-candidate bytes across selected representations per root. |
| `TRACE_MAX_BASELINE_CHILDREN` | 1,000; 0 to 1,000 | Child lifecycles per local root, reserving START and END together. |
| `TRACE_MAX_FIELDS` | 24; 1 to 24 | Fields in each supplied mapping. |
| `TRACE_MAX_STRING_CHARS` | 256; 1 to 256 | Characters in each supplied string. |

The core validates every supplied field before admission. Suppressed detail is
recorded once per root when possible, with `event_limit`, `byte_limit` or
`child_limit` and both omission counters. Counters saturate at unsigned
64-bit maximum and say so. A rejected child writes neither START nor END.

## Context, sinks and records

`TraceContext` carries a 32-hex `trace_id`, 16-hex `span_id`, 32-hex
`operation_id`, nullable parent span, and explicit attempt metadata. The host
passes it across a request, queue, process or retry boundary; the package does
not choose headers, create tasks or persist context. `TraceAttempt` represents
an explicit retry or continuation and never increments automatically.

`TraceSinks` supplies the selected callable destinations. Each receives one
complete LF-terminated line and must acknowledge with `None`. Selected sinks
are validated before root entry. Human and structured destinations must be
distinct callable objects. Sinks own I/O, buffering, flushing, closing and
delivery guarantees.

The package attempts immediate baseline `start` and `end` records for an
admitted operation regardless of `TRACE_ENABLED`, `DEBUG` or logging level.
It may also emit `note`, `detail_suppressed`, and a supervisor-owned
`incomplete` record. Canonical structured output is ASCII JSONL with sorted
keys and a final LF. Machines consume JSONL; human text is not a parsing API.

`TraceOutputHealth(output_failed, output_error_type)` is a bounded snapshot
of local sink acceptance. It records the first safe exception class only.
Health failure does not replace a domain result; healthy output does not prove
delivery. Inspect root and supervisor health independently and use a separate
host alerting route where necessary.

## Public operations and failure outcomes

| Operation | Contract |
| --- | --- |
| `trace(name, **fields)` | Uses the active binding, nesting under its root, or the local stderr binding. |
| `bind_trace(policy_resolver, sinks, parent_context=None, attempt=None)` | Returns an independent callable with explicit root configuration. Resolves policy at root entry. An explicit different binding under an active root raises `TraceInputError`. |
| `current_trace_context()` | Returns immutable current context or `None`. |
| `emit_incomplete(observation, policy, sinks)` | Emits one supervisor observation and returns its health. Invalid input/policy/sinks fails before a write. |
| `capture_callback(callback)` | Same-thread, live-owner bridge only. It never transfers work to another thread or process, registers callbacks, or creates an operation. |

The default binding reads documented `TRACE_*` environment values and writes
human lines to stderr. Structured output requires an explicit binding. Optional
framework adapters belong to hosts and must fail explicitly if a selected
dependency is unavailable.

## Supervision is host-owned

`TraceIncomplete(context, operation, reason)` accepts only a host observation
with `hard_timeout`, `worker_lost`, `dispatch_failed` or
`unknown_termination`. A supervisor must obtain the identity from dispatch or
worker-start evidence. A missing END alone never authorises an incomplete
record. Unknown or stale identity goes to the host failure route, without
invented trace IDs. The package neither creates a task nor stores job state;
the host owns deduplication and reconciliation with late worker evidence.

## Compatibility

The package-root exports documented in the README are the supported Python
surface. The `icv-trace.*.v1` contracts are compatibility promises. New
optional record fields may be ignored by consumers; changing a named input,
output meaning, validation rule, failure outcome or public import requires a
documented compatibility decision and an appropriate release version.
