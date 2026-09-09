"""Direct-core hardening checks. Clock/counter seams are labelled test injection."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading

import pytest

from icv_trace import (
    TraceConfigurationError,
    TraceContext,
    TraceInputError,
    TracePolicy,
    TraceSinks,
    bind_trace,
    current_trace_context,
    trace,
)


class Lines:
    def __init__(self):
        self.lines = []

    def __call__(self, line):
        self.lines.append(line)


def policy(**changes):
    base = dict(
        TRACE_ENABLED=True,
        TRACE_LEVEL="detail",
        TRACE_DESTINATIONS=("human",),
        TRACE_MAX_DETAIL_EVENTS=100,
        TRACE_MAX_BYTES=16384,
        TRACE_MAX_BASELINE_CHILDREN=1000,
        TRACE_MAX_FIELDS=24,
        TRACE_MAX_STRING_CHARS=256,
    )
    base.update(changes)
    return TracePolicy(**base)


def call(lines, **changes):
    return bind_trace(lambda: policy(**changes), TraceSinks(human=lines))


@pytest.mark.parametrize("name", ["a", "a.b", "a_b", "a-b", "a1.b2"])
def test_core_valid_operation_grammar(name):
    with call(Lines())(name):
        pass


@pytest.mark.parametrize("name", ["", "A", "a.", "a..b", "a/", "a" * 97])
def test_core_invalid_name_is_trace_input_before_any_start(name):
    out = Lines()
    with pytest.raises(TraceInputError), call(out)(name):
        pass
    assert out.lines == []


@pytest.mark.parametrize("raw", ["1", "yes", " false ", "", "True,False"])
def test_core_environment_boolean_grammar_is_exact(monkeypatch, raw):
    monkeypatch.setenv("TRACE_ENABLED", raw)
    with pytest.raises(TraceConfigurationError), trace("environment.invalid"):
        pass


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("TRACE_MAX_FIELDS", " 2"),
        ("TRACE_MAX_FIELDS", "+2"),
        ("TRACE_SCOPE", "root, ,other"),
        ("TRACE_DESTINATIONS", "human,structured,human"),
    ],
)
def test_core_environment_tuple_and_integer_boundaries(monkeypatch, key, raw):
    monkeypatch.setenv(key, raw)
    with pytest.raises(TraceConfigurationError), trace("environment.invalid"):
        pass


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("TRACE_MAX_DETAIL_EVENTS", "000"),
        ("TRACE_MAX_DETAIL_EVENTS", "1000"),
        ("TRACE_MAX_BYTES", "01024"),
        ("TRACE_MAX_BYTES", "65536"),
        ("TRACE_MAX_BASELINE_CHILDREN", "0"),
        ("TRACE_MAX_BASELINE_CHILDREN", "1000"),
        ("TRACE_MAX_FIELDS", "1"),
        ("TRACE_MAX_FIELDS", "24"),
        ("TRACE_MAX_STRING_CHARS", "1"),
        ("TRACE_MAX_STRING_CHARS", "256"),
    ],
)
def test_core_environment_ascii_decimal_limits_include_leading_zeroes(monkeypatch, key, raw):
    monkeypatch.setenv(key, raw)
    with trace("environment.valid"):
        pass


@pytest.mark.parametrize("value", [True, 1.0, [], {}, object()])
def test_core_policy_unhashable_or_wrong_enum_values_raise_configuration_error(value):
    with pytest.raises(TraceConfigurationError):
        TracePolicy(TRACE_LEVEL=value)
    with pytest.raises(TraceConfigurationError):
        TracePolicy(TRACE_SCOPE=value)
    with pytest.raises(TraceConfigurationError):
        TracePolicy(TRACE_DESTINATIONS=value)


def test_core_active_root_policy_is_immutable_and_children_inherit_summary_scope():
    lines = Lines()
    resolver_value = policy(TRACE_LEVEL="summary", TRACE_SCOPE=("selected",))
    configured = bind_trace(lambda: resolver_value, TraceSinks(human=lines))
    with configured("selected") as root, trace("unrelated.child") as child:
        child.note("note", evidence=1)
    assert "unrelated.child" in "".join(lines.lines)
    assert "TRACE note unrelated.child" not in "".join(lines.lines)
    assert root.context.trace_id


def test_core_invalid_entry_emits_nothing_but_invalid_note_result_keep_existing_baseline():
    out = Lines()
    with call(out)("body") as handle:
        with pytest.raises(TraceInputError):
            handle.note("Bad")
        with pytest.raises(TraceInputError):
            handle.result(bad=object())
        handle.result(ok=True)
    text = "".join(out.lines)
    assert "TRACE start body" in text and "TRACE end body" in text and '"ok":true' in text


def test_core_disabled_and_exhausted_validate_inputs_but_keep_valid_terminal_result():
    out = Lines()
    with call(out, TRACE_ENABLED=False, TRACE_MAX_DETAIL_EVENTS=0)("disabled") as handle:
        with pytest.raises(TraceInputError):
            handle.note("note", bad=object())
        handle.result(count=0)
    assert '"count":0' in "".join(out.lines)


def test_core_exact_event_capacity_and_event_reason_wins_when_both_limits_fail():
    out = Lines()
    with call(out, TRACE_MAX_DETAIL_EVENTS=1)("capacity") as handle:
        handle.note("one")
        handle.note("two")
    assert "TRACE note capacity" in "".join(out.lines)
    assert 'reason="event_limit"' in "".join(out.lines)
    out = Lines()
    with call(out, TRACE_MAX_DETAIL_EVENTS=0, TRACE_MAX_BYTES=1024)("both") as handle:
        handle.note("refused", value="x" * 256)
    assert 'reason="event_limit"' in "".join(out.lines)


def test_core_saturation_fault_injection_is_not_black_box_evidence():
    import icv_trace.core as core

    out = Lines()
    with call(out, TRACE_MAX_DETAIL_EVENTS=0)("saturate") as handle:
        handle._root.omitted_detail = core._MAX_U64 - 1  # infeasible public path
        handle.note("one")
        handle.note("two")
    end = next(line for line in out.lines if line.startswith("TRACE end saturate"))
    assert f"omitted_detail_records={core._MAX_U64}" in end and "counts_saturated=true" in end


def test_core_closed_record_shape_and_reserved_fields_cannot_be_overwritten():
    out = Lines()
    structured = Lines()
    bound = bind_trace(
        lambda: policy(TRACE_DESTINATIONS=("human", "structured")), TraceSinks(human=out, structured=structured)
    )
    with bound("shape", outcome="caller", elapsed_ms=1) as handle:
        handle.result(outcome="reported", elapsed_ms=2)
    import json

    end = next(json.loads(x) for x in structured.lines if '"kind":"end"' in x)
    assert end["execution"] == "returned" and end["fields"] == {"elapsed_ms": 2, "outcome": "reported"}
    assert set(end) == {
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
        "omitted_detail_records",
        "omitted_child_operations",
        "counts_saturated",
    }


def test_core_constructor_errors_are_contract_errors_not_type_errors():
    with pytest.raises(TraceInputError):
        TraceContext("a" * 32, "b" * 16, "c" * 32, attempt_number=True)
    with pytest.raises(TraceInputError):
        TraceContext("a" * 32, "b" * 16, "c" * 32, parent_span_id="0" * 16)
    with pytest.raises(TraceConfigurationError):
        TracePolicy(TRACE_SCOPE=["a"])
    with pytest.raises(TraceConfigurationError), bind_trace(lambda: policy(), TraceSinks(human=object()))("sink"):
        pass


@pytest.mark.parametrize("kwargs", [{"attempt_kind": []}, {"attempt_kind": object()}, {"attempt_number": 1.0}])
def test_core_context_unhashable_and_wrong_attempt_shapes_are_trace_input(kwargs):
    with pytest.raises(TraceInputError):
        TraceContext("a" * 32, "b" * 16, "c" * 32, **kwargs)


def test_core_ordinary_manager_constructed_before_parent_inherits_explicit_competitor_does_not():
    out = Lines()
    inherited = trace("ordinary")
    first, second = call(out), call(Lines())
    with first("root"):
        with inherited:
            pass
        with pytest.raises(TraceInputError), second("competing"):
            pass
    assert "ordinary" in "".join(out.lines)


def test_core_entry_failure_restores_context_and_thread_async_are_isolated():
    previous = current_trace_context()
    with (
        pytest.raises(TraceConfigurationError),
        bind_trace(lambda: policy(TRACE_DESTINATIONS=("structured",)), TraceSinks())("entry.fail"),
    ):
        pass
    assert current_trace_context() is previous
    observed = []
    thread = threading.Thread(target=lambda: observed.append(current_trace_context()))
    thread.start()
    thread.join()
    assert observed == [None]

    async def roots():
        async def one():
            with call(Lines())("async.root") as handle:
                return handle.context.trace_id

        return await asyncio.gather(one(), one())

    assert len(set(asyncio.run(roots()))) == 2


def test_core_shared_callable_across_distinct_roots_is_serialised_without_interleaved_lines():
    class SlowSink:
        def __init__(self):
            self.lock = threading.Lock()
            self.active = 0
            self.overlap = False
            self.entered = threading.Event()
            self.release = threading.Event()

        def __call__(self, _line):
            with self.lock:
                self.active += 1
                self.overlap |= self.active > 1
                self.entered.set()
            self.release.wait(1)
            with self.lock:
                self.active -= 1

    sink, errors = SlowSink(), []

    def worker(name):
        try:
            with bind_trace(lambda: policy(), TraceSinks(human=sink))(name):
                pass
        except BaseException as error:
            errors.append(error)

    one = threading.Thread(target=worker, args=("one",))
    two = threading.Thread(target=worker, args=("two",))
    one.start()
    assert sink.entered.wait(1)
    two.start()
    sink.release.set()
    one.join(2)
    two.join(2)
    assert not one.is_alive() and not two.is_alive() and not errors and not sink.overlap


def test_package_import_has_no_global_side_effects_instrumented_subprocess():
    # Every observation surrounds the import in the child itself. Parent state
    # cannot detect side effects that happen only in another interpreter.
    code = r"""
import logging, logging.config, os, socket, sqlite3, sys
from pathlib import Path
root = logging.getLogger()
before = (list(root.handlers), list(root.filters), root.level,
          logging.root.manager.disable, sys.gettrace(), sys.getprofile())
events = []
def record(event):
    def forbidden(*args, **kwargs):
        events.append(event)
    return forbidden
socket.socket.connect = record("network.connect")
socket.socket.connect_ex = record("network.connect_ex")
socket.create_connection = record("network.create_connection")
sqlite3.connect = record("database")
logging.basicConfig = record("logging.basicConfig")
logging.config.dictConfig = record("logging.dictConfig")
logging.config.fileConfig = record("logging.fileConfig")
logging.Logger.addHandler = record("logging.addHandler")
sys.settrace = record("sys.settrace")
sys.setprofile = record("sys.setprofile")
import icv_trace
assert not events, events
assert (list(root.handlers), list(root.filters), root.level,
        logging.root.manager.disable, sys.gettrace(), sys.getprofile()) == before
assert not {"django", "celery", "eliot", "opentelemetry"}.intersection(sys.modules)
if os.environ.get("ICV_TRACE_REQUIRE_WHEEL") == "1":
    assert "site-packages" in str(Path(icv_trace.__file__).resolve()), icv_trace.__file__
"""
    environment = os.environ.copy()
    result = subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
