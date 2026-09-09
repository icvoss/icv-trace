"""Additional independent edge checks for the trace adapter trial.

The saturation test imports one documented runtime test seam because exercising
2**64 public refusals is infeasible.  Every other assertion uses public API.
"""

from __future__ import annotations

import json

import pytest

from icv_trace import (
    TraceConfigurationError,
    TraceContext,
    TraceInputError,
    TracePolicy,
    TraceSinks,
    bind_trace,
    trace,
)


class Lines:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, line: str):
        self.lines.append(line)


def policy(**changes):
    values = dict(
        TRACE_ENABLED=True,
        TRACE_LEVEL="detail",
        TRACE_DESTINATIONS=("human",),
        TRACE_MAX_DETAIL_EVENTS=100,
        TRACE_MAX_BYTES=16384,
        TRACE_MAX_BASELINE_CHILDREN=1000,
        TRACE_MAX_FIELDS=24,
        TRACE_MAX_STRING_CHARS=256,
    )
    values.update(changes)
    return TracePolicy(**values)


def bound(lines, **changes):
    return bind_trace(lambda: policy(**changes), TraceSinks(human=lines))


def test_edge_exactly_one_enriched_start_and_no_duplicate_baseline_start():
    lines = Lines()
    with bound(lines)("operation", phase="entry"):
        pass
    starts = [line for line in lines.lines if line.startswith("TRACE start operation")]
    assert len(starts) == 1
    assert 'fields={"phase":"entry"}' in starts[0]


def test_edge_structured_root_child_records_have_closed_keys_parent_ids_and_matching_human():
    human, structured = Lines(), Lines()
    call = bind_trace(
        lambda: policy(TRACE_DESTINATIONS=("human", "structured")), TraceSinks(human=human, structured=structured)
    )
    with call("root") as root:
        with trace("child") as child:
            child.result(count=0)
        root.result(done=True)
    records = [json.loads(line) for line in structured.lines]
    root_start = next(item for item in records if item["kind"] == "start" and item["operation"] == "root")
    child_start = next(item for item in records if item["kind"] == "start" and item["operation"] == "child")
    child_end = next(item for item in records if item["kind"] == "end" and item["operation"] == "child")
    assert child_start["trace_id"] == root_start["trace_id"]
    assert child_start["parent_span_id"] == root_start["span_id"]
    assert child_end["elapsed_ms"] >= 0 and set(child_end) == {
        "schema",
        "kind",
        "operation",
        "trace_id",
        "span_id",
        "operation_id",
        "parent_span_id",
        "attempt_number",
        "attempt_kind",
        "predecessor_span_id",
        "execution",
        "reported",
        "elapsed_ms",
        "fields",
    }
    rendered = (
        "TRACE "
        + child_end["kind"]
        + " "
        + child_end["operation"]
        + "".join(
            f" {key}={json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))}"
            for key, value in sorted(child_end.items())
            if key not in {"schema", "kind", "operation"}
        )
        + "\n"
    )
    assert rendered in human.lines


def test_edge_result_last_wins_and_empty_is_distinct_from_unreported():
    lines = Lines()
    call = bound(lines)
    with call("replacement") as handle:
        handle.result(first=1)
        handle.result(second=2)
    with call("explicit.empty") as handle:
        handle.result()
    with call("unreported"):
        pass
    output = "".join(lines.lines)
    replacement = next(line for line in lines.lines if line.startswith("TRACE end replacement"))
    empty = next(line for line in lines.lines if line.startswith("TRACE end explicit.empty"))
    missing = next(line for line in lines.lines if line.startswith("TRACE end unreported"))
    assert 'fields={"second":2}' in replacement and "first" not in replacement
    assert "fields={}" in empty and "reported=true" in empty
    assert "fields=" not in missing and "reported=false" in missing
    assert output.count("TRACE end ") == 3


def test_edge_maximum_valid_baseline_result_survives_disabled_and_exhausted_detail():
    lines = Lines()
    fields = {f"f{number:02}": "x" * 128 for number in range(24)}
    with bound(lines, TRACE_ENABLED=False, TRACE_MAX_DETAIL_EVENTS=0)("baseline.max") as handle:
        handle.result(**fields)
    end = next(line for line in lines.lines if line.startswith("TRACE end baseline.max"))
    assert '"f00":"' in end and '"f23":"' in end and "reported=true" in end


def _mapping_at_exact_json_bytes(target: int):
    fields = {f"f{number:02}": "x" * 256 for number in range(15)}
    for length in range(257):
        candidate = {**fields, "tail": "x" * length}
        encoded = json.dumps(candidate, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) == target:
            return candidate
    raise AssertionError("fixture construction error")


def test_edge_mapping_limit_accepts_4096_and_rejects_4097_before_output():
    exact = _mapping_at_exact_json_bytes(4096)
    lines = Lines()
    with bound(lines)("mapping.limit") as handle:
        handle.result(**exact)
    assert any(line.startswith("TRACE end mapping.limit") for line in lines.lines)
    too_large = {**exact, "tail": exact["tail"] + "x"}
    rejected = Lines()
    with pytest.raises(TraceInputError), bound(rejected)("mapping.too-large", **too_large):
        pass
    assert not rejected.lines
    disabled = Lines()
    with pytest.raises(TraceInputError), bound(disabled, TRACE_ENABLED=False)("mapping.result") as handle:
        handle.result(**too_large)
    assert all('"tail"' not in line for line in disabled.lines)


class IntegerSubclass(int):
    pass


class StringSubclass(str):
    pass


class FloatSubclass(float):
    pass


@pytest.mark.parametrize(
    "value",
    [IntegerSubclass(1), StringSubclass("x"), FloatSubclass(1.0), "\x00", "\u200b", "\ud800", "\u2028", "\u2029"],
)
def test_edge_rejects_scalar_subclasses_and_all_forbidden_unicode_categories(value):
    with pytest.raises(TraceInputError), bound(Lines())("input.boundary", value=value):
        pass


def test_edge_ascii_fast_path_keeps_unicode_validation_and_canonical_dual_channel_records():
    human, structured = Lines(), Lines()
    call = bind_trace(
        lambda: policy(TRACE_DESTINATIONS=("human", "structured")), TraceSinks(human=human, structured=structured)
    )
    with call("unicode.output", value="caf\u00e9", spacing="a\u00a0b") as handle:
        handle.result(value="caf\u00e9")
    with pytest.raises(TraceInputError), call("ascii.control", value="x\x1fy"):
        pass
    records = [json.loads(line) for line in structured.lines]
    start = next(record for record in records if record["kind"] == "start")
    assert start["fields"] == {"spacing": "a\u00a0b", "value": "caf\u00e9"}
    assert 'fields={"spacing":"a\\u00a0b","value":"caf\\u00e9"}' in human.lines[0]
    assert '"fields":{"spacing":"a\\u00a0b","value":"caf\\u00e9"}' in structured.lines[0]


def test_edge_handle_lifecycle_rejects_note_and_result_before_after_and_reentry():
    manager = bound(Lines())("lifecycle")
    with pytest.raises(TraceInputError):
        manager.note("before")
    with pytest.raises(TraceInputError):
        manager.result(ok=True)
    with manager as handle:
        handle.note("inside")
        handle.result(ok=True)
    with pytest.raises(TraceInputError):
        handle.note("after")
    with pytest.raises(TraceInputError):
        handle.result(ok=True)
    with pytest.raises(TraceInputError):
        manager.__enter__()


def test_edge_public_context_and_sinks_reject_invalid_shapes_and_binding_reuses_cleanly():
    with pytest.raises(TraceInputError):
        TraceContext("a" * 32, "b" * 16, "c" * 32, attempt_number=True)
    with pytest.raises(TraceInputError):
        TraceContext("0" * 32, "b" * 16, "c" * 32)
    with pytest.raises(TraceInputError):
        TraceContext("a" * 32, "b" * 16, "c" * 32, unknown=True)
    with pytest.raises(TraceConfigurationError), bind_trace(lambda: policy(), TraceSinks(human=1))("bad.sink"):
        pass
    lines = Lines()
    reusable = bound(lines)
    with reusable("first"):
        pass
    with reusable("second"):
        pass
    assert any("TRACE start first" in line for line in lines.lines)
    assert any("TRACE start second" in line for line in lines.lines)


def test_edge_manager_constructed_before_root_inherits_when_entered_inside():
    lines = Lines()
    child_manager = trace("ambient.child")
    with bound(lines)("ambient.root"), child_manager, trace("ambient.grandchild"):
        pass
    output = "".join(lines.lines)
    assert "ambient.child" in output and "ambient.grandchild" in output


def test_edge_child_failure_is_local_but_root_health_aggregates_and_sibling_stays_healthy():
    lines = Lines()

    def selective(line):
        lines(line)
        if " child.bad " in line:
            raise OSError("sink failure")

    call = bind_trace(lambda: policy(), TraceSinks(human=selective))
    with call("root") as root:
        with trace("child.bad") as bad:
            pass
        with trace("child.good") as good:
            pass
    assert bad.output_failed and bad.output_error_type == "OSError"
    assert root.output_failed and root.output_error_type == "OSError"
    assert not good.output_failed


def test_edge_selected_root_allows_unrelated_named_descendant_detail():
    lines = Lines()
    with bound(lines, TRACE_SCOPE=("root",))("root"), trace("different.name") as child:
        child.note("detail", selected=True)
    assert "different.name" in "".join(lines.lines) and "detail" in "".join(lines.lines)


def test_edge_event_capacity_is_exact_and_event_reason_precedes_byte_reason():
    lines = Lines()
    with bound(lines, TRACE_MAX_DETAIL_EVENTS=1)("events") as handle:
        handle.note("first")
        handle.note("second")
    assert "first" in "".join(lines.lines) and "second" not in "".join(lines.lines)
    both = Lines()
    with bind_trace(
        lambda: policy(TRACE_DESTINATIONS=("human", "structured"), TRACE_MAX_DETAIL_EVENTS=0, TRACE_MAX_BYTES=1024),
        TraceSinks(human=both, structured=Lines()),
    )("both") as handle:
        handle.note("refused", value="x" * 256)
    suppressed = next(line for line in both.lines if "detail_suppressed" in line)
    assert 'reason="event_limit"' in suppressed


def test_edge_entry_refusal_follows_root_start_and_preserves_root_identity():
    structured = Lines()
    call = bind_trace(
        lambda: policy(TRACE_DESTINATIONS=("structured",), TRACE_MAX_DETAIL_EVENTS=0),
        TraceSinks(structured=structured),
    )
    with call("entry.refused", value="detail"):
        pass
    records = [json.loads(line) for line in structured.lines]
    assert [record["kind"] for record in records] == ["start", "detail_suppressed", "end"]
    assert {record["trace_id"] for record in records} == {records[0]["trace_id"]}


def test_edge_elapsed_is_monotonic_finite_and_excludes_start_end_sink_delay(monkeypatch):
    import icv_trace.core as core

    clock = [100.0]
    monkeypatch.setattr(core.time, "monotonic", lambda: clock[0])
    lines = Lines()

    def delayed(line):
        lines(line)
        clock[0] += 10.0

    call = bind_trace(lambda: policy(), TraceSinks(human=delayed))
    with call("timed"):
        clock[0] += 0.0125
        pass
    end = next(line for line in lines.lines if line.startswith("TRACE end timed"))
    assert "elapsed_ms=12.5" in end


def test_edge_saturation_uses_runtime_fault_injection_seam_and_reports_lower_bound():
    """White-box only: seed the otherwise infeasible u64 counter boundary."""
    import icv_trace.core as core

    lines = Lines()
    call = bound(lines, TRACE_MAX_DETAIL_EVENTS=0)
    with call("saturation") as handle:
        handle._root.omitted_detail = core._MAX_U64 - 1  # documented trial fault seam
        handle.note("first.refusal")
        handle.note("second.refusal")
    end = next(line for line in lines.lines if line.startswith("TRACE end saturation"))
    assert f"omitted_detail_records={core._MAX_U64}" in end and "counts_saturated=true" in end
