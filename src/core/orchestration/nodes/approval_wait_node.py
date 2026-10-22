import logging
from typing import Dict, Any, Callable
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger("ChadSupervisor")


def make_approval_wait_node() -> Callable:
    """No-op gate node — graph pauses after this via interrupt_after for plan approval."""

    async def approval_wait_node(state: Dict[str, Any], config: RunnableConfig):
        logger.info("[ PLANNER ] Initial plan ready — awaiting user approval (graph paused).")
        return {}

    return approval_wait_node
