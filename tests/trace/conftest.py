"""Test configuration for the packaged trace suite.

Normal development runs use the editable install declared by the repository.
Built-wheel jobs set ``ICV_TRACE_REQUIRE_WHEEL=1`` and remove ``src`` from the
import path, so their assertions prove the installed artefact is used.
"""

from __future__ import annotations

import os
from pathlib import Path


def pytest_sessionstart() -> None:
    if os.environ.get("ICV_TRACE_REQUIRE_WHEEL") != "1":
        return
    import icv_trace

    origin = Path(icv_trace.__file__).resolve()
    assert "site-packages" in str(origin), f"the source tree shadowed the wheel: {origin}"
