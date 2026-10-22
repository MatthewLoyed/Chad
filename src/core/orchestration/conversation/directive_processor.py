"""
DirectiveProcessor — Reply Directive Dispatch

Extracted from ConversationOrchestrator to satisfy the single-responsibility
principle. Owns the registry of directive handlers and the dispatch loop
that routes ParsedResponse directives to those handlers.

The orchestrator holds a DirectiveProcessor instance and proxies the public
API methods (register_directive_handler, etc.) through to it so external
callers (server.py, SessionManager) are unaffected.
"""

import asyncio
import logging
from typing import Callable, Dict, List, Optional

from src.shared.logger import setup_logger
from src.core.intelligence.reply_parser import ParsedResponse, ReplyDirective

logger = setup_logger("DirectiveProcessor")


class DirectiveProcessor:
    """
    Manages a registry of reply directive handlers and dispatches directives
    from a ParsedResponse to the appropriate handler.

    Directive handlers are callables that accept a single ReplyDirective
    argument.  They may be sync or async — both are supported.

    Usage::

        processor = DirectiveProcessor()
        processor.register("learn", my_async_learn_handler)
        await processor.process(parsed_response)
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, Callable] = {}

    # ------------------------------------------------------------------
    # Public registration API (proxied from ConversationOrchestrator)
    # ------------------------------------------------------------------

    def register(self, directive_type: str, handler: Callable) -> None:
        """Register a handler for a specific directive type.

        Args:
            directive_type: Directive name, e.g. ``"learn"``, ``"media"``.
            handler: Callable that accepts a :class:`ReplyDirective`.  May be
                a plain function or a coroutine function.
        """
        self._handlers[directive_type] = handler
        logger.info(f"[ DIRECTIVE ] Registered handler: {directive_type}")

    def unregister(self, directive_type: str) -> None:
        """Unregister the handler for *directive_type* (no-op if not found)."""
        if directive_type in self._handlers:
            del self._handlers[directive_type]
            logger.info(f"[ DIRECTIVE ] Unregistered handler: {directive_type}")

    def list_handlers(self) -> List[str]:
        """Return the directive type names that have registered handlers."""
        return list(self._handlers.keys())

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def process(self, parsed_response: ParsedResponse) -> None:
        """Dispatch each directive in *parsed_response* to its handler.

        Unknown directive types are logged at DEBUG level and silently skipped.
        Handler exceptions are caught and logged; they never propagate so a
        single bad handler cannot abort the turn.
        """
        if not parsed_response or not parsed_response.directives:
            return

        for directive in parsed_response.directives:
            directive_type = directive.directive_type.value

            if directive_type not in self._handlers:
                logger.debug(
                    f"[ DIRECTIVE ] No handler for directive '{directive_type}' "
                    f"(data: {directive.data})"
                )
                continue

            handler = self._handlers[directive_type]
            try:
                logger.debug(
                    f"[ DIRECTIVE ] Dispatching '{directive_type}' "
                    f"(data: {directive.data})"
                )
                if asyncio.iscoroutinefunction(handler):
                    await handler(directive)
                else:
                    handler(directive)
            except Exception as exc:
                logger.error(
                    f"[ DIRECTIVE ] Handler failed for '{directive_type}': {exc}",
                    exc_info=True,
                )


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------

async def handle_learn_directive(directive: ReplyDirective) -> None:
    """Legacy text directives have no approval channel and cannot write memory."""
    if directive.data:
        logger.warning("[ DIRECTIVE ] Ignored learn directive; memory changes require the approved manage_user_memory tool")
