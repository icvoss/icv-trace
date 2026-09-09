"""Black-box regression for child admission occurring before field validation."""

from __future__ import annotations

import json

import pytest

from icv_trace import TraceInputError, TracePolicy, TraceSinks, bind_trace, trace


class Lines:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, line: str):
        self.lines.append(line)


def run(*, cap: int, invalid_child: bool):
    structured = Lines()
    policy = TracePolicy(TRACE_ENABLED=True, TRACE_DESTINATIONS=("structured",), TRACE_MAX_BASELINE_CHILDREN=cap)
    call = bind_trace(lambda: policy, TraceSinks(structured=structured))
    with call("root"):
        with trace("first"):
            pass
        if invalid_child:
            with pytest.raises(TraceInputError), trace("invalid", value=object()):
                pass
        with trace("second"):
            pass
    return [json.loads(line) for line in structured.lines]


def summary(records):
    """Discard generated IDs and measured duration; retain contract observables."""
    return [
        (
            record["kind"],
            record["operation"],
            record.get("execution"),
            record.get("omitted_child_operations"),
            record.get("omitted_detail_records"),
        )
        for record in records
    ]


def test_invalid_child_below_cap_has_no_admission_or_output_effect():
    control = run(cap=2, invalid_child=False)
    trial = run(cap=2, invalid_child=True)
    assert summary(trial) == summary(control)
    assert all(record["operation"] != "invalid" for record in trial)
    root_end = next(record for record in trial if record["kind"] == "end" and record["operation"] == "root")
    assert root_end["omitted_child_operations"] == 0


def test_invalid_child_at_cap_leaves_existing_budget_and_omission_count_unchanged():
    control = run(cap=1, invalid_child=False)
    trial = run(cap=1, invalid_child=True)
    assert summary(trial) == summary(control)
    assert all(record["operation"] != "invalid" for record in trial)
    root_end = next(record for record in trial if record["kind"] == "end" and record["operation"] == "root")
    assert root_end["omitted_child_operations"] == 1
