"""Acceptance coverage for the standalone readable trace projection."""

from __future__ import annotations

import json
import threading
import time

import pytest

from icv_trace import (
    HumanRenderer,
    HumanTraceSink,
    TextIOSink,
    TraceConfigurationError,
    TraceInputError,
    TracePolicy,
    TraceSinks,
    bind_trace,
    trace,
)


def line(kind: str, operation: str = "demo.command", **more: object) -> str:
    record = {
        "schema": "icv-trace.record.v1",
        "kind": kind,
        "operation": operation,
        "trace_id": "1" * 32,
        "span_id": "2" * 16,
        "operation_id": "3" * 32,
        "parent_span_id": None,
        "attempt_number": 1,
        "attempt_kind": "initial",
        "predecessor_span_id": None,
        **more,
    }
    return json.dumps(record, separators=(",", ":")) + "\n"


def child(kind: str, span: str, parent: str, operation: str, **more: object) -> str:
    return line(kind, operation, span_id=span, parent_span_id=parent, **more)


def test_human_renderer_projects_a_nested_command_pipeline_and_services_with_labels():
    renderer = HumanRenderer(
        labels={
            "demo.command": "command",
            "demo.pipeline": "pipeline",
            "demo.service": "service",
        }
    )
    assert renderer.render(line("start", fields={"entrypoint": "management_command"})) == (
        'START command entrypoint="management_command"\n'
    )
    assert renderer.render(child("start", "4" * 16, "2" * 16, "demo.pipeline")) == "  START pipeline\n"
    assert renderer.render(child("start", "5" * 16, "4" * 16, "demo.service", fields={"source": "api"})) == (
        '    START service source="api"\n'
    )
    assert renderer.render(child("note", "5" * 16, "4" * 16, "demo.service", event="loaded", fields={"count": 0})) == (
        "    NOTE service: loaded count=0\n"
    )
    assert renderer.render(
        child(
            "end",
            "5" * 16,
            "4" * 16,
            "demo.service",
            execution="returned",
            reported=True,
            fields={"count": 0},
            elapsed_ms=1.5,
        )
    ) == ("    END service reported count=0 elapsed_ms=1.5\n")


def test_human_renderer_preserves_terminal_outcomes_suppression_and_attempts():
    renderer = HumanRenderer()
    assert renderer.render(line("end", execution="returned", reported=False, elapsed_ms=0)) == (
        "END demo.command unreported elapsed_ms=0.0\n"
    )
    assert renderer.render(line("end", execution="returned", reported=True, fields={}, elapsed_ms=0)) == (
        "END demo.command reported elapsed_ms=0.0\n"
    )
    assert renderer.render(
        line("end", execution="failed", exception_type="ValueError", reported=True, fields={"ok": False}, elapsed_ms=4)
    ) == ("FAIL demo.command exception=ValueError reported ok=false elapsed_ms=4.0\n")
    assert renderer.render(
        line("end", execution="cancelled", exception_type="KeyboardInterrupt", reported=False, elapsed_ms=4)
    ) == ("CANCELLED demo.command exception=KeyboardInterrupt unreported elapsed_ms=4.0\n")
    assert renderer.render(
        line(
            "detail_suppressed",
            reason="byte_limit",
            omitted_detail_records=2,
            omitted_child_operations=1,
            counts_saturated=True,
        )
    ) == ("DETAIL SUPPRESSED demo.command: reason=byte_limit omitted_detail_records=>=2 omitted_child_operations=>=1\n")
    retry = line(
        "incomplete",
        "worker.task",
        execution="incomplete",
        reason="worker_lost",
        attempt_number=2,
        attempt_kind="retry",
        predecessor_span_id="4" * 16,
    )
    assert renderer.render(retry) == "INCOMPLETE worker.task attempt=retry:2: reason=worker_lost\n"
    retry_note = line(
        "note",
        "worker.task",
        event="retried",
        fields={},
        attempt_number=2,
        attempt_kind="retry",
        predecessor_span_id="4" * 16,
    )
    assert renderer.render(retry_note) == "NOTE worker.task attempt=retry:2: retried\n"
    retry_suppression = line(
        "detail_suppressed",
        "worker.task",
        reason="byte_limit",
        omitted_detail_records=1,
        omitted_child_operations=0,
        counts_saturated=False,
        attempt_number=2,
        attempt_kind="retry",
        predecessor_span_id="4" * 16,
    )
    assert renderer.render(retry_suppression) == (
        "DETAIL SUPPRESSED worker.task attempt=retry:2: reason=byte_limit "
        "omitted_detail_records=1 omitted_child_operations=0\n"
    )


def test_human_renderer_bounds_spans_by_trace_and_keeps_missing_parents_usable():
    renderer = HumanRenderer(max_open_spans=1)
    assert renderer.render(line("start")) == "START demo.command\n"
    assert renderer.render(line("start", "other.root", span_id="4" * 16, trace_id="5" * 32)) == "START other.root\n"
    assert renderer.render(child("note", "2" * 16, "9" * 16, "demo.command", event="lost_parent", fields={})) == (
        "NOTE demo.command: lost_parent\n"
    )
    # The same span ID in another trace has independent indentation state.
    assert renderer.render(line("start", "other.again", span_id="2" * 16, trace_id="6" * 32)) == "START other.again\n"


def test_human_renderer_caps_indentation_at_32_levels():
    renderer = HumanRenderer(max_open_spans=40)
    parent = None
    for number in range(34):
        span = f"{number + 1:016x}"
        rendered = renderer.render(line("start", "depth.test", span_id=span, parent_span_id=parent))
        parent = span
    assert rendered == "  " * 32 + "START depth.test\n"


@pytest.mark.parametrize(
    "raw",
    [
        "not json\n",
        '{"schema":"icv-trace.record.v1","kind":"start","operation":"bad\\nname"}\n',
        line("start").replace('"kind":"start"', '"kind":"unknown"'),
        line("start", fields={"message": "bad\u0000value"}),
    ],
)
def test_human_renderer_rejects_invalid_input_without_echoing_it(raw):
    with pytest.raises(TraceInputError) as caught:
        HumanRenderer().render(raw)
    assert raw.strip() not in str(caught.value)


def test_human_renderer_rejects_unsafe_labels_and_limits():
    with pytest.raises(TraceConfigurationError):
        HumanRenderer(labels={"demo.command": "bad\nlabel"})
    with pytest.raises(TraceConfigurationError):
        HumanRenderer(max_open_spans=True)


def test_human_renderer_accepts_json_whitespace_without_a_final_lf():
    assert HumanRenderer().render("  " + line("start").rstrip("\n") + "  ") == "START demo.command\n"


def test_invalid_start_does_not_change_later_indentation():
    renderer = HumanRenderer()
    invalid = line("start", fields={"unsafe": "line\nbreak"})
    with pytest.raises(TraceInputError):
        renderer.render(invalid)
    assert renderer.render(child("start", "4" * 16, "2" * 16, "demo.service")) == "START demo.service\n"


def test_invalid_incomplete_reason_does_not_remove_a_live_span():
    renderer = HumanRenderer()
    renderer.render(line("start"))
    with pytest.raises(TraceInputError):
        renderer.render(line("incomplete", execution="incomplete", reason="not_a_reason"))
    assert renderer.render(child("start", "4" * 16, "2" * 16, "demo.service")) == "  START demo.service\n"


def test_terminal_removal_prevents_stale_parent_indentation():
    renderer = HumanRenderer()
    renderer.render(line("start"))
    renderer.render(line("end", execution="returned", reported=False, elapsed_ms=0))
    assert renderer.render(child("start", "4" * 16, "2" * 16, "demo.service")) == "START demo.service\n"


def test_human_trace_sink_fanout_attempts_both_destinations_and_preserves_jsonl():
    structured: list[str] = []

    def broken_human(_line: str) -> None:
        raise OSError("broken")

    sink = HumanTraceSink(human=broken_human, structured=structured.append)
    source = line("start", fields={"empty": ""})
    assert sink(source) is None
    assert structured == [source]
    assert sink.output_failed and sink.output_error_type == "OSError"

    human: list[str] = []

    def bad_ack(_line: str) -> int:
        return 1

    sink = HumanTraceSink(human=human.append, structured=bad_ack)
    sink(source)
    assert human == ['START demo.command empty=""\n']
    assert sink.output_error_type == "TraceSinkError"


def test_human_trace_sink_records_renderer_errors_then_attempts_structured():
    structured: list[str] = []
    sink = HumanTraceSink(human=lambda _line: None, structured=structured.append)
    source = "not json\n"
    sink(source)
    assert structured == [source]
    assert sink.output_error_type == "TraceInputError"


def test_human_trace_sink_integrates_with_core_for_command_pipeline_and_service():
    human: list[str] = []
    structured: list[str] = []
    sink = HumanTraceSink(human=human.append, structured=structured.append)
    policy = TracePolicy(TRACE_ENABLED=True, TRACE_DESTINATIONS=("structured",))
    run = bind_trace(lambda: policy, TraceSinks(structured=sink))

    with run("demo.command", entrypoint="management_command") as command:
        with trace("demo.pipeline"), trace("demo.service") as service:
            service.note("loaded", count=2)
            service.result(count=2)
        command.result(completed=True)

    assert human[:4] == [
        'START demo.command entrypoint="management_command"\n',
        "  START demo.pipeline\n",
        "    START demo.service\n",
        "    NOTE demo.service: loaded count=2\n",
    ]
    assert human[4].startswith("    END demo.service reported count=2 elapsed_ms=")
    assert human[5].startswith("  END demo.pipeline unreported elapsed_ms=")
    assert human[6].startswith("END demo.command reported completed=true elapsed_ms=")
    assert all(item.endswith("\n") for item in structured)
    assert [json.loads(item)["kind"] for item in structured] == ["start", "start", "start", "note", "end", "end", "end"]
    assert not sink.output_failed and not command.output_failed


def test_human_trace_sink_does_not_replace_a_body_exception_when_human_output_breaks():
    class DomainError(Exception):
        pass

    structured: list[str] = []

    def broken_human(_line: str) -> None:
        raise OSError("broken")

    sink = HumanTraceSink(human=broken_human, structured=structured.append)
    run = bind_trace(
        lambda: TracePolicy(TRACE_DESTINATIONS=("structured",)),
        TraceSinks(structured=sink),
    )
    error = DomainError("body")
    with pytest.raises(DomainError) as caught, run("demo.command"):
        raise error
    assert caught.value is error
    assert [json.loads(item)["execution"] for item in structured if '"kind":"end"' in item] == ["failed"]
    assert sink.output_error_type == "OSError"


def test_human_trace_sink_keeps_core_cancellation_and_its_own_health_visible():
    human: list[str] = []
    sink = HumanTraceSink(human=human.append)
    run = bind_trace(
        lambda: TracePolicy(TRACE_DESTINATIONS=("structured",)),
        TraceSinks(structured=sink),
    )
    cancellation = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as caught, run("demo.command") as operation:
        raise cancellation
    assert caught.value is cancellation
    assert any(item.startswith("CANCELLED demo.command exception=KeyboardInterrupt") for item in human)
    assert not operation.output_failed and not sink.output_failed


def test_human_trace_sink_records_textio_short_writes_and_keeps_structured_fanout():
    class ShortStream:
        def write(self, value: str) -> int:
            return len(value) - 1

    structured: list[str] = []
    sink = HumanTraceSink(human=TextIOSink(ShortStream()), structured=structured.append)
    source = line("start")
    sink(source)
    assert structured == [source]
    assert sink.output_error_type == "TraceSinkError"


def test_human_trace_sink_serialises_concurrent_fanout_and_trace_scoped_parents():
    class BlockingHuman:
        def __init__(self) -> None:
            self.active = 0
            self.overlap = False
            self.lines: list[str] = []
            self.entered = threading.Event()
            self.release = threading.Event()
            self.lock = threading.Lock()

        def __call__(self, line: str) -> None:
            with self.lock:
                self.active += 1
                self.overlap |= self.active > 1
                self.lines.append(line)
                self.entered.set()
            self.release.wait(1)
            with self.lock:
                self.active -= 1

    human = BlockingHuman()
    structured: list[str] = []
    sink = HumanTraceSink(human=human, structured=structured.append)
    first = threading.Thread(target=sink, args=(line("start", "thread.one", trace_id="4" * 32),))
    second = threading.Thread(target=sink, args=(line("start", "thread.two", trace_id="5" * 32),))
    first.start()
    assert human.entered.wait(1)
    second.start()
    time.sleep(0.02)
    human.release.set()
    first.join(1)
    second.join(1)
    assert not first.is_alive() and not second.is_alive() and not human.overlap
    assert [item.split()[1] for item in human.lines] == [json.loads(item)["operation"] for item in structured]

    renderer = HumanRenderer(max_open_spans=4)
    renderer.render(line("start", span_id="2" * 16, trace_id="4" * 32))
    renderer.render(line("start", span_id="6" * 16, parent_span_id="2" * 16, trace_id="4" * 32))
    renderer.render(line("start", span_id="6" * 16, trace_id="5" * 32))
    assert (
        renderer.render(child("note", "6" * 16, "2" * 16, "demo.service", trace_id="4" * 32, event="kept", fields={}))
        == "  NOTE demo.service: kept\n"
    )
    assert (
        renderer.render(child("note", "6" * 16, "2" * 16, "demo.service", trace_id="5" * 32, event="kept", fields={}))
        == "NOTE demo.service: kept\n"
    )


def test_human_trace_sink_rejects_invalid_configuration_and_preserves_interruptions():
    def destination(_line: str) -> None:
        return None

    with pytest.raises(TraceConfigurationError):
        HumanTraceSink(human=destination, structured=destination)
    with pytest.raises(TraceConfigurationError):
        HumanTraceSink(human=lambda _line: None, structured=lambda _line: None, renderer=object())  # type: ignore[arg-type]

    structured: list[str] = []

    def interrupted(_line: str) -> None:
        raise KeyboardInterrupt

    sink = HumanTraceSink(human=interrupted, structured=structured.append)
    source = line("start")
    with pytest.raises(KeyboardInterrupt):
        sink(source)
