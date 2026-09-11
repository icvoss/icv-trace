"""Executable checks derived from docs/specs/icv-trace, never candidate code."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from dataclasses import FrozenInstanceError

import pytest

from icv_trace import (
    TraceAttempt,
    TraceConfigurationError,
    TraceContext,
    TraceIncomplete,
    TraceInputError,
    TracePolicy,
    TraceSinks,
    bind_trace,
    current_trace_context,
    emit_incomplete,
    trace,
)


class Lines:
    def __init__(self, fail_at: int | None = None, returns: object = None):
        self.lines: list[str] = []
        self.fail_at, self.returns = fail_at, returns

    def __call__(self, line: str):
        self.lines.append(line)
        if self.fail_at == len(self.lines):
            raise OSError("SINK_SECRET_MUST_NOT_ESCAPE")
        return self.returns


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


def bound(lines: Lines, **changes):
    return bind_trace(lambda: policy(**changes), TraceSinks(human=lines))


def canonical_json(value: object) -> str:
    """Spec-owned canonical JSON, deliberately independent of candidate formatters."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def note_fixture_bytes(value: str) -> tuple[int, int]:
    record = {
        "schema": "icv-trace.record.v1",
        "kind": "note",
        "operation": "byte.boundary",
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "operation_id": "c" * 32,
        "parent_span_id": None,
        "attempt_number": 1,
        "attempt_kind": "initial",
        "predecessor_span_id": None,
        "event": "candidate",
        "fields": {"value": value},
    }
    structured = canonical_json(record) + "\n"
    human = (
        "TRACE note byte.boundary"
        + "".join(
            f" {key}={canonical_json(item)}"
            for key, item in sorted(record.items())
            if key not in {"schema", "kind", "operation"}
        )
        + "\n"
    )
    return len(human.encode("utf-8")), len(structured.encode("utf-8"))


def test_ac_trace_001_baseline_survives_disabled_detail_and_input_is_eager():
    lines = Lines()
    call = bound(lines, TRACE_ENABLED=False)
    with call("hostmap.resolve") as handle:
        handle.result(domain_outcome="resolved", matched=0)
    output = "".join(lines.lines)
    assert "start" in output and "end" in output and '"matched":0' in output
    assert handle.context.trace_id and handle.context.parent_span_id is None
    entered = False
    with pytest.raises(TraceInputError), call("Bad Name"):
        entered = True
    assert not entered  # mutation: remove eager entry validation


def test_ac_trace_002_policy_is_root_scoped_and_explicit_binding_wins(monkeypatch):
    lines, calls = Lines(), []

    def resolver():
        calls.append(1)
        return policy(TRACE_ENABLED=False)

    explicit = bind_trace(resolver, TraceSinks(human=lines))
    monkeypatch.setenv("TRACE_ENABLED", "true")
    with explicit("one") as root:
        root.note("hidden")
        monkeypatch.setenv("TRACE_ENABLED", "false")
        with trace("one.child") as child:
            child.note("still_hidden")
    with explicit("two"):
        pass
    assert calls == [1, 1]
    assert "hidden" not in "".join(lines.lines)
    with pytest.raises(TraceConfigurationError):
        bind_trace(lambda: "true", TraceSinks(human=Lines()))("invalid").__enter__()


@pytest.mark.parametrize(("spelling", "enabled"), [("true", True), ("TRUE", True), ("False", False)])
def test_ac_trace_002_environment_boolean_grammar_and_fresh_roots(monkeypatch, capsys, spelling, enabled):
    monkeypatch.setenv("TRACE_ENABLED", spelling)
    monkeypatch.setenv("DEBUG", "true")
    logging.getLogger().setLevel(logging.DEBUG)
    with trace("environment.one") as handle:
        handle.note("detail")
    first = capsys.readouterr().err
    monkeypatch.setenv("TRACE_ENABLED", "false" if enabled else "true")
    with trace("environment.two") as handle:
        handle.note("detail")
    second = capsys.readouterr().err
    assert ('event="detail"' in first) is enabled
    assert ('event="detail"' in second) is not enabled
    monkeypatch.setenv("TRACE_ENABLED", "yes")
    with pytest.raises(TraceConfigurationError), trace("environment.invalid"):
        pass


def test_ac_trace_003_live_note_and_dual_sinks_have_same_semantics():
    human, structured, released, observed = Lines(), Lines(), threading.Event(), threading.Event()

    def human_sink(line):
        human(line)
        if "rule_evaluated" in line:
            observed.set()

    call = bind_trace(
        lambda: policy(TRACE_DESTINATIONS=("human", "structured")), TraceSinks(human=human_sink, structured=structured)
    )

    def operation():
        with call("hostmap.resolve") as handle:
            handle.note("rule_evaluated", count=1)
            released.wait(1)

    worker = threading.Thread(target=operation)
    worker.start()
    try:
        assert observed.wait(1)  # live barrier: record precedes body release
        assert any("rule_evaluated" in line for line in human.lines)
        assert any('"event":"rule_evaluated"' in line for line in structured.lines)
    finally:
        released.set()
        worker.join()


class IntegerSubclass(int):
    pass


@pytest.mark.parametrize(
    "bad", ["x\n", "x\u200b", "x" * 257, b"x", [], {"x": 1}, 2**63, float("nan"), IntegerSubclass(1)]
)
def test_ac_trace_004_public_scalar_boundary_rejects_bad_values(bad):
    with pytest.raises(TraceInputError), bound(Lines())("safe.operation", value=bad):
        pass


def test_ac_trace_004_core_does_not_pretend_to_detect_secrets():
    lines = Lines()
    with bound(lines)("safe.operation", harmless_looking="token=synthetic-but-caller-owned"):
        pass
    assert "token=synthetic-but-caller-owned" in "".join(lines.lines)


def test_ac_trace_005_budget_is_prewrite_and_child_omission_is_not_success():
    lines = Lines()
    call = bound(lines, TRACE_MAX_DETAIL_EVENTS=1, TRACE_MAX_BASELINE_CHILDREN=1)
    with call("root", entry="a") as root:
        root.note("first", n=1)
        root.note("refused", n=2)
        with trace("root.first"):
            pass
        with trace("root.second") as omitted:
            omitted.note("must_not_appear")
    output = "".join(lines.lines)
    assert "first" in output and "refused" not in output and "must_not_appear" not in output
    assert "detail_suppressed" in output and "omitted_detail_records" in output
    assert "root.second" not in output  # mutation: emit an invented omitted-child END


def test_ac_trace_005_one_and_two_sink_byte_boundaries_and_failed_sink_no_refund():
    """The candidate must charge independently rendered UTF-8 records before writes."""
    detail = "x" * 256
    human_bytes, structured_bytes = note_fixture_bytes(detail)
    assert human_bytes <= 1024 < human_bytes + structured_bytes
    one, two_human, two_structured = Lines(), Lines(), Lines()
    one_call = bound(one, TRACE_MAX_BYTES=1024)
    two_call = bind_trace(
        lambda: policy(TRACE_DESTINATIONS=("human", "structured"), TRACE_MAX_BYTES=1024),
        TraceSinks(human=two_human, structured=two_structured),
    )
    with one_call("byte.boundary") as handle:
        handle.note("candidate", value=detail)
    with two_call("byte.boundary") as handle:
        handle.note("candidate", value=detail)
    one_seen = "candidate" in "".join(one.lines)
    two_seen = "candidate" in "".join(two_human.lines)
    assert one_seen and not two_seen  # same event becomes unaffordable on two destinations
    failed, healthy = Lines(fail_at=1), Lines()
    no_refund = bind_trace(
        lambda: policy(TRACE_DESTINATIONS=("human", "structured"), TRACE_MAX_BYTES=1024),
        TraceSinks(human=failed, structured=healthy),
    )
    with no_refund("byte.boundary") as handle:
        handle.note("candidate", value=detail)
        handle.note("candidate.two", value=detail)
    assert "candidate.two" not in "".join(healthy.lines)  # mutation: refund failed-sink bytes


def test_ac_trace_005_child_cap_is_exact_at_1000_and_descendants_are_silent():
    lines = Lines()
    call = bound(lines, TRACE_MAX_BASELINE_CHILDREN=1000)
    with call("root"):
        for index in range(1000):
            with trace(f"root.child{index}"):
                pass
        with trace("root.child1000"), trace("root.child1000.descendant"):
            pass
    output = "".join(lines.lines)
    assert "TRACE start root.child999 " in output and "TRACE start root.child1000 " not in output
    # Both the over-cap child and its attempted descendant are omitted
    # operations.  The child cap does not collapse that second omission.
    assert "TRACE start root.child1000.descendant " not in output and "omitted_child_operations=2" in output


@pytest.mark.parametrize(
    ("exc", "execution"),
    [(RuntimeError("x"), "failed"), (KeyboardInterrupt(), "cancelled"), (SystemExit(4), "cancelled")],
)
def test_ac_trace_006_exact_body_exception_and_report_semantics(exc, execution):
    lines = Lines()
    call = bound(lines)
    with pytest.raises(type(exc)) as caught, call("operation") as handle:
        handle.result(count=0)
        raise exc
    assert caught.value is exc
    output = "".join(lines.lines)
    # Caller fields remain in the closed ``fields`` mapping; flattening would
    # allow them to overwrite structural record fields.
    assert execution in output and '"count":0' in output
    with call("unreported"):
        pass
    assert "reported=false" in "".join(lines.lines)


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_ac_trace_007_sink_failure_isolated_and_secret_not_retained(fail_at):
    failing, healthy = Lines(fail_at=fail_at), Lines()
    call = bind_trace(
        lambda: policy(TRACE_DESTINATIONS=("human", "structured")), TraceSinks(human=failing, structured=healthy)
    )
    body = RuntimeError("BODY_TOKEN_DO_NOT_DISCLOSE")
    with pytest.raises(RuntimeError) as caught, call("op") as h:
        h.note("note")
        h.result(ok=True)
        raise body
    assert caught.value is body and h.output_failed and h.output_error_type == "OSError"
    assert "BODY_TOKEN_DO_NOT_DISCLOSE" not in "".join(healthy.lines)
    assert len(failing.lines) == fail_at  # failed destination is disabled for root


def test_ac_trace_007_sink_baseexception_cannot_replace_body_exception():
    class SinkInterrupt(BaseException):
        pass

    calls = 0

    def interrupted(_line):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SinkInterrupt()

    call = bind_trace(lambda: policy(), TraceSinks(human=interrupted))
    body = RuntimeError("body identity")
    with pytest.raises(RuntimeError) as caught, call("operation"):
        raise body
    assert caught.value is body


def test_ac_trace_007_hostile_exception_class_names_are_normalised():
    hostile = type("class-name-must-not-leak", (Exception,), {})
    lines = Lines()
    with pytest.raises(hostile), bound(lines)("operation"):
        raise hostile("SENSITIVE_EXCEPTION_TEXT")
    output = "".join(lines.lines)
    assert "unknown_exception" in output
    assert "class-name-must-not-leak" not in output and "SENSITIVE_EXCEPTION_TEXT" not in output


def test_ac_trace_007_sink_baseexception_propagates_without_body_error():
    class SinkInterrupt(BaseException):
        pass

    with (
        pytest.raises(SinkInterrupt),
        bind_trace(lambda: policy(), TraceSinks(human=lambda _: (_ for _ in ()).throw(SinkInterrupt())))("operation"),
    ):
        pass


def test_ac_trace_008_async_context_isolation_and_threads_need_transfer():
    lines = Lines()
    call = bound(lines)

    async def child(name):
        with trace(name) as handle:
            return handle.context

    async def run():
        with call("root") as root:
            return root.context, await asyncio.gather(child("root.a"), child("root.b"))

    root, children = asyncio.run(run())
    assert {c.trace_id for c in children} == {root.trace_id}
    assert {c.parent_span_id for c in children} == {root.span_id}
    assert len({c.span_id for c in children}) == 2
    seen = []
    thread = threading.Thread(target=lambda: seen.append(current_trace_context()))
    thread.start()
    thread.join()
    assert seen == [None]


def test_ac_trace_008_concurrent_child_budget_race_never_admits_over_cap():
    lines = Lines()
    call = bound(lines, TRACE_MAX_BASELINE_CHILDREN=1)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        waiting = 0

        async def child(name):
            nonlocal waiting
            with trace(name):
                waiting += 1
                if waiting == 2:
                    entered.set()
                await release.wait()

        with call("root"):
            first = asyncio.create_task(child("root.a"))
            second = asyncio.create_task(child("root.b"))
            await entered.wait()
            release.set()
            await asyncio.gather(first, second)

    asyncio.run(run())
    output = "".join(lines.lines)
    assert sum(name in output for name in ("root.a", "root.b")) == 1


def test_ac_trace_008_remote_context_cannot_change_local_policy():
    remote = TraceContext("a" * 32, "b" * 16, "c" * 32)
    lines = Lines()
    call = bind_trace(lambda: policy(TRACE_ENABLED=False), TraceSinks(human=lines), parent_context=remote)
    with call("worker.run") as handle:
        handle.note("private.detail")
    assert handle.context.trace_id == remote.trace_id and "private.detail" not in "".join(lines.lines)


def test_ac_trace_009_real_hard_kill_has_no_fabricated_end(tmp_path):
    """A process kill is distinct from an ordinary context-manager unwind."""
    import os
    import subprocess

    script = (
        "from icv_trace import trace\n"
        "import time\n"
        "with trace('worker.run'):\n"
        " print('BODY-ENTERED', flush=True)\n"
        " time.sleep(30)\n"
    )
    environment = os.environ.copy()
    environment["TRACE_ENABLED"] = "false"
    process = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment
    )
    assert process.stdout.readline().strip() == "BODY-ENTERED"
    process.kill()
    _, stderr = process.communicate(timeout=5)
    assert "start" in stderr and "end" not in stderr and "elapsed_ms=0" not in stderr


def test_ac_trace_009_incomplete_is_supervisor_record_not_fabricated_end():
    context = TraceContext("a" * 32, "b" * 16, "c" * 32, None, 1, "initial", None)
    lines = Lines()
    health = emit_incomplete(
        TraceIncomplete(context, "worker.run", "worker_lost"), policy=policy(), sinks=TraceSinks(human=lines)
    )
    output = "".join(lines.lines)
    assert not health.output_failed and "incomplete" in output and "elapsed_ms" not in output and "end" not in output
    with pytest.raises(TraceInputError):
        emit_incomplete(
            TraceIncomplete(context, "Bad", "worker_lost"), policy=policy(), sinks=TraceSinks(human=Lines())
        )


def test_ac_trace_010_public_shapes_are_immutable_and_optional_adapters_are_lazy():
    value = policy()
    sinks = TraceSinks(human=Lines())
    django_loaded_before = "django" in sys.modules
    adapters_loaded_before = "icv_trace.adapters" in sys.modules
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        value.TRACE_ENABLED = True
    with pytest.raises(TraceConfigurationError):
        bind_trace(lambda: policy(TRACE_DESTINATIONS=("structured",)), sinks)("op").__enter__()
    assert ("django" in sys.modules) is django_loaded_before
    assert ("icv_trace.adapters" in sys.modules) is adapters_loaded_before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"UNKNOWN": True},
        {"TRACE_ENABLED": 1},
        {"TRACE_LEVEL": "verbose"},
        {"TRACE_SCOPE": ()},
        {"TRACE_DESTINATIONS": ("human", "human")},
        {"TRACE_MAX_BYTES": True},
        {"TRACE_MAX_FIELDS": 25},
    ],
)
def test_ac_trace_013_policy_rejects_every_public_invalid_shape(kwargs):
    with pytest.raises(TraceConfigurationError):
        TracePolicy(**kwargs)


def test_ac_trace_011_hostmap_consumer_proof_is_external_to_adapter_conformance():
    pytest.xfail("Hostmap-only: parent trial owns real diagnosis, defect repair and held-out domain proof")


def test_ac_trace_012_stdout_and_sink_acknowledgement_contract(capsys):
    business = b"HOSTMAP-RESULT\n"

    def run(*, enabled: bool) -> Lines:
        structured = Lines()
        call = bind_trace(
            lambda: policy(TRACE_ENABLED=enabled, TRACE_DESTINATIONS=("structured",)),
            TraceSinks(structured=structured),
        )
        with call("hostmap.resolve") as handle:
            print(business.decode(), end="")
            handle.result(domain_outcome="resolved")
        assert structured.lines, "structured sink must receive records"
        return structured

    assert run(enabled=False) and capsys.readouterr().out.encode() == business
    assert run(enabled=True) and capsys.readouterr().out.encode() == business

    bad = Lines(returns="short")
    with bound(bad)("op") as handle:
        pass
    assert handle.output_failed and handle.output_error_type == "TraceSinkError"


def test_ac_trace_013_binding_routes_nested_calls_and_rejects_competing_binding():
    one, two = Lines(), Lines()
    first, second = bound(one), bound(two)
    with first("root"):
        with trace("root.child"):
            pass
        with pytest.raises(TraceInputError), second("other"):
            pass
    assert "root.child" in "".join(one.lines) and not two.lines


def test_ac_trace_013_context_restores_after_entry_and_exit_failure():
    prior = current_trace_context()
    with (
        pytest.raises(TraceConfigurationError),
        bind_trace(lambda: policy(TRACE_DESTINATIONS=("structured",)), TraceSinks())("bad.entry"),
    ):
        pass
    assert current_trace_context() is prior
    with pytest.raises(RuntimeError), bound(Lines())("bad.exit"):
        raise RuntimeError("application fault")
    assert current_trace_context() is prior


def test_ac_trace_014_retry_context_and_malformed_supervision_fail_before_writes():
    parent = TraceContext("a" * 32, "b" * 16, "c" * 32, None, 1, "initial", None)
    attempt = TraceAttempt("c" * 32, 2, "retry", "b" * 16)
    lines = Lines()
    retry = bind_trace(lambda: policy(), TraceSinks(human=lines), parent_context=parent, attempt=attempt)
    with retry("worker.run") as handle:
        assert handle.context.attempt_number == 2 and handle.context.predecessor_span_id == parent.span_id
    assert handle.context.operation_id == parent.operation_id
    bad_lines = Lines()
    with pytest.raises(TraceInputError):
        bind_trace(
            lambda: policy(),
            TraceSinks(human=bad_lines),
            parent_context=parent,
            attempt=TraceAttempt("d" * 32, 2, "retry", "b" * 16),
        )
    assert bad_lines.lines == []


@pytest.mark.parametrize("kind", ["retry", "continuation"])
def test_ac_trace_014_retry_and_continuation_have_new_span_same_operation(kind):
    parent = TraceContext("a" * 32, "b" * 16, "c" * 32)
    call = bind_trace(
        lambda: policy(),
        TraceSinks(human=Lines()),
        parent_context=parent,
        attempt=TraceAttempt(parent.operation_id, 2, kind, parent.span_id),
    )
    with call("worker.run") as handle:
        assert handle.context.span_id != parent.span_id
        assert handle.context.operation_id == parent.operation_id
        assert handle.context.parent_span_id == handle.context.predecessor_span_id == parent.span_id
