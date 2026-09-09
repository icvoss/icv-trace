"""Independent checks for same-thread host callback capture."""

from __future__ import annotations

import asyncio
import gc
import inspect
import threading
import weakref

import pytest

from icv_trace import (
    TraceInputError,
    TracePolicy,
    TraceSinks,
    bind_trace,
    capture_callback,
    current_trace_context,
    trace,
)


class Lines:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)


def bound(lines: Lines, **changes: object):
    values = {
        "TRACE_ENABLED": True,
        "TRACE_LEVEL": "detail",
        "TRACE_DESTINATIONS": ("human",),
        "TRACE_MAX_DETAIL_EVENTS": 100,
        "TRACE_MAX_BYTES": 16384,
        "TRACE_MAX_BASELINE_CHILDREN": 48,
    }
    values.update(changes)
    return bind_trace(lambda: TracePolicy(**values), TraceSinks(human=lines))


def test_capture_requires_active_scope_and_callable() -> None:
    with pytest.raises(TraceInputError):
        capture_callback(lambda: None)
    with bound(Lines())("scan"), pytest.raises(TraceInputError):
        capture_callback(object())


def test_callback_children_have_captured_parent_and_restore_invoker_context() -> None:
    lines = Lines()
    call = bound(lines)
    observed = []

    def registered_callback() -> None:
        assert current_trace_context() == root.context
        with trace("scan.playwright.response") as child:
            observed.append(child.context)

    with call("scan") as root:
        callback = capture_callback(registered_callback)
        callback()
        assert current_trace_context() == root.context

    assert observed[0].trace_id == root.context.trace_id
    assert observed[0].parent_span_id == root.context.span_id


def test_callback_rejects_late_use_and_releases_captured_owner() -> None:
    call = bound(Lines())
    with call("scan") as root:
        owner = weakref.ref(root)
        callback = capture_callback(lambda: None)

    with pytest.raises(TraceInputError, match="no longer active"):
        callback()
    del root
    gc.collect()
    assert owner() is None


def test_callback_rejects_wrong_os_thread() -> None:
    call = bound(Lines())
    result: list[BaseException] = []
    invoked: list[bool] = []
    with call("scan"):
        callback = capture_callback(lambda: invoked.append(True))
        worker = threading.Thread(target=lambda: _capture_failure(callback, result), daemon=True)
        worker.start()
        worker.join()

    assert len(result) == 1
    assert isinstance(result[0], TraceInputError)
    assert "registration thread" in str(result[0])
    assert invoked == []


def test_callback_preserves_registered_signature_for_dispatchers() -> None:
    def one_argument(route: object) -> None:
        return None

    def two_arguments(source: object, message: object) -> None:
        return None

    with bound(Lines())("scan"):
        captured_route = capture_callback(one_argument)
        captured_binding = capture_callback(two_arguments)

    assert inspect.signature(captured_route) == inspect.signature(one_argument)
    assert inspect.signature(captured_binding) == inspect.signature(two_arguments)


def test_callback_signature_metadata_cannot_replace_its_lifetime_state() -> None:
    def callback(route: object) -> None:
        return None

    callback._state = "caller metadata"  # type: ignore[attr-defined]
    with bound(Lines())("scan"):
        captured = capture_callback(callback)

    with pytest.raises(TraceInputError, match="no longer active"):
        captured(object())


def _capture_failure(callback, result: list[BaseException]) -> None:
    try:
        callback()
    except BaseException as exc:
        result.append(exc)


def test_callback_preserves_original_exception_and_invoking_context() -> None:
    call = bound(Lines())
    marker = RuntimeError("caller failure")
    with call("scan") as root:
        callback = capture_callback(lambda: (_ for _ in ()).throw(marker))
        with pytest.raises(RuntimeError) as caught:
            callback()
        assert caught.value is marker
        assert current_trace_context() == root.context


def test_callback_shares_root_child_admission() -> None:
    lines = Lines()
    call = bound(lines, TRACE_MAX_BASELINE_CHILDREN=1)

    def callback() -> None:
        with trace("scan.playwright.response"):
            pass

    with call("scan"):
        registered = capture_callback(callback)
        registered()
        registered()

    starts = [line for line in lines.lines if "start scan.playwright.response" in line]
    assert len(starts) == 1
    assert "omitted_child_operations=1" in "".join(lines.lines)


def test_reentrant_callbacks_share_the_one_root_child_cap() -> None:
    lines = Lines()
    call = bound(lines, TRACE_MAX_BASELINE_CHILDREN=1)
    children = []
    with call("scan") as root:

        def inner() -> None:
            with trace("scan.playwright.inner") as child:
                children.append(child.context)

        nested = capture_callback(inner)

        def outer() -> None:
            with trace("scan.playwright.outer") as child:
                children.append(child.context)
            nested()

        capture_callback(outer)()

    assert {child.trace_id for child in children} == {root.context.trace_id}
    assert {child.parent_span_id for child in children} == {root.context.span_id}
    starts = [line for line in lines.lines if " start scan.playwright." in line]
    assert len(starts) == 1
    assert "omitted_child_operations=1" in "".join(lines.lines)


def test_concurrent_same_thread_scans_keep_identity_and_parentage_isolated() -> None:
    async def scan(label: str):
        lines = Lines()
        call = bound(lines)
        children = []
        with call("scan") as root:

            def callback() -> None:
                with trace("scan.playwright.response") as child:
                    children.append(child.context)

            registered = capture_callback(callback)
            await asyncio.sleep(0)
            registered()
            await asyncio.sleep(0)
        return root.context, children[0], lines.lines

    async def run_scans():
        return await asyncio.gather(scan("one"), scan("two"))

    first, second = asyncio.run(run_scans())
    first_root, first_child, first_lines = first
    second_root, second_child, second_lines = second
    assert first_root.trace_id != second_root.trace_id
    assert first_child.trace_id == first_root.trace_id
    assert first_child.parent_span_id == first_root.span_id
    assert second_child.trace_id == second_root.trace_id
    assert second_child.parent_span_id == second_root.span_id
    assert first_root.trace_id not in "".join(second_lines)
    assert second_root.trace_id not in "".join(first_lines)
