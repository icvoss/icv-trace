"""Pure-Python core for the ICV Trace contract."""

from __future__ import annotations

import contextvars
import functools
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import unicodedata
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, cast

_NAME = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX16 = re.compile(r"[0-9a-f]{16}\Z")
_EXC = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_MAX_U64 = (1 << 64) - 1
_current: contextvars.ContextVar[TraceHandle | None] = contextvars.ContextVar("icv_trace", default=None)
_endpoint_registry_lock = threading.Lock()
_endpoint_registry: dict[int, _EndpointEntry] = {}


class TraceInputError(ValueError):
    pass


class TraceConfigurationError(ValueError):
    pass


class TraceSinkError(Exception):
    pass


@dataclass(slots=True)
class _EndpointEntry:
    sink: Callable[[str], None]
    lock: threading.RLock = field(default_factory=threading.RLock)
    leases: int = 0


def _acquire_endpoint_locks(policy: TracePolicy, sinks: TraceSinks) -> dict[str, threading.RLock]:
    """Lease one identity-only process lock per selected sink for one root.

    The registry holds a strong reference only while a root lease exists.
    This supports unhashable and non-weak-referenceable callable objects.
    Opaque wrappers remain host-owned distinct channels.
    """
    locks: dict[str, threading.RLock] = {}
    with _endpoint_registry_lock:
        try:
            for label in policy.TRACE_DESTINATIONS:
                sink = getattr(sinks, label)
                identifier = id(sink)
                entry = _endpoint_registry.get(identifier)
                if entry is None or entry.sink is not sink:
                    entry = _EndpointEntry(sink=sink)
                    _endpoint_registry[identifier] = entry
                entry.leases += 1
                locks[label] = entry.lock
        except BaseException:
            for label in locks:
                sink = getattr(sinks, label)
                entry = _endpoint_registry.get(id(sink))
                if entry is None or entry.sink is not sink:
                    continue
                entry.leases -= 1
                if entry.leases == 0:
                    del _endpoint_registry[id(sink)]
            raise
    return locks


def _release_endpoint_locks(root: _Root) -> None:
    """Drop root leases and the registry's final strong sink references."""
    with _endpoint_registry_lock:
        for label in root.endpoint_locks:
            sink = getattr(root.sinks, label)
            entry = _endpoint_registry.get(id(sink))
            if entry is None or entry.sink is not sink:
                continue
            entry.leases -= 1
            if entry.leases == 0:
                del _endpoint_registry[id(sink)]
        root.endpoint_locks.clear()


def _exc_name(value: object) -> str:
    name = value.__name__ if isinstance(value, type) else type(value).__name__
    return name if isinstance(name, str) and _EXC.fullmatch(name) else "unknown_exception"


def _id(n: int) -> str:
    return secrets.token_hex(n // 2)


def _valid_name(value: str) -> None:
    if type(value) is not str or not (1 <= len(value) <= 96) or not _NAME.fullmatch(value):
        raise TraceInputError("invalid trace operation name")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _mapping(fields: dict[str, object], policy: TracePolicy) -> dict[str, object]:
    if len(fields) > policy.TRACE_MAX_FIELDS:
        raise TraceInputError("too many trace fields")
    out: dict[str, object] = {}
    for key, value in fields.items():
        if type(key) is not str or not _KEY.fullmatch(key):
            raise TraceInputError("invalid trace field key")
        if (
            value is None
            or type(value) in (str, bool)
            or type(value) is int
            and -(1 << 63) <= value < (1 << 63)
            or type(value) is float
            and math.isfinite(value)
        ):
            pass
        else:
            raise TraceInputError("invalid trace field value")
        if type(value) is str:
            if len(value) > policy.TRACE_MAX_STRING_CHARS:
                raise TraceInputError("invalid trace field string")
            # Printable ASCII has none of the excluded Unicode categories.
            # Keep the full category check for every non-ASCII value so this
            # fast path changes neither the accepted grammar nor JSON output.
            if value.isascii():
                invalid_string = not value.isprintable()
            else:
                invalid_string = any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for c in value)
            if invalid_string:
                raise TraceInputError("invalid trace field string")
        out[key] = value
    if len(_json(out).encode()) > 4096:
        raise TraceInputError("trace field mapping too large")
    return out


@dataclass(frozen=True, slots=True, init=False)
class TracePolicy:
    TRACE_ENABLED: bool = False
    TRACE_LEVEL: Literal["summary", "detail"] = "detail"
    TRACE_SCOPE: tuple[str, ...] = ("*",)
    TRACE_DESTINATIONS: tuple[str, ...] = ("human",)
    TRACE_MAX_DETAIL_EVENTS: int = 100
    TRACE_MAX_BYTES: int = 16384
    TRACE_MAX_BASELINE_CHILDREN: int = 1000
    TRACE_MAX_FIELDS: int = 24
    TRACE_MAX_STRING_CHARS: int = 256

    def __init__(self, **settings: object) -> None:
        names = set(type(self).__dataclass_fields__)
        if set(settings) - names:
            raise TraceConfigurationError("unknown trace policy setting")
        for name, definition in type(self).__dataclass_fields__.items():
            object.__setattr__(self, name, settings.get(name, definition.default))
        self.__post_init__()

    def __post_init__(self) -> None:
        if (
            type(self.TRACE_ENABLED) is not bool
            or type(self.TRACE_LEVEL) is not str
            or self.TRACE_LEVEL not in {"summary", "detail"}
        ):
            raise TraceConfigurationError("invalid trace policy")
        if type(self.TRACE_SCOPE) is not tuple or not 1 <= len(self.TRACE_SCOPE) <= 32:
            raise TraceConfigurationError("invalid TRACE_SCOPE")
        for prefix in self.TRACE_SCOPE:
            if type(prefix) is not str or len(prefix) > 96 or (prefix != "*" and not _NAME.fullmatch(prefix)):
                raise TraceConfigurationError("invalid TRACE_SCOPE")
        if len(set(self.TRACE_SCOPE)) != len(self.TRACE_SCOPE):
            raise TraceConfigurationError("invalid TRACE_SCOPE")
        if self.TRACE_SCOPE != ("*",) and "*" in self.TRACE_SCOPE:
            raise TraceConfigurationError("invalid TRACE_SCOPE")
        if type(self.TRACE_DESTINATIONS) is not tuple or not self.TRACE_DESTINATIONS:
            raise TraceConfigurationError("invalid TRACE_DESTINATIONS")
        if (
            any(type(destination) is not str for destination in self.TRACE_DESTINATIONS)
            or len(set(self.TRACE_DESTINATIONS)) != len(self.TRACE_DESTINATIONS)
            or set(self.TRACE_DESTINATIONS) - {"human", "structured"}
        ):
            raise TraceConfigurationError("invalid TRACE_DESTINATIONS")
        for val, lo, hi in (
            (self.TRACE_MAX_DETAIL_EVENTS, 0, 1000),
            (self.TRACE_MAX_BYTES, 1024, 65536),
            (self.TRACE_MAX_BASELINE_CHILDREN, 0, 1000),
            (self.TRACE_MAX_FIELDS, 1, 24),
            (self.TRACE_MAX_STRING_CHARS, 1, 256),
        ):
            if type(val) is not int or not lo <= val <= hi:
                raise TraceConfigurationError("invalid trace limit")

    def selected(self, name: str) -> bool:
        return "*" in self.TRACE_SCOPE or any(name == p or name.startswith(p + ".") for p in self.TRACE_SCOPE)


@dataclass(frozen=True, slots=True, init=False)
class TraceSinks:
    human: Callable[[str], None] | None = None
    structured: Callable[[str], None] | None = None

    def __init__(
        self,
        human: Callable[[str], None] | None = None,
        structured: Callable[[str], None] | None = None,
        **unknown: object,
    ) -> None:
        if unknown:
            raise TraceConfigurationError("unknown trace sink setting")
        if human is not None and not callable(human) or structured is not None and not callable(structured):
            raise TraceConfigurationError("trace sink must be callable")
        object.__setattr__(self, "human", human)
        object.__setattr__(self, "structured", structured)

    def validate(self, policy: TracePolicy) -> None:
        for label in policy.TRACE_DESTINATIONS:
            if not callable(getattr(self, label)):
                raise TraceConfigurationError(f"missing {label} sink")
        if (
            "human" in policy.TRACE_DESTINATIONS
            and "structured" in policy.TRACE_DESTINATIONS
            and self.human is self.structured
        ):
            raise TraceConfigurationError("sinks must be distinct")


@dataclass(frozen=True, slots=True, init=False)
class TraceContext:
    trace_id: str
    span_id: str
    operation_id: str
    parent_span_id: str | None = None
    attempt_number: int = 1
    attempt_kind: Literal["initial", "retry", "continuation"] = "initial"
    predecessor_span_id: str | None = None

    def __init__(
        self,
        trace_id: str,
        span_id: str,
        operation_id: str,
        parent_span_id: str | None = None,
        attempt_number: int = 1,
        attempt_kind: Literal["initial", "retry", "continuation"] = "initial",
        predecessor_span_id: str | None = None,
        **unknown: object,
    ) -> None:
        if unknown:
            raise TraceInputError("unknown trace context field")
        for key, value in (
            ("trace_id", trace_id),
            ("span_id", span_id),
            ("operation_id", operation_id),
            ("parent_span_id", parent_span_id),
            ("attempt_number", attempt_number),
            ("attempt_kind", attempt_kind),
            ("predecessor_span_id", predecessor_span_id),
        ):
            object.__setattr__(self, key, value)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not all(
            type(x) is str and pat.fullmatch(x) and int(x, 16)
            for x, pat in (
                (self.trace_id, _HEX32),
                (self.span_id, _HEX16),
                (self.operation_id, _HEX32),
            )
        ):
            raise TraceInputError("invalid trace context id")
        for x in (self.parent_span_id, self.predecessor_span_id):
            if x is not None and (type(x) is not str or not _HEX16.fullmatch(x) or not int(x, 16)):
                raise TraceInputError("invalid trace context parent")
        if (
            self.parent_span_id == self.span_id
            or type(self.attempt_number) is not int
            or not 1 <= self.attempt_number <= 2147483647
        ):
            raise TraceInputError("invalid trace attempt")
        if type(self.attempt_kind) is not str or self.attempt_kind not in {
            "initial",
            "retry",
            "continuation",
        }:
            raise TraceInputError("invalid trace attempt")
        if self.attempt_kind == "initial" and (self.attempt_number != 1 or self.predecessor_span_id is not None):
            raise TraceInputError("invalid initial attempt")
        if self.attempt_kind != "initial" and (
            self.attempt_number < 2 or self.predecessor_span_id is None or self.predecessor_span_id == self.span_id
        ):
            raise TraceInputError("invalid continued attempt")


@dataclass(frozen=True, slots=True, init=False)
class TraceAttempt:
    operation_id: str
    number: int
    kind: Literal["retry", "continuation"]
    predecessor_span_id: str

    def __init__(
        self,
        operation_id: str,
        number: int,
        kind: Literal["retry", "continuation"],
        predecessor_span_id: str,
        **unknown: object,
    ) -> None:
        if unknown:
            raise TraceInputError("unknown trace attempt field")
        object.__setattr__(self, "operation_id", operation_id)
        object.__setattr__(self, "number", number)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "predecessor_span_id", predecessor_span_id)
        self.__post_init__()

    def __post_init__(self) -> None:
        if (
            type(self.operation_id) is not str
            or not _HEX32.fullmatch(self.operation_id)
            or not int(self.operation_id, 16)
            or type(self.number) is not int
            or not 2 <= self.number <= 2147483647
            or type(self.kind) is not str
            or self.kind not in {"retry", "continuation"}
            or type(self.predecessor_span_id) is not str
            or not _HEX16.fullmatch(self.predecessor_span_id)
            or not int(self.predecessor_span_id, 16)
        ):
            raise TraceInputError("invalid trace attempt")


@dataclass(frozen=True, slots=True, init=False)
class TraceIncomplete:
    context: TraceContext
    operation: str
    reason: Literal["hard_timeout", "worker_lost", "dispatch_failed", "unknown_termination"]

    def __init__(
        self,
        context: TraceContext,
        operation: str,
        reason: Literal["hard_timeout", "worker_lost", "dispatch_failed", "unknown_termination"],
        **unknown: object,
    ) -> None:
        if unknown:
            raise TraceInputError("unknown incomplete observation field")
        object.__setattr__(self, "context", context)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "reason", reason)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.context, TraceContext):
            raise TraceInputError("invalid incomplete context")
        _valid_name(self.operation)
        if type(self.reason) is not str or self.reason not in {
            "hard_timeout",
            "worker_lost",
            "dispatch_failed",
            "unknown_termination",
        }:
            raise TraceInputError("invalid incomplete reason")


@dataclass(frozen=True, slots=True)
class TraceOutputHealth:
    output_failed: bool = False
    output_error_type: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.output_failed) is not bool
            or self.output_error_type is not None
            and (type(self.output_error_type) is not str or not _EXC.fullmatch(self.output_error_type))
        ):
            raise TraceInputError("invalid output health")


@dataclass(slots=True)
class _Root:
    policy: TracePolicy
    sinks: TraceSinks
    lock: threading.RLock = field(default_factory=threading.RLock)
    failures: dict[str, str] = field(default_factory=dict)
    events: int = 0
    bytes: int = 0
    children: int = 0
    omitted_detail: int = 0
    omitted_children: int = 0
    saturated: bool = False
    suppression: bool = False
    scope_selected: bool = False
    root_handle: TraceHandle | None = None
    pending_suppression: str | None = None
    endpoint_locks: dict[str, threading.RLock] = field(default_factory=dict)

    def increment(self, attr: str) -> None:
        old = getattr(self, attr)
        if old == _MAX_U64:
            self.saturated = True
        else:
            setattr(self, attr, old + 1)


def _record(kind: str, operation: str, context: TraceContext, **more: object) -> dict[str, object]:
    return {
        "schema": "icv-trace.record.v1",
        "kind": kind,
        "operation": operation,
        "trace_id": context.trace_id,
        "span_id": context.span_id,
        "operation_id": context.operation_id,
        "parent_span_id": context.parent_span_id,
        "attempt_number": context.attempt_number,
        "attempt_kind": context.attempt_kind,
        "predecessor_span_id": context.predecessor_span_id,
        **more,
    }


def _human(record: dict[str, object]) -> str:
    return (
        "TRACE "
        + str(record["kind"])
        + " "
        + str(record["operation"])
        + "".join(
            " " + k + "=" + _json(v) for k, v in sorted(record.items()) if k not in {"schema", "kind", "operation"}
        )
        + "\n"
    )


def _structured(record: dict[str, object]) -> str:
    return _json(record) + "\n"


class TraceHandle:
    def __init__(
        self,
        name: str,
        fields: dict[str, object],
        factory: _Factory,
        parent: TraceHandle | None,
    ) -> None:
        self.name, self._fields, self._factory, self._parent = (
            name,
            fields,
            factory,
            parent,
        )
        self._entered = self._closed = False
        self._token: contextvars.Token[TraceHandle | None] | None = None
        self._root: _Root | None = None
        self._reported: dict[str, object] | None = None
        self.context: TraceContext | None = None
        self._start = 0.0
        self._admitted = True
        self._local_failure: str | None = None
        self._ambient = False
        # Weak state references let a callback wrapper outlive its registration
        # without making this trace handle, or its captured Context, live past
        # scope exit.
        self._callback_captures: weakref.WeakSet[_CapturedCallbackState] = weakref.WeakSet()

    @property
    def output_failed(self) -> bool:
        return bool(self._root and (self._root.failures if self._parent is None else self._local_failure))

    @property
    def output_error_type(self) -> str | None:
        return (
            next(iter(self._root.failures.values()), None)
            if self._root and self._parent is None
            else self._local_failure
        )

    @property
    def output_health(self) -> TraceOutputHealth:
        return TraceOutputHealth(self.output_failed, self.output_error_type)

    def __enter__(self) -> TraceHandle:
        if self._entered:
            raise TraceInputError("trace handle cannot be re-entered")

        try:
            return self._enter()
        except BaseException:
            # Entry can fail before a context token or sink lease exists.  A
            # failed manager is permanently closed, but a failed child never
            # releases its active root's lease or restores its parent's token.
            self._closed = True
            if self._token is not None:
                _current.reset(self._token)
                self._token = None
            if self._parent is None and self._root is not None:
                _release_endpoint_locks(self._root)
            raise

    def _enter(self) -> TraceHandle:
        self._entered = True
        active = _current.get()
        if active is not None and self._parent is None:
            if active._closed:
                self._closed = True
                raise TraceInputError("cannot enter a child of a closed trace handle")
            if self._ambient:
                # Ordinary trace calls inherit the binding active at entry,
                # including a manager constructed before its parent entered.
                self._factory = active._factory
            elif active._factory is not self._factory:
                self._closed = True
                raise TraceInputError("cannot enter explicit binding inside active root")
            self._parent = active
        if self._parent is None:
            try:
                policy = self._factory.resolver()
            except BaseException:
                self._closed = True
                raise
            if not isinstance(policy, TracePolicy):
                self._closed = True
                raise TraceConfigurationError("resolver must return TracePolicy")
            try:
                self._factory.sinks.validate(policy)
            except BaseException:
                self._closed = True
                raise
            self._root = _Root(policy, self._factory.sinks, scope_selected=policy.selected(self.name))
            parent = self._factory.parent
            attempt = self._factory.attempt
            if attempt is not None and parent is None:
                raise TraceInputError("attempt requires parent context")
            if attempt is not None:
                parent = cast(TraceContext, parent)
                if (
                    attempt.operation_id != parent.operation_id
                    or attempt.predecessor_span_id != parent.span_id
                    or attempt.number != parent.attempt_number + 1
                ):
                    raise TraceInputError("attempt does not follow parent")
                self.context = TraceContext(
                    parent.trace_id,
                    _id(16),
                    attempt.operation_id,
                    parent.span_id,
                    attempt.number,
                    attempt.kind,
                    parent.span_id,
                )
            elif parent:
                self.context = TraceContext(parent.trace_id, _id(16), _id(32), parent.span_id)
            else:
                self.context = TraceContext(_id(32), _id(16), _id(32))
        else:
            self._root = self._parent._root
            assert self._root and self._parent.context
            try:
                # Invalid child input is a caller error, not an omitted
                # operation.  Validate before touching shared admission state.
                self._fields = _mapping(self._fields, self._root.policy)
            except BaseException:
                self._closed = True
                raise
            with self._root.lock:
                if not self._parent._admitted:
                    # An excluded branch emits no individual lifecycle, but
                    # every attempted descendant remains an explicit omission.
                    self._admitted = False
                    self._root.increment("omitted_children")
                elif self._root.children >= self._root.policy.TRACE_MAX_BASELINE_CHILDREN:
                    self._admitted = False
                    self._root.increment("omitted_children")
                    self._suppress("child_limit")
                else:
                    self._root.children += 1
            self.context = TraceContext(
                self._parent.context.trace_id,
                _id(16),
                _id(32),
                self._parent.context.span_id,
            )
        assert self._root
        try:
            if self._parent is None:
                self._fields = _mapping(self._fields, self._root.policy)
                self._root.endpoint_locks = _acquire_endpoint_locks(self._root.policy, self._root.sinks)
        except BaseException:
            self._closed = True
            if self._parent is None:
                _release_endpoint_locks(self._root)
            raise
        self._token = _current.set(self)
        try:
            if self._parent is None:
                self._root.root_handle = self
            if self._admitted and not self._try_entry_detail():
                self._emit("start")
                if self._root.pending_suppression is not None:
                    self._suppress(self._root.pending_suppression)
            self._start = time.monotonic()
        except BaseException:
            _current.reset(self._token)
            self._token = None
            self._closed = True
            if self._parent is None:
                _release_endpoint_locks(self._root)
            raise
        return self

    def _detail_allowed(self) -> bool:
        assert self._root
        return self._root.policy.TRACE_ENABLED and self._root.scope_selected

    def _try_entry_detail(self) -> bool:
        assert self._root
        if (
            not self._fields
            or not self._detail_allowed()
            or (self._parent and self._root.policy.TRACE_LEVEL != "detail")
        ):
            return False
        return self._admit_detail("start", {"fields": self._fields}, defer_suppression=self._parent is None)

    def _emit(self, kind: str, **more: object) -> None:
        assert self._root and self.context
        rec = _record(kind, self.name, self.context, **more)
        with self._root.lock:
            for label, line in (
                ("human", _human(rec)),
                ("structured", _structured(rec)),
            ):
                if label not in self._root.policy.TRACE_DESTINATIONS or label in self._root.failures:
                    continue
                try:
                    sink = getattr(self._root.sinks, label)
                    with self._root.endpoint_locks[label]:
                        ack = sink(line)
                    if ack is not None:
                        raise TraceSinkError("sink acknowledgement was not None")
                except Exception as exc:
                    name = _exc_name(exc)
                    self._root.failures[label] = name
                    self._local_failure = self._local_failure or name

    def _suppress(self, reason: str) -> None:
        assert self._root
        if not self._root.suppression:
            self._root.suppression = True
            root = self._root.root_handle
            if root is not None:
                root._emit(
                    "detail_suppressed",
                    reason=reason,
                    omitted_detail_records=self._root.omitted_detail,
                    omitted_child_operations=self._root.omitted_children,
                    counts_saturated=self._root.saturated,
                )

    def _admit_detail(self, kind: str, more: dict[str, object], *, defer_suppression: bool = False) -> bool:
        assert self._root and self.context
        rec = _record(kind, self.name, self.context, **more)
        charge = sum(
            len(line.encode())
            for label, line in (
                ("human", _human(rec)),
                ("structured", _structured(rec)),
            )
            if label in self._root.policy.TRACE_DESTINATIONS
        )
        with self._root.lock:
            reason = (
                "event_limit"
                if self._root.events >= self._root.policy.TRACE_MAX_DETAIL_EVENTS
                else "byte_limit"
                if self._root.bytes + charge > self._root.policy.TRACE_MAX_BYTES
                else None
            )
            if reason:
                self._root.increment("omitted_detail")
                if defer_suppression:
                    self._root.pending_suppression = reason
                else:
                    self._suppress(reason)
                return False
            self._root.events += 1
            self._root.bytes += charge
        self._emit(kind, **more)
        return True

    def note(self, event: str, /, **fields: object) -> None:
        if not self._entered or self._closed:
            raise TraceInputError("trace handle is not active")
        _valid_name(event)
        assert self._root
        clean = _mapping(fields, self._root.policy)
        if self._admitted and self._detail_allowed() and self._root.policy.TRACE_LEVEL == "detail":
            self._admit_detail("note", {"event": event, "fields": clean})

    def result(self, **fields: object) -> None:
        if not self._entered or self._closed:
            raise TraceInputError("trace handle is not active")
        assert self._root
        self._reported = _mapping(fields, self._root.policy)

    def __exit__(self, typ: type[BaseException] | None, value: BaseException | None, tb: object) -> Literal[False]:
        try:
            if self._admitted:
                elapsed = max(0.0, (time.monotonic() - self._start) * 1000)
                more: dict[str, object] = {
                    "execution": "returned" if typ is None else "failed" if issubclass(typ, Exception) else "cancelled",
                    "reported": self._reported is not None,
                    "elapsed_ms": round(elapsed, 1),
                }
                if self._reported is not None:
                    more["fields"] = self._reported
                if typ is not None:
                    more["exception_type"] = _exc_name(value)
                assert self._root
                if self._parent is None:
                    more.update(
                        omitted_detail_records=self._root.omitted_detail,
                        omitted_child_operations=self._root.omitted_children,
                        counts_saturated=self._root.saturated,
                    )
                try:
                    self._emit("end", **more)
                except BaseException:
                    # A body exception or cancellation always retains priority.
                    if typ is None:
                        raise
        finally:
            self._closed = True
            for capture in self._callback_captures:
                capture.close()
            if self._token is not None:
                _current.reset(self._token)
            if self._parent is None and self._root is not None:
                _release_endpoint_locks(self._root)
        return False


class _Factory:
    def __init__(
        self,
        resolver: Callable[[], TracePolicy],
        sinks: TraceSinks,
        parent: TraceContext | None = None,
        attempt: TraceAttempt | None = None,
    ) -> None:
        self.resolver, self.sinks, self.parent, self.attempt = (
            resolver,
            sinks,
            parent,
            attempt,
        )

    def __call__(self, name: str, /, **fields: object) -> TraceHandle:
        _valid_name(name)
        # Parent context is chosen at entry, so a manager created before a
        # parent opens still nests when it is entered inside that parent.
        return TraceHandle(name, dict(fields), self, None)


def _env_policy() -> TracePolicy:
    values: dict[str, object] = {}
    for key in TracePolicy.__dataclass_fields__:
        raw = os.environ.get(key)
        if raw is None:
            continue
        if raw == "":
            raise TraceConfigurationError("empty environment setting")
        if key == "TRACE_ENABLED":
            if raw.lower() not in {"true", "false"}:
                raise TraceConfigurationError("invalid boolean")
            values[key] = raw.lower() == "true"
        elif key in {"TRACE_LEVEL"}:
            values[key] = raw
        elif key in {"TRACE_SCOPE", "TRACE_DESTINATIONS"}:
            values[key] = tuple(x.strip(" ") for x in raw.split(","))
        else:
            if not raw.isascii() or not raw.isdecimal():
                raise TraceConfigurationError("invalid integer")
            values[key] = int(raw)
    return TracePolicy(**values)


def _stderr(line: str) -> None:
    if sys.stderr.write(line) != len(line):
        raise TraceSinkError("short stderr write")
    sys.stderr.flush()


_default = _Factory(_env_policy, TraceSinks(human=_stderr))


def trace(name: str, /, **fields: object) -> TraceHandle:
    """Use the ambient binding when a host has established one."""
    _valid_name(name)
    handle = TraceHandle(name, dict(fields), _default, None)
    handle._ambient = True
    return handle


def bind_trace(
    policy_resolver: Callable[[], TracePolicy],
    sinks: TraceSinks,
    *,
    parent_context: TraceContext | None = None,
    attempt: TraceAttempt | None = None,
) -> _Factory:
    if not callable(policy_resolver) or not isinstance(sinks, TraceSinks):
        raise TraceConfigurationError("invalid trace binding")
    if parent_context is not None and not isinstance(parent_context, TraceContext):
        raise TraceInputError("invalid parent context")
    if attempt is not None and not isinstance(attempt, TraceAttempt):
        raise TraceInputError("invalid attempt")
    if attempt is not None:
        if parent_context is None:
            raise TraceInputError("attempt requires parent context")
        if (
            attempt.operation_id != parent_context.operation_id
            or attempt.predecessor_span_id != parent_context.span_id
            or attempt.number != parent_context.attempt_number + 1
        ):
            raise TraceInputError("attempt does not follow parent")
    return _Factory(policy_resolver, sinks, parent_context, attempt)


def current_trace_context() -> TraceContext | None:
    handle = _current.get()
    return handle.context if handle else None


class _CapturedCallbackState:
    """The bounded lifetime of one host-owned callback registration."""

    def __init__(self, callback: Callable[..., object], owner: TraceHandle, context: contextvars.Context) -> None:
        self.callback = callback
        self.owner: TraceHandle | None = owner
        self.context: contextvars.Context | None = context
        self.thread_id = threading.get_ident()
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self.owner = None
        self.context = None


class _CapturedCallback:
    """Invoke a callback under a still-active, same-thread trace scope."""

    def __init__(self, state: _CapturedCallbackState) -> None:
        self._state = state
        functools.update_wrapper(self, state.callback, updated=())

    def __call__(self, *args: object, **kwargs: object) -> object:
        state = self._state
        owner = state.owner
        context = state.context
        if state.closed or owner is None or context is None or owner._closed:
            raise TraceInputError("captured callback owner is no longer active")
        if threading.get_ident() != state.thread_id:
            raise TraceInputError("captured callback must run in its registration thread")

        # Context.run restores the invoking context, including when the host
        # callback raises.  A fresh copy prevents one invocation's ContextVar
        # mutations from becoming another invocation's ambient state.
        return context.copy().run(state.callback, *args, **kwargs)


def capture_callback(callback: Callable[..., object]) -> Callable[..., object]:
    """Capture the active trace for a same-thread host callback registration.

    The returned callback is valid only until the active handle closes.  Its
    nested trace operations share the owner's root identity, policy, budgets
    and output health.  It deliberately does not transfer tracing to threads.
    """
    if not callable(callback):
        raise TraceInputError("captured callback must be callable")
    owner = _current.get()
    if owner is None or owner._closed:
        raise TraceInputError("captured callback requires an active trace scope")
    state = _CapturedCallbackState(callback, owner, contextvars.copy_context())
    owner._callback_captures.add(state)
    return _CapturedCallback(state)


def emit_incomplete(observation: TraceIncomplete, *, policy: TracePolicy, sinks: TraceSinks) -> TraceOutputHealth:
    if not isinstance(observation, TraceIncomplete):
        raise TraceInputError("invalid incomplete observation")
    if not isinstance(policy, TracePolicy) or not isinstance(sinks, TraceSinks):
        raise TraceConfigurationError("invalid incomplete configuration")
    sinks.validate(policy)
    root = _Root(policy, sinks)
    root.endpoint_locks = _acquire_endpoint_locks(policy, sinks)
    handle = TraceHandle(observation.operation, {}, _Factory(lambda: policy, sinks), None)
    handle._root = root
    handle.context = observation.context
    try:
        handle._emit("incomplete", execution="incomplete", reason=observation.reason)
        return handle.output_health
    finally:
        _release_endpoint_locks(root)
