"""Turn-initiator classification shared by LimbCoordinator and its collaborators.

Mirrors TurnInitiator in src/core/orchestration/conversation_orchestrator.py.
"""

VOICE_SESSION_ID = "main:default:voice:limb"

# Only these TurnInitiator values belong in the chat UI. USER is a real user message;
# FOLLOWUP is a model-requested continuation of that same user turn. Every other
# initiator (SYSTEM = cron/heartbeat, SKILL, WEBHOOK = gmail) is an AUTONOMOUS turn:
# its live thinking/tool stream must NEVER leak into whatever chat the user has open —
# it surfaces only as an AUTONOMOUS_NOTIFICATION toast at completion.
CHAT_INITIATORS = frozenset({"user", "followup"})


def initiator_to_str(initiator) -> str:
    return initiator.value if hasattr(initiator, "value") else str(initiator)


def is_autonomous_initiator(initiator) -> bool:
    """True for any non-user-facing turn (system/cron/heartbeat, skill, webhook/gmail)."""
    return initiator_to_str(initiator) not in CHAT_INITIATORS
