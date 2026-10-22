from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


class TracingSpan:
    """Owns one LangSmith parent-span lifecycle end-to-end.

    ``TurnCoordinator`` used to open a raw ``langsmith.trace`` context manager
    and hand-roll ``__enter__``/``__exit__``/``run.end`` across three separate
    exit paths (an early tool-selection-error return, the main ``except``, and
    the outer ``finally``) — exactly the shape that produces "forgot to close
    the span on this one exit path" bugs. This wraps that lifecycle in one
    place; :meth:`close` is idempotent so calling it from more than one exit
    path (or not at all, if construction failed) is always safe.
    """

    def __init__(
        self,
        *,
        name: str,
        inputs: Dict[str, Any],
        tags: List[str],
        metadata: Dict[str, Any],
        project_name: Optional[str] = None,
    ) -> None:
        self._ctx = None
        self.run = None
        try:
            from langsmith import trace as _ls_trace
            self._ctx = _ls_trace(
                name=name,
                run_type="chain",
                inputs=inputs,
                project_name=(
                    project_name
                    or os.environ.get("LANGSMITH_PROJECT")
                    or os.environ.get("LANGCHAIN_PROJECT")
                    or "ChadAi"
                ),
                tags=tags,
                metadata=metadata,
            )
            self.run = self._ctx.__enter__()
        except Exception:
            self._ctx = None
            self.run = None

    def add_metadata(self, metadata: Dict[str, Any]) -> None:
        """Attach extra metadata to the running span. No-op if tracing is unavailable."""
        if self.run is None:
            return
        try:
            self.run.add_metadata(metadata)
        except Exception:
            pass

    def close(self, outputs: Dict[str, Any]) -> None:
        """End the span and exit its context. Idempotent — safe to call from
        more than one exit path; subsequent calls are no-ops."""
        if self._ctx is None:
            return
        ctx = self._ctx
        self._ctx = None
        try:
            if self.run is not None:
                self.run.end(outputs=outputs)
            ctx.__exit__(None, None, None)
        except Exception:
            pass
        finally:
            self.run = None
