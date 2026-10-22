"""
TurnResponseRouter - routes model response payloads to type-specific handlers.

Extracted from ConversationOrchestrator (issue #375) to separate response-type
routing (audio/tool_call/tool_call_batch/turn_complete/thinking) from turn
lifecycle management (start/finalize/cancel).
"""

import logging
import time
from typing import Any, Dict, List, Optional

from src.application.events import EventBus, ThinkingUpdate, TurnCompleted, AudioResponseReceived

logger = logging.getLogger("ConversationOrchestrator")


class TurnResponseRouter:
    """
    Dispatches a single model response payload to the handler for its type.

    Mutates the passed-in `turn` (TurnContext) and pending tool-call/response
    lists in place; the orchestrator owns that state and its lifetime.
    """

    def __init__(self, tool_coord, state_coord, event_bus: EventBus):
        self.tool_coord = tool_coord
        self.state_coord = state_coord
        self.event_bus = event_bus

    async def route(
        self,
        response: Dict[str, Any],
        turn,
        pending_tool_calls: List[Dict[str, Any]],
        pending_tool_responses: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Route `response` to its type-specific handler. Returns an audio transcript, if any."""
        response_type = response.get('type')
        logger.debug(f"[ ORCHESTRATOR ] Processing response type: {response_type}")

        if response_type == 'audio':
            return self.handle_audio_response(response, turn)
        elif response_type == 'tool_call':
            await self.handle_tool_call(response, turn, pending_tool_calls, pending_tool_responses)
        elif response_type == 'tool_call_batch':
            await self.handle_tool_batch(response, turn, pending_tool_calls, pending_tool_responses)
        elif response_type == 'turn_complete':
            await self.handle_turn_complete(response, turn)
        elif response_type in ('thinking', 'reasoning'):
            await self.handle_thinking_update(response)
        elif response_type == 'grounding_metadata':
            # Pass through to event bus for UI
            pass
        else:
            logger.debug(f"[ ORCHESTRATOR ] Unhandled response type: {response_type}")
        return None

    def handle_audio_response(self, response: Dict[str, Any], turn) -> Optional[str]:
        """Handle audio response from model. Returns the transcript, if any, for the caller to store."""
        turn.audio_played = True

        transcript = response.get('transcript')

        self.event_bus.publish(AudioResponseReceived(
            audio_data=response.get('data'),
            turn_id=turn.turn_id,
            mute_input=bool(response.get('_mute_input', False))
        ))

        return transcript

    async def handle_tool_call(
        self,
        response: Dict[str, Any],
        turn,
        pending_tool_calls: List[Dict[str, Any]],
        pending_tool_responses: List[Dict[str, Any]],
    ) -> None:
        """Handle single tool call."""
        tool_name = response.get('name', 'unknown')
        tool_id = response.get('id', 'unknown')

        logger.info(f"[ ORCHESTRATOR ] Executing tool: {tool_name} (id: {tool_id})")

        pending_tool_calls.append(response)

        turn.tool_count += 1
        turn.tools_executed.append(tool_name)

        await self.state_coord.transition_to("thinking")

        try:
            # ToolCoordinator's actual API is execute_single(tool_call_dict, ...) —
            # not execute_tool(tool_name=, tool_args=, tool_id=). It also has no
            # 'args' key; response_harvester.py emits 'params' (#641 audit: this
            # mismatch meant every voice-mode tool call silently failed here,
            # caught by the except below as "Tool execution failed").
            result = await self.tool_coord.execute_single({
                "id": tool_id,
                "name": tool_name,
                "params": response.get('params', {}),
            })

            pending_tool_responses.append({
                'tool_name': tool_name,
                'tool_id': tool_id,
                'result': result.result,
                'success': result.success
            })

        except Exception as e:
            logger.error(f"[ ORCHESTRATOR ] Tool execution failed: {e}")
            pending_tool_responses.append({
                'tool_name': tool_name,
                'tool_id': tool_id,
                'result': None,
                'success': False,
                'error': str(e)
            })

    async def handle_tool_batch(
        self,
        response: Dict[str, Any],
        turn,
        pending_tool_calls: List[Dict[str, Any]],
        pending_tool_responses: List[Dict[str, Any]],
    ) -> None:
        """Handle batch of tool calls."""
        tool_calls = response.get('calls', [])
        logger.info(f"[ ORCHESTRATOR ] Executing tool batch ({len(tool_calls)} tools)")

        pending_tool_calls.extend(tool_calls)

        turn.tool_count += len(tool_calls)
        for call in tool_calls:
            turn.tools_executed.append(call.get('name', 'unknown'))

        await self.state_coord.transition_to("thinking")

        try:
            # ToolCoordinator's actual API is execute_batch (#641 audit — same
            # method-name mismatch as handle_tool_call above). `tool_calls` items
            # already carry 'id'/'name'/'params' from response_harvester.py, which
            # is the shape execute_batch expects.
            results = await self.tool_coord.execute_batch(tool_calls)

            for result in results:
                pending_tool_responses.append({
                    'tool_name': result.tool_name,
                    'tool_id': result.tool_id,
                    'result': result.result,
                    'success': result.success,
                    'error': result.error
                })

        except Exception as e:
            logger.error(f"[ ORCHESTRATOR ] Tool batch execution failed: {e}")

    async def handle_turn_complete(self, response: Dict[str, Any], turn) -> None:
        """
        Handle turn completion signal from model.

        Note: For SessionManager integration, we DON'T automatically finalize the
        turn here because SessionManager's _handle_turn_complete has voice-specific
        logic (audio playback, followup mode, etc.) that must run first.
        The SessionManager/orchestrator's finalize_turn() runs it when appropriate.
        """
        logger.info(f"[ ORCHESTRATOR ] Turn complete signal received for {turn.turn_id}")

        model_response = response.get('text') or response.get('response')
        turn.model_response = model_response

        if model_response:
            try:
                from src.core.intelligence.reply_parser import parse_response
                turn.parsed_response = parse_response(model_response)

                if turn.parsed_response.directives:
                    logger.debug(
                        f"[ ORCHESTRATOR ] Parsed "
                        f"{len(turn.parsed_response.directives)} reply directives"
                    )
            except Exception as e:
                logger.error(f"[ ORCHESTRATOR ] Failed to parse reply directives: {e}")

        self.event_bus.publish(TurnCompleted(
            turn_id=turn.turn_id,
            turn_context=turn,
            duration=time.time() - turn.start_time,
            tool_count=turn.tool_count,
            model_response=model_response
        ))

        logger.debug("[ ORCHESTRATOR ] Turn complete handled (finalization deferred to SessionManager)")

    async def handle_thinking_update(self, response: Dict[str, Any]) -> None:
        """Handle thinking/reasoning update from model."""
        thinking_text = response.get('text', '')
        append = response.get('append', True)  # Default to True for fragments

        self.event_bus.publish(ThinkingUpdate(
            thinking_text=thinking_text,
            thinking_type=response.get('type', 'thinking'),
            append=append
        ))
