"""Turn-execution collaborators for :class:`TurnCoordinator`.

This package decomposes what used to be a single ~1.4k-line ``turn_coordinator``
god object into focused, single-responsibility helpers. ``TurnCoordinator``
remains the public orchestrator and composes these collaborators:

- :class:`~.history_loader.TurnHistoryLoader` — load/normalize chat history.
- :class:`~.mode_resolver.TurnModeResolver` — conversation/agent/plan routing.
- :class:`~.tool_selector.TurnToolSelector` — per-turn tool/skill selection.
- :class:`~.consolidation.TurnConsolidator` — preflight + compaction loop.
- :class:`~.event_capture.TurnEventCapture` — thinking-event capture/consolidation.
- :class:`~.telemetry.TurnTelemetry` — token/thinking/usage streaming hooks.
- :class:`~.tracing.TracingSpan` — LangSmith parent-span lifecycle (single close path).
- :class:`~.state_builder.TurnStateBuilder` — scope resolution + ``ChadState`` assembly.
- :class:`~.response_processor.TurnResponsePostProcessor` — final_state → response/outcome.
- :class:`~.status_emitter.TurnStatusEmitter` — transient pre-first-token UI narration.
- :class:`~.model_preloader.TurnModelPreloader` — supervisor-model residency (#592).
- :class:`~.tool_preparation.TurnToolPreparation` — knowledge prefetch + sticky local tools.
- :mod:`~.status_labels` — shared rotating status strings (leaf; no internal imports).
- :mod:`~.scratch` — per-turn state, ContextVar-isolated between concurrent turns.
- :class:`~.pipeline.TurnPipeline` — executes one turn; owns that turn's working state.
- :mod:`~.metrics` — stop-reason classification + metrics scaffold.
"""

from src.core.orchestration.turn.history_loader import TurnHistoryLoader
from src.core.orchestration.turn.mode_resolver import TurnModeResolver
from src.core.orchestration.turn.tool_selector import TurnToolSelector, ToolSelection
from src.core.orchestration.turn.consolidation import TurnConsolidator
from src.core.orchestration.turn.event_capture import TurnEventCapture
from src.core.orchestration.turn.telemetry import TurnTelemetry
from src.core.orchestration.turn.perf import TurnPerfTracker
from src.core.orchestration.turn.tracing import TracingSpan
from src.core.orchestration.turn.state_builder import TurnStateBuilder
from src.core.orchestration.turn.response_processor import TurnResponsePostProcessor, TurnOutcome
from src.core.orchestration.turn.status_emitter import TurnStatusEmitter
from src.core.orchestration.turn.model_preloader import TurnModelPreloader
from src.core.orchestration.turn.tool_preparation import TurnToolPreparation
from src.core.orchestration.turn.scratch import TurnScratch, current_scratch, begin_turn
from src.core.orchestration.turn.pipeline import TurnPipeline
from src.core.orchestration.turn import metrics
from src.core.orchestration.turn import status_labels

__all__ = [
    "TurnHistoryLoader",
    "TurnModeResolver",
    "TurnToolSelector",
    "ToolSelection",
    "TurnConsolidator",
    "TurnEventCapture",
    "TurnTelemetry",
    "TurnPerfTracker",
    "TracingSpan",
    "TurnStateBuilder",
    "TurnResponsePostProcessor",
    "TurnOutcome",
    "TurnStatusEmitter",
    "TurnModelPreloader",
    "TurnToolPreparation",
    "TurnPipeline",
    "TurnScratch",
    "current_scratch",
    "begin_turn",
    "metrics",
    "status_labels",
]
