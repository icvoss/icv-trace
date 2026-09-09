"""Optional delivery adapters.  They deliberately do not supply trace semantics."""

from __future__ import annotations

import logging
from typing import Any, TextIO

from .core import TraceConfigurationError, TraceSinkError


class TextIOSink:
    """Turn a caller-owned text stream into a complete-line sink.

    The stream is never flushed or closed here.  A short write is a failed
    acknowledgement because a partial diagnostic cannot be treated as sent.
    """

    def __init__(self, stream: TextIO) -> None:
        if not callable(getattr(stream, "write", None)):
            raise TraceConfigurationError("TextIOSink needs a writable text stream")
        self.stream = stream

    def __call__(self, line: str) -> None:
        written = self.stream.write(line)
        if written != len(line):
            raise TraceSinkError("short text write")


def textio_sink(stream: TextIO) -> TextIOSink:
    return TextIOSink(stream)


class LoggingHandlerSink:
    """Explicitly route complete diagnostic lines through one host handler.

    This does not install the handler, alter logger levels, or capture any
    ambient logging context.
    """

    def __init__(self, handler: logging.Handler, *, logger_name: str = "icv_icv_trace") -> None:
        if not isinstance(handler, logging.Handler):
            raise TraceConfigurationError("handler must be logging.Handler")
        self.handler = handler
        self.logger_name = logger_name

    def __call__(self, line: str) -> None:
        """Deliver through the supplied handler without mutating host logging.

        ``Handler.handle`` has no complete-line acknowledgement and a
        ``StreamHandler`` can call ``handleError`` instead of surfacing a write
        error.  The trial core will therefore see a normal ``None`` return in
        those cases.  This is an intentional, measured compatibility loss.
        """
        record = logging.LogRecord(
            self.logger_name,
            logging.INFO,
            "",
            0,
            "%s",
            (line.rstrip("\n"),),
            None,
        )
        self.handler.handle(record)


class EliotLoggerSink:
    """Private Eliot Logger adapter, imported only when selected.

    Eliot has no complete-line destination contract, output health or policy
    model.  This adapter only projects an already-rendered trace line.
    """

    def __init__(self, logger: object) -> None:
        self.logger = logger

    def __call__(self, line: str) -> None:
        """Use Eliot's private logger-routing seam for a trial transport.

        Eliot's public ``Message.log(logger=...)`` records ``logger`` as data
        and writes through the global default destination.  ``log_message``
        accepts ``__eliot_logger__`` for instance routing.  This remains an
        experimental private seam with no complete-line acknowledgement: a
        swallowed Eliot destination failure can look healthy to the core.
        """
        try:
            from eliot import log_message  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TraceConfigurationError("Eliot adapter requires eliot") from exc
        log_message(
            message_type="icv_icv_trace.line",
            line=line.rstrip("\n"),
            __eliot_logger__=self.logger,
        )


class OpenTelemetrySink:
    """Experimental event bridge for an explicitly supplied span.

    Ended-span exporters cannot deliver immediate notes, so this is a delivery
    transport comparison only.  It never configures a provider or global SDK.
    """

    def __init__(self, span: object) -> None:
        if not callable(getattr(span, "add_event", None)):
            raise TraceConfigurationError("OpenTelemetrySink needs a span")
        self.span = span

    def __call__(self, line: str) -> None:
        span: Any = self.span
        span.add_event("icv_icv_trace.line", {"line": line.rstrip("\n")})
