"""Independent encoded fixtures for exact admission limits.

The fixture supplies hypothetical IDs with the contract's fixed lengths. Actual
random hexadecimal IDs have the same canonical encoded length. No candidate
formatter, record factory or private budget state supplies these expectations.
"""

import json

import pytest

from icv_trace import TracePolicy, TraceSinks, bind_trace

FIELDS = {f"field{number}": "x" * 200 for number in range(4)}
NOTE = {
    "schema": "icv-trace.record.v1",
    "kind": "note",
    "operation": "precision",
    "trace_id": "a" * 32,
    "span_id": "b" * 16,
    "operation_id": "c" * 32,
    "parent_span_id": None,
    "attempt_number": 1,
    "attempt_kind": "initial",
    "predecessor_span_id": None,
    "event": "candidate",
    "fields": FIELDS,
}


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fixture_size(record, destinations):
    structured = canonical(record) + "\n"
    human = (
        "TRACE "
        + record["kind"]
        + " "
        + record["operation"]
        + " "
        + " ".join(
            key + "=" + canonical(value)
            for key, value in sorted(record.items())
            if key not in {"schema", "kind", "operation"}
        )
        + "\n"
    )
    lines = {"human": human, "structured": structured}
    return sum(len(lines[name].encode("utf-8")) for name in destinations)


@pytest.mark.parametrize("destinations", [("human",), ("structured",), ("human", "structured")])
@pytest.mark.parametrize("shortfall", [0, 1])
def test_note_exact_encoded_budget_boundary(destinations, shortfall):
    capacity = fixture_size(NOTE, destinations)
    assert 1024 < capacity <= 65536
    human, structured = [], []
    trace = bind_trace(
        lambda: TracePolicy(
            TRACE_ENABLED=True,
            TRACE_DESTINATIONS=destinations,
            TRACE_MAX_BYTES=capacity - shortfall,
        ),
        TraceSinks(human=human.append, structured=structured.append),
    )
    with trace("precision") as operation:
        operation.note("candidate", **FIELDS)
        operation.result(count=0)
    selected = human if "human" in destinations else structured
    has_note = any(line.startswith("TRACE note ") or '"kind":"note"' in line for line in selected)
    assert has_note is (shortfall == 0)
    assert sum("detail_suppressed" in line for line in selected) == shortfall
    assert '"count":0' in selected[-1]
    if shortfall:
        assert 'reason="byte_limit"' in "".join(human) or '"reason":"byte_limit"' in "".join(structured)


def test_failed_destination_does_not_refund_pre_rendered_candidate_bytes():
    destinations = ("human", "structured")
    capacity = fixture_size(NOTE, destinations)
    calls, records = [], []

    def failing_human(line):
        calls.append(line)
        raise OSError("synthetic destination failure")

    trace = bind_trace(
        lambda: TracePolicy(
            TRACE_ENABLED=True,
            TRACE_DESTINATIONS=destinations,
            TRACE_MAX_BYTES=capacity,
        ),
        TraceSinks(human=failing_human, structured=records.append),
    )
    with trace("precision") as operation:
        operation.note("candidate", **FIELDS)
        operation.note("candidate", **FIELDS)
    decoded = [json.loads(line) for line in records]
    assert len(calls) == 1
    assert [r["kind"] for r in decoded] == ["start", "note", "detail_suppressed", "end"]
    assert decoded[-1]["omitted_detail_records"] == 1
    assert decoded[-2]["reason"] == "byte_limit"


def test_event_limit_wins_when_exact_candidate_also_exceeds_bytes():
    destinations = ("human", "structured")
    records = []
    trace = bind_trace(
        lambda: TracePolicy(
            TRACE_ENABLED=True,
            TRACE_DESTINATIONS=destinations,
            TRACE_MAX_BYTES=fixture_size(NOTE, destinations) - 1,
            TRACE_MAX_DETAIL_EVENTS=0,
        ),
        TraceSinks(human=lambda line: None, structured=records.append),
    )
    with trace("precision") as operation:
        operation.note("candidate", **FIELDS)
        operation.note("candidate", **FIELDS)
    decoded = [json.loads(line) for line in records]
    assert [r["kind"] for r in decoded] == ["start", "detail_suppressed", "end"]
    assert decoded[1]["reason"] == "event_limit"
    assert decoded[-1]["omitted_detail_records"] == 2


def test_enriched_start_exact_boundary_preserves_one_start_and_root_suppression_order():
    destinations = ("human", "structured")
    start = {key: value for key, value in NOTE.items() if key != "event"}
    start["kind"] = "start"
    capacity = fixture_size(start, destinations)
    for shortfall in (0, 1):
        records = []
        trace = bind_trace(
            lambda shortfall=shortfall: TracePolicy(
                TRACE_ENABLED=True,
                TRACE_DESTINATIONS=destinations,
                TRACE_MAX_BYTES=capacity - shortfall,
            ),
            TraceSinks(human=lambda line: None, structured=records.append),
        )
        with trace("precision", **FIELDS):
            pass
        decoded = [json.loads(line) for line in records]
        assert [r["kind"] for r in decoded] == (
            ["start", "detail_suppressed", "end"] if shortfall else ["start", "end"]
        )
        assert ("fields" in decoded[0]) is (shortfall == 0)
        assert decoded[-1]["omitted_detail_records"] == shortfall
