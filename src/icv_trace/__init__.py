"""Bounded operation diagnostics, correlation and output health for Python hosts."""

from .adapters import TextIOSink, textio_sink
from .core import (
    TraceAttempt,
    TraceConfigurationError,
    TraceContext,
    TraceHandle,
    TraceIncomplete,
    TraceInputError,
    TraceOutputHealth,
    TracePolicy,
    TraceSinkError,
    TraceSinks,
    bind_trace,
    capture_callback,
    current_trace_context,
    emit_incomplete,
    trace,
)

__all__ = [
    "TextIOSink",
    "TraceAttempt",
    "TraceConfigurationError",
    "TraceContext",
    "TraceHandle",
    "TraceIncomplete",
    "TraceInputError",
    "TraceOutputHealth",
    "TracePolicy",
    "TraceSinkError",
    "TraceSinks",
    "bind_trace",
    "capture_callback",
    "current_trace_context",
    "emit_incomplete",
    "textio_sink",
    "trace",
]

__version__ = "0.1.0rc2"
