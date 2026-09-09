"""Run a dependency-free command, pipeline, service and remote-task trace example."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from secrets import token_hex
from typing import TextIO

from icv_trace import (
    HumanRenderer,
    HumanTraceSink,
    TraceContext,
    TraceIncomplete,
    TraceOutputHealth,
    TracePolicy,
    TraceSinkError,
    TraceSinks,
    bind_trace,
    emit_incomplete,
    trace,
)


class DemoValidationError(Exception):
    """The intentional service failure used by ``--fail``."""


class FlushingTextSink:
    """Caller-owned stream sink that acknowledges only after a flush."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def __call__(self, line: str) -> None:
        if self.stream.write(line) != len(line):
            raise TraceSinkError("short stream write")
        self.stream.flush()


def run_pipeline(*, fail: bool) -> dict[str, int]:
    """Services use the ambient command binding without knowing its sinks."""
    with trace("example.pipeline") as pipeline:
        with trace("example.service.load") as load:
            load.note("fixture_loaded", row_count=2)
            load.result(row_count=2)
        with trace("example.service.normalise") as normalise:
            if fail:
                raise DemoValidationError("the demonstration row is invalid")
            normalise.note("normalised", row_count=2, total_quantity=5)
            normalise.result(row_count=2, total_quantity=5)
        pipeline.result(row_count=2, total_quantity=5)
    return {"row_count": 2, "total_quantity": 5}


def report_output_health(*health: TraceOutputHealth) -> None:
    """Report diagnostic loss without replacing the command's domain result."""
    for item in health:
        if item.output_failed:
            try:
                print(f"Trace output failed: {item.output_error_type}", file=sys.stderr)
            except (OSError, ValueError):
                # A broken warning channel cannot replace the domain outcome.
                return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detail", action="store_true", help="Include admitted entry fields and notes.")
    parser.add_argument("--fail", action="store_true", help="Raise the intentional service validation failure.")
    parser.add_argument("--jsonl-file", type=Path, help="New JSONL file to receive exact structured records.")
    parser.add_argument(
        "--simulate-worker-lost",
        action="store_true",
        help="Emit a supervisor-owned incomplete observation for a dispatched remote task.",
    )
    return parser.parse_args()


def main() -> int:
    options = parse_args()
    jsonl_file: TextIO | None = None
    if options.jsonl_file is not None:
        jsonl_file = options.jsonl_file.open("x", encoding="utf-8")
    command_health: TraceOutputHealth | None = None
    sink: HumanTraceSink | None = None
    status = 0
    try:
        human = FlushingTextSink(sys.stderr)
        structured = FlushingTextSink(jsonl_file) if jsonl_file is not None else None
        sink = HumanTraceSink(
            human=human,
            structured=structured,
            renderer=HumanRenderer(
                labels={
                    "example.command": "trace command",
                    "example.pipeline": "pipeline",
                    "example.service.load": "load fixture",
                    "example.service.normalise": "normalise rows",
                    "example.worker.task": "remote worker task",
                }
            ),
        )
        policy = TracePolicy(
            TRACE_ENABLED=options.detail,
            TRACE_DESTINATIONS=("structured",),
        )
        command = bind_trace(lambda: policy, TraceSinks(structured=sink))
        try:
            with command("example.command", entrypoint="cli") as operation:
                report = run_pipeline(fail=options.fail)
                operation.result(**report)
                command_context = operation.context
        except DemoValidationError as exc:
            command_health = operation.output_health
            print(f"Command failed: {type(exc).__name__}", file=sys.stderr)
            status = 2
        else:
            command_health = operation.output_health
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))

        # A host stores this context at dispatch. This flag simulates a later
        # supervisor observation, rather than inferring incompleteness from a
        # missing terminal record.
        if status == 0 and options.simulate_worker_lost:
            assert command_context is not None
            remote_context = TraceContext(
                command_context.trace_id,
                token_hex(8),
                token_hex(16),
                command_context.span_id,
            )
            emit_incomplete(
                TraceIncomplete(remote_context, "example.worker.task", "worker_lost"),
                policy=policy,
                sinks=TraceSinks(structured=sink),
            )
        assert sink is not None and command_health is not None
        report_output_health(command_health, sink.output_health)
        return status
    finally:
        if jsonl_file is not None:
            try:
                jsonl_file.close()
            except (OSError, ValueError) as exc:
                try:
                    print(f"Trace output close failed: {type(exc).__name__}", file=sys.stderr)
                except (OSError, ValueError):
                    # The exhausted warning channel has no independent fallback.
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
