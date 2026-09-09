"""Readable, bounded projection of ``icv-trace.record.v1`` JSONL records."""

from __future__ import annotations

import json
import math
import re
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Mapping

from .core import TraceConfigurationError, TraceInputError, TraceOutputHealth, TraceSinkError

_NAME = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX16 = re.compile(r"[0-9a-f]{16}\Z")
_EXC = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_KINDS = {"start", "note", "end", "detail_suppressed", "incomplete"}
_SUPPRESSION_REASONS = {"event_limit", "byte_limit", "child_limit"}
_INCOMPLETE_REASONS = {"hard_timeout", "worker_lost", "dispatch_failed", "unknown_termination"}
_Sink = Callable[[str], object]


def _safe_text(value: object, *, limit: int = 256, nonempty: bool = False) -> str:
    if type(value) is not str or len(value) > limit or nonempty and not value:
        raise TraceInputError("invalid rendered trace value")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value):
        raise TraceInputError("invalid rendered trace value")
    return value


def _name(value: object) -> str:
    value = _safe_text(value, limit=96, nonempty=True)
    if not _NAME.fullmatch(value):
        raise TraceInputError("invalid rendered trace value")
    return value


def _identifier(value: object, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or not pattern.fullmatch(value) or not int(value, 16):
        raise TraceInputError("invalid rendered trace value")
    return value


def _scalar(value: object) -> object:
    if value is None or type(value) in {bool, int}:
        if type(value) is int and not -(1 << 63) <= value < (1 << 63):
            raise TraceInputError("invalid rendered trace value")
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is str:
        return _safe_text(value)
    raise TraceInputError("invalid rendered trace value")


def _fields(value: object) -> dict[str, object]:
    if type(value) is not dict or len(value) > 24:
        raise TraceInputError("invalid rendered trace fields")
    clean: dict[str, object] = {}
    for key, item in value.items():
        if type(key) is not str or not _KEY.fullmatch(key):
            raise TraceInputError("invalid rendered trace fields")
        clean[key] = _scalar(item)
    try:
        encoded = json.dumps(clean, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:  # Defensive against exotic dict subclasses.
        raise TraceInputError("invalid rendered trace fields") from exc
    if len(encoded.encode()) > 4096:
        raise TraceInputError("invalid rendered trace fields")
    return clean


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _render_fields(fields: dict[str, object]) -> str:
    return "".join(f" {key}={_json(value)}" for key, value in sorted(fields.items()))


def _count(record: dict[str, object], key: str) -> int:
    value = record.get(key)
    if type(value) is not int or not 0 <= value <= (1 << 64) - 1:
        raise TraceInputError("invalid rendered trace count")
    return value


class HumanRenderer:
    """Render one admitted JSONL trace record at a time for a human reader."""

    def __init__(self, *, labels: Mapping[str, str] | None = None, max_open_spans: int = 1024) -> None:
        if type(max_open_spans) is not int or not 1 <= max_open_spans <= 10000:
            raise TraceConfigurationError("invalid max_open_spans")
        if labels is not None and not isinstance(labels, Mapping):
            raise TraceConfigurationError("invalid labels")
        self._labels: dict[str, str] = {}
        if labels is not None:
            for operation, label in labels.items():
                try:
                    self._labels[_name(operation)] = _safe_text(label, limit=96, nonempty=True)
                except TraceInputError as exc:
                    raise TraceConfigurationError("invalid labels") from exc
        self._max_open_spans = max_open_spans
        self._spans: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._lock = threading.RLock()

    def render(self, line: str) -> str:
        """Project one complete ``icv-trace.record.v1`` JSONL line."""
        if type(line) is not str:
            raise TraceInputError("invalid rendered trace input")
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            raise TraceInputError("invalid rendered trace input") from None
        if type(record) is not dict:
            raise TraceInputError("invalid rendered trace input")
        return self._render(record)

    def _render(self, record: dict[str, object]) -> str:
        kind_value = record.get("kind")
        if record.get("schema") != "icv-trace.record.v1" or type(kind_value) is not str or kind_value not in _KINDS:
            raise TraceInputError("invalid rendered trace input")
        kind = kind_value
        operation = _name(record.get("operation"))
        trace_id = _identifier(record.get("trace_id"), _HEX32)
        span_id = _identifier(record.get("span_id"), _HEX16)
        _identifier(record.get("operation_id"), _HEX32)
        parent_span_id = record.get("parent_span_id")
        if parent_span_id is not None:
            parent_span_id = _identifier(parent_span_id, _HEX16)
        attempt_number = record.get("attempt_number", 1)
        attempt_kind = record.get("attempt_kind", "initial")
        predecessor = record.get("predecessor_span_id")
        if type(attempt_number) is not int or not 1 <= attempt_number <= 2147483647:
            raise TraceInputError("invalid rendered trace attempt")
        if type(attempt_kind) is not str or attempt_kind not in {"initial", "retry", "continuation"}:
            raise TraceInputError("invalid rendered trace attempt")
        if predecessor is not None:
            _identifier(predecessor, _HEX16)
        if attempt_kind == "initial" and (attempt_number != 1 or predecessor is not None):
            raise TraceInputError("invalid rendered trace attempt")
        if attempt_kind != "initial" and (attempt_number < 2 or predecessor is None):
            raise TraceInputError("invalid rendered trace attempt")

        with self._lock:
            identity = (trace_id, span_id)
            label = self._labels.get(operation, operation)
            attempt = "" if attempt_kind == "initial" else f" attempt={attempt_kind}:{attempt_number}"
            # Validate every presentation value before changing bounded span
            # state, so direct malformed input has no later indentation effect.
            rendered = self._line(kind, label, record, attempt)
            if kind == "start":
                depth = self._spans.get((trace_id, parent_span_id), -1) + 1 if parent_span_id else 0
                depth = min(depth, 32)
                self._spans[identity] = depth
                self._spans.move_to_end(identity)
                while len(self._spans) > self._max_open_spans:
                    self._spans.popitem(last=False)
            else:
                depth = self._spans.get(identity, -1)
                if depth == -1:
                    depth = min(self._spans.get((trace_id, parent_span_id), -1) + 1, 32) if parent_span_id else 0
            prefix = "  " * depth
            if kind in {"end", "incomplete"}:
                self._spans.pop(identity, None)
            return prefix + rendered + "\n"

    def _line(self, kind: str, label: str, record: dict[str, object], attempt: str) -> str:
        if kind == "start":
            fields = _fields(record.get("fields", {}))
            return f"START {label}{attempt}{_render_fields(fields)}"
        if kind == "note":
            event = _name(record.get("event"))
            if "fields" not in record:
                raise TraceInputError("invalid rendered trace input")
            return f"NOTE {label}{attempt}: {event}{_render_fields(_fields(record['fields']))}"
        if kind == "detail_suppressed":
            reason = _name(record.get("reason"))
            if reason not in _SUPPRESSION_REASONS:
                raise TraceInputError("invalid rendered trace input")
            omitted = _count(record, "omitted_detail_records")
            children = _count(record, "omitted_child_operations")
            saturated = record.get("counts_saturated")
            if type(saturated) is not bool:
                raise TraceInputError("invalid rendered trace count")
            marker = ">=" if saturated else ""
            return (
                f"DETAIL SUPPRESSED {label}{attempt}: reason={reason} "
                f"omitted_detail_records={marker}{omitted} omitted_child_operations={marker}{children}"
            )
        if kind == "incomplete":
            if record.get("execution") != "incomplete":
                raise TraceInputError("invalid rendered trace input")
            reason = _name(record.get("reason"))
            if reason not in _INCOMPLETE_REASONS:
                raise TraceInputError("invalid rendered trace input")
            return f"INCOMPLETE {label}{attempt}: reason={reason}"
        execution = record.get("execution")
        if (
            type(execution) is not str
            or execution not in {"returned", "failed", "cancelled"}
            or type(record.get("reported")) is not bool
        ):
            raise TraceInputError("invalid rendered trace input")
        elapsed = record.get("elapsed_ms")
        if type(elapsed) is int:
            valid_elapsed = 0 <= elapsed < (1 << 63)
        elif type(elapsed) is float:
            valid_elapsed = math.isfinite(elapsed) and elapsed >= 0
        else:
            valid_elapsed = False
        if not valid_elapsed:
            raise TraceInputError("invalid rendered trace input")
        reported = record["reported"]
        if reported and "fields" not in record:
            raise TraceInputError("invalid rendered trace input")
        if reported:
            fields = _fields(record["fields"])
        else:
            if "fields" in record:
                raise TraceInputError("invalid rendered trace input")
            fields = {}
        state = "reported" if reported else "unreported"
        verb = {"returned": "END", "failed": "FAIL", "cancelled": "CANCELLED"}[execution]
        exception = ""
        if execution != "returned":
            value = record.get("exception_type")
            if type(value) is not str or not _EXC.fullmatch(value):
                raise TraceInputError("invalid rendered trace input")
            exception = f" exception={value}"
        omissions = ""
        if "omitted_detail_records" in record or "omitted_child_operations" in record or "counts_saturated" in record:
            omitted = _count(record, "omitted_detail_records")
            children = _count(record, "omitted_child_operations")
            saturated = record.get("counts_saturated")
            if type(saturated) is not bool:
                raise TraceInputError("invalid rendered trace count")
            marker = ">=" if saturated else ""
            if omitted or children:
                omissions = f" omitted_detail_records={marker}{omitted} omitted_child_operations={marker}{children}"
        return f"{verb} {label}{attempt}{exception} {state}{_render_fields(fields)}{omissions} elapsed_ms={elapsed:.1f}"


class HumanTraceSink:
    """Fan one JSONL record out to readable and optional structured sinks."""

    def __init__(
        self,
        *,
        human: _Sink,
        structured: _Sink | None = None,
        renderer: HumanRenderer | None = None,
    ) -> None:
        if not callable(human) or structured is not None and not callable(structured) or human is structured:
            raise TraceConfigurationError("invalid human trace sink")
        if renderer is not None and not isinstance(renderer, HumanRenderer):
            raise TraceConfigurationError("invalid human renderer")
        self._human = human
        self._structured = structured
        self._renderer = renderer or HumanRenderer()
        self._lock = threading.RLock()
        self._error_type: str | None = None

    @property
    def output_health(self) -> TraceOutputHealth:
        with self._lock:
            return TraceOutputHealth(self._error_type is not None, self._error_type)

    @property
    def output_failed(self) -> bool:
        return self.output_health.output_failed

    @property
    def output_error_type(self) -> str | None:
        return self.output_health.output_error_type

    def _failed(self, exc: Exception) -> None:
        if self._error_type is None:
            name = type(exc).__name__
            self._error_type = name if _EXC.fullmatch(name) else "unknown_exception"

    @staticmethod
    def _deliver(sink: _Sink, line: str) -> None:
        if sink(line) is not None:
            raise TraceSinkError("sink acknowledgement was not None")

    def __call__(self, line: str) -> None:
        with self._lock:
            try:
                rendered = self._renderer.render(line)
            except Exception as exc:
                self._failed(exc)
            else:
                try:
                    self._deliver(self._human, rendered)
                except Exception as exc:
                    self._failed(exc)
            if self._structured is not None:
                try:
                    self._deliver(self._structured, line)
                except Exception as exc:
                    self._failed(exc)
